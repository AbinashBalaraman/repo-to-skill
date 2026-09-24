"""Source adapters: how a repository becomes a working directory."""

import re
import subprocess
from pathlib import Path

# Git transports we are willing to hand to the git binary. An allowlist rather than a
# denylist: a URL is user-supplied and goes straight to a subprocess argument list.
_ALLOWED_URL = re.compile(
    r"^(?:"
    r"https?://[^\s]+"
    r"|git://[^\s]+"
    r"|ssh://[^\s]+"
    r"|git@[A-Za-z0-9._\-]+:[^\s]+"
    r"|file:///[^\s]+"
    r")$"
)

_SHA = re.compile(r"^[0-9a-fA-F]{7,40}$")


class SourceError(Exception):
    pass


def validate_git_url(url):
    """Reject anything that could be read as a git option or a shell payload."""
    if not url or not isinstance(url, str):
        raise SourceError("empty git URL")
    if url.startswith("-"):
        raise SourceError(
            f"refusing a URL beginning with '-': it would be parsed as a git option ({url!r})"
        )
    if not _ALLOWED_URL.match(url):
        raise SourceError(
            f"unsupported or unsafe git URL: {url!r}. Expected an http(s), git, ssh or "
            f"file URL, or an scp-style git@host:path."
        )
    return url


def validate_sha(sha):
    if sha is None:
        return None
    if not _SHA.match(sha):
        raise SourceError(f"not a valid commit SHA: {sha!r}")
    return sha


def fetch_git(url, sha=None, dest=None):
    """Shallow-clone a repo, optionally checking out a pinned SHA.

    Pinning matters: an unpinned conversion is not reproducible, and the stability eval
    diffs emitted skills across two SHAs of the same source.
    """
    validate_git_url(url)
    validate_sha(sha)

    dest = Path(dest or Path.cwd() / "_r2s_src")
    if dest.exists() and any(dest.iterdir()):
        raise SourceError(f"destination already exists and is not empty: {dest}")

    cmd = ["git", "clone", "--depth", "1"]
    if sha:
        cmd += ["--no-single-branch"]
    # `--` terminates option parsing, so neither the URL nor the destination can be
    # interpreted as a flag even if validation above were bypassed.
    cmd += ["--", url, str(dest)]
    _run(cmd)

    if sha:
        _run(["git", "-C", str(dest), "fetch", "--depth", "1", "--", "origin", sha])
        _run(["git", "-C", str(dest), "checkout", "--detach", "--", sha])

    return dest


def resolve_local(path):
    path = Path(path).expanduser().resolve()
    if not path.exists():
        raise SourceError(f"path does not exist: {path}")
    if not path.is_dir():
        raise SourceError(f"not a directory: {path}")
    return path


def detect_git_meta(path):
    """Return (url, sha, license) for a local checkout, where discoverable."""
    url = sha = None
    try:
        url = (
            subprocess.run(
                ["git", "-C", str(path), "config", "--get", "remote.origin.url"],
                capture_output=True,
                text=True,
                timeout=10,
            ).stdout.strip()
            or None
        )
        sha = (
            subprocess.run(
                ["git", "-C", str(path), "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                timeout=10,
            ).stdout.strip()
            or None
        )
    except (OSError, subprocess.SubprocessError):
        pass
    return url, sha


def _run(cmd):
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except FileNotFoundError as exc:
        raise SourceError("git is not installed or not on PATH") from exc
    except subprocess.SubprocessError as exc:
        raise SourceError(f"command failed: {' '.join(cmd)}: {exc}") from exc
    if result.returncode != 0:
        raise SourceError(
            f"command failed ({result.returncode}): {' '.join(cmd)}\n{result.stderr.strip()}"
        )
    return result
