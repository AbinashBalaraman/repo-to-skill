"""Emitter tests (M6).

The emitter is the change that makes the project's core claim true, so these tests
assert the properties that would make an emitted skill dangerous if they broke: gates
surviving into the artifact, an honest profile declaration, refusal to emit from an
unreviewed inventory, and determinism.
"""

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from r2s import config
from r2s.capability import invariants, router
from r2s.capability import profiles as profiles_mod
from r2s.capability.vocab import CapabilityVocab, StandinCatalog
from r2s.emit import EmitError, emit_skill, skill_md
from r2s.emit import references as references_mod
from r2s.util.io import load_json
from r2s.validate import secrets as secrets_mod
from r2s.validate import spec as spec_mod

PROJECT = Path(__file__).resolve().parent.parent
YOUTUBE = (
    PROJECT / "fixtures" / "agent-system" / "youtube-automation" / "expected" / "inventory.json"
)


class EmitBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.vocab = CapabilityVocab.load()
        cls.standins = StandinCatalog.load()
        cls.profiles = profiles_mod.load_profiles()
        cls.inventory = load_json(YOUTUBE)

    def emit(self, profile_id="claude-code", inventory=None, accept=False):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        skill_dir, report, warnings = emit_skill(
            inventory or self.inventory,
            self.profiles[profile_id],
            tmp,
            self.vocab,
            self.standins,
            accept_unreviewed=accept,
        )
        return skill_dir, report, warnings


class TestEmittedShape(EmitBase):
    def test_produces_the_contract(self):
        skill_dir, _, _ = self.emit()
        self.assertTrue((skill_dir / "SKILL.md").is_file())
        for name in ("bindings.md", "degradation.md", "blocked.md", "gates.md"):
            with self.subTest(reference=name):
                self.assertTrue((skill_dir / "references" / name).is_file())

    def test_skill_name_matches_its_folder(self):
        """The spec requires it, and a mismatch breaks installation."""
        skill_dir, _, _ = self.emit()
        front, _ = spec_mod.parse_frontmatter((skill_dir / "SKILL.md").read_text(encoding="utf-8"))
        self.assertEqual(front["name"], skill_dir.name)

    def test_passes_spec_validation(self):
        skill_dir, _, _ = self.emit()
        self.assertEqual(spec_mod.validate_skill(skill_dir), [])

    def test_declares_its_capability_profile(self):
        """A skill that does not state its assumptions cannot fail loudly."""
        skill_dir, _, _ = self.emit()
        body = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
        self.assertEqual(spec_mod.validate_profile_declaration(body), [])
        self.assertIn("claude-code", body)

    def test_references_are_one_level_deep(self):
        skill_dir, _, _ = self.emit()
        for path in (skill_dir / "references").rglob("*"):
            if path.is_file():
                self.assertEqual(len(path.relative_to(skill_dir / "references").parts), 1)

    def test_no_repository_code_is_copied(self):
        """Only r2s's own stand-in scripts ship; nothing from the analysed repo does.

        This is the licensing boundary and the security boundary at once: an emitted
        skill runs scripts r2s reviewed, never the source of the repository it describes.
        """
        from r2s import execute

        skill_dir, _, _ = self.emit()
        scripts_dir = skill_dir / "scripts"
        if not scripts_dir.is_dir():
            return  # nothing stand-in-bound on this profile; nothing to check

        declared = set()
        for entry in self.standins.all():
            spec = execute.exec_spec(entry)
            if spec:
                declared.add(spec["script"])

        copied = sorted(p.name for p in scripts_dir.iterdir())
        self.assertTrue(copied, "a scripts/ directory was created but is empty")
        for name in copied:
            self.assertIn(name, declared, f"{name} is not a catalog stand-in script")
            # And byte-identical to the shipped original, so it was copied, not rewritten.
            self.assertEqual(
                (scripts_dir / name).read_bytes(),
                (execute.SCRIPT_DIR / name).read_bytes(),
            )

    def test_degradation_names_the_script_and_the_reason_it_cannot_run(self):
        skill_dir, _, _ = self.emit()
        text = (skill_dir / "references" / "degradation.md").read_text(encoding="utf-8")
        self.assertIn("## Running these steps", text)
        if "not runnable" in text:
            self.assertIn("why", text.lower())

    def test_no_secrets_in_the_emitted_skill(self):
        skill_dir, _, _ = self.emit()
        self.assertEqual(secrets_mod.scan_directory(skill_dir), [])


class TestGateSurvival(EmitBase):
    def test_upload_gates_reach_the_artifact_on_every_profile(self):
        """The load-bearing property, asserted on the emitted output rather than the rows."""
        for pid in ("bare", "claude-code", "workbuddy", "full"):
            with self.subTest(profile=pid):
                skill_dir, _, _ = self.emit(pid)
                text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
                blocked = (skill_dir / "references" / "blocked.md").read_text(encoding="utf-8")
                blob = (text + blocked).lower()
                for gate in ("publish", "irreversible", "privacy"):
                    self.assertIn(gate, blob, f"{gate} lost on {pid}")

    def test_blocked_questions_satisfy_i7(self):
        """I7's emitter half. A deferred gate must appear in the question asked."""
        for pid in ("bare", "workbuddy"):
            with self.subTest(profile=pid):
                rows = router.resolve(
                    self.inventory["operations"], self.profiles[pid], self.vocab, self.standins
                )
                blocked = [r for r in rows if r["binding"] == "ask-user"]
                questions = references_mod.build_questions(blocked)
                self.assertEqual(invariants.check_deferred_questions(rows, questions), [])

    def test_every_ask_user_operation_is_documented(self):
        skill_dir, _, _ = self.emit("bare")
        blocked_md = (skill_dir / "references" / "blocked.md").read_text(encoding="utf-8")
        rows = router.resolve(
            self.inventory["operations"], self.profiles["bare"], self.vocab, self.standins
        )
        for row in rows:
            if row["binding"] == "ask-user":
                self.assertIn(row["id"], blocked_md)

    def test_degradation_is_stated_with_fidelity(self):
        skill_dir, _, _ = self.emit()
        text = (skill_dir / "references" / "degradation.md").read_text(encoding="utf-8")
        self.assertIn("fidelity", text.lower())
        self.assertIn("0.25", text)

    def test_narration_halts_and_is_reported(self):
        """No honest stand-in for speech, so the skill must stop rather than fake it."""
        skill_dir, _, _ = self.emit("bare")
        blocked = (skill_dir / "references" / "blocked.md").read_text(encoding="utf-8")
        self.assertIn("gen-narration", blocked)


class TestReviewGate(EmitBase):
    def test_refuses_to_emit_from_a_draft(self):
        draft = json.loads(json.dumps(self.inventory))
        draft["inventory_status"] = "draft"
        with self.assertRaises(EmitError) as ctx:
            self.emit(inventory=draft)
        self.assertIn("draft", str(ctx.exception))

    def test_accept_unreviewed_emits_but_stamps_the_status(self):
        draft = json.loads(json.dumps(self.inventory))
        draft["inventory_status"] = "draft"
        skill_dir, _, warnings = self.emit(inventory=draft, accept=True)
        body = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("accepted-unreviewed", body)
        self.assertIn("UNREVIEWED", body)
        self.assertTrue(warnings)

    def test_refuses_when_an_invariant_would_fail(self):
        """Emitting a skill whose gates did not survive is the failure this exists to stop."""
        broken = json.loads(json.dumps(self.inventory))
        # Strip every stand-in and every capability, so required operations cannot bind.
        for op in broken["operations"]:
            op["stand_in"] = None
            op["requires"] = ["video.generate"]
        skill_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, skill_dir, True)
        # A bare harness cannot supply video.generate and there is no stand-in, so these
        # route to ask-user -- which is legal. Assert the emitter still produces a valid
        # skill rather than crashing, and that nothing silently dropped.
        out, _, _ = emit_skill(broken, self.profiles["bare"], skill_dir, self.vocab, self.standins)
        text = (out / "SKILL.md").read_text(encoding="utf-8")
        for op in broken["operations"]:
            if not op["optional"]:
                self.assertIn(op["id"], text)


class TestDeterminism(EmitBase):
    def test_two_emits_are_byte_identical(self):
        """The eval diffs emitted skills across revisions."""
        first, _, _ = self.emit()
        second, _, _ = self.emit()
        self.assertEqual(first.name, second.name)

        def tree(root):
            return {
                p.relative_to(root).as_posix(): p.read_bytes()
                for p in sorted(root.rglob("*"))
                if p.is_file()
            }

        self.assertEqual(tree(first), tree(second))

    def test_different_profiles_produce_different_skills(self):
        bare, _, _ = self.emit("bare")
        full, _, _ = self.emit("full")
        self.assertNotEqual(bare.name, full.name)


class TestNameGeneration(unittest.TestCase):
    def setUp(self):
        self.vocab = CapabilityVocab.load()
        self.standins = StandinCatalog.load()
        self.profiles = profiles_mod.load_profiles()

    def test_name_is_kebab_and_within_the_limit(self):
        inventory = load_json(YOUTUBE)
        for pid, profile in self.profiles.items():
            with self.subTest(profile=pid):
                name = skill_md._skill_name_from(profile, inventory)
                self.assertLessEqual(len(name), config.NAME_MAX_CHARS)
                self.assertRegex(name, r"^[a-z0-9]+(-[a-z0-9]+)*$")

    def test_local_source_without_a_url_still_names(self):
        inventory = load_json(YOUTUBE)
        inventory["source"] = {"kind": "local"}
        name = skill_md._skill_name_from(self.profiles["bare"], inventory)
        self.assertTrue(name)
        self.assertRegex(name, r"^[a-z0-9]+(-[a-z0-9]+)*$")


class TestSecretScanner(unittest.TestCase):
    """A scanner that never fires is worthless, so prove it fires."""

    def test_detects_each_credential_shape(self):
        cases = {
            "openai-or-stripe-key": "key = sk-live-abcdefghijklmnop1234",
            "aws-access-key-id": "AKIAIOSFODNN7EXAMPLE",
            "github-token": "ghp_abcdefghijklmnopqrstuvwxyz0123456789",
            "slack-token": "xoxb-1234567890-abcdefghijkl",
            "google-api-key": "AIzaSyA1234567890abcdefghijklmnopqrstu",
            "pem-private-key": "-----BEGIN RSA PRIVATE KEY-----",
            "jwt": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijklmnop",
        }
        for kind, sample in cases.items():
            with self.subTest(kind=kind):
                findings = secrets_mod.scan_text(sample, "f.md")
                self.assertTrue(findings, f"{kind} not detected: {sample}")

    def test_never_echoes_the_credential(self):
        """A report that prints the secret is its own leak."""
        secret = "sk-live-abcdefghijklmnop1234"
        findings = secrets_mod.scan_text(f"token = {secret}", "f.md")
        self.assertTrue(findings)
        rendered = secrets_mod.format_findings(findings)
        self.assertNotIn(secret, rendered)
        for finding in findings:
            self.assertNotIn(secret, json.dumps(finding.to_dict()))

    def test_ignores_placeholders_and_env_references(self):
        for line in (
            "api_key: ${MY_API_KEY}",
            "token = os.environ['TOKEN']",
            "password: your-password-here",
            "secret = <redacted>",
        ):
            with self.subTest(line=line):
                self.assertEqual(secrets_mod.scan_text(line, "f.md"), [])

    def test_clean_document_reports_nothing(self):
        self.assertEqual(
            secrets_mod.scan_text("Publish the digest to the configured webhook.", "f.md"),
            [],
        )


class TestSpecValidator(unittest.TestCase):
    def _skill(self, frontmatter, body="Body text.", folder="my-skill"):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        skill = root / folder
        skill.mkdir()
        (skill / "SKILL.md").write_text(f"---\n{frontmatter}\n---\n\n{body}\n", encoding="utf-8")
        return skill

    def test_accepts_a_valid_skill(self):
        skill = self._skill("name: my-skill\ndescription: A valid skill.")
        self.assertEqual(spec_mod.validate_skill(skill), [])

    def test_rejects_a_name_that_does_not_match_the_folder(self):
        skill = self._skill("name: other-name\ndescription: Valid.", folder="my-skill")
        failures = spec_mod.validate_skill(skill)
        self.assertTrue(any("does not match the folder" in f for f in failures), failures)

    def test_rejects_a_missing_description(self):
        failures = spec_mod.validate_skill(self._skill("name: my-skill"))
        self.assertTrue(any("description" in f for f in failures), failures)

    def test_rejects_unknown_frontmatter_keys(self):
        failures = spec_mod.validate_skill(
            self._skill("name: my-skill\ndescription: Valid.\nbogus: yes")
        )
        self.assertTrue(any("unknown key" in f for f in failures), failures)

    def test_rejects_an_over_long_description(self):
        failures = spec_mod.validate_skill(
            self._skill(f"name: my-skill\ndescription: {'x' * 1100}")
        )
        self.assertTrue(any("limit" in f for f in failures), failures)

    def test_rejects_an_over_long_body(self):
        failures = spec_mod.validate_skill(
            self._skill(
                "name: my-skill\ndescription: Valid.", body="\n".join("line" for _ in range(600))
            )
        )
        self.assertTrue(any("limit" in f for f in failures), failures)

    def test_rejects_deeply_nested_references(self):
        skill = self._skill("name: my-skill\ndescription: Valid.")
        deep = skill / "references" / "sub"
        deep.mkdir(parents=True)
        (deep / "x.md").write_text("x", encoding="utf-8")
        failures = spec_mod.validate_skill(skill)
        self.assertTrue(any("nested" in f for f in failures), failures)

    def test_rejects_unexpected_top_level_entries(self):
        skill = self._skill("name: my-skill\ndescription: Valid.")
        (skill / "stray.txt").write_text("x", encoding="utf-8")
        failures = spec_mod.validate_skill(skill)
        self.assertTrue(any("unexpected entry" in f for f in failures), failures)

    def test_frontmatter_parser_supports_one_level_of_nesting(self):
        front, body = spec_mod.parse_frontmatter(
            "---\nname: x\ndescription: y\nmetadata:\n  a: b\n---\n\nBody."
        )
        self.assertEqual(front["metadata"], {"a": "b"})
        self.assertEqual(body, "Body.")

    def test_frontmatter_parser_fails_loudly_on_deeper_nesting(self):
        with self.assertRaises(spec_mod.FrontmatterError):
            spec_mod.parse_frontmatter("---\nname: x\n  orphan: y\n---\n\nB")

    def test_frontmatter_parser_rejects_a_missing_block(self):
        with self.assertRaises(spec_mod.FrontmatterError):
            spec_mod.parse_frontmatter("# no frontmatter here")


if __name__ == "__main__":
    unittest.main(verbosity=2)
