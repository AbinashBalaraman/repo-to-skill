#!/usr/bin/env python3
"""Stand-in `gradient-card-1080p` / `gradient-card-720p`: render a title card.

Declared fidelity 0.25-0.30. This is a *reducing* stand-in for image/video generation:
it produces a real, playable video file, but the content is a coloured card with the
title on it. There is no imagery and no subject, so the output reads as a slideshow.

Prerequisites
-------------
`ffmpeg` is a hard requirement and is declared in the catalog as `requires_tools`. If it
is missing the script **fails closed**: it exits non-zero and reports the missing tool.
It never writes a placeholder file and reports success, because a downstream step cannot
tell a fabricated artifact from a real one.

A font is *best effort*. If none can be found the card is still rendered and the omission
is reported in `notes` -- the gradient is real, the text is not, and the caller is told.

Protocol: see `srt_from_text.py` for the shared stdin/stdout contract.

Stdlib only. Invokes ffmpeg with an argv list, never a shell, and never interpolates
caller-supplied text into a filter graph without escaping it.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

_FFMPEG_TIMEOUT = 120

_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "C:/Windows/Fonts/segoeui.ttf",
)


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


def _resolve_font(explicit):
    """An explicit font wins; otherwise the first candidate that exists."""
    if explicit:
        return explicit if Path(explicit).is_file() else None
    from_env = os.environ.get("R2S_FONT")
    if from_env and Path(from_env).is_file():
        return from_env
    for candidate in _FONT_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    return None


def _supports_gradients(ffmpeg):
    """True if this ffmpeg build has the `gradients` source filter."""
    try:
        result = subprocess.run(
            [ffmpeg, "-hide_banner", "-filters"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return " gradients " in f" {result.stdout} "


def _escape_filter_text(value):
    """Escape a string for use inside an ffmpeg drawtext `text=` value.

    Backslash, colon and percent are filter-graph syntax; a single quote would close the
    quoted value. Newlines cannot be represented, so they collapse to spaces.
    """
    out = value.replace("\\", "\\\\")
    out = out.replace(":", "\\:").replace("%", "\\%")
    out = out.replace("'", "\\'")
    return " ".join(out.split())


def main():
    job = _read_job()
    inputs = job.get("inputs") or {}

    title = inputs.get("title")
    if not isinstance(title, str) or not title.strip():
        return _fail("input 'title' is required and must be a non-empty string")

    try:
        duration = float(inputs.get("duration", 8.0))
        width = int(inputs.get("width", 1920))
        height = int(inputs.get("height", 1080))
    except (TypeError, ValueError):
        return _fail("'duration' must be a number and 'width'/'height' integers")

    if not (0.5 <= duration <= 600):
        return _fail("'duration' must be between 0.5 and 600 seconds")
    if not (16 <= width <= 7680) or not (16 <= height <= 4320):
        return _fail("'width' and 'height' must be between 16 and 7680/4320")

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return _fail(
            "ffmpeg is not on PATH. This stand-in cannot render a card without it. "
            "Nothing was written; install ffmpeg or route this operation to ask-user."
        )

    out_dir = Path(job.get("out_dir") or ".")
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / "card.mp4"

    notes = []
    if _supports_gradients(ffmpeg):
        source = f"gradients=s={width}x{height}:c0=0x1E1B4B:c1=0x0F766E:d={duration}"
    else:
        # Older builds: a solid colour is still an honest card, and the substitution is
        # reported rather than hidden.
        source = f"color=c=0x1E1B4B:s={width}x{height}:d={duration}"
        notes.append("this ffmpeg build has no `gradients` filter; used a solid colour")

    filters = [source]
    font = _resolve_font(inputs.get("font"))
    if font:
        size = max(18, int(height * 0.06))
        text = _escape_filter_text(title.strip())
        filters.append(
            f"drawtext=fontfile='{font}':text='{text}':fontcolor=white:fontsize={size}"
            f":x=(w-text_w)/2:y=(h-text_h)/2"
        )
    else:
        notes.append("no font found; the title text was NOT burned in (gradient only)")

    argv = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        ",".join(filters),
        "-t",
        str(duration),
        "-pix_fmt",
        "yuv420p",
        "-c:v",
        "libx264",
        str(target),
    ]

    try:
        result = subprocess.run(
            argv, capture_output=True, text=True, timeout=_FFMPEG_TIMEOUT, check=False
        )
    except subprocess.TimeoutExpired:
        return _fail(f"ffmpeg exceeded {_FFMPEG_TIMEOUT}s and was killed")
    except OSError as exc:
        # The binary exists but cannot be executed (permissions, wrong architecture).
        return _fail(f"ffmpeg could not be executed: {exc}")

    if result.returncode != 0 or not target.is_file():
        tail = (result.stderr or "").strip().splitlines()[-3:]
        return _fail("ffmpeg failed: " + (" | ".join(tail) or f"exit {result.returncode}"))

    notes.append(
        f"rendered a {width}x{height} card for {duration}s; this is a slideshow-grade "
        f"substitute, not generated footage"
    )
    _emit(
        {
            "artifacts": [{"path": target.name, "kind": "video"}],
            "notes": notes,
            "degraded": True,
        }
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
