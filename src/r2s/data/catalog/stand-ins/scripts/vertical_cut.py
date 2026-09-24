#!/usr/bin/env python3
"""Stand-in `vertical-cut`: cut vertical clips at fixed intervals, no reframing.

Declared fidelity 0.45. A *reducing* stand-in for video editing: it produces real,
playable vertical clips, but the cut points are arithmetic rather than scene-detected,
and the frame is centre-cropped rather than reframed. Subjects will be cropped out. That
caveat is the whole reason the fidelity is below 0.5.

Prerequisites
-------------
`ffmpeg` is declared as `requires_tools` and is mandatory. An `input` video is required
and must exist. Either being absent is a fail-closed error: no clips are written and the
reason is reported.

Protocol: see `srt_from_text.py` for the shared stdin/stdout contract.

Stdlib only. argv list, never a shell.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

_FFMPEG_TIMEOUT = 300
_MAX_CLIPS = 40


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


def _probe_duration(ffprobe, source):
    """Duration in seconds, or None if it cannot be determined."""
    try:
        result = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(source),
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    try:
        return float(result.stdout.strip())
    except ValueError:
        return None


def main():
    job = _read_job()
    inputs = job.get("inputs") or {}

    raw_input = inputs.get("input")
    if not isinstance(raw_input, str) or not raw_input.strip():
        return _fail("input 'input' is required and must be a path to a video")
    source = Path(raw_input)
    if not source.is_file():
        return _fail(f"input video does not exist: {source}")

    try:
        scene_seconds = float(inputs.get("scene_seconds", 5.0))
        width = int(inputs.get("width", 1080))
        height = int(inputs.get("height", 1920))
    except (TypeError, ValueError):
        return _fail("'scene_seconds' must be a number and 'width'/'height' integers")

    if not (0.5 <= scene_seconds <= 600):
        return _fail("'scene_seconds' must be between 0.5 and 600")

    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        missing = "ffmpeg" if not ffmpeg else "ffprobe"
        return _fail(
            f"{missing} is not on PATH. This stand-in cannot cut clips without it. "
            f"Nothing was written; install ffmpeg or route this operation to ask-user."
        )

    total = _probe_duration(ffprobe, source)
    if not total or total <= 0:
        return _fail(f"could not determine the duration of {source.name}")

    clip_count = min(_MAX_CLIPS, max(1, int(total // scene_seconds) or 1))

    out_dir = Path(job.get("out_dir") or ".")
    out_dir.mkdir(parents=True, exist_ok=True)

    notes = []
    artifacts = []
    for index in range(clip_count):
        start = index * scene_seconds
        target = out_dir / f"clip-{index + 1:02d}.mp4"
        chain = (
            f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},format=yuv420p"
        )
        argv = [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            str(start),
            "-t",
            str(scene_seconds),
            "-i",
            str(source),
            "-vf",
            chain,
            "-c:v",
            "libx264",
            "-an",
            str(target),
        ]
        try:
            result = subprocess.run(
                argv, capture_output=True, text=True, timeout=_FFMPEG_TIMEOUT, check=False
            )
        except subprocess.TimeoutExpired:
            return _fail(f"ffmpeg exceeded {_FFMPEG_TIMEOUT}s cutting clip {index + 1}")
        except OSError as exc:
            return _fail(f"ffmpeg could not be executed: {exc}")

        if result.returncode != 0 or not target.is_file():
            tail = (result.stderr or "").strip().splitlines()[-3:]
            return _fail(
                f"ffmpeg failed cutting clip {index + 1}: "
                + (" | ".join(tail) or f"exit {result.returncode}")
            )
        artifacts.append({"path": target.name, "kind": "video"})

    if clip_count >= _MAX_CLIPS:
        notes.append(f"stopped at the {_MAX_CLIPS}-clip ceiling; longer input was truncated")

    notes += [
        f"{clip_count} clip(s) of {scene_seconds}s cut from a {total:.1f}s input",
        "cut points are arithmetic, not scene-detected; the frame is centre-cropped with "
        "no reframing, so subjects may be cropped out",
    ]
    _emit({"artifacts": artifacts, "notes": notes, "degraded": True})
    return 0


if __name__ == "__main__":
    sys.exit(main())
