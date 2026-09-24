#!/usr/bin/env python3
"""Stand-in `srt-from-text`: derive an SRT from script text using estimated timing.

Declared fidelity 0.6. This is a *reducing* stand-in: it produces a real subtitle file
with real cue boundaries, but the timings are estimated from text length rather than
measured against audio, so captions drift. The caveat is reported in the result, not
buried.

Protocol (every stand-in script speaks this, so the executor stays generic):

    stdin   {"standin": id, "operation": id, "inputs": {...}, "out_dir": "..."}
    stdout  {"artifacts": [{"path": rel, "kind": str}], "notes": [...], "degraded": true}

Exit 0 on success. Exit non-zero with {"error": "..."} on failure -- never write a
partial artifact and call it success.

Stdlib only, no network, no shell. Deterministic: the same inputs produce the same
bytes, which is what lets the stability eval diff emitted artifacts.
"""

import json
import re
import sys
from pathlib import Path

DEFAULT_SECONDS_PER_CUE = 4.0
DEFAULT_MAX_CHARS = 84

# Sentence-ish boundaries. Deliberately conservative: a missed split produces a longer
# cue, which is far less annoying than splitting mid-abbreviation.
_SPLIT = re.compile(r"(?<=[.!?])\s+|\n{2,}")


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


def _wrap(text, limit):
    """Greedy wrap into lines of at most `limit` characters."""
    words = text.split()
    lines = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if current and len(candidate) > limit:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def _cues(text, seconds_per_cue, max_chars):
    """Split text into (start, end, lines) cues with estimated timing."""
    chunks = [c.strip() for c in _SPLIT.split(text) if c and c.strip()]
    cues = []
    cursor = 0.0
    for chunk in chunks:
        # Longer text earns proportionally longer on screen, floored at the base
        # duration so a two-word cue is not a flicker.
        span = max(seconds_per_cue, len(chunk) / 18.0)
        lines = _wrap(chunk, max_chars)
        cues.append((cursor, cursor + span, lines))
        cursor += span
    return cues


def _timestamp(seconds):
    total_ms = int(round(seconds * 1000))
    hours, rest = divmod(total_ms, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    secs, millis = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _render_srt(cues):
    blocks = []
    for index, (start, end, lines) in enumerate(cues, start=1):
        body = "\n".join(lines)
        blocks.append(f"{index}\n{_timestamp(start)} --> {_timestamp(end)}\n{body}\n")
    return "\n".join(blocks)


def main():
    job = _read_job()
    inputs = job.get("inputs") or {}

    text = inputs.get("text")
    if not isinstance(text, str) or not text.strip():
        return _fail("input 'text' is required and must be a non-empty string")

    try:
        seconds_per_cue = float(inputs.get("seconds_per_cue", DEFAULT_SECONDS_PER_CUE))
        max_chars = int(inputs.get("max_chars_per_line", DEFAULT_MAX_CHARS))
    except (TypeError, ValueError):
        return _fail("'seconds_per_cue' must be a number and 'max_chars_per_line' an integer")

    if seconds_per_cue <= 0:
        return _fail("'seconds_per_cue' must be greater than zero")
    if max_chars < 16:
        return _fail("'max_chars_per_line' must be at least 16")

    cues = _cues(text, seconds_per_cue, max_chars)
    if not cues:
        return _fail("no cues could be derived from the supplied text")

    out_dir = Path(job.get("out_dir") or ".")
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / "subtitles.srt"
    target.write_text(_render_srt(cues), encoding="utf-8")

    _emit(
        {
            "artifacts": [{"path": "subtitles.srt", "kind": "srt", "cues": len(cues)}],
            "notes": [
                f"{len(cues)} cue(s) derived from {len(text.split())} word(s)",
                "timings are estimated from text length, not measured against audio",
            ],
            "degraded": True,
        }
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
