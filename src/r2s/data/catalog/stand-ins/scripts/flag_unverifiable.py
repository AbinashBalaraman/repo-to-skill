#!/usr/bin/env python3
"""Stand-in `flag-unverifiable-claims`: flag claims that look unverifiable.

Declared fidelity 0.4. This stand-in does not check anything -- checking a claim needs
live sources, which is the capability the operation asked for and did not get. What it
does instead is mark the claims a reader should not take on trust, so the gap is
visible in the artifact rather than passing silently.

Protocol: see `srt_from_text.py` for the shared stdin/stdout contract.

Stdlib only, no network, no shell. Deterministic.
"""

import json
import re
import sys
from pathlib import Path

# Shapes that usually need a citation to be trustworthy. Each carries the reason, so
# the report explains itself rather than just listing hits.
_MARKERS = (
    ("statistic", re.compile(r"\b\d+(?:\.\d+)?\s?(?:%|percent|per cent)\b", re.IGNORECASE)),
    ("quantity", re.compile(r"\b\d[\d,]{2,}\b")),
    ("date", re.compile(r"\b(?:19|20)\d{2}\b")),
    (
        "study",
        re.compile(
            r"\b(?:studies|research|scientists|experts)\s+(?:show|shows|say|says|found|suggest)",
            re.IGNORECASE,
        ),
    ),
    (
        "superlative",
        re.compile(
            r"\b(?:best|fastest|largest|biggest|most|worst|leading|world[- ]class)\b", re.IGNORECASE
        ),
    ),
    ("absolute", re.compile(r"\b(?:always|never|everyone|nobody|all|none)\b", re.IGNORECASE)),
    ("attribution", re.compile(r"\baccording to\b", re.IGNORECASE)),
)

_SENTENCE = re.compile(r"(?<=[.!?])\s+")


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


def _claims(text):
    """Every sentence carrying at least one verifiability marker, with the reasons."""
    findings = []
    for sentence in _SENTENCE.split(text):
        sentence = sentence.strip()
        if not sentence:
            continue
        reasons = sorted({name for name, pattern in _MARKERS if pattern.search(sentence)})
        if reasons:
            findings.append({"sentence": sentence, "reasons": reasons})
    return findings


def main():
    job = _read_job()
    inputs = job.get("inputs") or {}

    text = inputs.get("text")
    if not isinstance(text, str) or not text.strip():
        return _fail("input 'text' is required and must be a non-empty string")

    findings = _claims(text)
    out_dir = Path(job.get("out_dir") or ".")
    out_dir.mkdir(parents=True, exist_ok=True)

    report = {
        "kind": "r2s.unverifiable-claims",
        "checked": False,
        "note": (
            "No claim was verified. Live sources were not available, so this report "
            "flags what a reader should not take on trust rather than confirming it."
        ),
        "sentences_total": len([s for s in _SENTENCE.split(text) if s.strip()]),
        "flagged_total": len(findings),
        "findings": findings,
    }
    (out_dir / "unverifiable-claims.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    lines = [
        "# Unverifiable claims",
        "",
        f"{len(findings)} of {report['sentences_total']} sentence(s) need a citation.",
        "",
        report["note"],
        "",
    ]
    for finding in findings:
        lines.append(f"- **{', '.join(finding['reasons'])}** — {finding['sentence']}")
    (out_dir / "unverifiable-claims.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    _emit(
        {
            "artifacts": [
                {"path": "unverifiable-claims.json", "kind": "json"},
                {"path": "unverifiable-claims.md", "kind": "markdown"},
            ],
            "notes": [
                f"{len(findings)} claim(s) flagged, none verified",
                "detection is heuristic; a claim that looks fine may still be wrong",
            ],
            "degraded": True,
        }
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
