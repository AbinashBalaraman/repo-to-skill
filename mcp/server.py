#!/usr/bin/env python3
"""Minimal MCP server for a converted repo's stand-in-backed operations (M8 spike).

This is the server `r2s` would emit for a repo whose operations need capabilities a
bare harness lacks. It is a *spike*, not a pipeline stage: it proves the shape of the
idea and documents its limits honestly.

What it does
------------
Reads a routed report (the JSON that `r2s convert --json-out` produces) and exposes
one MCP tool per operation whose binding is `script`, i.e. every operation the router
resolved to a curated stand-in. Native and ask-user operations are deliberately NOT
exposed: a native operation is already performed by the harness, and an ask-user
operation is a question for a human, not a tool.

What it does NOT do
-------------------
It does not execute anything. A stand-in in the catalog is a curated *description*
with a declared fidelity, not an executable script -- the scripts come from the
emitter (M6), which does not exist yet. So a tool call returns the declared plan:
the binding, the stand-in, its fidelity and caveat, the required capability and the
credential *names*. It reports ``"executed": false`` and refuses to pretend otherwise.

Credentials
-----------
Scoped, never hardcoded, never logged. The server only ever reads credential values
from the process environment, and it only ever emits credential *names*. A missing
credential makes the call fail closed (``isError: true``) with the name of the
missing environment variable, never its value.

Protocol: MCP over stdio, newline-delimited JSON-RPC 2.0. Stdlib only.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

PROTOCOL_VERSION = "2025-06-18"
SERVER_NAME = "r2s-mcp-spike"
SERVER_VERSION = "0.1.0"

DEFAULT_REPORT = Path(__file__).resolve().parent / "fixtures" / "sample-report.json"

# The single binding whose operations become tools. `native` is already the harness's
# job; `mcp` means some other server already provides it; `ask-user` and `drop` are
# not tools at all.
EXPOSED_BINDING = "script"


def load_report(path):
    """Load a routed report. Raises on anything that is not valid JSON."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _credential_names(row):
    """Credential environment-variable names for an operation, never values."""
    names = []
    for cred in row.get("credentials") or []:
        name = cred.get("env") or cred.get("name")
        if name:
            names.append(name)
    return names


def _tool_description(row):
    detail = row.get("stand_in_detail") or {}
    parts = [
        f"{row['name']} (stage {row['stage']}, effect {row['effect']}).",
        f"Requires {', '.join(row['requires'])}.",
        (
            f"Bound to stand-in '{row['stand_in']}' at declared fidelity "
            f"{detail.get('fidelity')}: {detail.get('desc', '')}"
        ),
        f"Caveat: {detail.get('caveat', '')}",
        "This spike reports the declared degradation; it does not execute the step.",
    ]
    names = _credential_names(row)
    if names:
        parts.append("Reads credentials from environment variables: " + ", ".join(names) + ".")
    return " ".join(p for p in parts if p)


def build_tools(report):
    """Return (tool_defs, rows_by_name) for every stand-in-backed operation."""
    tool_defs = []
    rows_by_name = {}
    for row in report.get("rows", []):
        if row.get("binding") != EXPOSED_BINDING or not row.get("stand_in"):
            continue
        tool_defs.append(
            {
                "name": row["id"],
                "description": _tool_description(row),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "inputs": {
                            "type": "object",
                            "description": (
                                "Arguments the underlying step would consume. Recorded in "
                                "the returned plan; not executed by this spike."
                            ),
                        }
                    },
                    "additionalProperties": False,
                },
            }
        )
        rows_by_name[row["id"]] = row
    tool_defs.sort(key=lambda tool: tool["name"])
    return tool_defs, rows_by_name


def call_tool(row, arguments):
    """Build the honest result for one tool call. Returns (payload, missing_creds)."""
    detail = row.get("stand_in_detail") or {}
    required = _credential_names(row)
    missing = [name for name in required if not os.environ.get(name)]
    inputs = arguments.get("inputs") if isinstance(arguments, dict) else None
    payload = {
        "operation": row["id"],
        "binding": row["binding"],
        "stage": row["stage"],
        "effect": row["effect"],
        "requires": list(row["requires"]),
        "gates": list(row.get("gates") or []),
        "deferred_gates": list(row.get("deferred_gates") or []),
        "executed": False,
        "execution_note": (
            "Not executed. A catalog stand-in is a declared description, not an "
            "executable script; the scripts come from the emitter, which does not "
            "exist in this build. This tool surfaces the routing decision so the "
            "harness can act on it instead of guessing."
        ),
        "stand_in": {
            "id": row["stand_in"],
            "fidelity": detail.get("fidelity"),
            "does": detail.get("desc"),
            "caveat": detail.get("caveat"),
        },
        "credentials": {
            "required_env": required,
            "missing_env": missing,
        },
        "inputs": inputs or {},
    }
    return payload, missing


def _text_result(payload, is_error):
    return {
        "content": [{"type": "text", "text": json.dumps(payload, indent=2, sort_keys=True)}],
        "isError": is_error,
    }


def _result(msg_id, result):
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _error(msg_id, code, message):
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def handle(message, tool_defs, rows_by_name):
    """Dispatch one JSON-RPC message. Returns a response dict, or None for a
    notification (which by protocol must not be answered)."""
    if not isinstance(message, dict):
        return _error(None, -32600, "invalid request")

    method = message.get("method")
    msg_id = message.get("id")

    if method == "initialize":
        return _result(
            msg_id,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                "instructions": (
                    "Exposes the stand-in-backed operations of one converted repo. "
                    "Each tool reports a declared degradation plan; none of them execute."
                ),
            },
        )

    if method in ("notifications/initialized", "initialized"):
        return None

    if method == "ping":
        return _result(msg_id, {})

    if method == "tools/list":
        return _result(msg_id, {"tools": tool_defs})

    if method == "tools/call":
        params = message.get("params") or {}
        name = params.get("name")
        row = rows_by_name.get(name)
        if row is None:
            return _error(msg_id, -32602, f"unknown tool: {name}")
        try:
            payload, missing = call_tool(row, params.get("arguments") or {})
        except Exception as exc:  # never surface a traceback that could carry env state
            return _result(msg_id, _text_result({"error": str(exc)}, is_error=True))
        return _result(msg_id, _text_result(payload, is_error=bool(missing)))

    return _error(msg_id, -32601, f"method not found: {method}")


def serve(report_path, stdin=None, stdout=None):
    stdin = stdin if stdin is not None else sys.stdin
    stdout = stdout if stdout is not None else sys.stdout

    report = load_report(report_path)
    tool_defs, rows_by_name = build_tools(report)

    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            _write(stdout, _error(None, -32700, "parse error"))
            continue
        response = handle(message, tool_defs, rows_by_name)
        if response is not None:
            _write(stdout, response)
    return 0


def _write(stdout, response):
    stdout.write(json.dumps(response) + "\n")
    stdout.flush()


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    report_path = argv[0] if argv else os.environ.get("R2S_REPORT", str(DEFAULT_REPORT))
    if not Path(report_path).exists():
        print(f"error: report not found: {report_path}", file=sys.stderr)
        return 2
    return serve(report_path)


if __name__ == "__main__":
    sys.exit(main())
