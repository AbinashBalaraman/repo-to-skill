"""Language, runtime and dependency detection from manifests.

Dependencies are the input to T2 (the provider catalog). Manifests are also the
cheapest signal available, so this runs before anything else.
"""

import json
import re
import xml.etree.ElementTree as ElementTree
from pathlib import Path

from ..util import toml as toml_util

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


def read_python_deps(snapshot, failures=None):
    deps = set()
    failures = failures if failures is not None else []

    for rel in snapshot.find("requirements.txt"):
        text = snapshot.read(rel)
        if text:
            for line in text.splitlines():
                name = _strip_requirement(line)
                if name:
                    deps.add(name)

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
                f"{rel}: could not be parsed ({exc}); its dependencies "
                f"and console scripts are missing from this inventory"
            )
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


def read_node_deps(snapshot, failures=None):
    deps = set()
    failures = failures if failures is not None else []
    for rel in snapshot.find("package.json"):
        text = snapshot.read(rel)
        if not text:
            continue
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            failures.append(
                f"{rel}: could not be parsed ({exc}); its dependencies are missing "
                f"from this inventory"
            )
            continue
        for key in ("dependencies", "devDependencies", "peerDependencies"):
            deps.update((data.get(key) or {}).keys())
    return deps


def read_cargo_deps(snapshot, failures=None):
    """Rust dependencies, keyed by crate name exactly as Cargo.toml writes it.

    Cargo splits dependencies across four tables; a crate declared only as a
    `[dev-dependencies]` entry is still a crate the repo imports, so all four are read.
    The catalog matches on the crate name, which is the same string in every table.
    """
    deps = set()
    failures = failures if failures is not None else []

    for rel in snapshot.find("Cargo.toml"):
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
                f"{rel}: could not be parsed ({exc}); its dependencies are missing "
                f"from this inventory"
            )
            continue
        for table in ("dependencies", "dev-dependencies", "build-dependencies"):
            deps.update(_cargo_table(data.get(table)))
        workspace = data.get("workspace")
        if isinstance(workspace, dict):
            deps.update(_cargo_table(workspace.get("dependencies")))
    return deps


def _cargo_table(table):
    if not isinstance(table, dict):
        return set()
    return set(table)


_GO_REQUIRE_BLOCK = re.compile(r"^require\s*\($")
_GO_REQUIRE_SINGLE = re.compile(r"^require\s+(\S+)\s+\S+")
_GO_MODULE = re.compile(r"^module\s+(\S+)")


def read_go_deps(snapshot, failures=None):
    """Go module requirements, normalised to the module path.

    The catalog matches on the module path exactly as `require` writes it, including any
    major-version suffix -- `github.com/redis/go-redis/v9` is a different module from
    `github.com/redis/go-redis/v8`, and the catalog lists both. Reading the module path
    as well is what makes a go.mod recognisable as a go.mod: a file with requirements but
    no `module` directive is malformed, and is reported rather than silently accepted.
    """
    deps = set()
    failures = failures if failures is not None else []

    for rel in snapshot.find("go.mod"):
        text = snapshot.read(rel)
        if not text:
            continue
        modules, requires, unclosed = _go_mod_entries(text)
        if not modules:
            failures.append(
                f"{rel}: no `module` directive; its dependencies are missing from this inventory"
            )
            continue
        if unclosed:
            failures.append(
                f"{rel}: a `require (` block is never closed; the modules after it are "
                f"missing from this inventory"
            )
        deps.update(requires)
    return deps


def _go_mod_entries(text):
    """(module paths, required module paths, unclosed-require-block flag)."""
    modules = []
    requires = set()
    in_block = False
    for raw_line in text.splitlines():
        line = raw_line.split("//", 1)[0].strip()
        if not line:
            continue
        if in_block:
            if line == ")":
                in_block = False
                continue
            parts = line.split()
            if parts:
                requires.add(parts[0])
            continue
        module_match = _GO_MODULE.match(line)
        if module_match:
            modules.append(module_match.group(1))
            continue
        if _GO_REQUIRE_BLOCK.match(line):
            in_block = True
            continue
        single = _GO_REQUIRE_SINGLE.match(line)
        if single:
            requires.add(single.group(1))
    return modules, requires, in_block


_GRADLE_DEP = re.compile(
    r"(?m)^[ \t]*(?:implementation|api|compileOnly|compileOnlyApi|runtimeOnly|"
    r"testImplementation|testRuntimeOnly|testCompileOnly|annotationProcessor|kapt|"
    r"ksp|developmentOnly|providedCompile)\s*[(\s]\s*"
    r"[\"']([^\"':\s]+):([^\"':\s]+)(?::[^\"']*)?[\"']"
)


def read_jvm_deps(snapshot, failures=None):
    """JVM dependencies, keyed by ecosystem: `maven` and `gradle`.

    Maven coordinates are `groupId:artifactId`, and Gradle coordinates are
    `group:artifact` -- the same shape with different names -- so the catalog lists the
    identical string under both keys.
    """
    deps = {"maven": set(), "gradle": set()}
    failures = failures if failures is not None else []

    for rel in snapshot.find("pom.xml"):
        text = snapshot.read(rel)
        if not text:
            continue
        try:
            deps["maven"].update(_maven_deps(text))
        except ElementTree.ParseError as exc:
            failures.append(
                f"{rel}: could not be parsed ({exc}); its dependencies are missing "
                f"from this inventory"
            )

    for rel in snapshot.find("build.gradle", "build.gradle.kts"):
        text = snapshot.read(rel)
        if not text:
            continue
        deps["gradle"].update(_gradle_deps(text))

    return deps


def _maven_deps(text):
    """`groupId:artifactId` for every dependency in a pom.xml."""
    root = ElementTree.fromstring(text)
    namespace = ""
    if root.tag.startswith("{"):
        namespace = root.tag.split("}", 1)[0] + "}"
    out = set()
    for container in root.iter(f"{namespace}dependencies"):
        for dependency in container.findall(f"{namespace}dependency"):
            group = dependency.findtext(f"{namespace}groupId")
            artifact = dependency.findtext(f"{namespace}artifactId")
            if group and artifact:
                out.add(f"{group.strip()}:{artifact.strip()}")
    return out


def _gradle_deps(text):
    return {f"{match.group(1)}:{match.group(2)}" for match in _GRADLE_DEP.finditer(text)}


def detect(snapshot):
    """Return a profile of the repo: languages, ecosystems, dependencies, marker files.

    `parse_failures` lists manifests that could not be read. They are surfaced rather
    than swallowed: a pyproject.toml that fails to parse takes every console script and
    every declared dependency with it, which silently changes the repo class and the
    inventory.
    """
    found = {}
    for rel in snapshot.files:
        name = Path(rel).name
        if name in MANIFEST_NAMES:
            found.setdefault(name, []).append(rel)

    languages = sorted({LANGUAGE_MARKERS[n] for n in found if n in LANGUAGE_MARKERS})

    parse_failures = []
    ecosystems = {}
    py = read_python_deps(snapshot, parse_failures)
    if py:
        ecosystems["pypi"] = py
    node = read_node_deps(snapshot, parse_failures)
    if node:
        ecosystems["npm"] = node
    cargo = read_cargo_deps(snapshot, parse_failures)
    if cargo:
        ecosystems["cargo"] = cargo
    go = read_go_deps(snapshot, parse_failures)
    if go:
        ecosystems["go"] = go
    for ecosystem, names in read_jvm_deps(snapshot, parse_failures).items():
        if names:
            ecosystems[ecosystem] = names

    return {
        "languages": languages,
        "manifests": dict(sorted(found.items())),
        "ecosystems": ecosystems,
        "parse_failures": parse_failures,
        "has_docker": bool(
            found.get("Dockerfile") or found.get("docker-compose.yml") or found.get("compose.yml")
        ),
        "has_ci": any(rel.startswith(".github/workflows/") for rel in snapshot.files),
        "has_make": bool(found.get("Makefile")),
        "has_terraform": any(rel.endswith(".tf") for rel in snapshot.files),
    }
