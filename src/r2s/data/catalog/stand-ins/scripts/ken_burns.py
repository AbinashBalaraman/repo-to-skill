#!/usr/bin/env python3
"""Stand-in `ken-burns-still`: pan and zoom slowly across a single still image.

Declared fidelity 0.4. A *reducing* stand-in for video generation: the output is a real
video file with real apparent motion, produced from one still. Nothing is generated --
the motion is the camera, not the subject. Usually a better substitute than a static
card, and still not footage.

Prerequisites
-------------
`ffmpeg` is declared as `requires_tools` and is mandatory: without it the script fails
closed and writes nothing. An `image` input is required and must exist.

Protocol: see `srt_from_text.py` for the shared stdin/stdout contract.

Stdlib only. argv list, never a shell.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

_FFMPEG_TIMEOUT = 180
_FPS = 25


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

    image = inputs.get("image")
    if not isinstance(image, str) or not image.strip():
        return _fail("input 'image' is required and must be a path to a still image")
    source = Path(image)
    if not source.is_file():
        return _fail(f"input image does not exist: {source}")

    try:
        duration = float(inputs.get("duration", 8.0))
        width = int(inputs.get("width", 1920))
        height = int(inputs.get("height", 1080))
        zoom_to = float(inputs.get("zoom_to", 1.35))
    except (TypeError, ValueError):
        return _fail("'duration'/'zoom_to' must be numbers and 'width'/'height' integers")

    if not (0.5 <= duration <= 600):
        return _fail("'duration' must be between 0.5 and 600 seconds")
    if not (1.01 <= zoom_to <= 4.0):
        return _fail("'zoom_to' must be between 1.01 and 4.0")
    if not (16 <= width <= 7680) or not (16 <= height <= 4320):
        return _fail("'width' and 'height' must be between 16 and 7680/4320")

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return _fail(
            "ffmpeg is not on PATH. This stand-in cannot produce motion without it. "
            "Nothing was written; install ffmpeg or route this operation to ask-user."
        )

    out_dir = Path(job.get("out_dir") or ".")
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / "ken-burns.mp4"

    frames = max(1, int(duration * _FPS))
    # Linear zoom, centred. `zoompan` needs an explicit output size or it silently keeps
    # the input's, which would ignore the requested dimensions.
    step = (zoom_to - 1.0) / frames
    zoompan = (
        f"zoompan=z='min(zoom+{step:.6f},{zoom_to})'"
        f":d={frames}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
        f":s={width}x{height}:fps={_FPS}"
    )
    # Scale up first so the pan has pixels to move across without visible softness.
    chain = (
        f"scale={width * 2}:{height * 2}:force_original_aspect_ratio=increase,"
        f"crop={width * 2}:{height * 2},{zoompan},format=yuv420p"
    )

    argv = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-loop",
        "1",
        "-i",
        str(source),
        "-t",
        str(duration),
        "-vf",
        chain,
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
        return _fail(f"ffmpeg could not be executed: {exc}")

    if result.returncode != 0 or not target.is_file():
        tail = (result.stderr or "").strip().splitlines()[-3:]
        return _fail("ffmpeg failed: " + (" | ".join(tail) or f"exit {result.returncode}"))

    _emit(
        {
            "artifacts": [{"path": target.name, "kind": "video"}],
            "notes": [
                f"pan/zoom across '{source.name}' for {duration}s at {width}x{height}",
                "the apparent motion is the camera, not the subject; no content was generated",
            ],
            "degraded": True,
        }
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
