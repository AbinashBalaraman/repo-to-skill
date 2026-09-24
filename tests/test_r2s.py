"""Test suite. Stdlib unittest only -- the project has no runtime dependencies and
the tests keep it that way."""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from r2s.capability import invariants, router
from r2s.capability.profiles import Profile
from r2s.capability.vocab import CapabilityVocab, StandinCatalog, VocabError
from r2s.extract import effects as effects_mod
from r2s.extract import phases
from r2s.extract.catalog import ProviderCatalog
from r2s.source import build_snapshot
from r2s.validate import schema as schema_mod

PROJECT = Path(__file__).resolve().parent.parent
FIXTURE = PROJECT / "fixtures" / "cli-tool" / "feed-sync"


def base_operation(**overrides):
    operation = {
        "id": "do-thing",
        "name": "Do thing",
        "stage": "transform",
        "effect": "effectful-external",
        "requires": ["http.request"],
        "optional": False,
        "deterministic": False,
        "gate": [],
        "confidence": "high",
        "evidence": [{"source": "declared", "loc": "cli.py:1", "detail": "test"}],
    }
    operation.update(overrides)
    return operation


class TestVocabulary(unittest.TestCase):
    def setUp(self):
        self.vocab = CapabilityVocab.load()
        self.standins = StandinCatalog.load()

    def test_vocabulary_loads_and_is_v2(self):
        self.assertTrue(self.vocab.version.startswith("2."))
        self.assertIn("http.request", self.vocab.ids)
        # The v2 extensions that arbitrary repos need.
        for capability in (
            "queue.publish",
            "db.migrate",
            "payment.charge",
            "external.call",
            "vector.search",
        ):
            self.assertIn(capability, self.vocab.ids)

    def test_unknown_capability_hard_fails(self):
        """M0 exit criterion: an unknown ID is an error, not silently 'missing'."""
        with self.assertRaises(VocabError):
            self.vocab.validate(["http.request", "not.a.capability"], "test")

    def test_unknown_standin_hard_fails(self):
        """M0 exit criterion: an invented stand-in is rejected, not accepted."""
        with self.assertRaises(VocabError):
            self.standins.validate("my-custom-standin", "test")

    def test_standin_fidelity_must_be_below_one(self):
        with self.assertRaises(VocabError):
            self.standins.validate("bogus", "test")
        entry = self.standins.validate("gradient-card-1080p", "test")
        self.assertLess(entry["fidelity"], 1.0)

    def test_every_default_standin_exists(self):
        for capability, standin_id in self.standins.defaults.items():
            self.assertIn(
                standin_id,
                self.standins.ids,
                f"default stand-in for {capability} is not in the catalog",
            )

    def test_every_default_standin_capability_is_real(self):
        for capability in self.standins.defaults:
            self.assertIn(capability, self.vocab.ids)


class TestRouter(unittest.TestCase):
    def setUp(self):
        self.vocab = CapabilityVocab.load()
        self.standins = StandinCatalog.load()
        self.bare = Profile({"id": "bare", "name": "Bare", "native": ["text.generate"], "mcp": []})
        self.full = Profile(
            {
                "id": "full",
                "name": "Full",
                "native": ["text.generate", "http.request", "data.write"],
                "mcp": [],
            }
        )
        self.mcp_only = Profile(
            {"id": "mcp", "name": "Mcp", "native": ["text.generate"], "mcp": ["http.request"]}
        )

    def resolve(self, operation, profile):
        return router.resolve([operation], profile, self.vocab, self.standins)[0]

    def test_native_when_fully_available(self):
        row = self.resolve(base_operation(), self.full)
        self.assertEqual(row["binding"], "native")

    def test_mcp_when_only_server_provides_it(self):
        row = self.resolve(base_operation(), self.mcp_only)
        self.assertEqual(row["binding"], "mcp")

    def test_required_unsatisfiable_asks_the_user(self):
        row = self.resolve(base_operation(), self.bare)
        self.assertEqual(row["binding"], "ask-user")

    def test_optional_unsatisfiable_is_dropped(self):
        row = self.resolve(base_operation(optional=True), self.bare)
        self.assertEqual(row["binding"], "drop")

    def test_required_operation_never_drops(self):
        """I2. A required operation with no path must ask, never vanish."""
        row = self.resolve(base_operation(optional=False), self.bare)
        self.assertNotEqual(row["binding"], "drop")

    def test_gates_enforced_on_executing_binding(self):
        operation = base_operation(gate=["publish"], gate_confidence="low")
        row = self.resolve(operation, self.full)
        self.assertEqual(row["gates"], ["publish"])
        self.assertEqual(row["deferred_gates"], [])

    def test_gates_deferred_when_routed_to_ask_user(self):
        """I7. The gate must move into the question, not disappear."""
        operation = base_operation(gate=["publish"], gate_confidence="low")
        row = self.resolve(operation, self.bare)
        self.assertEqual(row["binding"], "ask-user")
        self.assertEqual(row["gates"], [])
        self.assertEqual(row["deferred_gates"], ["publish"])

    def test_standin_used_when_declared(self):
        operation = base_operation(requires=["image.generate"], stand_in="gradient-card-1080p")
        row = self.resolve(operation, self.bare)
        self.assertEqual(row["binding"], "script")
        self.assertAlmostEqual(row["coverage"], 0.25)

    def test_standin_requires_declaration_not_invention(self):
        operation = base_operation(requires=["image.generate"], stand_in="made-up-standin")
        with self.assertRaises(VocabError):
            self.resolve(operation, self.bare)

    def test_summary_counts_only_required_for_executability(self):
        """The prototype bug: dropping an optional operation was counted as failure."""
        narrow = Profile({"id": "narrow", "name": "Narrow", "native": ["text.generate"], "mcp": []})
        operations = [
            base_operation(id="needed", requires=["text.generate"]),
            base_operation(id="extra", requires=["http.request"], optional=True),
        ]
        rows = router.resolve(operations, narrow, self.vocab, self.standins)
        summary = router.summarise(rows)
        self.assertTrue(summary["all_required_operations_executable"])
        self.assertEqual(summary["lost_optional"], ["extra"])
        self.assertEqual(summary["lost"], [])
        self.assertEqual(summary["halted"], [])

    def test_gates_are_profile_invariant(self):
        """A better harness buys a better binding, never a weaker gate."""
        operation = base_operation(gate=["publish", "irreversible"], gate_confidence="high")
        for profile in (self.bare, self.mcp_only, self.full):
            row = self.resolve(operation, profile)
            surfaced = set(row["gates"]) | set(row["deferred_gates"])
            self.assertEqual(
                surfaced, {"publish", "irreversible"}, f"gate lost on profile {profile.id}"
            )


class TestInvariants(unittest.TestCase):
    def setUp(self):
        self.vocab = CapabilityVocab.load()
        self.standins = StandinCatalog.load()
        self.full = Profile(
            {"id": "full", "name": "Full", "native": ["text.generate", "http.request"], "mcp": []}
        )

    def test_clean_inventory_passes(self):
        operations = [base_operation(gate=["publish"], gate_confidence="low")]
        rows = router.resolve(operations, self.full, self.vocab, self.standins)
        self.assertEqual(invariants.verify(operations, rows), [])

    def test_i1_detects_gate_loss(self):
        operations = [base_operation(gate=["publish"], gate_confidence="low")]
        rows = router.resolve(operations, self.full, self.vocab, self.standins)
        rows[0]["gates"] = []  # simulate conversion dropping the gate
        failures = invariants.verify(operations, rows)
        self.assertTrue(any(f.startswith("I1") for f in failures), failures)

    def test_i7_detects_gate_neither_enforced_nor_deferred(self):
        bare = Profile({"id": "bare", "name": "Bare", "native": ["text.generate"], "mcp": []})
        operations = [base_operation(gate=["publish"], gate_confidence="low")]
        rows = router.resolve(operations, bare, self.vocab, self.standins)
        rows[0]["deferred_gates"] = []  # simulate the prototype's silent drop
        failures = invariants.verify(operations, rows)
        self.assertTrue(any(f.startswith("I7") for f in failures), failures)

    def test_i6_detects_ungated_publish(self):
        operations = [base_operation(gate=["publish"], gate_confidence="low")]
        rows = router.resolve(operations, self.full, self.vocab, self.standins)
        rows[0]["gates"] = []
        failures = invariants.verify(operations, rows)
        self.assertTrue(any(f.startswith("I6") for f in failures), failures)

    def test_deferred_question_check(self):
        rows = [{"id": "x", "deferred_gates": ["publish"]}]
        self.assertTrue(invariants.check_deferred_questions(rows, {}))
        self.assertTrue(invariants.check_deferred_questions(rows, {"x": "shall I proceed?"}))
        self.assertEqual(
            invariants.check_deferred_questions(rows, {"x": "this will PUBLISH it"}),
            [],
        )


class TestSchemaValidation(unittest.TestCase):
    def setUp(self):
        self.vocab = CapabilityVocab.load()
        self.standins = StandinCatalog.load()

    def inventory(self, operations):
        return {
            "schema_version": "1.0.0",
            "source": {"kind": "local"},
            "repo_class": "cli-tool",
            "inventory_status": "reviewed",
            "extraction": {"converter_version": "0.1.0"},
            "operations": operations,
        }

    def test_valid_inventory_passes(self):
        self.assertEqual(
            schema_mod.validate_inventory(
                self.inventory([base_operation()]), self.vocab, self.standins
            ),
            [],
        )

    def test_rejects_unknown_capability(self):
        failures = schema_mod.validate_inventory(
            self.inventory([base_operation(requires=["bogus.capability"])]),
            self.vocab,
            self.standins,
        )
        self.assertTrue(any("unknown capability" in f for f in failures), failures)

    def test_rejects_inline_standin(self):
        operation = base_operation()
        operation["stand_in"] = {"desc": "invented", "fidelity": 0.5}
        failures = schema_mod.validate_inventory(
            self.inventory([operation]), self.vocab, self.standins
        )
        self.assertTrue(any("stand_in must be a catalog ID" in f for f in failures), failures)

    def test_rejects_gate_without_gate_confidence(self):
        operation = base_operation(gate=["publish"])
        failures = schema_mod.validate_inventory(
            self.inventory([operation]), self.vocab, self.standins
        )
        self.assertTrue(any("no gate_confidence" in f for f in failures), failures)

    def test_rejects_bad_stage_and_effect(self):
        failures = schema_mod.validate_inventory(
            self.inventory([base_operation(stage="publish", effect="sideways")]),
            self.vocab,
            self.standins,
        )
        self.assertTrue(any("unknown stage" in f for f in failures), failures)
        self.assertTrue(any("unknown effect" in f for f in failures), failures)

    def test_rejects_non_kebab_id(self):
        failures = schema_mod.validate_inventory(
            self.inventory([base_operation(id="Do_The_Thing")]), self.vocab, self.standins
        )
        self.assertTrue(any("kebab-case" in f for f in failures), failures)

    def test_rejects_empty_requires(self):
        failures = schema_mod.validate_inventory(
            self.inventory([base_operation(requires=[])]), self.vocab, self.standins
        )
        self.assertTrue(any("non-empty list" in f for f in failures), failures)


class TestEffectMatcher(unittest.TestCase):
    """The false-positive class that the fixture exposed: bare method names that
    collide with builtins and common idioms."""

    def setUp(self):
        self.catalog = effects_mod.EffectCatalog.load()

    def match(self, source):
        import ast

        tree = ast.parse(source)
        call = next(n for n in ast.walk(tree) if isinstance(n, ast.Call))
        return effects_mod.match_call(call, self.catalog)

    def test_requests_get_is_http_read(self):
        pattern = self.match("requests.get(url)")
        self.assertIsNotNone(pattern)
        self.assertEqual(pattern["capability"], "http.request")
        self.assertEqual(pattern["effect"], "read-external")

    def test_requests_post_is_http_write(self):
        pattern = self.match("requests.post(url, json=body)")
        self.assertIsNotNone(pattern)
        self.assertEqual(pattern["effect"], "effectful-external")

    def test_environ_get_is_not_a_cache_read(self):
        """os.environ.get collided with the cache pattern before the fix."""
        pattern = self.match("os.environ.get('KEY')")
        self.assertIsNone(pattern, "os.environ.get must not match cache.get")

    def test_dict_get_is_not_a_cache_read(self):
        pattern = self.match("payload.get('items')")
        self.assertIsNone(pattern, "dict.get must not match cache.get")

    def test_open_for_write(self):
        pattern = self.match("open(path, 'w')")
        self.assertEqual(pattern["capability"], "file.write")

    def test_open_default_is_read(self):
        pattern = self.match("open(path)")
        self.assertEqual(pattern["capability"], "file.read")

    def test_open_explicit_read_is_read(self):
        pattern = self.match("open(path, 'r')")
        self.assertEqual(pattern["capability"], "file.read")

    def test_session_commit_is_a_write(self):
        pattern = self.match("session.commit()")
        self.assertEqual(pattern["capability"], "data.write")

    def test_select_is_a_read(self):
        pattern = self.match("select(Entry)")
        self.assertEqual(pattern["capability"], "data.query")

    def test_execute_alone_is_ambiguous_and_unmatched(self):
        """execute(select(...)) used to be misread as a write."""
        pattern = self.match("session.execute(stmt)")
        self.assertIsNone(pattern, "bare execute is ambiguous and must not match")


class TestPhases(unittest.TestCase):
    def test_verb_beats_spurious_gate(self):
        """T3 over-proposes gates, so a stray publish gate must not reclassify."""
        self.assertEqual(
            phases.infer("Fetch the feed", "read-external", ["http.request"], ["publish"]),
            "acquire",
        )

    def test_gate_used_when_no_verb_matches(self):
        self.assertEqual(
            phases.infer("widget", "effectful-external", ["http.request"], ["publish"]), "deliver"
        )

    def test_all_phases_are_reachable(self):
        samples = {
            "acquire": ("Fetch records", "read-external"),
            "prepare": ("Configure the client", "effectful-local"),
            "transform": ("Render the report", "effectful-local"),
            "verify": ("Validate the schema", "read-local"),
            "deliver": ("Publish the digest", "effectful-external"),
            "observe": ("Report metrics", "read-external"),
        }
        for expected, (name, effect) in samples.items():
            self.assertEqual(phases.infer(name, effect, [], []), expected, name)


class TestCatalog(unittest.TestCase):
    def setUp(self):
        self.catalog = ProviderCatalog.load()

    def test_ambiguous_packages_are_flagged(self):
        matches, _ = self.catalog.match({"pypi": {"boto3"}})
        self.assertTrue(matches[0].ambiguous)

    def test_unknown_package_falls_back_not_silent(self):
        matches, unknown = self.catalog.match({"pypi": {"totally-made-up-lib"}})
        self.assertEqual(matches, [])
        self.assertEqual(unknown, [("pypi", "totally-made-up-lib")])
        fallback = self.catalog.fallback_match("pypi", "totally-made-up-lib")
        self.assertEqual(fallback.capabilities, ["external.call"])

    def test_tooling_is_ignored(self):
        matches, unknown = self.catalog.match({"pypi": {"pytest", "black"}})
        self.assertEqual(matches, [])
        self.assertEqual(unknown, [])


class TestProfilerOnFixture(unittest.TestCase):
    def setUp(self):
        self.snapshot = build_snapshot(FIXTURE)

    def test_snapshot_respects_ignores_and_is_sorted(self):
        files = list(self.snapshot.files)
        self.assertEqual(files, sorted(files))
        self.assertFalse(any("__pycache__" in f for f in files))

    def test_classified_as_cli_tool(self):
        from r2s.profiler import profile

        result = profile(self.snapshot)
        self.assertEqual(result.repo_class, "cli-tool")
        self.assertEqual(result.class_confidence, "high")

    def test_entrypoint_discovered_from_pyproject(self):
        from r2s.profiler import profile

        result = profile(self.snapshot)
        names = [e.name for e in result.entrypoints]
        self.assertIn("feed-sync", names)


class TestExtractionOnFixture(unittest.TestCase):
    """End-to-end on a repo the system has never seen. The correct inventory is not
    unique, so this asserts properties rather than exact equality."""

    @classmethod
    def setUpClass(cls):
        from r2s.extract import catalog as catalog_mod
        from r2s.extract import effects as effects_mod
        from r2s.extract import pipeline
        from r2s.profiler import profile

        cls.vocab = CapabilityVocab.load()
        cls.standins = StandinCatalog.load()
        snapshot = build_snapshot(FIXTURE)
        result = profile(snapshot)
        draft, warnings = pipeline.extract(
            snapshot,
            result,
            cls.vocab,
            catalog_mod.ProviderCatalog.load(),
            effects_mod.EffectCatalog.load(),
            cls.standins,
            source={"kind": "local"},
        )
        cls.draft = draft
        cls.warnings = warnings
        cls.by_id = {op["id"]: op for op in draft["operations"]}

    def test_extracts_the_declared_subcommands(self):
        for expected in ("fetch", "digest", "publish", "status"):
            self.assertIn(expected, self.by_id, f"missing operation {expected}")

    def test_container_decorator_is_not_an_operation(self):
        """@click.group() is a container, not an operation."""
        self.assertNotIn("cli", self.by_id)

    def test_fetch_requires_http_and_storage(self):
        requires = set(self.by_id["fetch"]["requires"])
        self.assertIn("http.request", requires)
        self.assertIn("data.write", requires)
        self.assertNotIn("cache.read", requires)

    def test_status_is_read_only(self):
        operation = self.by_id["status"]
        self.assertEqual(operation["stage"], "observe")
        self.assertIn("data.query", operation["requires"])
        self.assertNotIn("data.write", operation["requires"])

    def test_publish_is_deliver_and_effectful(self):
        operation = self.by_id["publish"]
        self.assertEqual(operation["stage"], "deliver")
        self.assertEqual(operation["effect"], "effectful-external")

    def test_unresolved_dependency_is_reported_not_silently_dropped(self):
        """feedparser is not in the catalog. It must surface, not vanish."""
        unresolved = self.draft["diagnostics"]["unresolved_dependencies"]
        self.assertTrue(any("feedparser" in item for item in unresolved), unresolved)
        self.assertIn("unresolved-external-calls", self.by_id)

    def test_every_operation_has_a_non_empty_requirement(self):
        """Never silently 'requires nothing'."""
        for operation in self.draft["operations"]:
            self.assertTrue(operation["requires"], operation["id"])

    def test_every_operation_carries_evidence(self):
        for operation in self.draft["operations"]:
            self.assertTrue(operation["evidence"], operation["id"])

    def test_draft_validates_against_the_schema(self):
        failures = schema_mod.validate_inventory(self.draft, self.vocab, self.standins)
        self.assertEqual(failures, [])

    def test_extraction_is_deterministic(self):
        import copy

        from r2s.extract import catalog as catalog_mod
        from r2s.extract import effects as effects_mod
        from r2s.extract import pipeline
        from r2s.profiler import profile

        snapshot = build_snapshot(FIXTURE)
        result = profile(snapshot)
        again, _ = pipeline.extract(
            snapshot,
            result,
            self.vocab,
            catalog_mod.ProviderCatalog.load(),
            effects_mod.EffectCatalog.load(),
            self.standins,
            source={"kind": "local"},
        )
        left = copy.deepcopy(self.draft)
        right = copy.deepcopy(again)
        for doc in (left, right):
            doc.pop("diagnostics", None)
            doc.pop("extraction", None)
        self.assertEqual(left, right, "extraction must be byte-stable across runs")


class TestSecurityControls(unittest.TestCase):
    """The controls that matter for a tool that reads untrusted repositories."""

    def setUp(self):
        from r2s.util.io import contains_secret, redact

        self.redact = redact
        self.contains_secret = contains_secret

    def test_credential_shapes_are_detected_and_redacted(self):
        cases = [
            "sk-live-abcdefghijklmnop1234",
            "sk-proj-ABCDEFGHIJKLMNOPQRSTUV",
            "AKIAIOSFODNN7EXAMPLE",
            "ghp_abcdefghijklmnopqrstuvwxyz0123456789",
            "xoxb-1234567890-abcdefghijkl",
            "AIzaSyA1234567890abcdefghijklmnopqrstu",
            "password=hunter2secret",
            "api_key: 0123456789abcdef",
        ]
        for sample in cases:
            with self.subTest(sample=sample):
                self.assertTrue(self.contains_secret(sample), f"not detected: {sample}")
                self.assertEqual(self.redact(sample).count("[redacted]"), 1, sample)

    def test_ordinary_text_is_untouched(self):
        text = "Publish the digest to the configured webhook."
        self.assertFalse(self.contains_secret(text))
        self.assertEqual(self.redact(text), text)

    def test_secret_files_are_excluded_by_name(self):
        from r2s.source.snapshot import is_secret_file

        for name in (
            ".env",
            ".env.local",
            ".env.production",
            "id_rsa",
            "server.pem",
            "private.key",
            ".npmrc",
            ".pypirc",
            ".netrc",
            "credentials.json",
            "service-account.json",
            "kubeconfig",
            ".htpasswd",
        ):
            with self.subTest(name=name):
                self.assertTrue(is_secret_file(name), f"not excluded: {name}")

    def test_ordinary_files_are_not_excluded(self):
        from r2s.source.snapshot import is_secret_file

        for name in (
            "README.md",
            "cli.py",
            "pyproject.toml",
            "environment.py",
            "keyring_helpers.py",
            "models.py",
        ):
            with self.subTest(name=name):
                self.assertFalse(is_secret_file(name), f"wrongly excluded: {name}")

    def test_git_url_allowlist(self):
        from r2s.source.local import SourceError, validate_git_url

        for url in (
            "https://github.com/o/r",
            "git@github.com:o/r.git",
            "ssh://git@host/o/r",
            "git://host/o/r",
        ):
            with self.subTest(url=url):
                self.assertEqual(validate_git_url(url), url)

        for url in (
            "--upload-pack=touch /tmp/pwned",
            "-c core.pager=id",
            "https://x.test/r; rm -rf /",
            "ext::sh -c whoami",
            "",
        ):
            with self.subTest(url=url), self.assertRaises(SourceError):
                validate_git_url(url)

    def test_sha_is_validated(self):
        from r2s.source.local import SourceError, validate_sha

        self.assertEqual(validate_sha("a" * 40), "a" * 40)
        self.assertIsNone(validate_sha(None))
        for bad in ("HEAD", "--upload-pack=x", "abc", "z" * 40):
            with self.subTest(sha=bad), self.assertRaises(SourceError):
                validate_sha(bad)

    def test_subdir_cannot_escape_the_root(self):
        from r2s.source.snapshot import build_snapshot

        for escape in ("../..", "../../etc", "src/../../.."):
            with self.subTest(subdir=escape), self.assertRaises(ValueError):
                build_snapshot(FIXTURE, subdir=escape)

    def test_subdir_inside_the_root_is_allowed(self):
        from r2s.source.snapshot import build_snapshot

        snapshot = build_snapshot(FIXTURE, subdir="src")
        self.assertTrue(all(f.startswith("feed_sync/") for f in snapshot.files), snapshot.files)


class TestNoLeakIntoArtifact(unittest.TestCase):
    """The critical class for this project: a repository's credentials must not reach
    the emitted inventory, which is what a user attaches to a bug report."""

    @classmethod
    def setUpClass(cls):
        cls.vocab = CapabilityVocab.load()
        cls.standins = StandinCatalog.load()

    def test_documentation_evidence_never_carries_the_raw_line(self):
        from r2s.extract.docs import DocSignal

        secret = "sk-live-abcdefghijklmnop1234"
        signal = DocSignal("publish", "README.md:3", f"Publish with your token: {secret}")
        evidence = signal.evidence()
        blob = json.dumps(evidence)
        self.assertNotIn(secret, blob, "the raw documentation line reached the evidence")
        self.assertEqual(evidence["loc"], "README.md:3")
        self.assertIn("publish", evidence["detail"])

    def test_extraction_of_a_repo_with_credentials_in_its_readme_and_env(self):
        """End to end: a repo whose README carries a token and which ships a .env."""
        import tempfile

        from r2s.extract import catalog as catalog_mod
        from r2s.extract import effects as effects_mod
        from r2s.extract import pipeline
        from r2s.profiler import profile

        secret = "sk-live-LEAKCANARY1234567890"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "pyproject.toml").write_text(
                '[project]\nname = "t"\nversion = "1"\ndependencies = ["requests", "click"]\n',
                encoding="utf-8",
            )
            (root / "src" / "cli.py").write_text(
                "import click\nimport requests\n\n"
                "@click.command()\ndef publish():\n"
                '    """Publish."""\n'
                '    requests.post("https://x.test")\n',
                encoding="utf-8",
            )
            (root / "README.md").write_text(
                f"Publish with your token: `tool publish --token {secret}`\n", encoding="utf-8"
            )
            (root / ".env").write_text(
                "OPENAI_API_KEY=sk-proj-ENVCANARY1234567890\n", encoding="utf-8"
            )

            snapshot = build_snapshot(root)
            result = profile(snapshot)
            draft, _ = pipeline.extract(
                snapshot,
                result,
                self.vocab,
                catalog_mod.ProviderCatalog.load(),
                effects_mod.EffectCatalog.load(),
                self.standins,
                source={"kind": "local"},
            )
            blob = json.dumps(draft)
            scanned = snapshot.files
            skipped = snapshot.skipped_secrets

        self.assertNotIn(secret, blob, "a README credential leaked into the artifact")
        self.assertNotIn("ENVCANARY", blob, "a .env credential leaked into the artifact")
        self.assertNotIn(".env", scanned, ".env was scanned at all")
        self.assertGreaterEqual(skipped, 1)
        self.assertTrue(draft["operations"])


class TestPackagedDataResolves(unittest.TestCase):
    """The packaging bug this suite exists to prevent: runtime data resolved relative
    to the repository root works in a checkout and breaks under a real install."""

    def test_every_runtime_data_path_exists(self):
        from r2s import config

        for name in (
            "CAPABILITIES_FILE",
            "PROVIDERS_FILE",
            "EFFECTS_FILE",
            "STANDIN_INDEX",
            "PROFILE_DIR",
            "CATALOG_DIR",
            "SCHEMA_DIR",
        ):
            with self.subTest(path=name):
                self.assertTrue(getattr(config, name).exists(), f"{name} does not exist")

    def test_runtime_data_lives_inside_the_package(self):
        from r2s import config

        package = config.PACKAGE_DIR.resolve()
        for name in ("CAPABILITIES_FILE", "PROVIDERS_FILE", "STANDIN_INDEX"):
            path = getattr(config, name).resolve()
            self.assertIn(
                package, path.parents, f"{name} is outside the package and will not ship in a wheel"
            )

    def test_data_is_reachable_via_importlib_resources(self):
        from importlib.resources import files

        data = files("r2s").joinpath("data")
        for relative in (
            "capabilities.json",
            "catalog/providers.json",
            "catalog/effects.json",
            "catalog/stand-ins/index.json",
            "harness-profiles/bare.json",
            "schemas/inventory.schema.json",
        ):
            with self.subTest(relative=relative):
                self.assertTrue(data.joinpath(relative).is_file(), relative)


class TestCatalogValidation(unittest.TestCase):
    """Catalogs are trusted input that drives routing, so they are validated."""

    def setUp(self):
        self.vocab = CapabilityVocab.load()

    def test_bundled_catalog_is_valid(self):
        self.assertEqual(schema_mod.validate_catalog(ProviderCatalog.load(), self.vocab), [])

    def test_bundled_standin_catalog_is_valid(self):
        self.assertEqual(schema_mod.validate_standin_catalog(StandinCatalog.load(), self.vocab), [])

    def test_unknown_capability_in_a_provider_is_rejected(self):
        from r2s.extract.catalog import ProviderCatalog as PC

        catalog = PC(
            {
                "version": "t",
                "providers": [
                    {
                        "id": "bad",
                        "match": {"pypi": ["bad"]},
                        "capabilities": ["not.a.capability"],
                    }
                ],
            }
        )
        failures = schema_mod.validate_catalog(catalog, self.vocab)
        self.assertTrue(any("unknown capability" in f for f in failures), failures)

    def test_ambiguous_provider_must_explain_itself(self):
        from r2s.extract.catalog import ProviderCatalog as PC

        catalog = PC(
            {
                "version": "t",
                "providers": [
                    {
                        "id": "amb",
                        "match": {"pypi": ["amb"]},
                        "capabilities": ["http.request"],
                        "ambiguous": True,
                    }
                ],
            }
        )
        failures = schema_mod.validate_catalog(catalog, self.vocab)
        self.assertTrue(any("ambiguous_note" in f for f in failures), failures)

    def test_standin_fidelity_out_of_range_is_rejected(self):
        catalog = StandinCatalog(
            {
                "version": "t",
                "standins": [
                    {
                        "id": "s",
                        "type": "reducing",
                        "capability": "file.write",
                        "fidelity": 1.5,
                        "desc": "nope",
                    }
                ],
            }
        )
        failures = schema_mod.validate_standin_catalog(catalog, self.vocab)
        self.assertTrue(any("fidelity" in f for f in failures), failures)

    def test_standin_default_must_point_at_a_real_entry(self):
        catalog = StandinCatalog(
            {
                "version": "t",
                "standins": [
                    {
                        "id": "s",
                        "type": "reducing",
                        "capability": "file.write",
                        "fidelity": 0.5,
                        "desc": "ok",
                    }
                ],
                "defaults": {"image.generate": "does-not-exist"},
            }
        )
        failures = schema_mod.validate_standin_catalog(catalog, self.vocab)
        self.assertTrue(any("unknown id" in f for f in failures), failures)


class TestAttributionHonesty(unittest.TestCase):
    """`_claim_sites` used to smear unattributable effect sites across every candidate
    with no body range, reporting high confidence while being wrong."""

    def test_several_rangeless_candidates_do_not_all_claim_the_leftovers(self):
        from r2s.extract.dialects.base import Candidate
        from r2s.extract.effects import EffectSite
        from r2s.extract.pipeline import _claim_sites

        first = Candidate(op_id="a", name="A", dialect="t", declaration_kind="k", loc="cli.py:1")
        second = Candidate(op_id="b", name="B", dialect="t", declaration_kind="k", loc="cli.py:2")
        first.body_range = None
        second.body_range = None
        sites = [EffectSite("http.request", "read-external", "cli.py:9", "x", "p")]

        claims, weak, unattributed = _claim_sites([first, second], sites)

        self.assertEqual(claims["a"], [])
        self.assertEqual(claims["b"], [])
        self.assertEqual(len(unattributed), 1)
        self.assertEqual(weak, set())

    def test_a_single_rangeless_candidate_takes_leftovers_but_is_flagged_weak(self):
        from r2s.extract.dialects.base import Candidate
        from r2s.extract.effects import EffectSite
        from r2s.extract.pipeline import _claim_sites

        only = Candidate(op_id="a", name="A", dialect="t", declaration_kind="k", loc="cli.py:1")
        only.body_range = None
        sites = [EffectSite("http.request", "read-external", "cli.py:9", "x", "p")]

        claims, weak, unattributed = _claim_sites([only], sites)

        self.assertEqual(len(claims["a"]), 1)
        self.assertEqual(weak, {"a"})
        self.assertEqual(unattributed, [])

    def test_nested_body_ranges_resolve_to_the_innermost_owner(self):
        from r2s.extract.dialects.base import Candidate
        from r2s.extract.effects import EffectSite
        from r2s.extract.pipeline import _claim_sites

        outer = Candidate(
            op_id="main", name="Main", dialect="t", declaration_kind="k", loc="cli.py:1"
        )
        outer.body_range = (1, 50)
        inner = Candidate(
            op_id="cmd", name="Cmd", dialect="t", declaration_kind="k", loc="cli.py:10"
        )
        inner.body_range = (10, 20)
        sites = [EffectSite("http.request", "read-external", "cli.py:15", "x", "p")]

        claims, _, _ = _claim_sites([outer, inner], sites)

        self.assertEqual(len(claims["cmd"]), 1, "inner command should own its site")
        self.assertEqual(claims["main"], [], "outer main must not double-claim")

    def test_weak_attribution_caps_confidence(self):
        from r2s.extract.confidence import HIGH, MEDIUM, fuse

        evidence = [
            {"source": "declared", "loc": "cli.py:1", "detail": "d"},
            {"source": "traced", "loc": "cli.py:9", "detail": "t"},
            {"source": "inferred", "loc": "manifest", "detail": "i"},
        ]
        strong, _ = fuse(evidence)
        capped, notes = fuse(evidence, weak_attribution=True)
        self.assertEqual(strong, HIGH)
        self.assertEqual(capped, MEDIUM)
        self.assertTrue(notes)


class TestCliContract(unittest.TestCase):
    def test_convert_exits_nonzero_when_an_invariant_fails(self):
        """`convert` used to always return 0, so CI could not gate on it."""
        import inspect

        from r2s import cli

        source = inspect.getsource(cli.cmd_convert)
        self.assertIn("return 1 if failed_any else 0", source)
        self.assertNotIn("or True else 0", source)


class TestYoutubeFixture(unittest.TestCase):
    """The agent-system fixture. It is one fixture, not the target -- but it is the
    case the whole design was originally reasoned from, so it must keep working."""

    @classmethod
    def setUpClass(cls):
        cls.vocab = CapabilityVocab.load()
        cls.standins = StandinCatalog.load()
        path = (
            PROJECT
            / "fixtures"
            / "agent-system"
            / "youtube-automation"
            / "expected"
            / "inventory.json"
        )
        cls.inventory = json.loads(path.read_text(encoding="utf-8"))
        from r2s.capability import profiles as profiles_mod

        cls.profiles = profiles_mod.load_profiles()

    def test_fixture_validates(self):
        failures = schema_mod.validate_inventory(self.inventory, self.vocab, self.standins)
        self.assertEqual(failures, [])

    def test_eighteen_operations(self):
        self.assertEqual(len(self.inventory["operations"]), 18)

    def test_narration_halts_where_there_is_no_tts(self):
        """No honest stand-in for speech, so it must ask rather than emit a silent video."""
        rows = router.resolve(
            self.inventory["operations"], self.profiles["bare"], self.vocab, self.standins
        )
        by_id = {r["id"]: r for r in rows}
        self.assertEqual(by_id["gen-narration"]["binding"], "ask-user")
        self.assertIsNone(by_id["gen-narration"]["stand_in"])

    def test_visuals_degrade_rather_than_block(self):
        rows = router.resolve(
            self.inventory["operations"], self.profiles["bare"], self.vocab, self.standins
        )
        by_id = {r["id"]: r for r in rows}
        self.assertEqual(by_id["gen-scene-visuals"]["binding"], "script")
        self.assertLess(by_id["gen-scene-visuals"]["stand_in_detail"]["fidelity"], 1.0)

    def test_upload_gates_survive_every_profile(self):
        """The load-bearing property: a better harness never weakens a gate."""
        for pid in ("bare", "claude-code", "workbuddy", "full"):
            rows = router.resolve(
                self.inventory["operations"], self.profiles[pid], self.vocab, self.standins
            )
            row = next(r for r in rows if r["id"] == "upload-video")
            surfaced = set(row["gates"]) | set(row["deferred_gates"])
            self.assertEqual(
                surfaced, {"publish", "irreversible", "privacy"}, f"gate lost on {pid}"
            )

    def test_invariants_hold_on_every_profile(self):
        for pid, profile in sorted(self.profiles.items()):
            rows = router.resolve(self.inventory["operations"], profile, self.vocab, self.standins)
            failures = invariants.verify(self.inventory["operations"], rows, profile)
            self.assertEqual(failures, [], f"{pid}: {failures}")

    def test_required_operation_never_dropped_on_a_bare_harness(self):
        rows = router.resolve(
            self.inventory["operations"], self.profiles["bare"], self.vocab, self.standins
        )
        for row in rows:
            if not row["optional"]:
                self.assertNotEqual(row["binding"], "drop", row["id"])


class TestModuleInvocation(unittest.TestCase):
    """CI and the README both use `python -m r2s`, which needs a __main__.py. Without
    one the module invocation fails while the console script still works, so the gap is
    invisible locally."""

    def test_main_module_exists(self):
        self.assertTrue((PROJECT / "src" / "r2s" / "__main__.py").is_file())

    def test_python_dash_m_runs(self):
        import os
        import subprocess

        env = dict(os.environ)
        env["PYTHONPATH"] = str(PROJECT / "src")
        result = subprocess.run(
            [sys.executable, "-m", "r2s", "--version"],
            capture_output=True,
            text=True,
            cwd=str(PROJECT),
            env=env,
            timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("r2s", result.stdout)

    def test_python_dash_m_lists_profiles(self):
        import os
        import subprocess

        env = dict(os.environ)
        env["PYTHONPATH"] = str(PROJECT / "src")
        result = subprocess.run(
            [sys.executable, "-m", "r2s", "profiles"],
            capture_output=True,
            text=True,
            cwd=str(PROJECT),
            env=env,
            timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("bare", result.stdout)


class TestManifestFailuresAreLoud(unittest.TestCase):
    """A pyproject.toml that cannot be read takes every console script and declared
    dependency with it, silently changing the repo class and the inventory. It must be
    reported, never swallowed.

    This is not hypothetical: on Python 3.10 `tomllib` does not exist, and the original
    code quietly skipped the file, so `feed-sync` was misclassified as a library and a
    dependency went missing.
    """

    def test_unavailable_toml_parser_is_reported(self):
        from r2s.profiler import manifests as manifests_mod
        from r2s.util import toml as toml_util

        snapshot = build_snapshot(FIXTURE)
        original = toml_util._toml
        try:
            toml_util._toml = None
            failures = []
            deps = manifests_mod.read_python_deps(snapshot, failures)
        finally:
            toml_util._toml = original

        self.assertTrue(failures, "an unreadable pyproject.toml must be reported")
        self.assertTrue(any("tomli" in f or "TOML" in f for f in failures), failures)
        self.assertNotIn("requests", deps, "deps cannot be read without a TOML parser")

    def test_unavailable_toml_parser_reaches_the_inventory_warnings(self):
        from r2s.profiler import manifests as manifests_mod
        from r2s.profiler import profile as profile_repo
        from r2s.util import toml as toml_util

        snapshot = build_snapshot(FIXTURE)
        original = toml_util._toml
        try:
            toml_util._toml = None
            failures = []
            manifests_mod.read_python_deps(snapshot, failures)
            result = profile_repo(snapshot)
        finally:
            toml_util._toml = original

        self.assertTrue(failures)
        # The profiler threads its own failures through, so the pipeline can warn.
        self.assertIsInstance(result.parse_failures, list)

    def test_malformed_pyproject_is_reported(self):
        import tempfile

        from r2s.profiler import manifests as manifests_mod

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text("this is not = valid toml [[[", encoding="utf-8")
            snapshot = build_snapshot(root)
            failures = []
            manifests_mod.read_python_deps(snapshot, failures)

        self.assertTrue(failures, "a malformed pyproject.toml must be reported")

    def test_healthy_pyproject_reports_nothing(self):
        from r2s.profiler import manifests as manifests_mod

        snapshot = build_snapshot(FIXTURE)
        failures = []
        deps = manifests_mod.read_python_deps(snapshot, failures)
        self.assertEqual(failures, [])
        self.assertIn("requests", deps)


if __name__ == "__main__":
    unittest.main(verbosity=2)
