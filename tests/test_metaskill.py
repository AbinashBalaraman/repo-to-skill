"""M7 meta-skill and M8 MCP spike tests. Stdlib unittest only.

Two artefacts are covered:

  * ``SKILL.md`` at the repository root -- the meta-skill that drives the installed
    ``r2s`` CLI. Tested against the Agent Skills constraints the README cites.
  * ``mcp/server.py`` -- the MCP spike. Tested for the honest behaviour it promises:
    it exposes only stand-in-backed operations, reports ``executed: false``, and never
    leaks a credential value.
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

KEBAB = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def load_server_module():
    """Import mcp/server.py by path. It is a spike script, not a packaged module."""
    spec = importlib.util.spec_from_file_location("r2s_mcp_spike_server", SERVER_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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

    def test_only_stand_in_backed_operations_become_tools(self):
        tools, rows = self.server.build_tools(self.report)
        names = sorted(tool["name"] for tool in tools)
        self.assertEqual(names, ["caption-audio", "fetch-trends", "gen-scene-visuals"])
        for tool in tools:
            self.assertEqual(tool["name"], rows[tool["name"]]["id"])
            self.assertTrue(rows[tool["name"]]["stand_in"])

    def test_native_and_ask_user_operations_are_not_exposed(self):
        tools, _ = self.server.build_tools(self.report)
        names = {tool["name"] for tool in tools}
        for hidden in ("assemble-video", "gen-narration", "upload-video"):
            self.assertNotIn(hidden, names)

    def test_call_reports_the_plan_without_executing(self):
        _, rows = self.server.build_tools(self.report)
        payload, missing = self.server.call_tool(rows["gen-scene-visuals"], {})
        self.assertFalse(payload["executed"])
        self.assertEqual(payload["stand_in"]["id"], "gradient-card-1080p")
        self.assertEqual(payload["stand_in"]["fidelity"], 0.25)
        self.assertEqual(payload["credentials"]["required_env"], [])
        self.assertEqual(missing, [])

    def test_missing_credentials_fail_closed_with_names_not_values(self):
        _, rows = self.server.build_tools(self.report)
        payload, missing = self.server.call_tool(rows["fetch-trends"], {})
        self.assertEqual(payload["credentials"]["required_env"], ["TRENDS_API_KEY"])
        self.assertEqual(missing, ["TRENDS_API_KEY"])


class McpServerProtocolTests(unittest.TestCase):
    """End-to-end over stdio, the way a harness actually drives it."""

    def run_server(self, messages, env=None):
        payload = "".join(json.dumps(m) + "\n" for m in messages)
        proc = subprocess.run(
            [sys.executable, str(SERVER_PY), str(SAMPLE_REPORT)],
            input=payload,
            capture_output=True,
            text=True,
            env=env,
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
                    "params": {"name": "gen-scene-visuals", "arguments": {}},
                },
            ]
        )
        self.assertEqual(proc.returncode, 0)
        # The initialized notification must not be answered.
        self.assertEqual([r["id"] for r in responses], [1, 2, 3])

        self.assertEqual(responses[0]["result"]["serverInfo"]["name"], "r2s-mcp-spike")
        listed = [t["name"] for t in responses[1]["result"]["tools"]]
        self.assertEqual(listed, ["caption-audio", "fetch-trends", "gen-scene-visuals"])

        result = responses[2]["result"]
        self.assertFalse(result["isError"])
        plan = json.loads(result["content"][0]["text"])
        self.assertFalse(plan["executed"])

    def test_credential_value_is_never_leaked(self):
        sentinel = "SUPER-SECRET-SENTINEL-VALUE"
        env = dict(os.environ)
        env["TRENDS_API_KEY"] = sentinel
        responses, proc = self.run_server(
            [
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": "fetch-trends", "arguments": {"inputs": {"topic": "ai"}}},
                }
            ],
            env=env,
        )
        self.assertNotIn(sentinel, proc.stdout)
        self.assertNotIn(sentinel, proc.stderr)
        plan = json.loads(responses[0]["result"]["content"][0]["text"])
        # Present, so the call is not blocked -- but only the name is ever emitted.
        self.assertEqual(plan["credentials"]["missing_env"], [])
        self.assertEqual(plan["credentials"]["required_env"], ["TRENDS_API_KEY"])

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
