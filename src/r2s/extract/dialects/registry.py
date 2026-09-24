"""Dialect discovery and execution.

Ordering is deterministic: dialects run in a fixed order so the same repo always
yields the same candidate order, which the stability eval depends on.
"""

from .cli_python import CliPythonDialect

_REGISTRY = [
    CliPythonDialect(),
]


def register(dialect):
    """Add a dialect. Ordering is by id, so registration order does not matter."""
    _REGISTRY.append(dialect)


def all_dialects():
    return sorted(_REGISTRY, key=lambda d: d.id)


def run(snapshot, profile):
    """Run every dialect that fits the repo class. Returns (candidates, ran)."""
    candidates = []
    ran = []
    for dialect in all_dialects():
        if not dialect.fits(profile.repo_class):
            continue
        ran.append(dialect.id)
        candidates.extend(dialect.match(snapshot, profile))
    return candidates, ran
