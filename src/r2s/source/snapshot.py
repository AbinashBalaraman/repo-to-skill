"""A bounded, filtered view of a repository.

Extraction runs against a Snapshot rather than the raw tree so that a large repository
cannot hang the run, and so results are reproducible: the same snapshot yields the same
file set in the same order.

Three properties this module is responsible for, all of which are security-relevant:

  bounded     -- the walk is pruned and capped *as it goes*, not after materialising
                 the whole tree. `sorted(root.rglob("*"))` builds a list of every path
                 before a single cap is checked, which is useless against a repository
                 engineered to have millions of files.
  contained   -- a requested subdirectory must resolve inside the repository root.
  credential-free -- credential-bearing files are never read.
"""

import os
import time
from pathlib import Path

from .. import config


class Snapshot:
    def __init__(self, root, files, truncated=False, skipped_reason=None, skipped_secrets=0):
        self.root = Path(root).resolve()
        self.files = tuple(files)  # sorted, relative posix paths
        self.truncated = truncated
        self.skipped_reason = skipped_reason
        self.skipped_secrets = skipped_secrets

    def __len__(self):
        return len(self.files)

    def iter_files(self, suffixes=None, names=None):
        for rel in self.files:
            if suffixes and not rel.endswith(tuple(suffixes)):
                continue
            if names and Path(rel).name not in names:
                continue
            yield rel

    def path(self, rel):
        return self.root / rel

    def read(self, rel):
        """Read a file, refusing anything that looks like a credential store."""
        if is_secret_file(rel):
            return None
        full = self.root / rel
        try:
            if full.stat().st_size > config.MAX_FILE_BYTES:
                return None
            return full.read_text(encoding="utf-8", errors="replace")
        except (OSError, UnicodeDecodeError):
            return None

    def exists(self, rel):
        return rel in self.files

    def find(self, *names):
        """Relative paths whose basename matches any of `names`, shallowest first."""
        wanted = set(names)
        hits = [f for f in self.files if Path(f).name in wanted]
        return sorted(hits, key=lambda p: (p.count("/"), p))


def is_secret_file(rel):
    """True for credential-bearing files, matched on basename."""
    name = Path(rel).name.lower()
    if name in config.SECRET_FILE_NAMES:
        return True
    if name.startswith(".env"):
        return True
    return name.endswith(config.SECRET_FILE_SUFFIXES)


def _resolve_subdir(root, subdir):
    """Resolve a requested subdirectory, refusing anything outside the root."""
    if not subdir:
        return root
    root = root.resolve()
    candidate = (root / subdir).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError(
            f"--subdir {subdir!r} resolves outside the repository root ({candidate}). "
            f"Refusing to scan outside the repository."
        )
    if not candidate.exists():
        raise ValueError(f"--subdir {subdir!r} does not exist in the repository")
    return candidate


def build_snapshot(root, subdir=None):
    """Walk a directory, applying ignore rules and hard caps incrementally."""
    root = Path(root).resolve()
    if not root.is_dir():
        raise ValueError(f"not a directory: {root}")

    root = _resolve_subdir(root, subdir)

    files = []
    total_bytes = 0
    truncated = False
    reason = None
    skipped_secrets = 0
    deadline = time.monotonic() + config.MAX_SECONDS

    for current, dirnames, filenames in os.walk(root, followlinks=False):
        # Prune ignored directories in place so os.walk never descends into them.
        dirnames[:] = sorted(d for d in dirnames if d not in config.IGNORE_DIRS)

        if time.monotonic() > deadline:
            truncated, reason = True, f"hit time cap ({config.MAX_SECONDS}s)"
            break
        if len(files) >= config.MAX_FILES_SCANNED:
            truncated, reason = True, f"hit file cap ({config.MAX_FILES_SCANNED})"
            break

        for name in sorted(filenames):
            if len(files) >= config.MAX_FILES_SCANNED:
                truncated, reason = True, f"hit file cap ({config.MAX_FILES_SCANNED})"
                break

            full = Path(current) / name
            try:
                # Never follow symlinks: a symlink loop hangs the walk, and a symlink
                # pointing outside the repository is an exfiltration path.
                if full.is_symlink():
                    continue
                if not full.is_file():
                    continue
                size = full.stat().st_size
            except OSError:
                continue

            if name.endswith(config.IGNORE_FILE_SUFFIXES):
                continue
            if size > config.MAX_FILE_BYTES:
                continue

            rel = full.relative_to(root).as_posix()
            if is_secret_file(rel):
                skipped_secrets += 1
                continue

            total_bytes += size
            if total_bytes > config.MAX_TOTAL_BYTES:
                truncated, reason = True, f"hit byte cap ({config.MAX_TOTAL_BYTES})"
                break

            files.append(rel)

        if truncated:
            break

    # Sort the final relative strings explicitly. Path objects compare case-insensitively
    # on Windows and case-sensitively elsewhere, which would make the emitted inventory
    # differ between platforms and break the stability eval.
    files = sorted(set(files))

    return Snapshot(
        root,
        files,
        truncated=truncated,
        skipped_reason=reason,
        skipped_secrets=skipped_secrets,
    )
