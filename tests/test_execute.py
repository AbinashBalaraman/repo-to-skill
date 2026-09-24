"""Executor tests (Gap 1): a stand-in is run, or the reason it cannot be is reported.

The property under test is honesty. Every test here exists because the dangerous failure
is not "the step did not run" -- it is "the step did not run and something said it did".
So the assertions are mostly negative: no artifact on failure, no success without an
artifact, no ignored typo, no path escape, no credential in the output.

Stdlib unittest only.
"""

import json
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from r2s import execute
from r2s.capability.vocab import StandinCatalog

PROJECT = Path(__file__).resolve().parent.parent


class FakeCatalog:
    """A catalog with one synthetic entry, for cases the real catalog cannot express."""

    def __init__(self, entry):
        self._entry = entry

    def get(self, standin_id):
        if standin_id != self._entry["id"]:
            raise KeyError(standin_id)
        return self._entry


class CatalogHonestyTests(unittest.TestCase):
    """The catalog itself must not contain an unexplained gap."""

    @classmethod
    def setUpClass(cls):
        cls.standins = StandinCatalog.load()

    def test_every_stand_in_is_runnable_or_states_why_it_is_not(self):
        for entry in self.standins.all():
            spec = execute.exec_spec(entry)
            if spec is not None:
                self.assertTrue(
                    (execute.SCRIPT_DIR / spec["script"]).is_file(),
                    f"{entry['id']} declares script {spec['script']!r}, which does not exist",
                )
                continue
            reason = execute.unavailable_reason(entry)
            self.assertTrue(
                reason and len(reason) > 20,
                f"{entry['id']} cannot be run and gives no usable reason",
            )
            self.assertIn("exec", json.dumps(entry), f"{entry['id']} has no exec or reason")

    def test_describe_covers_every_stand_in(self):
        rows = execute.describe(self.standins)
        self.assertEqual(len(rows), len(self.standins.all()))
        self.assertEqual([r["id"] for r in rows], sorted(r["id"] for r in rows))

    def test_a_stand_in_can_be_both_runnable_and_unexplained_only_once(self):
        """`reasoning-without-sources` is the documented non-runnable case."""
        entry = self.standins.get("reasoning-without-sources")
        self.assertIsNone(execute.exec_spec(entry))
        self.assertIn("model call", execute.unavailable_reason(entry))


class SpecTests(unittest.TestCase):
    def test_unknown_runtime_is_not_runnable(self):
        entry = {"id": "x", "exec": {"script": "a.py", "runtime": "node"}}
        self.assertIsNone(execute.exec_spec(entry))

    def test_timeout_is_bounded_by_the_ceiling(self):
        entry = {"exec": {"script": "a.py", "timeout_seconds": 10_000_000}}
        self.assertEqual(execute.exec_spec(entry)["timeout_seconds"], execute.HARD_TIMEOUT_CEILING)

    def test_a_nonsense_timeout_falls_back_to_the_default(self):
        entry = {"exec": {"script": "a.py", "timeout_seconds": "soon"}}
        self.assertEqual(execute.exec_spec(entry)["timeout_seconds"], execute.DEFAULT_TIMEOUT)

    def test_script_path_rejects_traversal(self):
        with self.assertRaises(execute.ExecError):
            execute.script_path("../../../../etc/passwd")

    def test_script_path_accepts_a_real_script(self):
        path = execute.script_path("srt_from_text.py")
        self.assertTrue(path.is_file())
        self.assertEqual(path.parent, execute.SCRIPT_DIR.resolve())


class InputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec = execute.exec_spec(StandinCatalog.load().get("srt-from-text"))

    def test_defaults_are_applied_and_required_is_enforced(self):
        resolved = execute.resolve_inputs(self.spec, {"text": "hello"})
        self.assertEqual(resolved["text"], "hello")
        self.assertEqual(resolved["seconds_per_cue"], 4.0)
        self.assertEqual(resolved["max_chars_per_line"], 84)

    def test_a_missing_required_input_fails_closed(self):
        with self.assertRaises(execute.ExecError) as caught:
            execute.resolve_inputs(self.spec, {})
        self.assertIn("text", str(caught.exception))

    def test_an_unknown_input_is_rejected_not_ignored(self):
        """A typo that is silently dropped produces a plausible artifact from nothing."""
        with self.assertRaises(execute.ExecError) as caught:
            execute.resolve_inputs(self.spec, {"text": "x", "txt": "typo"})
        self.assertIn("unknown input", str(caught.exception))
        self.assertIn("txt", str(caught.exception))

    def test_wrong_types_are_rejected(self):
        with self.assertRaises(execute.ExecError):
            execute.resolve_inputs(self.spec, {"text": "x", "max_chars_per_line": "wide"})
        with self.assertRaises(execute.ExecError):
            execute.resolve_inputs(self.spec, {"text": 42})


class ExecutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.standins = StandinCatalog.load()

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_a_stdlib_stand_in_executes_and_writes_a_real_artifact(self):
        result = execute.run(
            "srt-from-text",
            self.standins,
            inputs={"text": "One sentence. A second, longer sentence follows here."},
            workdir=self.tmp / "srt",
            operation="caption-audio",
        )
        self.assertTrue(result.executed, result.reason)
        self.assertEqual(result.status, execute.STATUS_OK)
        self.assertEqual([a["path"] for a in result.artifacts], ["subtitles.srt"])

        written = (self.tmp / "srt" / "subtitles.srt").read_text(encoding="utf-8")
        self.assertIn("-->", written)
        self.assertIn("One sentence.", written)
        self.assertIn("1", written.splitlines()[0])

    def test_execution_is_deterministic(self):
        inputs = {"text": "Alpha. Beta gamma delta. Epsilon."}
        first = execute.run("srt-from-text", self.standins, inputs=inputs, workdir=self.tmp / "a")
        second = execute.run("srt-from-text", self.standins, inputs=inputs, workdir=self.tmp / "b")
        self.assertTrue(first.executed and second.executed)
        self.assertEqual(
            (self.tmp / "a" / "subtitles.srt").read_bytes(),
            (self.tmp / "b" / "subtitles.srt").read_bytes(),
        )

    def test_an_unrunnable_stand_in_reports_unavailable_without_running(self):
        result = execute.run("reasoning-without-sources", self.standins, workdir=self.tmp / "x")
        self.assertFalse(result.executed)
        self.assertEqual(result.status, execute.STATUS_UNAVAILABLE)
        self.assertIn("model call", result.reason)
        self.assertEqual(result.artifacts, [])

    def test_a_missing_declared_tool_fails_closed(self):
        entry = {
            "id": "needs-a-unicorn",
            "exec": {
                "script": "srt_from_text.py",
                "requires_tools": ["definitely-not-installed-xyz"],
                "inputs": {},
            },
        }
        result = execute.run("needs-a-unicorn", FakeCatalog(entry), workdir=self.tmp / "t")
        self.assertFalse(result.executed)
        self.assertEqual(result.status, execute.STATUS_UNAVAILABLE)
        self.assertIn("definitely-not-installed-xyz", result.reason)
        # Nothing ran, so nothing was written -- not even the working directory.
        workdir = self.tmp / "t"
        self.assertTrue(
            not workdir.exists() or not list(workdir.iterdir()),
            "a blocked stand-in must not create output",
        )

    def test_a_missing_script_fails_closed(self):
        entry = {"id": "ghost", "exec": {"script": "not-a-real-script.py", "inputs": {}}}
        result = execute.run("ghost", FakeCatalog(entry), workdir=self.tmp / "g")
        self.assertEqual(result.status, execute.STATUS_UNAVAILABLE)
        self.assertIn("missing from this install", result.reason)

    def test_a_failing_script_reports_its_own_error_message(self):
        """The no-op stand-in refuses a required operation; that refusal must surface."""
        result = execute.run(
            "no-op-report",
            self.standins,
            workdir=self.tmp / "noop",
            operation="required-thing",
            optional=False,
        )
        self.assertFalse(result.executed)
        self.assertEqual(result.status, execute.STATUS_FAILED)
        self.assertIn("optional operations", result.reason)
        self.assertEqual(result.artifacts, [])
        self.assertNotEqual(result.exit_code, 0)

    def test_timeout_is_enforced(self):
        """A hanging stand-in is killed rather than allowed to stall the pipeline."""
        slow = self.tmp / "scripts"
        slow.mkdir()
        (slow / "hang.py").write_text(
            textwrap.dedent(
                """
                import json, sys, time
                json.loads(sys.stdin.read() or "{}")
                time.sleep(30)
                print(json.dumps({"artifacts": [], "notes": []}))
                """
            ),
            encoding="utf-8",
        )
        entry = {"id": "hang", "exec": {"script": "hang.py", "inputs": {}, "timeout_seconds": 1}}

        original = execute.SCRIPT_DIR
        execute.SCRIPT_DIR = slow
        self.addCleanup(setattr, execute, "SCRIPT_DIR", original)

        started = time.monotonic()
        result = execute.run("hang", FakeCatalog(entry), workdir=self.tmp / "hang")
        elapsed = time.monotonic() - started

        self.assertFalse(result.executed)
        self.assertEqual(result.status, execute.STATUS_FAILED)
        self.assertIn("timed out", result.reason)
        self.assertLess(elapsed, 20, "the timeout was not enforced")

    def test_a_stand_in_cannot_claim_a_file_outside_its_working_directory(self):
        outside = self.tmp / "outside.txt"
        outside.write_text("should never be claimed", encoding="utf-8")
        workdir = self.tmp / "inside"
        workdir.mkdir()

        kept, rejected = execute._collect_artifacts(
            {
                "artifacts": [
                    {"path": "fine.txt", "kind": "text"},
                    {"path": "../outside.txt", "kind": "text"},
                    {"path": str(outside), "kind": "text"},
                ]
            },
            workdir,
        )
        self.assertEqual([a["path"] for a in kept], [])
        self.assertEqual(len(rejected), 3)
        for path in ("../outside.txt", str(outside)):
            self.assertIn(path, rejected)

    def test_an_artifact_that_was_never_written_is_not_reported(self):
        workdir = self.tmp / "w"
        workdir.mkdir()
        (workdir / "real.txt").write_text("x", encoding="utf-8")
        kept, rejected = execute._collect_artifacts(
            {"artifacts": [{"path": "real.txt"}, {"path": "imaginary.txt"}]}, workdir
        )
        self.assertEqual([a["path"] for a in kept], ["real.txt"])
        self.assertEqual(rejected, ["imaginary.txt (reported but not written)"])

    def test_output_is_redacted_and_truncated(self):
        secret = "sk-live-abcdefghijklmnop1234"
        clipped = execute._clip(f"token is {secret} and that is bad")
        self.assertNotIn(secret, clipped)
        self.assertIn("[redacted]", clipped)

        long = execute._clip("x" * (execute._MAX_OUTPUT_CHARS + 500))
        self.assertIn("truncated", long)
        self.assertLess(len(long), execute._MAX_OUTPUT_CHARS + 200)


class CatalogValidationTests(unittest.TestCase):
    """An exec contract that does not hold must fail at load, not at the point of use."""

    @classmethod
    def setUpClass(cls):
        from r2s.capability.vocab import CapabilityVocab
        from r2s.validate import schema

        cls.schema = schema
        cls.vocab = CapabilityVocab.load()

    def validate(self, entries):
        catalog = FakeCatalogCollection(entries)
        return self.schema.validate_standin_catalog(catalog, self.vocab)

    def entry(self, **overrides):
        base = {
            "id": "sample",
            "type": "reducing",
            "capability": "file.write",
            "fidelity": 0.5,
            "desc": "a sample",
        }
        base.update(overrides)
        return base

    def test_a_script_that_is_not_shipped_is_rejected(self):
        failures = self.validate([self.entry(exec={"script": "not-shipped.py", "inputs": {}})])
        self.assertTrue(any("not shipped" in f for f in failures), failures)

    def test_a_stand_in_that_cannot_run_must_say_why(self):
        failures = self.validate([self.entry()])
        self.assertTrue(any("gives no" in f for f in failures), failures)

    def test_declaring_both_exec_and_unavailable_is_a_contradiction(self):
        failures = self.validate(
            [
                self.entry(
                    exec={"script": "srt_from_text.py", "inputs": {}},
                    exec_unavailable="it cannot run",
                )
            ]
        )
        self.assertTrue(any("contradiction" in f for f in failures), failures)

    def test_an_unknown_input_type_is_rejected(self):
        failures = self.validate(
            [self.entry(exec={"script": "srt_from_text.py", "inputs": {"n": {"type": "float"}}})]
        )
        self.assertTrue(any("unknown type" in f for f in failures), failures)

    def test_the_shipped_catalog_is_valid(self):
        self.assertEqual(
            self.schema.validate_standin_catalog(StandinCatalog.load(), self.vocab), []
        )


class FakeCatalogCollection:
    """Minimal stand-in catalog, for validating entries the shipped one does not contain."""

    def __init__(self, entries):
        self._entries = entries
        self.ids = frozenset(e["id"] for e in entries)
        self.defaults = {}

    def all(self):
        return list(self._entries)


class RunCommandTests(unittest.TestCase):
    """`r2s run` end to end, with a hand-built report so the outcome is deterministic."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.report_path = self.tmp / "report.json"
        self.report_path.write_text(
            json.dumps(
                {
                    "kind": "r2s.report",
                    "rows": [
                        {
                            "id": "caption-audio",
                            "binding": "script",
                            "stand_in": "srt-from-text",
                            "optional": False,
                            "credentials": [],
                        },
                        {
                            "id": "fetch-trends",
                            "binding": "script",
                            "stand_in": "reasoning-without-sources",
                            "optional": False,
                            "credentials": [],
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )

    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, "-m", "r2s", *args],
            capture_output=True,
            text=True,
            cwd=str(PROJECT),
            env={
                **dict(__import__("os").environ),
                "PYTHONPATH": str(PROJECT / "src"),
            },
            check=False,
        )

    def test_running_a_stdlib_operation_succeeds_and_writes_the_artifact(self):
        out = self.tmp / "out"
        proc = self.run_cli(
            "run",
            str(self.report_path),
            "--operation",
            "caption-audio",
            "--inputs",
            json.dumps({"text": "First. Second sentence here."}),
            "--out",
            str(out),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("caption-audio", proc.stdout)
        self.assertTrue((out / "caption-audio" / "subtitles.srt").is_file())

    def test_a_required_operation_that_cannot_run_fails_the_command(self):
        proc = self.run_cli(
            "run",
            str(self.report_path),
            "--operation",
            "fetch-trends",
            "--out",
            str(self.tmp / "o"),
        )
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn("did not execute", proc.stderr)

    def test_an_unknown_operation_name_is_a_usage_error(self):
        proc = self.run_cli(
            "run", str(self.report_path), "--operation", "nope", "--out", str(self.tmp / "o")
        )
        self.assertEqual(proc.returncode, 2)
        self.assertIn("no stand-in-backed operation named nope", proc.stderr)

    def test_without_a_selector_it_explains_itself_rather_than_guessing(self):
        proc = self.run_cli("run", str(self.report_path))
        self.assertEqual(proc.returncode, 2)
        self.assertIn("--operation", proc.stderr)

    def test_dry_run_runs_nothing(self):
        out = self.tmp / "dry"
        proc = self.run_cli("run", str(self.report_path), "--all", "--dry-run", "--out", str(out))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("would run", proc.stdout)
        self.assertFalse(out.exists())


if __name__ == "__main__":
    unittest.main()
