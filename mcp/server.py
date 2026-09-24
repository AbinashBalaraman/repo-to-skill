#!/usr/bin/env python3
"""MCP server for a converted repo's stand-in-backed operations (M8).

This is the server `r2s` emits for a repo whose operations need capabilities a bare
harness lacks. It reads a routed report (the JSON `r2s convert --json-out` produces) and
exposes one MCP tool per operation whose binding is `script` -- i.e. every operation the
router resolved to a curated stand-in. Native operations are already the harness's job;
`ask-user` operations are questions for a human, not tools.

What it does
------------
It **executes**. `tools/call` runs the operation's catalog stand-in through
`r2s.execute`, which invokes a script that ships with r2s (never repository code) with an
argv list and a JSON job on stdin, and returns the artifacts it produced.

Where a stand-in cannot be run it says so instead of faking it. Two cases are honest
outcomes rather than bugs:

  * The stand-in declares no `exec` block -- `reasoning-without-sources` is a model call,
    and r2s is stdlib-only and offline, so there is no deterministic substitute.
  * A declared prerequisite is missing -- no `ffmpeg` on PATH, say.

In both cases the tool returns `executed: false` with the reason, and the operation
should be routed to `ask-user`. A caller can tell the difference between "ran" and "could
not run"; that distinction is the point of the project.

Credentials
-----------
Scoped, never hardcoded, never logged. Values are read from the process environment by
the stand-in script and are never written into a result, a note or a log line. A missing
credential makes the call fail closed (`isError: true`) naming the variable, never its
value.

Protocol: MCP over stdio, newline-delimited JSON-RPC 2.0. Stdlib only.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

PROTOCOL_VERSION = "2025-06-18"
SERVER_NAME = "r2s-mcp"
SERVER_VERSION = "0.2.0"

DEFAULT_REPORT = Path(__file__).resolve().parent / "fixtures" / "sample-report.json"

# The single binding whose operations become tools. `native` is already the harness's
# job; `mcp` means some other server provides it; `ask-user` and `drop` are not tools.
EXPOSED_BINDING = "script"

# Imported lazily so the server still starts -- and still answers tools/list -- when it
# is run from a checkout without r2s installed. It just cannot execute in that case.
try:
    from r2s import execute as execute_mod
    from r2s.capability.vocab import StandinCatalog
except Exception:  # pragma: no cover - depends on how the server was launched
    execute_mod = None
    StandinCatalog = None


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


def _tool_description(row, runnable, reason):
    detail = row.get("stand_in_detail") or {}
    parts = [
        f"{row['name']} (stage {row['stage']}, effect {row['effect']}).",
        f"Requires {', '.join(row['requires'])}.",
        (
            f"Bound to stand-in '{row['stand_in']}' at declared fidelity "
            f"{detail.get('fidelity')}: {detail.get('desc', '')}"
        ),
        f"Caveat: {detail.get('caveat', '')}",
    ]
    if runnable:
        parts.append("Runs the stand-in and returns the artifacts it produces.")
    else:
        parts.append(f"NOT RUNNABLE here, so it reports the plan instead: {reason}")
    names = _credential_names(row)
    if names:
        parts.append("Reads credentials from environment variables: " + ", ".join(names) + ".")
    return " ".join(p for p in parts if p)


def build_tools(report, standins):
    """Return (tool_defs, rows_by_name) for every stand-in-backed operation."""
    tool_defs = []
    rows_by_name = {}
    for row in report.get("rows", []):
        if row.get("binding") != EXPOSED_BINDING or not row.get("stand_in"):
            continue

        runnable = False
        reason = "r2s is not importable in this environment, so nothing can be run"
        if execute_mod is not None and standins is not None:
            try:
                entry = standins.get(row["stand_in"])
            except Exception:
                entry = None
            reason = execute_mod.unavailable_reason(entry) or ""
            spec = execute_mod.exec_spec(entry)
            if spec is not None and not reason:
                absent = execute_mod.missing_tools(spec)
                if absent:
                    reason = f"required tool(s) not on PATH: {', '.join(absent)}"
                else:
                    runnable = True

        tool_defs.append(
            {
                "name": row["id"],
                "description": _tool_description(row, runnable, reason),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "inputs": {
                            "type": "object",
                            "description": (
                                "Arguments the stand-in consumes. An undeclared key is "
                                "rejected rather than ignored, so a typo cannot produce "
                                "a plausible artifact from the wrong arguments."
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


def _run_dir(operation):
    root = Path(os.environ.get("R2S_RUN_DIR") or Path(tempfile.gettempdir()) / "r2s-mcp-runs")
    return root / operation


def call_tool(row, arguments, standins):
    """Run one tool call. Returns (payload, missing_credentials)."""
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
        "stand_in": {
            "id": row["stand_in"],
            "fidelity": detail.get("fidelity"),
            "does": detail.get("desc"),
            "caveat": detail.get("caveat"),
        },
        "credentials": {"required_env": required, "missing_env": missing},
        "inputs": inputs or {},
    }

    # Credentials first: a step that needs a secret it does not have must not run at all.
    if missing:
        payload.update(
            {
                "executed": False,
                "execution_note": (
                    "Not executed: required credentials are absent from the environment. "
                    "The step needs "
                    + ", ".join(missing)
                    + ". Provide them and call again, or route this operation to ask-user."
                ),
            }
        )
        return payload, missing

    if execute_mod is None or standins is None:
        payload.update(
            {
                "executed": False,
                "execution_note": (
                    "r2s is not importable in this environment, so the stand-in could "
                    "not be run. Install repo-to-skill, or route this operation to "
                    "ask-user."
                ),
            }
        )
        return payload, []

    result = execute_mod.run(
        row["stand_in"],
        standins,
        inputs=inputs or {},
        workdir=_run_dir(row["id"]),
        operation=row["id"],
        optional=row.get("optional"),
        credentials=row.get("credentials"),
    )

    payload.update(
        {
            "executed": result.executed,
            "status": result.status,
            "reason": result.reason,
            "artifacts": result.artifacts,
            "notes": result.notes,
            "exit_code": result.exit_code,
            "duration_seconds": round(result.duration_seconds, 3),
        }
    )
    if result.executed:
        payload["execution_note"] = (
            "Executed. The artifacts listed were produced by the stand-in script; the "
            "declared fidelity and caveat above still apply to them."
        )
        if result.artifacts:
            payload["artifact_dir"] = str(_run_dir(row["id"]).resolve())
    else:
        payload["execution_note"] = (
            f"Not executed ({result.status}): {result.reason} "
            "Route this operation to ask-user rather than assuming it was handled."
        )
    return payload, []


def _text_result(payload, is_error):
    return {
        "content": [{"type": "text", "text": json.dumps(payload, indent=2, sort_keys=True)}],
        "isError": is_error,
    }


def _result(msg_id, result):
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _error(msg_id, code, message):
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def handle(message, tool_defs, rows_by_name, standins):
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
                    "Exposes the stand-in-backed operations of one converted repo. Each "
                    "tool runs its catalog stand-in and returns the artifacts produced. "
                    "Where a stand-in cannot be run -- it has no exec contract, or a "
                    "declared tool is missing -- the result says so with executed:false "
                    "and the operation should be routed to ask-user."
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
            payload, missing = call_tool(row, params.get("arguments") or {}, standins)
        except Exception as exc:  # never surface a traceback that could carry env state
            return _result(msg_id, _text_result({"error": str(exc)}, is_error=True))
        return _result(msg_id, _text_result(payload, is_error=bool(missing)))

    return _error(msg_id, -32601, f"method not found: {method}")


def serve(report_path, stdin=None, stdout=None):
    stdin = stdin if stdin is not None else sys.stdin
    stdout = stdout if stdout is not None else sys.stdout

    report = load_report(report_path)
    standins = StandinCatalog.load() if StandinCatalog is not None else None
    tool_defs, rows_by_name = build_tools(report, standins)

    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            _write(stdout, _error(None, -32700, "parse error"))
            continue
        response = handle(message, tool_defs, rows_by_name, standins)
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
