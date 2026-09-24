"""The eval harness: L1 through L5, runnable as a gate.

    PYTHONPATH=src python -m eval

Exits non-zero if any layer fails, so CI can call it directly.

Layer status is reported honestly. A layer with nothing to check reports `skipped`, not
`pass` -- a layer that silently passes when it had no input is worse than no layer at
all, because it manufactures confidence.
"""

import json
import tempfile
from pathlib import Path

from r2s import config
from r2s.capability import invariants, router
from r2s.capability import profiles as profiles_mod
from r2s.capability.vocab import CapabilityVocab, StandinCatalog
from r2s.extract import catalog as catalog_mod
from r2s.extract import effects as effects_mod
from r2s.extract import pipeline as pipeline_mod
from r2s.profiler import profile as profile_repo
from r2s.source import build_snapshot
from r2s.util.io import load_json
from r2s.validate import schema as schema_mod
from r2s.validate import secrets as secrets_mod
from r2s.validate import spec as spec_mod

PASS, FAIL, SKIP = "pass", "fail", "skip"


class Result:
    def __init__(self, layer, name):
        self.layer = layer
        self.name = name
        self.status = PASS
        self.failures = []
        self.notes = []

    def fail(self, message):
        self.status = FAIL
        self.failures.append(message)

    def note(self, message):
        self.notes.append(message)

    def skip(self, message):
        if self.status != FAIL:
            self.status = SKIP
        self.notes.append(message)


def _fixtures():
    """Every fixture inventory we have, as (name, path, repo_class)."""
    out = []
    root = config.FIXTURE_DIR
    if not root.is_dir():
        return out
    for path in sorted(root.rglob("expected/inventory.json")):
        try:
            data = load_json(path)
        except (OSError, json.JSONDecodeError):
            continue
        out.append((path.parent.parent.name, path, data.get("repo_class", "unknown")))
    return out


def _fixture_sources():
    """Every fixture *repository* (a directory with source to extract), as (name, path).

    Distinct from `_fixtures()`, which lists fixture *inventories*: a fixture that is
    there to exercise a dialect has source files but no golden inventory, and it is
    exactly the one whose determinism needs checking.
    """
    out = []
    root = config.FIXTURE_DIR
    if not root.is_dir():
        return out
    for path in sorted(root.glob("*/*")):
        if path.is_dir() and path.name != "expected":
            out.append((f"{path.parent.name}/{path.name}", path))
    return out


# ---------------------------------------------------------------- L1


def l1_invariants():
    result = Result("L1", "gate-survival invariants I1-I7")
    vocab = CapabilityVocab.load()
    standins = StandinCatalog.load()
    profiles = profiles_mod.load_profiles()

    fixtures = _fixtures()
    if not fixtures:
        result.skip("no fixture inventories found")
        return result

    checked = 0
    for name, path, _ in fixtures:
        inventory = load_json(path)
        for pid, profile in sorted(profiles.items()):
            rows = router.resolve(inventory["operations"], profile, vocab, standins)
            failures = invariants.verify(inventory["operations"], rows, profile)
            checked += 1
            for failure in failures:
                result.fail(f"{name} / {pid}: {failure}")
    result.note(f"{checked} fixture x profile combinations checked")
    return result


# ---------------------------------------------------------------- L2


def l2_behavioural():
    """Class-neutral cases keyed to gate classes.

    Scoring is strict by design: producing a plausible artefact when the correct
    behaviour is to halt is a failure, not a partial pass. A tool that quietly emits a
    broken skill is worse than one that refuses.
    """
    result = Result("L2", "behavioural cases")
    vocab = CapabilityVocab.load()
    standins = StandinCatalog.load()

    bare = profiles_mod.load_profiles()["bare"]
    fixtures = _fixtures()
    if not fixtures:
        result.skip("no fixture inventories found")
        return result

    cases = 0
    for name, path, _ in fixtures:
        inventory = load_json(path)
        operations = inventory["operations"]
        rows = router.resolve(operations, bare, vocab, standins)

        # (a)/(b)/(c) every gate on an executing binding is enforced, and the three
        # critical classes are never dropped silently.
        for row in rows:
            if row["binding"] in router.AUTO_BINDINGS:
                op = next(o for o in operations if o["id"] == row["id"])
                declared = set(op.get("gate", []))
                if declared - set(row["gates"]):
                    result.fail(f"{name}/{row['id']}: gate lost on an executing binding")
                cases += 1

        # (d) credentials must survive resolution, never be silently dropped. Note this
        # does NOT assert a binding: credentials are something the user supplies, not a
        # harness capability, so they do not by themselves force ask-user.
        for row in rows:
            op = next(o for o in operations if o["id"] == row["id"])
            if len(op.get("credentials") or []) != len(row["credentials"]):
                result.fail(f"{name}/{row['id']}: credentials were lost during resolution")
            cases += 1

        # (e) a capability mismatch must halt with a question -- UNLESS a stand-in is
        # declared, in which case binding to `script` is the degradation ladder working
        # as designed. Only an unsatisfiable operation with no stand-in must ask.
        for row in rows:
            if not row["missing"] or row["optional"]:
                continue
            has_standin = row["stand_in"] is not None
            if has_standin:
                if row["binding"] != "script":
                    result.fail(
                        f"{name}/{row['id']}: declares a stand-in but bound to "
                        f"{row['binding']} rather than script"
                    )
            elif row["binding"] != "ask-user":
                result.fail(
                    f"{name}/{row['id']}: missing {row['missing']} with no stand-in on a "
                    f"bare harness but bound to {row['binding']}, not ask-user"
                )
            cases += 1

        # (f) a stand-in must never be presented as the real thing: fidelity < 1.0.
        for row in rows:
            if row["binding"] == "script":
                detail = row["stand_in_detail"]
                if not detail or not (0.0 <= detail.get("fidelity", 1.0) < 1.0):
                    result.fail(f"{name}/{row['id']}: stand-in without honest fidelity")
                cases += 1

        # (g) an unknown capability must be rejected by the validator, not tolerated.
        broken = json.loads(json.dumps(inventory))
        broken["operations"][0]["requires"] = ["not.a.real.capability"]
        if not schema_mod.validate_inventory(broken, vocab, standins):
            result.fail(f"{name}: validator accepted an inventory with an unknown capability id")
        cases += 1

    result.note(f"{cases} behavioural assertions")
    return result


# ---------------------------------------------------------------- L3 / L4


def _emitted_skills():
    """Skill directories produced by the emitter.

    Emits a real skill into a temp directory rather than looking for one on disk. A
    layer that skips whenever nobody has run `convert --emit` recently is a layer that
    never runs -- and L3/L4 are exactly the layers whose whole job is to inspect emitted
    output.
    """

    from r2s.emit import EmitError, emit_skill

    vocab = CapabilityVocab.load()
    standins = StandinCatalog.load()
    profiles = profiles_mod.load_profiles()

    fixture = (
        config.FIXTURE_DIR / "agent-system" / "youtube-automation" / "expected" / "inventory.json"
    )
    if not fixture.is_file():
        return [], None

    tmp = tempfile.mkdtemp(prefix="r2s-eval-emit-")
    skills = []
    for pid in sorted(profiles):
        try:
            skill_dir, _, _ = emit_skill(load_json(fixture), profiles[pid], tmp, vocab, standins)
        except EmitError:
            continue
        skills.append(skill_dir)
    return skills, tmp


def _cleanup(tmp):
    if tmp:
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)


def l3_secret_scan():
    result = Result("L3", "secret-leak scan")
    skills, tmp = _emitted_skills()
    try:
        if not skills:
            result.skip("emission produced no skills to scan")
            return result
        for skill in skills:
            for finding in secrets_mod.scan_directory(skill):
                result.fail(f"{skill.name}: {finding}")
        result.note(f"{len(skills)} freshly emitted skill(s) scanned")
    finally:
        _cleanup(tmp)
    return result


def l4_spec():
    result = Result("L4", "Agent Skills spec validation")
    skills, tmp = _emitted_skills()
    try:
        if not skills:
            result.skip("emission produced no skills to validate")
            return result
        for skill in skills:
            for failure in spec_mod.validate_skill(skill):
                result.fail(f"{skill.name}: {failure}")
            body = (skill / "SKILL.md").read_text(encoding="utf-8")
            for failure in spec_mod.validate_profile_declaration(body):
                result.fail(f"{skill.name}: {failure}")
        result.note(f"{len(skills)} freshly emitted skill(s) validated")
    finally:
        _cleanup(tmp)
    return result


# ---------------------------------------------------------------- L5


def l5_stability():
    result = Result("L5", "extraction and emission stability")
    vocab = CapabilityVocab.load()
    standins = StandinCatalog.load()

    # Every fixture repo, not just one. Each dialect is its own determinism surface: a
    # new dialect that iterates a set, or orders by insertion, produces an inventory that
    # differs between runs, and the diff would only show up on the repo that dialect
    # handles. Running the check across all of them is what makes the guarantee cover
    # the dialects rather than the one fixture that existed first.
    sources = _fixture_sources()
    if not sources:
        result.skip("no fixture repositories found")
        return result

    def extract_once(fixture_src):
        snapshot = build_snapshot(fixture_src)
        result_profile = profile_repo(snapshot)
        draft, _ = pipeline_mod.extract(
            snapshot,
            result_profile,
            vocab,
            catalog_mod.ProviderCatalog.load(),
            effects_mod.EffectCatalog.load(),
            standins,
            source={"kind": "local"},
        )
        for key in ("diagnostics", "extraction"):
            draft.pop(key, None)
        return json.dumps(draft, sort_keys=True)

    unstable = []
    for name, fixture_src in sources:
        if extract_once(fixture_src) != extract_once(fixture_src):
            unstable.append(name)

    if unstable:
        result.fail(f"extraction is not deterministic on: {', '.join(unstable)}")
    else:
        result.note(f"extraction is byte-identical across two runs on {len(sources)} fixture(s)")

    # Emission stability: the same inventory must produce a byte-identical skill.
    skills, tmp = _emitted_skills()
    try:
        if not skills:
            result.note("emission stability not checked: emission produced no skills")
        else:
            import shutil
            import tempfile as _tempfile

            again_dir = _tempfile.mkdtemp(prefix="r2s-eval-emit2-")
            try:
                from r2s.emit import emit_skill

                vocab2 = CapabilityVocab.load()
                standins2 = StandinCatalog.load()
                fixture = (
                    config.FIXTURE_DIR
                    / "agent-system"
                    / "youtube-automation"
                    / "expected"
                    / "inventory.json"
                )
                unstable = []
                for skill in skills:
                    pid = skill.name.rsplit("-", 1)[-1]
                    profile = profiles_mod.load_profiles().get(pid)
                    if profile is None:
                        continue
                    out, _, _ = emit_skill(
                        load_json(fixture), profile, again_dir, vocab2, standins2
                    )
                    if _hash_tree(skill) != _hash_tree(out):
                        unstable.append(skill.name)
                if unstable:
                    result.fail(f"emission is not deterministic: {unstable}")
                else:
                    result.note(f"{len(skills)} emitted skill(s) are byte-identical on re-emit")
            finally:
                shutil.rmtree(again_dir, ignore_errors=True)
    finally:
        _cleanup(tmp)
    return result


def _hash_tree(root):
    import hashlib

    digest = hashlib.sha256()
    for path in sorted(Path(root).rglob("*")):
        if path.is_file():
            digest.update(path.relative_to(root).as_posix().encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


# ---------------------------------------------------------------- runner


LAYERS = (
    ("L1", l1_invariants),
    ("L2", l2_behavioural),
    ("L3", l3_secret_scan),
    ("L4", l4_spec),
    ("L5", l5_stability),
)


def run(selected=None):
    results = []
    for layer, fn in LAYERS:
        if selected and layer not in selected:
            continue
        results.append(fn())
    return results


def render(results):
    lines = ["=" * 74, "  r2s eval", "=" * 74]
    for result in results:
        marker = {PASS: "PASS", FAIL: "FAIL", SKIP: "SKIP"}[result.status]
        lines.append(f"  [{marker}] {result.layer}  {result.name}")
        for note in result.notes:
            lines.append(f"         {note}")
        for failure in result.failures:
            lines.append(f"         - {failure}")
    lines.append("-" * 74)
    failed = [r for r in results if r.status == FAIL]
    skipped = [r for r in results if r.status == SKIP]
    lines.append(
        f"  {len(results) - len(failed) - len(skipped)} passed, "
        f"{len(failed)} failed, {len(skipped)} skipped"
    )
    if skipped:
        lines.append("  Skipped layers had no input. They are not evidence of correctness.")
    return "\n".join(lines)


def main(argv=None):
    import sys

    selected = [a for a in (argv or sys.argv[1:]) if a.startswith("L")]
    results = run(selected or None)
    print(render(results))
    return 1 if any(r.status == FAIL for r in results) else 0
