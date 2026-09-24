"""Run a catalog stand-in for real.

A stand-in used to be a *description* -- "render the title on a gradient card via
FFmpeg" -- which meant the MCP server and the emitted skill could only report the
declared degradation, never perform it. This package closes that: a stand-in may now
declare an `exec` block naming a script that ships with r2s, and this module runs it.

What makes it safe to run against an untrusted repository
--------------------------------------------------------

  * **The script is ours, not theirs.** Scripts live in `data/stand-ins/scripts/` and are
    MIT-licensed with r2s. Repository code is never copied, never imported, never
    executed -- the licensing decision in the plan and the security boundary are the
    same line.
  * **argv only, never a shell.** `subprocess` is called with a list and no `shell=True`,
    so nothing in the inputs can become shell syntax.
  * **The job travels as JSON on stdin.** No argument templating, so there is no
    injection surface at all -- a title containing `; rm -rf /` is just a string.
  * **Fail closed.** A missing tool, a missing required input, an unknown input, a
    timeout or a non-zero exit is an error. The executor never synthesises a result and
    never writes a placeholder artifact and calls it success.
  * **Artifacts are contained.** Anything the script reports is checked to resolve
    inside the working directory, so a script cannot claim a path elsewhere on disk.
  * **Output is redacted and truncated.** stdout and stderr pass through `redact()` and
    are capped before they reach a report or a model.

A stand-in with no `exec` block is not an error -- it is a stand-in that cannot be run,
which is a fact about it. `run()` reports that distinctly (`status="unavailable"`) so the
caller can route the operation to ask-user rather than pretend it was handled.
"""

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

from .. import config
from ..util.io import redact

# A spec may ask for any timeout; this is the ceiling it cannot exceed.
HARD_TIMEOUT_CEILING = 900
DEFAULT_TIMEOUT = 120
_MAX_OUTPUT_CHARS = 4000

SCRIPT_DIR = config.STANDIN_DIR / "scripts"

STATUS_OK = "ok"
STATUS_UNAVAILABLE = "unavailable"
STATUS_FAILED = "failed"


class ExecError(Exception):
    """A stand-in could not be run, or ran and failed. Always carries a reason."""


class StandinResult:
    """The outcome of one stand-in run. Never a partial success dressed as a success."""

    def __init__(
        self,
        standin,
        status,
        executed=False,
        operation=None,
        reason=None,
        exit_code=None,
        artifacts=None,
        notes=None,
        stdout="",
        stderr="",
        duration_seconds=0.0,
        command=None,
    ):
        self.standin = standin
        self.status = status
        self.executed = executed
        self.operation = operation
        self.reason = reason
        self.exit_code = exit_code
        self.artifacts = list(artifacts or [])
        self.notes = list(notes or [])
        self.stdout = stdout
        self.stderr = stderr
        self.duration_seconds = duration_seconds
        self.command = list(command or [])

    @property
    def ok(self):
        return self.status == STATUS_OK

    def to_dict(self):
        return {
            "standin": self.standin,
            "status": self.status,
            "executed": self.executed,
            "operation": self.operation,
            "reason": self.reason,
            "exit_code": self.exit_code,
            "artifacts": list(self.artifacts),
            "notes": list(self.notes),
            "stdout": self.stdout,
            "stderr": self.stderr,
            "duration_seconds": round(self.duration_seconds, 3),
        }


def exec_spec(entry):
    """The normalised exec block for a catalog entry, or None if it cannot be run."""
    block = (entry or {}).get("exec")
    if not isinstance(block, dict):
        return None
    script = block.get("script")
    if not isinstance(script, str) or not script:
        return None
    runtime = block.get("runtime") or "python"
    if runtime != "python":
        # Only one runtime is supported, and saying so beats silently running nothing.
        return None
    return {
        "script": script,
        "runtime": runtime,
        "requires_tools": list(block.get("requires_tools") or []),
        "produces": list(block.get("produces") or []),
        "inputs": dict(block.get("inputs") or {}),
        "timeout_seconds": _timeout(block.get("timeout_seconds")),
    }


def _timeout(value):
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT
    if seconds <= 0:
        return DEFAULT_TIMEOUT
    return min(seconds, HARD_TIMEOUT_CEILING)


def script_path(script_name):
    """Resolve a declared script name inside the shipped script directory.

    Raises if the name escapes that directory. Catalog data is trusted input, but a
    containment check costs nothing and turns a hypothetical traversal into a loud
    failure instead of a silent one.
    """
    candidate = (SCRIPT_DIR / script_name).resolve()
    try:
        candidate.relative_to(SCRIPT_DIR.resolve())
    except ValueError as exc:
        raise ExecError(f"stand-in script escapes the script directory: {script_name!r}") from exc
    return candidate


def unavailable_reason(entry):
    """Why a stand-in cannot be run, as a sentence. None if it can be."""
    if exec_spec(entry) is not None:
        return None
    declared = (entry or {}).get("exec_unavailable")
    if isinstance(declared, str) and declared:
        return declared
    return "this stand-in declares no `exec` block, so it can only be reported, not run"


def missing_tools(spec):
    """Declared external tools that are not on PATH."""
    return [tool for tool in spec["requires_tools"] if shutil.which(tool) is None]


def _coerce(value, declared, name):
    """Validate and coerce one supplied input against its declaration."""
    kind = (declared or {}).get("type")
    if kind == "string":
        if not isinstance(value, str):
            raise ExecError(f"input {name!r} must be a string")
        return value
    if kind == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ExecError(f"input {name!r} must be a number")
        return float(value)
    if kind == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            raise ExecError(f"input {name!r} must be an integer")
        return value
    # An undeclared type is passed through unchanged rather than guessed at.
    return value


def resolve_inputs(spec, supplied):
    """Merge supplied inputs over the declared defaults. Raises on anything unknown.

    Rejecting unknown keys is deliberate: a misspelled input that is silently ignored
    produces a plausible artifact from the wrong arguments, which is the failure mode
    this project exists to prevent.
    """
    supplied = dict(supplied or {})
    declared = spec["inputs"]

    unknown = sorted(set(supplied) - set(declared))
    if unknown:
        raise ExecError(
            f"unknown input(s) {', '.join(repr(u) for u in unknown)}; "
            f"this stand-in accepts {', '.join(sorted(declared)) or 'no inputs'}"
        )

    resolved = {}
    missing = []
    for name in sorted(declared):
        declaration = declared[name] or {}
        if name in supplied:
            resolved[name] = _coerce(supplied[name], declaration, name)
        elif "default" in declaration:
            resolved[name] = declaration["default"]
        elif declaration.get("required"):
            missing.append(name)

    if missing:
        raise ExecError(
            f"missing required input(s): {', '.join(missing)}. "
            f"This stand-in cannot run without them, and will not guess."
        )
    return resolved


def _contained(workdir, relative):
    """A reported artifact path, if it stays inside the working directory."""
    try:
        resolved = (workdir / relative).resolve()
        resolved.relative_to(workdir.resolve())
    except (ValueError, OSError):
        return None
    return resolved


def run(
    standin_id,
    standins,
    inputs=None,
    workdir=None,
    operation=None,
    optional=None,
    credentials=None,
    timeout=None,
):
    """Run one stand-in. Returns a StandinResult; never raises for an expected failure.

    `standins` is the loaded StandinCatalog. An unknown stand-in id raises, because that
    is a caller bug rather than a capability gap.
    """
    entry = standins.get(standin_id)
    spec = exec_spec(entry)

    if spec is None:
        return StandinResult(
            standin_id,
            STATUS_UNAVAILABLE,
            executed=False,
            operation=operation,
            reason=unavailable_reason(entry),
        )

    path = script_path(spec["script"])
    if not path.is_file():
        return StandinResult(
            standin_id,
            STATUS_UNAVAILABLE,
            executed=False,
            operation=operation,
            reason=f"the declared script {spec['script']!r} is missing from this install",
        )

    absent = missing_tools(spec)
    if absent:
        return StandinResult(
            standin_id,
            STATUS_UNAVAILABLE,
            executed=False,
            operation=operation,
            reason=(
                f"required tool(s) not on PATH: {', '.join(absent)}. "
                f"Nothing was run and nothing was written."
            ),
        )

    try:
        resolved_inputs = resolve_inputs(spec, inputs)
    except ExecError as exc:
        return StandinResult(
            standin_id, STATUS_FAILED, operation=operation, reason=str(exc), executed=False
        )

    workdir = Path(workdir or ".")
    workdir.mkdir(parents=True, exist_ok=True)

    job = {
        "standin": standin_id,
        "operation": operation,
        "inputs": resolved_inputs,
        "out_dir": str(workdir.resolve()),
        "requires": list((entry or {}).get("requires") or []),
        "credentials": list(credentials or []),
    }
    if optional is not None:
        job["optional"] = bool(optional)

    limit = min(timeout, HARD_TIMEOUT_CEILING) if timeout else spec["timeout_seconds"]
    argv = [sys.executable, str(path)]
    started = time.monotonic()

    try:
        completed = subprocess.run(
            argv,
            input=json.dumps(job),
            capture_output=True,
            text=True,
            timeout=limit,
            cwd=str(workdir),
            check=False,
        )
    except subprocess.TimeoutExpired:
        return StandinResult(
            standin_id,
            STATUS_FAILED,
            operation=operation,
            reason=f"timed out after {limit}s and was killed",
            duration_seconds=time.monotonic() - started,
            command=argv,
        )
    except OSError as exc:
        return StandinResult(
            standin_id,
            STATUS_FAILED,
            operation=operation,
            reason=f"the stand-in script could not be executed: {exc}",
            duration_seconds=time.monotonic() - started,
            command=argv,
        )

    elapsed = time.monotonic() - started
    stdout = _clip(completed.stdout)
    stderr = _clip(completed.stderr)

    if completed.returncode != 0:
        return StandinResult(
            standin_id,
            STATUS_FAILED,
            operation=operation,
            reason=_error_reason(completed.stdout) or f"exited {completed.returncode}",
            exit_code=completed.returncode,
            stdout=stdout,
            stderr=stderr,
            duration_seconds=elapsed,
            command=argv,
        )

    try:
        payload = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError:
        return StandinResult(
            standin_id,
            STATUS_FAILED,
            operation=operation,
            reason="the stand-in produced output that is not valid JSON",
            exit_code=completed.returncode,
            stdout=stdout,
            stderr=stderr,
            duration_seconds=elapsed,
            command=argv,
        )

    artifacts, rejected = _collect_artifacts(payload, workdir)
    notes = [str(note) for note in (payload.get("notes") or [])]
    if rejected:
        notes.append(
            "the script reported artifact path(s) outside the working directory, which "
            f"were ignored: {', '.join(rejected)}"
        )

    return StandinResult(
        standin_id,
        STATUS_OK,
        executed=True,
        operation=operation,
        exit_code=completed.returncode,
        artifacts=artifacts,
        notes=notes,
        stdout=stdout,
        stderr=stderr,
        duration_seconds=elapsed,
        command=argv,
    )


def _collect_artifacts(payload, workdir):
    """Declared artifacts that exist and stay inside the workdir. Returns (kept, rejected)."""
    kept = []
    rejected = []
    for item in payload.get("artifacts") or []:
        if not isinstance(item, dict):
            continue
        relative = item.get("path")
        if not isinstance(relative, str) or not relative:
            continue
        resolved = _contained(workdir, relative)
        if resolved is None:
            rejected.append(relative)
            continue
        if not resolved.is_file():
            rejected.append(f"{relative} (reported but not written)")
            continue
        entry = {"path": relative, "kind": item.get("kind", "file")}
        if isinstance(item.get("cues"), int):
            entry["cues"] = item["cues"]
        kept.append(entry)
    return kept, rejected


def _error_reason(stdout):
    """The `error` field a failed script writes, if it wrote one."""
    try:
        payload = json.loads(stdout or "{}")
    except json.JSONDecodeError:
        return None
    message = payload.get("error") if isinstance(payload, dict) else None
    return redact(message) if isinstance(message, str) and message else None


def _clip(text):
    """Redact then truncate. A credential in a traceback must not reach the report."""
    if not text:
        return ""
    cleaned = redact(text)
    if len(cleaned) <= _MAX_OUTPUT_CHARS:
        return cleaned
    return (
        cleaned[:_MAX_OUTPUT_CHARS] + f"\n[... {len(cleaned) - _MAX_OUTPUT_CHARS} chars truncated]"
    )


def describe(standins):
    """Every catalog stand-in with its runnability, for docs and the `standins` command."""
    rows = []
    for entry in sorted(standins.all(), key=lambda e: e["id"]):
        spec = exec_spec(entry)
        rows.append(
            {
                "id": entry["id"],
                "capability": entry.get("capability"),
                "type": entry.get("type"),
                "fidelity": entry.get("fidelity"),
                "runnable": spec is not None,
                "reason": unavailable_reason(entry),
                "script": spec["script"] if spec else None,
                "requires_tools": spec["requires_tools"] if spec else [],
                "tools_present": (not missing_tools(spec) if spec else False),
            }
        )
    return rows


__all__ = [
    "DEFAULT_TIMEOUT",
    "HARD_TIMEOUT_CEILING",
    "ExecError",
    "STATUS_FAILED",
    "STATUS_OK",
    "STATUS_UNAVAILABLE",
    "StandinResult",
    "describe",
    "exec_spec",
    "missing_tools",
    "resolve_inputs",
    "run",
    "script_path",
    "unavailable_reason",
]
