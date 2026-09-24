"""M6: the emitter.

Turns a routed capability report into a portable Agent Skills package. This is the last
arrow in the chain:

    snapshot -> inventory.draft.json -> inventory.json -> report.json -> <skill>/

Design constraints that shape everything here:

  The skill declares its own capability profile. A skill that does not state its
  assumptions cannot fail loudly, so it fails silently. The profile block is the first
  thing in the body, not an appendix.

  Nothing is invented. Bindings come from `capability/router.py`, gates from the
  inventory, stand-ins from the curated catalog. The emitter formats; it does not decide.

  Repository code is never copied. Stand-ins live in the catalog precisely so that
  emitting a skill does not redistribute someone else's source.

  Deterministic. Same report in, byte-identical skill out -- the eval diffs emitted
  skills across revisions.
"""

from pathlib import Path

from .. import config
from ..capability import invariants, router
from ..capability import report as report_mod
from ..util.io import slugify
from . import references as references_mod
from . import skill_md


class EmitError(Exception):
    pass


def emit_skill(inventory, profile, out_dir, vocab, standins, accept_unreviewed=False):
    """Write a skill package. Returns (skill_dir, report_dict, warnings).

    Refuses to emit from a draft inventory unless `accept_unreviewed` is set. The router
    consumes a reviewed inventory by design, and that refusal is a feature: an
    unreviewed inventory has unconfirmed operations, and a skill built on unconfirmed
    operations is confidently wrong rather than visibly incomplete.
    """
    status = inventory.get("inventory_status")
    warnings = []

    if status == "draft" and not accept_unreviewed:
        raise EmitError(
            "refusing to emit from a draft inventory. Review it first, or pass "
            "--accept-unreviewed to emit with the unreviewed status recorded in the "
            "skill."
        )
    if status == "draft":
        inventory = dict(inventory)
        inventory["inventory_status"] = "accepted-unreviewed"
        warnings.append(
            "emitted from an UNREVIEWED inventory. Every low-confidence operation is "
            "flagged inside the skill; treat the bindings as unconfirmed."
        )

    rows = router.resolve(inventory["operations"], profile, vocab, standins)
    summary = router.summarise(rows)
    invariant_failures = invariants.verify(inventory["operations"], rows, profile)

    # I7's emitter half: a gate deferred onto an ask-user operation must appear in the
    # question the skill asks. Without this the gate is reported as satisfied when it
    # was only deferred.
    blocked = [row for row in rows if row["binding"] == "ask-user"]
    questions = references_mod.build_questions(blocked)
    question_failures = invariants.check_deferred_questions(rows, questions)
    invariant_failures = list(invariant_failures) + list(question_failures)

    if invariant_failures:
        # Emitting a skill whose gates did not survive conversion would be the exact
        # failure this project exists to prevent.
        raise EmitError(
            "refusing to emit: gate-survival invariants failed.\n  "
            + "\n  ".join(invariant_failures)
        )

    skill_name = _skill_name(inventory, profile)
    skill_dir = Path(out_dir) / skill_name
    if skill_dir.exists() and any(skill_dir.iterdir()):
        raise EmitError(f"output directory already exists and is not empty: {skill_dir}")

    (skill_dir / "references").mkdir(parents=True, exist_ok=True)

    frontmatter, body = skill_md.render(inventory, profile, rows, summary, warnings)
    (skill_dir / "SKILL.md").write_text(frontmatter + "\n\n" + body + "\n", encoding="utf-8")

    references_mod.write_all(
        skill_dir / "references", inventory, profile, rows, summary, questions, standins
    )

    # Only stand-in scripts that are actually used get a scripts/ directory.
    used_scripts = references_mod.write_scripts(skill_dir, rows, standins)
    if used_scripts:
        warnings.append(f"{len(used_scripts)} stand-in script(s) copied into scripts/")

    built = report_mod.build_report(inventory, profile, rows, summary, invariant_failures, warnings)
    return skill_dir, built, warnings


def _skill_name(inventory, profile):
    """Kebab-case name matching the folder, derived from the source.

    Must equal the folder name -- the spec requires it, and `validate/spec.py` enforces
    it. The harness is part of the name because the same repo resolves differently per
    harness, and two skills for one repo would otherwise collide.
    """
    source = inventory.get("source") or {}
    url = source.get("url") or ""
    if url:
        stem = url.rstrip("/").removesuffix(".git").rsplit("/", 1)[-1]
    else:
        stem = "repo"

    base = slugify(f"{stem}-skill")[:40].strip("-") or "repo-skill"
    profile_part = slugify(profile.id)[:16].strip("-")
    name = f"{base}-{profile_part}" if profile_part else base
    return name[: config.NAME_MAX_CHARS].strip("-")
