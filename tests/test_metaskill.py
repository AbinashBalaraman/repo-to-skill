"""M7 meta-skill and M8 MCP server tests. Stdlib unittest only.

Two artefacts are covered:

  * ``SKILL.md`` at the repository root -- the meta-skill that drives the installed
    ``r2s`` CLI. Tested against the Agent Skills constraints the README cites.
  * ``mcp/server.py`` -- the MCP server. Tested for the behaviour it promises: it
    exposes only stand-in-backed operations, it *executes* them where a stand-in is
    runnable, it reports ``executed: false`` with a concrete reason where one is not,
    and it never leaks a credential value.
"""

import importlib.util
import json
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
SKILL_MD = PROJECT / "SKILL.md"
MCP_DIR = PROJECT / "mcp"
SERVER_PY = MCP_DIR / "server.py"
SAMPLE_REPORT = MCP_DIR / "fixtures" / "sample-report.json"

# The server imports `r2s` to execute stand-ins. A checkout is not installed, so the
# tests put `src` on the path the same way the README's quickstart does.
SRC_DIR = PROJECT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

KEBAB = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def load_server_module():
    """Import mcp/server.py by path. It is a standalone script, not a packaged module."""
    spec = importlib.util.spec_from_file_location("r2s_mcp_server", SERVER_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_standins():
    from r2s.capability.vocab import StandinCatalog

    return StandinCatalog.load()


def parse_frontmatter(text):
    """Minimal YAML frontmatter reader: enough for scalar `key: value` pairs."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise AssertionError("SKILL.md must open with a '---' frontmatter fence")
    fields = {}
    for index in range(1, len(lines)):
        line = lines[index]
        if line.strip() == "---":
            return fields, lines[index + 1 :]
        if ":" in line and not line.startswith((" ", "\t")):
            key, _, value = line.partition(":")
            fields[key.strip()] = value.strip().strip("'\"")
    raise AssertionError("SKILL.md frontmatter fence is not closed")


class SkillMdTests(unittest.TestCase):
    def setUp(self):
        self.text = SKILL_MD.read_text(encoding="utf-8")
        self.fields, self.body = parse_frontmatter(self.text)

    def test_name_is_kebab_case_and_matches_its_folder(self):
        name = self.fields.get("name")
        self.assertTrue(name, "SKILL.md needs a name")
        self.assertRegex(name, KEBAB)
        self.assertEqual(name, PROJECT.name, "name must match the skill's folder")

    def test_description_is_present_and_within_spec(self):
        description = self.fields.get("description")
        self.assertTrue(description, "SKILL.md needs a description")
        self.assertGreaterEqual(len(description), 1)
        self.assertLessEqual(len(description), 1024)

    def test_description_is_third_person_and_says_when_to_use_it(self):
        description = self.fields["description"].lower()
        self.assertFalse(description.startswith(("i ", "you ", "we ")))
        self.assertIn("convert", description)
        self.assertTrue(
            "use " in description or "when" in description,
            "description should say when to use the skill",
        )

    def test_body_is_under_the_line_budget(self):
        self.assertLess(len(self.body), 500)

    def test_workflow_names_all_three_stages_and_the_mandatory_review(self):
        body = self.text
        self.assertIn("r2s extract", body)
        self.assertIn("r2s convert", body)
        self.assertIn("inventory.review.md", body)
        self.assertIn("inventory.json", body)
        self.assertIn("draft", body.lower())

    def test_is_honest_about_driving_a_cli_it_cannot_replace(self):
        lowered = self.text.lower()
        self.assertIn("cli", lowered)
        self.assertTrue(
            "cannot replace" in lowered or "does not replace" in lowered or "drives" in lowered,
            "the meta-skill must state it drives an installed CLI",
        )


class McpServerUnitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = load_server_module()
        cls.report = cls.server.load_report(SAMPLE_REPORT)
        cls.standins = load_standins()
        cls.tools, cls.rows = cls.server.build_tools(cls.report, cls.standins)

    def test_only_stand_in_backed_operations_become_tools(self):
        names = sorted(tool["name"] for tool in self.tools)
        self.assertEqual(names, ["caption-audio", "fetch-trends", "gen-scene-visuals"])
        for tool in self.tools:
            self.assertEqual(tool["name"], self.rows[tool["name"]]["id"])
            self.assertTrue(self.rows[tool["name"]]["stand_in"])

    def test_native_and_ask_user_operations_are_not_exposed(self):
        names = {tool["name"] for tool in self.tools}
        for hidden in ("assemble-video", "gen-narration", "upload-video"):
            self.assertNotIn(hidden, names)

    def test_a_stdlib_backed_operation_actually_executes(self):
        """The headline claim: tools/call runs the stand-in and returns real artifacts."""
        payload, missing = self.server.call_tool(
            self.rows["caption-audio"],
            {"inputs": {"text": "One sentence. A second, longer sentence follows here."}},
            self.standins,
        )
        self.assertEqual(missing, [])
        self.assertTrue(payload["executed"], payload.get("reason"))
        self.assertEqual(payload["status"], "ok")
        self.assertEqual([a["path"] for a in payload["artifacts"]], ["subtitles.srt"])
        self.assertEqual(payload["stand_in"]["id"], "srt-from-text")
        self.assertEqual(payload["stand_in"]["fidelity"], 0.6)

    def test_a_stand_in_without_an_exec_contract_reports_why_it_cannot_run(self):
        """`reasoning-without-sources` is a model call; r2s is offline and says so."""
        payload, missing = self.server.call_tool(
            self.rows["fetch-trends"], {"inputs": {"topic": "ai"}}, self.standins
        )
        # The credential is absent, so it fails closed before it ever gets to the stand-in.
        self.assertEqual(missing, ["TRENDS_API_KEY"])
        self.assertFalse(payload["executed"])
        self.assertIn("TRENDS_API_KEY", payload["execution_note"])

    def test_a_tool_dependent_stand_in_never_fakes_success(self):
        """`gradient-card-1080p` needs ffmpeg. Either it runs, or it says what went wrong.

        Three outcomes are legitimate and the test accepts all three, because which one
        occurs depends on the host: it runs; ffmpeg is absent (`unavailable`); or ffmpeg
        is present but cannot be executed (`failed`). What is never legitimate is
        reporting success with no artifact, or reporting success while an error was
        swallowed -- so those are asserted in every branch.
        """
        payload, _ = self.server.call_tool(
            self.rows["gen-scene-visuals"], {"inputs": {"title": "Test"}}, self.standins
        )
        if payload["executed"]:
            self.assertEqual(payload["status"], "ok")
            self.assertEqual([a["path"] for a in payload["artifacts"]], ["card.mp4"])
            self.assertEqual(payload["exit_code"], 0)
            return

        self.assertIn(payload["status"], ("unavailable", "failed"))
        self.assertEqual(payload["artifacts"], [], "a failed run must not report artifacts")
        self.assertTrue(payload["reason"], "a failed run must carry a reason")
        self.assertIn("ffmpeg", payload["reason"])
        # The note must route the caller somewhere rather than leaving them stuck.
        self.assertIn("ask-user", payload["execution_note"])

    def test_unknown_input_is_rejected_rather_than_ignored(self):
        payload, _ = self.server.call_tool(
            self.rows["caption-audio"], {"inputs": {"txt": "typo"}}, self.standins
        )
        self.assertFalse(payload["executed"])
        self.assertEqual(payload["status"], "failed")
        self.assertIn("unknown input", payload["reason"])


class McpServerProtocolTests(unittest.TestCase):
    """End-to-end over stdio, the way a harness actually drives it."""

    def run_server(self, messages, env=None):
        payload = "".join(json.dumps(m) + "\n" for m in messages)
        base = dict(os.environ)
        # A checkout is not installed; put `src` on the path so the server can import r2s
        # and therefore execute. This mirrors the documented quickstart.
        existing = base.get("PYTHONPATH")
        base["PYTHONPATH"] = f"{SRC_DIR}{os.pathsep}{existing}" if existing else str(SRC_DIR)
        if env:
            base.update(env)
        proc = subprocess.run(
            [sys.executable, str(SERVER_PY), str(SAMPLE_REPORT)],
            input=payload,
            capture_output=True,
            text=True,
            env=base,
            check=False,
        )
        responses = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
        return responses, proc

    def test_initialize_tools_list_and_call_round_trip(self):
        responses, proc = self.run_server(
            [
                {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {
                        "name": "caption-audio",
                        "arguments": {"inputs": {"text": "Hello there. This is a caption test."}},
                    },
                },
            ]
        )
        self.assertEqual(proc.returncode, 0)
        # The initialized notification must not be answered.
        self.assertEqual([r["id"] for r in responses], [1, 2, 3])

        self.assertEqual(responses[0]["result"]["serverInfo"]["name"], "r2s-mcp")
        listed = [t["name"] for t in responses[1]["result"]["tools"]]
        self.assertEqual(listed, ["caption-audio", "fetch-trends", "gen-scene-visuals"])

        result = responses[2]["result"]
        self.assertFalse(result["isError"])
        payload = json.loads(result["content"][0]["text"])
        # Executed for real over the wire, not merely described.
        self.assertTrue(payload["executed"], payload.get("reason"))
        self.assertEqual([a["path"] for a in payload["artifacts"]], ["subtitles.srt"])

    def test_credential_value_is_never_leaked(self):
        sentinel = "SUPER-SECRET-SENTINEL-VALUE"
        responses, proc = self.run_server(
            [
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": "fetch-trends", "arguments": {"inputs": {"topic": "ai"}}},
                }
            ],
            env={"TRENDS_API_KEY": sentinel},
        )
        self.assertNotIn(sentinel, proc.stdout)
        self.assertNotIn(sentinel, proc.stderr)
        payload = json.loads(responses[0]["result"]["content"][0]["text"])
        # Present, so the credential check does not block -- but only the name is emitted.
        self.assertEqual(payload["credentials"]["missing_env"], [])
        self.assertEqual(payload["credentials"]["required_env"], ["TRENDS_API_KEY"])

    def test_unknown_tool_and_method_return_jsonrpc_errors(self):
        responses, _ = self.run_server(
            [
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": "does-not-exist"},
                },
                {"jsonrpc": "2.0", "id": 2, "method": "not-a-method"},
            ]
        )
        self.assertEqual(responses[0]["error"]["code"], -32602)
        self.assertEqual(responses[1]["error"]["code"], -32601)


if __name__ == "__main__":
    unittest.main()
