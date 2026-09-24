"""T4 gap-fill contract tests.

T4 is the one evidence source not derived from the repository: it reads proposals a
*model* produced. That makes it the most dangerous source, and the only honest thing to
validate without shipping a model dependency is the fencing around it.

So these tests do not check whether a model gives good answers -- nothing here can. They
check that a bad answer cannot do damage: that a proposal cannot raise confidence, cannot
weaken a requirement or a gate, cannot introduce a capability outside the vocabulary,
cannot smuggle repository text or a credential into the artifact, and cannot run at all
unless it was asked for.

That distinction is the point. "The model is unvalidated" is a limitation; "the model's
output can override the pipeline" would be a defect. Only the second is testable, and it
is the one that matters.

Stdlib unittest only.
"""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from r2s.capability.vocab import CapabilityVocab, VocabError
from r2s.extract import gapfill

PROJECT = Path(__file__).resolve().parent.parent


def operation(**overrides):
    """A minimal coalesced operation, the shape `apply` receives."""
    base = {
        "id": "publish-video",
        "name": "Publish Video",
        "stage": "publish",
        "effect": "effectful-external",
        "requires": ["http.request"],
        "confidence": "high",
        "entrypoint": {"loc": "src/publish.py:12"},
        "evidence": [{"source": "traced", "loc": "src/publish.py:12", "detail": "requests.post"}],
    }
    base.update(overrides)
    return base


class DigestTests(unittest.TestCase):
    """The digest is what leaves the machine, so it must carry no repository content."""

    def draft(self):
        return {
            "repo_class": "cli-tool",
            "extraction": {"sources_run": ["T0", "T1"]},
            "diagnostics": {"tracing": {"entrypoints": 3}},
            "operations": [
                {
                    "id": "publish-video",
                    "name": "Publish Video",
                    "stage": "publish",
                    "effect": "effectful-external",
                    "requires": ["http.request"],
                    "gate": ["publish"],
                    "confidence": "high",
                    "summary": "Uploads the finished video.",
                    "evidence": [
                        {
                            "source": "traced",
                            "loc": "src/publish.py:12",
                            "detail": "requests.post to the upload endpoint",
                        }
                    ],
                }
            ],
        }

    def test_digest_is_structured_and_names_its_kind(self):
        digest = gapfill.digest(self.draft())
        self.assertEqual(digest["kind"], "r2s.digest")
        self.assertEqual(digest["repo_class"], "cli-tool")
        self.assertEqual(len(digest["operations"]), 1)

    def test_digest_carries_no_file_contents(self):
        """A model must see the normalised inventory, never the repository."""
        digest = gapfill.digest(self.draft())
        serialised = json.dumps(digest)
        # Evidence *details* are summarised away; only the source kind survives.
        self.assertNotIn("requests.post to the upload endpoint", serialised)
        self.assertEqual(digest["operations"][0]["evidence_sources"], ["traced"])

    def test_digest_keeps_the_fields_a_model_needs_to_reason(self):
        entry = gapfill.digest(self.draft())["operations"][0]
        for field in ("id", "name", "stage", "effect", "requires", "gate", "confidence"):
            self.assertIn(field, entry)


class LoadProposalsTests(unittest.TestCase):
    def test_a_bare_list_is_accepted(self):
        proposals = gapfill.load_proposals(
            [{"op_id": "publish-video", "requires": ["http.request"]}]
        )
        self.assertEqual(len(proposals), 1)

    def test_a_missing_proposals_list_is_rejected(self):
        with self.assertRaises(ValueError):
            gapfill.load_proposals({"kind": "r2s.proposals"})

    def test_a_proposal_without_an_op_id_is_rejected(self):
        with self.assertRaises(ValueError):
            gapfill.load_proposals([{"requires": ["http.request"]}])

    def test_non_string_requirements_are_rejected(self):
        with self.assertRaises(ValueError):
            gapfill.load_proposals([{"op_id": "x", "requires": [1, 2]}])

    def test_none_yields_nothing_rather_than_raising(self):
        self.assertEqual(gapfill.load_proposals(None), [])


class ApplyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.vocab = CapabilityVocab.load()

    def test_a_proposal_can_never_raise_confidence(self):
        """The whole safety story in one assertion: proposals only ever lower confidence."""
        ops = [operation(confidence="high")]
        applied, _ = gapfill.apply(
            ops,
            [{"op_id": "publish-video", "requires": [], "gate": [], "rationale": ""}],
            self.vocab,
        )
        self.assertEqual(applied[0]["confidence"], "low")

    def test_a_proposal_is_recorded_as_proposed_evidence_and_flagged_for_review(self):
        ops = [operation()]
        applied, notes = gapfill.apply(
            ops,
            [
                {
                    "op_id": "publish-video",
                    "requires": [],
                    "gate": [],
                    "rationale": "looks like a webhook",
                }
            ],
            self.vocab,
        )
        sources = [item["source"] for item in applied[0]["evidence"]]
        self.assertIn("proposed", sources)
        self.assertIn("confirm", applied[0]["notes"].lower())
        self.assertTrue(any("unconfirmed" in note for note in notes), notes)

    def test_a_proposal_cannot_remove_a_requirement(self):
        """Requirements are unioned, never replaced, so a model cannot weaken the inventory."""
        ops = [operation(requires=["http.request", "secret.oauth"])]
        applied, _ = gapfill.apply(
            ops,
            [{"op_id": "publish-video", "requires": [], "gate": [], "rationale": ""}],
            self.vocab,
        )
        self.assertEqual(applied[0]["requires"], ["http.request", "secret.oauth"])

    def test_a_proposal_cannot_remove_a_gate(self):
        ops = [operation(gate=["publish", "irreversible"])]
        applied, _ = gapfill.apply(
            ops,
            [{"op_id": "publish-video", "requires": [], "gate": [], "rationale": ""}],
            self.vocab,
        )
        self.assertEqual(sorted(applied[0]["gate"]), ["irreversible", "publish"])

    def test_a_proposal_may_add_a_gate_but_only_at_low_gate_confidence(self):
        ops = [operation()]
        applied, _ = gapfill.apply(
            ops,
            [{"op_id": "publish-video", "requires": [], "gate": ["spend"], "rationale": ""}],
            self.vocab,
        )
        self.assertIn("spend", applied[0]["gate"])
        self.assertEqual(applied[0]["gate_confidence"], "low")

    def test_an_unknown_capability_is_a_hard_error_not_a_soft_one(self):
        """Everywhere else an unknown ID hard-fails; T4 must not become the exception."""
        ops = [operation()]
        with self.assertRaises(VocabError):
            gapfill.apply(
                ops,
                [
                    {
                        "op_id": "publish-video",
                        "requires": ["not.a.capability"],
                        "gate": [],
                        "rationale": "",
                    }
                ],
                self.vocab,
            )

    def test_a_proposal_for_an_unknown_operation_is_reported_not_dropped(self):
        ops = [operation()]
        applied, notes = gapfill.apply(
            ops,
            [{"op_id": "ghost-operation", "requires": [], "gate": [], "rationale": ""}],
            self.vocab,
        )
        self.assertEqual(len(applied), 1)
        self.assertTrue(any("ghost-operation" in note for note in notes), notes)

    def test_a_credential_in_a_rationale_is_redacted(self):
        """A rationale is model-authored free text and must not carry a secret through."""
        secret = "sk-live-abcdefghijklmnop1234"
        ops = [operation()]
        applied, _ = gapfill.apply(
            ops,
            [
                {
                    "op_id": "publish-video",
                    "requires": [],
                    "gate": [],
                    "rationale": f"key is {secret}",
                }
            ],
            self.vocab,
        )
        detail = [item["detail"] for item in applied[0]["evidence"] if item["source"] == "proposed"]
        self.assertTrue(detail)
        self.assertNotIn(secret, json.dumps(applied))

    def test_no_proposals_means_no_change_at_all(self):
        ops = [operation()]
        applied, notes = gapfill.apply(ops, [], self.vocab)
        self.assertEqual(applied, ops)
        self.assertEqual(notes, [])


class WiringTests(unittest.TestCase):
    """T4 must be reachable and must be off unless explicitly asked for."""

    def run_cli(self, *args, cwd=None):
        import os
        import subprocess

        return subprocess.run(
            [sys.executable, "-m", "r2s", *args],
            capture_output=True,
            text=True,
            cwd=str(cwd or PROJECT),
            env={**dict(os.environ), "PYTHONPATH": str(PROJECT / "src")},
            check=False,
        )

    def test_extract_accepts_llm_proposals_and_the_flag_defaults_off(self):
        proc = self.run_cli("extract", "--help")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("--llm-proposals", proc.stdout)
        # argparse wraps help text, so compare against collapsed whitespace.
        help_text = " ".join(proc.stdout.split())
        self.assertIn("Off unless given", help_text)
        self.assertIn("can never raise confidence", help_text)

    def test_digest_is_a_command_and_emits_a_digest(self):
        import tempfile

        draft = {
            "kind": "r2s.inventory.draft",
            "repo_class": "cli-tool",
            "extraction": {"sources_run": ["T0"]},
            "diagnostics": {"tracing": {}},
            "operations": [
                {
                    "id": "fetch",
                    "name": "Fetch",
                    "stage": "ingest",
                    "effect": "read-external",
                    "requires": ["http.request"],
                    "gate": [],
                    "confidence": "medium",
                    "summary": "Fetches the feed.",
                    "evidence": [
                        {"source": "traced", "loc": "src/x.py:1", "detail": "requests.get"}
                    ],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "inventory.draft.json"
            path.write_text(json.dumps(draft), encoding="utf-8")
            proc = self.run_cli("digest", str(path))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["kind"], "r2s.digest")
        self.assertEqual([op["id"] for op in payload["operations"]], ["fetch"])

    def test_a_rejected_proposal_file_fails_loudly_rather_than_silently(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "proposals.json"
            bad.write_text(json.dumps({"kind": "r2s.proposals"}), encoding="utf-8")
            proc = self.run_cli(
                "extract",
                str(PROJECT / "fixtures" / "cli-tool" / "feed-sync"),
                "--llm-proposals",
                str(bad),
                "--out",
                str(Path(tmp) / "out"),
            )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("proposals", (proc.stderr + proc.stdout).lower())


if __name__ == "__main__":
    unittest.main()
