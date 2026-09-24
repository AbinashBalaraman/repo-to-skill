"""r2s command line.

`extract` is a first-class command, not a hidden stage: the router consumes a reviewed
inventory, never a draft, so review has to be a step the user can actually take.
"""

import argparse
import json
import sys
from pathlib import Path

from . import __version__, config
from . import execute as execute_mod
from . import profiler as profiler_pkg
from .capability import invariants, report, router
from .capability import profiles as profiles_mod
from .capability.profiles import ProfileError
from .capability.vocab import CapabilityVocab, StandinCatalog, VocabError
from .emit import EmitError, emit_skill
from .extract import catalog as catalog_mod
from .extract import effects as effects_mod
from .extract import gapfill as gapfill_mod
from .extract import pipeline as pipeline_mod
from .extract import review as review_mod
from .source import build_snapshot
from .source.local import SourceError, detect_git_meta, fetch_git, resolve_local
from .util.io import dump_json, load_json, write_text
from .validate import schema as schema_mod
from .validate import secrets as secrets_mod
from .validate import spec as spec_mod


def _context():
    """Load and validate every piece of shared data the pipeline trusts.

    Catalogs are validated here because they are trusted input that drives routing:
    an unknown capability ID inside a provider entry would otherwise fail much later,
    or silently widen a requirement.
    """
    vocab = CapabilityVocab.load()
    standins = StandinCatalog.load()
    profiles = profiles_mod.load_profiles()

    failures = []
    failures += schema_mod.validate_catalog(catalog_mod.ProviderCatalog.load(), vocab)
    failures += schema_mod.validate_standin_catalog(standins, vocab)
    for pid, profile in sorted(profiles.items()):
        failures += [f"profile {pid}: {f}" for f in schema_mod.validate_profile(profile.to_dict())]
        # Parenthesised deliberately: `|` binds looser than `-`, so the unparenthesised
        # form would be `native | (mcp - vocab.ids)` and would miss native IDs entirely.
        unknown = sorted((set(profile.native) | set(profile.mcp)) - vocab.ids)
        if unknown:
            failures.append(f"profile {pid}: unknown capability ids: {unknown}")

    if failures:
        raise VocabError("bundled data failed validation:\n  " + "\n  ".join(failures))
    return vocab, standins, profiles


def cmd_profiles(args):
    profiles = profiles_mod.load_profiles()
    for pid in sorted(profiles):
        profile = profiles[pid]
        warning = profiles_mod.check_expiry(profile)
        flag = "  [EXPIRED]" if warning else ""
        print(
            f"{pid:14} {profile.name:24} native={len(profile.native):2} "
            f"mcp={len(profile.mcp)}{flag}"
        )
    return 0


def cmd_extract(args):
    vocab, standins, _ = _context()
    providers = catalog_mod.ProviderCatalog.load()
    effects_catalog = effects_mod.EffectCatalog.load()

    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.url:
        root = fetch_git(args.url, sha=args.sha, dest=out_dir / "_src")
        source = {
            "kind": "git",
            "url": args.url,
            "sha": args.sha,
            "subdir": args.subdir,
            "license": None,
        }
    else:
        root = resolve_local(args.path or ".")
        url, sha = detect_git_meta(root)
        source = {
            "kind": "local",
            "url": url,
            "sha": args.sha or sha,
            "subdir": args.subdir,
            "license": None,
        }

    snapshot = build_snapshot(root, subdir=args.subdir)
    print(f"scanned {len(snapshot)} files under {root}")
    if snapshot.truncated:
        print(f"WARNING: scan truncated ({snapshot.skipped_reason})")

    result = profiler_pkg.profile(snapshot)
    print(f"class: {result.repo_class} ({result.class_confidence})")
    print(f"entrypoints: {len(result.entrypoints)}")

    draft, warnings = pipeline_mod.extract(
        snapshot,
        result,
        vocab,
        providers,
        effects_catalog,
        standins,
        source=source,
        llm_proposals=args.llm_proposals,
    )

    verdict = profiler_pkg.assess_suitability(snapshot, result, draft["operations"])

    if verdict.status == "not-suitable":
        path = write_text(
            out_dir / "NOT-SUITABLE.md", verdict.render_markdown(source.get("url") or str(root))
        )
        print(f"\nNOT SUITABLE -- wrote {path}")
        for reason in verdict.reasons:
            print(f"  - {reason}")
        return 0

    if verdict.status == "knowledge-only":
        print(
            "\nKNOWLEDGE-ONLY: no operations extracted, but there is substantial "
            "documentation. Emit a knowledge skill via Skill_Seekers; this "
            "converter has nothing to add."
        )
        for reason in verdict.reasons:
            print(f"  - {reason}")

    # merge with a previous reviewed inventory where one exists
    previous_path = out_dir / config.INVENTORY_NAME
    changes = []
    if previous_path.exists():
        draft, changes = review_mod.merge_reviewed(draft, load_json(previous_path))

    draft_path = dump_json(out_dir / config.DRAFT_NAME, draft)
    review_text = review_mod.build_review(draft, warnings)
    if changes:
        review_text += "\n\n## Changes since the last review\n\n"
        review_text += "\n".join(f"- {c}" for c in changes) + "\n"
    review_path = write_text(out_dir / config.REVIEW_NAME, review_text)

    failures = schema_mod.validate_inventory(draft, vocab, standins)
    print(f"\noperations: {len(draft['operations'])}")
    print(f"wrote {draft_path}")
    print(f"wrote {review_path}")
    if failures:
        print(f"\nSCHEMA: FAIL ({len(failures)})")
        for failure in failures[:20]:
            print(f"  - {failure}")
        return 1
    print("SCHEMA: OK")
    print("\nNext: review the draft, edit it into inventory.json, then run `r2s convert`.")
    return 0


def cmd_convert(args):
    vocab, standins, profiles = _context()
    inventory = load_json(args.inventory)

    failures = schema_mod.validate_inventory(inventory, vocab, standins)
    if failures:
        print("SCHEMA: FAIL -- refusing to route an invalid inventory")
        for failure in failures:
            print(f"  - {failure}")
        return 1

    if inventory["inventory_status"] == "draft" and not args.accept_unreviewed:
        print("inventory_status is 'draft'. Review it first, or pass --accept-unreviewed.")
        return 1

    if args.accept_unreviewed and inventory["inventory_status"] == "draft":
        inventory = dict(inventory)
        inventory["inventory_status"] = "accepted-unreviewed"

    selected = sorted(profiles) if args.all else [args.profile]
    warnings = []
    failed_any = False

    for pid in selected:
        profile = profiles_mod.get_profile(profiles, pid)
        expiry = profiles_mod.check_expiry(profile)
        if expiry:
            warnings.append(expiry)

        rows = router.resolve(inventory["operations"], profile, vocab, standins)
        summary = router.summarise(rows)
        failures = invariants.verify(inventory["operations"], rows, profile)
        if failures:
            failed_any = True

        built = report.build_report(inventory, profile, rows, summary, failures, warnings)
        print(report.render_report_text(built))

        if args.json_out:
            dump_json(Path(args.json_out), built)

        if args.emit:
            try:
                skill_dir, _, emit_warnings = emit_skill(
                    inventory,
                    profile,
                    Path(args.emit),
                    vocab,
                    standins,
                    accept_unreviewed=args.accept_unreviewed,
                )
            except EmitError as exc:
                print(f"\nEMIT: FAIL -- {exc}")
                failed_any = True
                continue
            print(f"\nEMITTED: {skill_dir}")
            for warning in emit_warnings:
                print(f"  WARNING: {warning}")

            # Validate what we just wrote, rather than trusting the writer.
            spec_failures = spec_mod.validate_skill(skill_dir)
            profile_failures = spec_mod.validate_profile_declaration(
                (skill_dir / "SKILL.md").read_text(encoding="utf-8")
            )
            leaks = secrets_mod.scan_directory(skill_dir)
            if spec_failures or profile_failures or leaks:
                failed_any = True
                for failure in spec_failures:
                    print(f"  SPEC: {failure}")
                for failure in profile_failures:
                    print(f"  PROFILE: {failure}")
                for leak in leaks:
                    print(f"  LEAK: {leak}")
            else:
                print("  spec: OK   profile declared: OK   secret scan: clean")

    # Non-zero exit when an invariant fails, so CI and shell callers can gate on it.
    return 1 if failed_any else 0


def cmd_scan(args):
    """L3: scan an emitted skill for credentials."""
    findings = secrets_mod.scan_directory(Path(args.path))
    print(secrets_mod.format_findings(findings, root=args.path))
    return 1 if findings else 0


def cmd_digest(args):
    """Write the T4 digest: the compact summary a user hands to their own model.

    r2s never calls a model -- it is stdlib-only and offline, which is what makes it safe
    to run over an untrusted repository. T4 is therefore a two-step handoff: this command
    produces the input, the user's model produces proposals, and `extract
    --llm-proposals` applies them. The digest carries the normalised inventory only: no
    file contents, so the model is never handed the repository.
    """
    draft = load_json(args.draft)
    payload = gapfill_mod.digest(draft)
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.out:
        path = write_text(Path(args.out), text)
        print(f"wrote {path}")
        print(
            f"  {len(payload['operations'])} operation(s). Hand this to a model, then "
            f"apply what it returns with:\n"
            f"    r2s extract <repo> --llm-proposals <proposals.json>"
        )
    else:
        sys.stdout.write(text)
    return 0


def cmd_standins(args):
    """List the catalog and say, per stand-in, whether it can actually be run."""
    _, standins, _ = _context()
    rows = execute_mod.describe(standins)
    width = max(len(row["id"]) for row in rows)
    runnable = 0
    for row in rows:
        if row["runnable"] and row["tools_present"]:
            state = "runnable"
            runnable += 1
        elif row["runnable"]:
            state = "blocked: missing " + ", ".join(row["requires_tools"])
        else:
            state = "not runnable"
        print(f"  {row['id']:<{width}}  {row['capability']:<18} fid={row['fidelity']:<5} {state}")
        if not row["runnable"]:
            print(f"  {'':<{width}}  why: {row['reason']}")
    print(f"\n{len(rows)} stand-in(s); {runnable} runnable in this environment")
    return 0


def cmd_run(args):
    """Execute the stand-in-backed operations of a routed report.

    This is the step that turns a report into output. It refuses to invent anything: an
    operation whose stand-in is not runnable is reported as such and counted as a gap,
    never quietly skipped.
    """
    _, standins, _ = _context()
    report_data = load_json(args.report)
    rows = report_data.get("rows") or []

    candidates = [row for row in rows if row.get("binding") == "script" and row.get("stand_in")]

    if args.operation:
        wanted = set(args.operation)
        known = {row["id"] for row in candidates}
        unknown = sorted(wanted - known)
        if unknown:
            available = ", ".join(sorted(known)) or "none"
            print(
                f"error: no stand-in-backed operation named {', '.join(unknown)}.\n"
                f"  operations with a runnable binding: {available}",
                file=sys.stderr,
            )
            return 2
        selected = [row for row in candidates if row["id"] in wanted]
    elif args.all:
        selected = candidates
    else:
        print(
            "error: pass --operation ID (repeatable) or --all to choose what to run.",
            file=sys.stderr,
        )
        return 2

    if not selected:
        print("nothing to run: the report has no stand-in-backed operations.")
        return 0

    inputs = {}
    if args.inputs_file:
        inputs = load_json(args.inputs_file)
        if not isinstance(inputs, dict):
            print("error: --inputs-file must contain a JSON object", file=sys.stderr)
            return 2
    if args.inputs:
        try:
            inline = json.loads(args.inputs)
        except json.JSONDecodeError as exc:
            print(f"error: --inputs is not valid JSON: {exc}", file=sys.stderr)
            return 2
        if not isinstance(inline, dict):
            print("error: --inputs must be a JSON object", file=sys.stderr)
            return 2
        inputs = {**inputs, **inline}

    out_root = Path(args.out)
    results = []
    for row in selected:
        if args.dry_run:
            entry = standins.get(row["stand_in"])
            reason = execute_mod.unavailable_reason(entry)
            print(f"  would run {row['id']} -> {row['stand_in']}")
            if reason:
                print(f"    cannot run: {reason}")
            results.append({"operation": row["id"], "status": "dry-run"})
            continue

        result = execute_mod.run(
            row["stand_in"],
            standins,
            inputs=inputs,
            workdir=out_root / row["id"],
            operation=row["id"],
            optional=row.get("optional"),
            credentials=row.get("credentials"),
        )
        results.append({"operation": row["id"], **result.to_dict()})

        mark = "ok" if result.ok else result.status
        print(f"  {row['id']} -> {row['stand_in']}: {mark}")
        if result.reason:
            print(f"    {result.reason}")
        for artifact in result.artifacts:
            print(f"    wrote {out_root / row['id'] / artifact['path']}")
        for note in result.notes:
            print(f"    note: {note}")

    if args.json_out:
        dump_json(Path(args.json_out), {"kind": "r2s.run", "results": results})

    if args.dry_run:
        return 0

    # Fail closed on required operations only. An optional operation that could not run
    # is a reported gap, not a build failure -- the same distinction the router makes.
    blocked = [
        row["id"]
        for row, result in zip(selected, results, strict=True)
        if not row.get("optional") and result["status"] != execute_mod.STATUS_OK
    ]
    if blocked:
        print(
            f"\n{len(blocked)} required operation(s) did not execute: {', '.join(blocked)}",
            file=sys.stderr,
        )
        return 1
    return 0


def cmd_validate(args):
    vocab, standins, _ = _context()
    inventory = load_json(args.inventory)
    failures = schema_mod.validate_inventory(inventory, vocab, standins)
    if failures:
        print(f"FAIL ({len(failures)})")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print(
        f"OK -- {len(inventory['operations'])} operations, "
        f"vocabulary v{vocab.version}, stand-ins v{standins.version}"
    )
    return 0


def cmd_diff(args):
    left = load_json(args.left)
    right = load_json(args.right)
    changes = []

    left_ops = {op["id"]: op for op in left.get("operations", [])}
    right_ops = {op["id"]: op for op in right.get("operations", [])}

    for op_id in sorted(set(left_ops) - set(right_ops)):
        changes.append(f"removed: {op_id}")
    for op_id in sorted(set(right_ops) - set(left_ops)):
        changes.append(f"added: {op_id}")
    for op_id in sorted(set(left_ops) & set(right_ops)):
        a, b = left_ops[op_id], right_ops[op_id]
        for field in ("stage", "effect", "requires", "gate", "optional", "confidence", "stand_in"):
            if a.get(field) != b.get(field):
                changes.append(f"changed {op_id}.{field}: {a.get(field)} -> {b.get(field)}")

    if not changes:
        print("no changes")
        return 0
    for change in changes:
        print(change)
    return 0


def cmd_catalog_lint(args):
    providers = catalog_mod.ProviderCatalog.load()
    root = resolve_local(args.path)
    snapshot = build_snapshot(root)
    result = profiler_pkg.profile(snapshot)
    matches, unknown = providers.match(result.manifests.get("ecosystems") or {})
    print(f"matched: {len(matches)}   unknown: {len(unknown)}")
    for ecosystem, name in unknown:
        print(f"  unknown: {ecosystem}:{name}")
    return 0 if not unknown else 1


def build_parser():
    parser = argparse.ArgumentParser(
        prog="r2s", description="Convert any repository into a portable capability skill."
    )
    parser.add_argument("--version", action="version", version=f"r2s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("profiles", help="list harness capability profiles")
    p.set_defaults(func=cmd_profiles)

    p = sub.add_parser("extract", help="profile a repo and extract a draft operation inventory")
    p.add_argument("path", nargs="?", help="local directory")
    p.add_argument("--url", help="git URL to clone instead of a local path")
    p.add_argument("--sha", help="pin to a commit")
    p.add_argument("--subdir", help="package inside a monorepo")
    p.add_argument("--out", default="./r2s-out", help="output directory")
    p.add_argument(
        "--llm-proposals",
        metavar="FILE",
        help=(
            "T4 gap-fill: a proposals JSON file to apply. Off unless given. Proposals "
            "are applied as low-confidence, unconfirmed evidence and can never raise "
            "confidence; see `r2s digest` for producing the input to hand a model."
        ),
    )
    p.set_defaults(func=cmd_extract)

    p = sub.add_parser("convert", help="route a reviewed inventory against a harness profile")
    p.add_argument("inventory")
    p.add_argument("--profile", default="bare")
    p.add_argument("--all", action="store_true", help="route against every profile")
    p.add_argument(
        "--accept-unreviewed",
        action="store_true",
        help="route a draft; the skill will be stamped unreviewed",
    )
    p.add_argument("--json-out", help="write the full report as JSON")
    p.add_argument(
        "--emit",
        metavar="DIR",
        help="write an Agent Skills package into DIR, one skill per profile",
    )
    p.set_defaults(func=cmd_convert)

    p = sub.add_parser("scan", help="scan an emitted skill for leaked credentials (L3)")
    p.add_argument("path", help="skill directory to scan")
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("standins", help="list stand-ins and whether each can actually run")
    p.set_defaults(func=cmd_standins)

    p = sub.add_parser(
        "digest", help="write the T4 digest to hand to your own model (r2s never calls one)"
    )
    p.add_argument("draft", help="an extracted draft inventory")
    p.add_argument("--out", help="write the digest here instead of stdout")
    p.set_defaults(func=cmd_digest)

    p = sub.add_parser("run", help="execute the stand-in-backed operations of a report")
    p.add_argument("report", help="a routed report (r2s convert --json-out)")
    p.add_argument(
        "--operation",
        action="append",
        help="operation id to run; repeatable",
    )
    p.add_argument("--all", action="store_true", help="run every stand-in-backed operation")
    p.add_argument("--out", default="./r2s-run", help="output directory for artifacts")
    p.add_argument("--inputs", help="JSON object of stand-in inputs, applied to every operation")
    p.add_argument("--inputs-file", help="path to a JSON object of stand-in inputs")
    p.add_argument("--dry-run", action="store_true", help="report what would run, run nothing")
    p.add_argument("--json-out", help="write the per-operation results as JSON")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("validate", help="validate an inventory against the schema and vocabulary")
    p.add_argument("inventory")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("diff", help="compare two inventories")
    p.add_argument("left")
    p.add_argument("right")
    p.set_defaults(func=cmd_diff)

    p = sub.add_parser("catalog-lint", help="report dependencies missing from the provider catalog")
    p.add_argument("path")
    p.set_defaults(func=cmd_catalog_lint)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (VocabError, SourceError, ProfileError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
