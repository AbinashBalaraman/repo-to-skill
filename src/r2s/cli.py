"""r2s command line.

`extract` is a first-class command, not a hidden stage: the router consumes a reviewed
inventory, never a draft, so review has to be a step the user can actually take.
"""

import argparse
import sys
from pathlib import Path

from . import __version__, config
from . import profiler as profiler_pkg
from .capability import invariants, report, router
from .capability import profiles as profiles_mod
from .capability.profiles import ProfileError
from .capability.vocab import CapabilityVocab, StandinCatalog, VocabError
from .extract import catalog as catalog_mod
from .extract import effects as effects_mod
from .extract import pipeline as pipeline_mod
from .extract import review as review_mod
from .source import build_snapshot
from .source.local import SourceError, detect_git_meta, fetch_git, resolve_local
from .util.io import dump_json, load_json, write_text
from .validate import schema as schema_mod


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
        snapshot, result, vocab, providers, effects_catalog, standins, source=source
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

    # Non-zero exit when an invariant fails, so CI and shell callers can gate on it.
    return 1 if failed_any else 0


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
    p.set_defaults(func=cmd_convert)

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
