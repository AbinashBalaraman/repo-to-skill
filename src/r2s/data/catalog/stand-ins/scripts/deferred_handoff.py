#!/usr/bin/env python3
"""Stand-in `deferred-stage-and-handoff`: write a handoff file and stop.

Declared fidelity 0.0 -- the honest floor. This stand-in does not attempt the work at
all. It packages everything a human needs to run the step elsewhere: the operation, the
capability that was missing, the inputs, and the credential names (never values).

The point is that the pipeline reports an incomplete step *loudly* instead of inventing
a plausible-looking artifact. A handoff file is a real artifact; a fabricated output
would be a lie.

Protocol: see `srt_from_text.py` for the shared stdin/stdout contract.

Stdlib only, no network, no shell. Deterministic.
"""

import json
import sys
from pathlib import Path


def _read_job():
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        _emit({"error": f"job on stdin is not valid JSON: {exc}"})
        raise SystemExit(2) from exc


def _emit(payload):
    sys.stdout.write(json.dumps(payload, sort_keys=True) + "\n")
    sys.stdout.flush()


def _fail(message):
    _emit({"error": message})
    return 1


def main():
    job = _read_job()
    inputs = job.get("inputs") or {}
    operation = job.get("operation") or "(unnamed operation)"
    standin = job.get("standin") or "deferred-stage-and-handoff"
    requires = list(job.get("requires") or [])
    credentials = list(job.get("credentials") or [])

    out_dir = Path(job.get("out_dir") or ".")
    out_dir.mkdir(parents=True, exist_ok=True)

    lines = [
        "# Handoff required",
        "",
        f"**Operation:** `{operation}`",
        f"**Stand-in:** `{standin}` (fidelity 0.0 — nothing was performed)",
        "",
        "This step was **not performed**. No substitute was available, so it was packaged",
        "for a human instead of being faked.",
        "",
        "## Why it could not run here",
        "",
        "The operation needs capabilities the target harness does not provide:",
        "",
    ]
    for capability in requires or ["(none recorded)"]:
        lines.append(f"- `{capability}`")
    lines += ["", "## Inputs to pass to the step", "", "```json"]
    lines.append(json.dumps(inputs, indent=2, sort_keys=True))
    lines += ["```", ""]

    if credentials:
        lines += [
            "## Credentials",
            "",
            "The step needs these environment variables. Values are never written here:",
            "",
        ]
        for credential in credentials:
            name = (
                credential.get("env") or credential.get("name")
                if isinstance(credential, dict)
                else credential
            )
            lines.append(f"- `{name}`")
        lines.append("")

    lines += [
        "## What to do",
        "",
        "1. Run the operation in an environment that has the capabilities above.",
        "2. Place its output in this directory.",
        "3. Re-run the pipeline so the step is no longer reported as outstanding.",
        "",
    ]

    (out_dir / "handoff.md").write_text("\n".join(lines), encoding="utf-8")
    (out_dir / "handoff.json").write_text(
        json.dumps(
            {
                "kind": "r2s.handoff",
                "operation": operation,
                "standin": standin,
                "performed": False,
                "requires": requires,
                "credential_names": [
                    (c.get("env") or c.get("name")) if isinstance(c, dict) else c
                    for c in credentials
                ],
                "inputs": inputs,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    _emit(
        {
            "artifacts": [
                {"path": "handoff.md", "kind": "markdown"},
                {"path": "handoff.json", "kind": "json"},
            ],
            "notes": [
                "the step was not performed; a handoff was written instead",
                "the pipeline does not complete until a human runs the step",
            ],
            "degraded": True,
        }
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
