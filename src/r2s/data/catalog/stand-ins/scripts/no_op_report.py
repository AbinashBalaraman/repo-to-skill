#!/usr/bin/env python3
"""Stand-in `no-op-report`: skip the step and record the omission.

Declared fidelity 0.0. Legal **only** for optional operations -- a required operation
routed here is a bug, and the router refuses to do it (`capability/router.py`). This
script records the omission so it shows up in the output manifest rather than
disappearing.

The distinction that matters: `deferred-stage-and-handoff` says "a human must do this";
this one says "this was deliberately not done, and here is the record". Both are honest;
only one implies follow-up.

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
    standin = job.get("standin") or "no-op-report"

    if job.get("optional") is False:
        return _fail(
            "no-op-report was invoked for a required operation. This stand-in is legal "
            "only for optional operations; a required operation must route to ask-user "
            "instead."
        )

    out_dir = Path(job.get("out_dir") or ".")
    out_dir.mkdir(parents=True, exist_ok=True)

    record = {
        "kind": "r2s.omission",
        "operation": operation,
        "standin": standin,
        "performed": False,
        "optional": True,
        "reason": (
            "Optional operation with no available capability and no substitute. "
            "Skipped by design and recorded here so the omission is visible."
        ),
        "inputs": inputs,
    }
    (out_dir / "omitted.json").write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    _emit(
        {
            "artifacts": [{"path": "omitted.json", "kind": "json"}],
            "notes": [
                "optional operation skipped; omission recorded",
                "no follow-up is implied -- this step was deliberately not done",
            ],
            "degraded": True,
        }
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
