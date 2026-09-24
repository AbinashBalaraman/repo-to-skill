"""Tests for the Rust, Go and JVM declaration dialects.

These three languages are not effect-traced -- T1 is stdlib `ast` over Python -- so the
declaration is the operation and the dialect declares the capability directly. That makes
two properties load-bearing and worth asserting here: every capability a candidate
declares exists in the vocabulary (an unknown ID hard-fails), and the candidate order is
byte-stable across runs (the stability eval depends on it).
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from r2s import profiler as profiler_mod
from r2s.capability.vocab import CapabilityVocab, StandinCatalog
from r2s.extract import catalog as catalog_mod
from r2s.extract import effects as effects_mod
from r2s.extract import pipeline as pipeline_mod
from r2s.extract.dialects import registry
from r2s.extract.dialects.common import brace_body
from r2s.extract.dialects.go import GoDialect
from r2s.extract.dialects.jvm import JvmDialect
from r2s.extract.dialects.rust import RustDialect
from r2s.profiler import manifests as manifests_mod
from r2s.source import build_snapshot
from r2s.validate import schema as schema_mod

PROJECT = Path(__file__).resolve().parent.parent
FIXTURES = PROJECT / "fixtures"

RUST_FIXTURE = FIXTURES / "library" / "rust-audit-cli"
GO_FIXTURE = FIXTURES / "library" / "go-queue-runner"
JVM_FIXTURE = FIXTURES / "library" / "spring-orders-api"

RUST_OPS = {
    # clap derive
    "audit",
    "fetch",
    "report",
    "status",
    "audit-report",
    # entry points, one per binary in the workspace
    "audit-main",
    "admin-main",
    "main-report",
    # axum routes
    "get-health",
    "post-audit",
    "get-audit-status",
    "get-metrics",
    "service-metrics-endpoint",
    # Cargo.toml
    "member-audit",
    "member-admin",
    "bin-audit",
    "bin-audit-report",
}

GO_OPS = {
    # cobra
    "queue-runner",
    "fetch",
    "report",
    # entry points
    "main",
    "execute",
    "serve",
    # net/http and the gin router
    "http-healthz",
    "http-readyz",
    "get-messages",
    "post-messages",
    "delete-messages-id",
}

JVM_OPS = {
    # Spring controller, mounted under the class-level @RequestMapping
    "get-api-orders",
    "get-api-orders-id",
    "post-api-orders",
    "delete-api-orders-id",
    # listeners and schedule
    "kafka-on-order",
    "scheduled-reconcile",
    "event-on-ready",
    # entry point
    "main",
}


def _extract(fixture):
    snapshot = build_snapshot(fixture)
    result = profiler_mod.profile(snapshot)
    vocab = CapabilityVocab.load()
    draft, _ = pipeline_mod.extract(
        snapshot,
        result,
        vocab,
        catalog_mod.ProviderCatalog.load(),
        effects_mod.EffectCatalog.load(),
        StandinCatalog.load(),
        source={"kind": "local"},
    )
    return snapshot, result, draft


def _candidates(dialect, fixture):
    snapshot = build_snapshot(fixture)
    result = profiler_mod.profile(snapshot)
    return dialect.match(snapshot, result)


def _snapshot(files):
    """A temp repo of `{relative path: text}` and a snapshot of it."""
    tmp = tempfile.TemporaryDirectory(prefix="r2s-dialect-test-")
    root = Path(tmp.name)
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return tmp, build_snapshot(root)


class DialectFixtureCase:
    """Shared assertions. A plain mixin: unittest would otherwise run it with no fixture."""

    dialect = None
    fixture = None
    expected = frozenset()
    others = ()

    def setUp(self):
        self.vocab = CapabilityVocab.load()

    def test_dialect_fires_on_its_fixture(self):
        candidates = _candidates(self.dialect, self.fixture)
        self.assertTrue(candidates, f"{self.dialect.id} produced nothing on its fixture")
        self.assertGreater(len(candidates), 1, "a fixture must yield more than one operation")

    def test_expected_operation_ids(self):
        ids = {candidate.op_id for candidate in _candidates(self.dialect, self.fixture)}
        self.assertEqual(ids, set(self.expected))

    def test_only_known_capability_ids(self):
        for candidate in _candidates(self.dialect, self.fixture):
            unknown = sorted(set(candidate.capabilities) - self.vocab.ids)
            self.assertEqual(
                unknown,
                [],
                f"{candidate.op_id} declares capability ids outside capabilities.json: {unknown}",
            )

    def test_declared_capabilities_are_actually_declared(self):
        """The dialects that declare a capability must do so on more than a placeholder."""
        candidates = _candidates(self.dialect, self.fixture)
        declared = {c.op_id: c.capabilities for c in candidates if c.capabilities}
        self.assertTrue(declared, "no candidate declared a capability")

    def test_candidate_order_is_stable(self):
        first = [c.op_id for c in _candidates(self.dialect, self.fixture)]
        second = [c.op_id for c in _candidates(self.dialect, self.fixture)]
        self.assertEqual(first, second)
        # No duplicates: a duplicate op_id is a schema violation downstream.
        self.assertEqual(len(first), len(set(first)))

    def test_does_not_fire_on_the_other_fixtures(self):
        for other in self.others:
            with self.subTest(fixture=other.name):
                self.assertEqual(_candidates(self.dialect, other), [])

    def test_pipeline_inventory_is_valid(self):
        _, _, draft = _extract(self.fixture)
        self.assertGreater(len(draft["operations"]), 1)
        self.assertIn(self.dialect.id, draft["diagnostics"]["dialects_ran"])
        failures = schema_mod.validate_inventory(draft, self.vocab, StandinCatalog.load())
        self.assertEqual(failures, [])
        for operation in draft["operations"]:
            for capability in operation["requires"]:
                self.assertIn(capability, self.vocab.ids)


class TestRustDialect(DialectFixtureCase, unittest.TestCase):
    dialect = RustDialect()
    fixture = RUST_FIXTURE
    expected = RUST_OPS
    others = (GO_FIXTURE, JVM_FIXTURE)

    def test_clap_commands_declare_shell_exec(self):
        by_id = {c.op_id: c for c in _candidates(self.dialect, self.fixture)}
        for op_id in ("audit", "fetch", "report", "status"):
            self.assertEqual(by_id[op_id].capabilities, ["shell.exec"])

    def test_async_main_is_marked_async(self):
        by_id = {c.op_id: c for c in _candidates(self.dialect, self.fixture)}
        self.assertEqual(by_id["audit-main"].declaration_kind, "rust-async-main")
        self.assertEqual(by_id["main-report"].declaration_kind, "rust-main")

    def test_route_attributes_and_workspace_members(self):
        """The declaration shapes the fixture does not use still work, from source text."""
        source = "\n".join(
            [
                '#[get("/health")]',
                "async fn health() -> &'static str {",
                '    "ok"',
                "}",
                '#[post("/audit")]',
                "async fn start() {}",
            ]
        )
        tmp, snapshot = _snapshot({"src/api.rs": source})
        self.addCleanup(tmp.cleanup)
        ids = {c.op_id for c in self.dialect.match(snapshot, profiler_mod.profile(snapshot))}
        self.assertEqual(ids, {"get-health", "post-audit"})

        manifest = "\n".join(
            [
                "[workspace]",
                'members = ["crates/one", "crates/two"]',
            ]
        )
        tmp2, snapshot2 = _snapshot({"Cargo.toml": manifest})
        self.addCleanup(tmp2.cleanup)
        ids2 = {c.op_id for c in self.dialect.match(snapshot2, profiler_mod.profile(snapshot2))}
        self.assertEqual(ids2, {"member-one", "member-two"})


class TestGoDialect(DialectFixtureCase, unittest.TestCase):
    dialect = GoDialect()
    fixture = GO_FIXTURE
    expected = GO_OPS
    others = (RUST_FIXTURE, JVM_FIXTURE)

    def test_command_functions_declare_shell_exec(self):
        by_id = {c.op_id: c for c in _candidates(self.dialect, self.fixture)}
        for op_id in ("main", "execute", "serve", "fetch", "report"):
            self.assertEqual(by_id[op_id].capabilities, ["shell.exec"])

    def test_handlers_declare_no_capability(self):
        """Serving a route is not in the vocabulary, so it must not be invented."""
        by_id = {c.op_id: c for c in _candidates(self.dialect, self.fixture)}
        for op_id in ("get-messages", "http-healthz"):
            self.assertEqual(by_id[op_id].capabilities, [])

    def test_plain_library_functions_are_not_commands(self):
        source = "\n".join(
            [
                "package demo",
                "",
                "func Transform(in []byte) []byte {",
                "\treturn in",
                "}",
                "",
                "func Run() error {",
                "\treturn nil",
                "}",
            ]
        )
        tmp, snapshot = _snapshot({"demo.go": source})
        self.addCleanup(tmp.cleanup)
        ids = {c.op_id for c in self.dialect.match(snapshot, profiler_mod.profile(snapshot))}
        self.assertEqual(ids, {"run"})


class TestJvmDialect(DialectFixtureCase, unittest.TestCase):
    dialect = JvmDialect()
    fixture = JVM_FIXTURE
    expected = JVM_OPS
    others = (RUST_FIXTURE, GO_FIXTURE)

    def test_controller_mapping_prefixes_the_class_path(self):
        by_id = {c.op_id: c for c in _candidates(self.dialect, self.fixture)}
        self.assertEqual(by_id["get-api-orders"].declaration_kind, "spring-controller-route")
        self.assertIn("/api/orders", by_id["get-api-orders-id"].detail)

    def test_listeners_and_schedules_declare_their_capability(self):
        by_id = {c.op_id: c for c in _candidates(self.dialect, self.fixture)}
        self.assertEqual(by_id["kafka-on-order"].capabilities, ["queue.consume"])
        self.assertEqual(by_id["scheduled-reconcile"].capabilities, ["schedule.cron"])
        self.assertEqual(by_id["scheduled-reconcile"].trigger, "scheduled")

    def test_kotlin_is_recognised(self):
        source = "\n".join(
            [
                "package com.example",
                "",
                "import org.springframework.web.bind.annotation.GetMapping",
                "import org.springframework.web.bind.annotation.RequestMapping",
                "import org.springframework.web.bind.annotation.RestController",
                "",
                "@RestController",
                '@RequestMapping("/api/items")',
                "class ItemController {",
                '    @GetMapping("/{id}")',
                "    fun get(id: String): String = id",
                "}",
                "",
                "fun main(args: Array<String>) {",
                "    println(args.size)",
                "}",
            ]
        )
        tmp, snapshot = _snapshot({"src/Items.kt": source})
        self.addCleanup(tmp.cleanup)
        candidates = self.dialect.match(snapshot, profiler_mod.profile(snapshot))
        ids = {c.op_id for c in candidates}
        self.assertEqual(ids, {"get-api-items-id", "main"})
        by_id = {c.op_id: c for c in candidates}
        self.assertEqual(by_id["main"].declaration_kind, "kotlin-main")


class TestBraceBody(unittest.TestCase):
    """The brace scanner is the difference between a correct body range and a wrong one."""

    @staticmethod
    def _naive_end_line(text, offset):
        """A counter that ignores nothing -- what the scanner exists to replace."""
        depth = 0
        opened = False
        for index in range(offset, len(text)):
            if text[index] == "{":
                depth += 1
                opened = True
            elif text[index] == "}" and opened:
                depth -= 1
                if depth == 0:
                    return text.count("\n", 0, index) + 1
        return None

    def test_braces_inside_a_rust_format_string_do_not_unbalance_the_range(self):
        source = "\n".join(
            [
                "fn render(name: &str) -> String {",
                '    let template = format!("{{}}");',
                '    let raw = r#"{"#;',
                "    let brace = '{';",
                '    format!("{template} {raw} {brace} {name}")',
                "}",
            ]
        )
        self.assertEqual(brace_body(source, 0, raw_hashes=True, lifetimes=True), (1, 6))
        # The naive counter never returns to zero and runs off the end of the file.
        self.assertIsNone(self._naive_end_line(source, 0))

    def test_lifetimes_are_not_read_as_char_literals(self):
        source = "fn pick<'a>(x: &'a str) -> &'a str {\n    x\n}"
        self.assertEqual(brace_body(source, 0, raw_hashes=True, lifetimes=True), (1, 3))
        # Without the lifetime rule `'a` opens a char literal that never closes, and the
        # scan runs off the end of the file instead of bounding the function.
        self.assertIsNone(brace_body(source, 0, raw_hashes=True))

    def test_nested_block_comments_and_unicode_escapes(self):
        source = "\n".join(
            [
                "fn escape() {",
                "    /* outer /* inner } */ still a comment */",
                "    let quiet = '\\u{7F}';",
                "    let loud = '}';",
                "}",
            ]
        )
        self.assertEqual(brace_body(source, 0, raw_hashes=True, lifetimes=True), (1, 5))

    def test_go_backtick_raw_strings_and_comments(self):
        source = "\n".join(
            [
                "func handler(w http.ResponseWriter) {",
                "\tmsg := `{`",
                '\tfmt.Fprintf(w, "%s", msg)',
                "}",
            ]
        )
        self.assertEqual(brace_body(source, 0, raw_ticks=True), (1, 4))
        self.assertIsNone(self._naive_end_line(source, 0))

    def test_java_text_blocks_and_kotlin_raw_strings(self):
        source = "\n".join(
            [
                "public void run() {",
                '    String s = "}";',
                "    /* { */",
                '    String t = """{ }""";',
                "}",
            ]
        )
        self.assertEqual(brace_body(source, 0, triple_quoted=True), (1, 5))

    def test_unclosed_block_returns_none(self):
        self.assertIsNone(brace_body("fn broken() {\n    let x = 1;\n", 0))


class TestRegistry(unittest.TestCase):
    def test_the_three_dialects_are_registered(self):
        ids = {dialect.id for dialect in registry.all_dialects()}
        self.assertLessEqual({"rust", "go", "jvm"}, ids)

    def test_registry_order_is_by_id(self):
        ids = [dialect.id for dialect in registry.all_dialects()]
        self.assertEqual(ids, sorted(ids))


class TestDependencyReaders(unittest.TestCase):
    def setUp(self):
        self.failures = []

    def test_cargo_dependencies(self):
        deps = manifests_mod.read_cargo_deps(build_snapshot(RUST_FIXTURE), self.failures)
        self.assertEqual(self.failures, [])
        # Declared in the member crate, including a workspace-inherited dependency.
        self.assertLessEqual({"clap", "tokio", "redis", "serde", "serde_json"}, deps)
        # Declared only under [workspace.dependencies] in the root manifest.
        self.assertIn("tracing", deps)
        # dev-dependencies count too: the crate still imports them.
        self.assertIn("criterion", deps)

    def test_go_dependencies_are_module_paths(self):
        deps = manifests_mod.read_go_deps(build_snapshot(GO_FIXTURE), self.failures)
        self.assertEqual(self.failures, [])
        # The major-version suffix is part of the module path, so it is kept.
        self.assertIn("github.com/redis/go-redis/v9", deps)
        # The single-line `require` form is read as well as the block.
        self.assertIn("github.com/go-resty/resty/v2", deps)
        self.assertIn("github.com/spf13/cobra", deps)

    def test_jvm_dependencies_from_a_pom(self):
        deps = manifests_mod.read_jvm_deps(build_snapshot(JVM_FIXTURE), self.failures)
        self.assertEqual(self.failures, [])
        self.assertIn("org.postgresql:postgresql", deps["maven"])
        self.assertIn("com.stripe:stripe-java", deps["maven"])
        # The project's own artifactId is not under <dependencies>, so it is not a dep.
        self.assertNotIn("com.example:orders-api", deps["maven"])
        self.assertEqual(deps["gradle"], set())

    def test_jvm_dependencies_from_gradle(self):
        gradle = "\n".join(
            [
                "dependencies {",
                '    implementation "org.postgresql:postgresql:42.7.1"',
                "    api 'io.lettuce:lettuce-core:6.3.2'",
                '    testImplementation("junit:junit:4.13.2")',
                '    implementation("com.squareup.okhttp3:okhttp")',
                "}",
            ]
        )
        tmp, snapshot = _snapshot({"build.gradle": gradle})
        self.addCleanup(tmp.cleanup)
        deps = manifests_mod.read_jvm_deps(snapshot, self.failures)
        self.assertEqual(self.failures, [])
        self.assertIn("org.postgresql:postgresql", deps["gradle"])
        self.assertIn("io.lettuce:lettuce-core", deps["gradle"])
        self.assertIn("com.squareup.okhttp3:okhttp", deps["gradle"])
        self.assertEqual(deps["maven"], set())

    def test_malformed_cargo_toml_is_reported(self):
        tmp, snapshot = _snapshot({"Cargo.toml": "[dependencies\nclap = "})
        self.addCleanup(tmp.cleanup)
        deps = manifests_mod.read_cargo_deps(snapshot, self.failures)
        self.assertEqual(deps, set())
        self.assertEqual(len(self.failures), 1)
        self.assertIn("Cargo.toml", self.failures[0])

    def test_malformed_pom_is_reported(self):
        tmp, snapshot = _snapshot({"pom.xml": "<project><dependencies></project>"})
        self.addCleanup(tmp.cleanup)
        deps = manifests_mod.read_jvm_deps(snapshot, self.failures)
        self.assertEqual(deps["maven"], set())
        self.assertEqual(len(self.failures), 1)
        self.assertIn("pom.xml", self.failures[0])

    def test_go_mod_without_a_module_directive_is_reported(self):
        tmp, snapshot = _snapshot({"go.mod": "go 1.22\n\nrequire github.com/x/y v1.0.0\n"})
        self.addCleanup(tmp.cleanup)
        deps = manifests_mod.read_go_deps(snapshot, self.failures)
        self.assertEqual(deps, set())
        self.assertEqual(len(self.failures), 1)
        self.assertIn("module", self.failures[0])

    def test_unclosed_go_require_block_is_reported(self):
        go_mod = "\n".join(
            [
                "module example.com/demo",
                "",
                "require (",
                "\tgithub.com/redis/go-redis/v9 v9.5.1",
            ]
        )
        tmp, snapshot = _snapshot({"go.mod": go_mod})
        self.addCleanup(tmp.cleanup)
        deps = manifests_mod.read_go_deps(snapshot, self.failures)
        self.assertIn("github.com/redis/go-redis/v9", deps)
        self.assertEqual(len(self.failures), 1)
        self.assertIn("never closed", self.failures[0])

    def test_parse_failures_reach_the_profile(self):
        tmp, snapshot = _snapshot({"Cargo.toml": "[[bin]\nname = "})
        self.addCleanup(tmp.cleanup)
        result = profiler_mod.profile(snapshot)
        self.assertTrue(result.parse_failures)
        self.assertTrue(any("Cargo.toml" in failure for failure in result.parse_failures))

    def test_ecosystems_are_wired_into_detect(self):
        for fixture, ecosystem in (
            (RUST_FIXTURE, "cargo"),
            (GO_FIXTURE, "go"),
            (JVM_FIXTURE, "maven"),
        ):
            with self.subTest(ecosystem=ecosystem):
                result = profiler_mod.profile(build_snapshot(fixture))
                self.assertIn(ecosystem, result.manifests["ecosystems"])
                self.assertTrue(result.manifests["ecosystems"][ecosystem])


class TestCatalogCoverage(unittest.TestCase):
    """The catalog must actually match what the new readers produce."""

    def test_new_ecosystem_dependencies_match_providers(self):
        catalog = catalog_mod.ProviderCatalog.load()
        probes = {
            ("cargo", "reqwest"): "requests",
            ("cargo", "aws-sdk-s3"): "s3",
            ("cargo", "tokio-postgres"): "postgres",
            ("go", "github.com/redis/go-redis/v9"): "redis",
            ("go", "github.com/segmentio/kafka-go"): "kafka",
            ("go", "github.com/stripe/stripe-go"): "stripe",
            ("maven", "org.postgresql:postgresql"): "postgres",
            ("maven", "com.stripe:stripe-java"): "stripe",
            ("maven", "org.springframework.kafka:spring-kafka"): "kafka",
            ("gradle", "io.lettuce:lettuce-core"): "redis",
        }
        for (ecosystem, name), expected in probes.items():
            with self.subTest(ecosystem=ecosystem, name=name):
                hit = catalog.lookup(ecosystem, name)
                self.assertIsNotNone(hit)
                self.assertEqual(hit["id"], expected)

    def test_existing_pypi_and_npm_keys_are_unchanged(self):
        catalog = catalog_mod.ProviderCatalog.load()
        self.assertEqual(catalog.lookup("npm", "@aws-sdk/client-s3")["id"], "boto3")
        self.assertEqual(catalog.lookup("pypi", "requests")["id"], "requests")

    def test_every_capability_in_the_catalog_is_known(self):
        vocab = CapabilityVocab.load()
        catalog = catalog_mod.ProviderCatalog.load()
        for provider in catalog.providers:
            for capability in provider.get("capabilities", []):
                self.assertIn(capability, vocab.ids, f"{provider['id']} declares {capability}")


if __name__ == "__main__":
    unittest.main()
