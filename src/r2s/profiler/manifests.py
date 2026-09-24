"""Language, runtime and dependency detection from manifests.

Dependencies are the input to T2 (the provider catalog). Manifests are also the
cheapest signal available, so this runs before anything else.
"""

import json
import re
from pathlib import Path

try:  # stdlib from 3.11
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None

MANIFEST_NAMES = (
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "requirements.txt",
    "package.json",
    "go.mod",
    "Cargo.toml",
    "composer.json",
    "Gemfile",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "Dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
    "compose.yml",
    "compose.yaml",
    "Makefile",
    "terraform.tf",
    "main.tf",
)

# Import/package name -> ecosystem, used to normalise what the catalog matches on.
LANGUAGE_MARKERS = {
    "pyproject.toml": "python",
    "setup.py": "python",
    "setup.cfg": "python",
    "requirements.txt": "python",
    "package.json": "javascript",
    "go.mod": "go",
    "Cargo.toml": "rust",
    "composer.json": "php",
    "Gemfile": "ruby",
    "pom.xml": "java",
    "build.gradle": "java",
    "build.gradle.kts": "java",
}

_REQ_LINE = re.compile(r"^\s*([A-Za-z0-9_.\-]+)\s*(\[[^\]]*\])?\s*(.*)$")


def _strip_requirement(line):
    line = line.split("#", 1)[0].strip()
    if not line or line.startswith("-"):
        return None
    match = _REQ_LINE.match(line)
    return match.group(1).lower().replace("_", "-") if match else None


def read_python_deps(snapshot):
    deps = set()

    for rel in snapshot.find("requirements.txt"):
        text = snapshot.read(rel)
        if text:
            for line in text.splitlines():
                name = _strip_requirement(line)
                if name:
                    deps.add(name)

    for rel in snapshot.find("pyproject.toml"):
        text = snapshot.read(rel)
        if not text or tomllib is None:
            continue
        try:
            data = tomllib.loads(text)
        except Exception:
            continue
        project = data.get("project", {})
        for entry in project.get("dependencies", []) or []:
            name = _strip_requirement(entry)
            if name:
                deps.add(name)
        for group in (project.get("optional-dependencies") or {}).values():
            for entry in group or []:
                name = _strip_requirement(entry)
                if name:
                    deps.add(name)
        poetry = (data.get("tool", {}) or {}).get("poetry", {})
        for name in poetry.get("dependencies") or {}:
            if name.lower() != "python":
                deps.add(name.lower().replace("_", "-"))

    for rel in snapshot.find("setup.cfg"):
        text = snapshot.read(rel)
        if text:
            in_install = False
            for line in text.splitlines():
                if line.strip().startswith("install_requires"):
                    in_install = True
                    continue
                if in_install:
                    if line and not line[0].isspace():
                        break
                    name = _strip_requirement(line)
                    if name:
                        deps.add(name)

    return deps


def read_node_deps(snapshot):
    deps = set()
    for rel in snapshot.find("package.json"):
        text = snapshot.read(rel)
        if not text:
            continue
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            continue
        for key in ("dependencies", "devDependencies", "peerDependencies"):
            deps.update((data.get(key) or {}).keys())
    return deps


def detect(snapshot):
    """Return a profile of the repo: languages, ecosystems, dependencies, marker files."""
    found = {}
    for rel in snapshot.files:
        name = Path(rel).name
        if name in MANIFEST_NAMES:
            found.setdefault(name, []).append(rel)

    languages = sorted({LANGUAGE_MARKERS[n] for n in found if n in LANGUAGE_MARKERS})

    ecosystems = {}
    py = read_python_deps(snapshot)
    if py:
        ecosystems["pypi"] = py
    node = read_node_deps(snapshot)
    if node:
        ecosystems["npm"] = node

    return {
        "languages": languages,
        "manifests": dict(sorted(found.items())),
        "ecosystems": ecosystems,
        "has_docker": bool(
            found.get("Dockerfile") or found.get("docker-compose.yml") or found.get("compose.yml")
        ),
        "has_ci": any(rel.startswith(".github/workflows/") for rel in snapshot.files),
        "has_make": bool(found.get("Makefile")),
        "has_terraform": any(rel.endswith(".tf") for rel in snapshot.files),
    }
