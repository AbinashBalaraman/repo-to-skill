"""Entrypoint discovery.

An entrypoint is where the repo's behaviour is actually reachable from. T1 effect
tracing walks outward from these, so a repo with no discoverable entrypoint cannot
be effect-traced at all -- which is itself a suitability signal.
"""

import json
import re
from pathlib import Path

from ..util import toml as toml_util

# Conventional names that behave as entrypoints even without a declaration.
CONVENTIONAL_NAMES = {
    "__main__.py": "module",
    "main.py": "script",
    "cli.py": "cli",
    "app.py": "server",
    "server.py": "server",
    "manage.py": "cli",
    "wsgi.py": "server",
    "asgi.py": "server",
    "run.py": "script",
}

_MAKE_TARGET = re.compile(r"^([A-Za-z0-9_][A-Za-z0-9_.\-]*)\s*:(?!=)", re.MULTILINE)


class Entrypoint:
    def __init__(self, kind, loc, name=None, source="convention"):
        self.kind = kind
        self.loc = loc
        self.name = name or loc
        self.source = source

    def to_dict(self):
        return {"kind": self.kind, "loc": self.loc, "name": self.name, "source": self.source}

    def __repr__(self):
        return f"<Entrypoint {self.kind} {self.loc}>"


def _python_console_scripts(snapshot, failures=None):
    out = []
    failures = failures if failures is not None else []
    for rel in snapshot.find("pyproject.toml"):
        text = snapshot.read(rel)
        if not text:
            continue
        try:
            data = toml_util.loads(text)
        except toml_util.TomlUnavailable as exc:
            failures.append(f"{rel}: {exc}")
            continue
        except Exception as exc:
            failures.append(
                f"{rel}: could not be parsed ({exc}); console scripts are "
                f"missing from this inventory"
            )
            continue
        scripts = (data.get("project", {}) or {}).get("scripts", {}) or {}
        for name, target in scripts.items():
            out.append(
                Entrypoint("console-script", _module_to_path(target), name=name, source="declared")
            )
        poetry = ((data.get("tool", {}) or {}).get("poetry", {}) or {}).get("scripts", {}) or {}
        for name, target in poetry.items():
            loc = target if isinstance(target, str) else name
            out.append(
                Entrypoint("console-script", _module_to_path(loc), name=name, source="declared")
            )
    return out


def _module_to_path(target):
    module = str(target).split(":")[0].split(".")[0]
    return f"{module}.py"


def _node_bins(snapshot, failures=None):
    out = []
    failures = failures if failures is not None else []
    for rel in snapshot.find("package.json"):
        text = snapshot.read(rel)
        if not text:
            continue
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            failures.append(
                f"{rel}: could not be parsed ({exc}); its bin and script entrypoints "
                f"are missing from this inventory"
            )
            continue
        for name, target in (data.get("bin") or {}).items():
            out.append(Entrypoint("cli", str(target), name=name, source="declared"))
        for name in data.get("scripts") or {}:
            if name in ("start", "serve", "dev", "build", "test"):
                out.append(
                    Entrypoint(
                        "script", f"package.json#scripts.{name}", name=name, source="declared"
                    )
                )
    return out


def _make_targets(snapshot):
    out = []
    for rel in snapshot.find("Makefile"):
        text = snapshot.read(rel)
        if not text:
            continue
        for target in _MAKE_TARGET.findall(text):
            if target.startswith("."):
                continue
            out.append(Entrypoint("make-target", f"{rel}#{target}", name=target, source="declared"))
    return out


def _conventional(snapshot):
    out = []
    for rel in snapshot.files:
        name = Path(rel).name
        if name in CONVENTIONAL_NAMES and rel.count("/") <= 2:
            out.append(Entrypoint(CONVENTIONAL_NAMES[name], rel, source="convention"))
    return out


def discover(snapshot, failures=None):
    """All entrypoints, deduplicated by (kind, loc), deterministic order."""
    found = []
    found += _python_console_scripts(snapshot, failures)
    found += _node_bins(snapshot, failures)
    found += _make_targets(snapshot)
    found += _conventional(snapshot)

    seen = set()
    unique = []
    for ep in found:
        key = (ep.kind, ep.loc)
        if key in seen:
            continue
        seen.add(key)
        unique.append(ep)

    # Declared before convention: a declaration is stronger evidence than a filename.
    order = {"declared": 0, "convention": 1}
    return sorted(unique, key=lambda e: (order.get(e.source, 9), e.loc))
