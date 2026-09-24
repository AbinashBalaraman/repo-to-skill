"""T0 dialects: workflow and automation declaration sites.

A repository with no application code still has operations -- they are its declarations.
A CI job, a Make target, a `package.json` script, a compose service, a systemd unit, a
crontab entry and an orchestrator DAG are all units of work that a harness has to
reproduce, and each is declared as data.

These dialects run where they fit and nowhere else. A CI job in a library is build
plumbing, not the library's operation surface, so the GitHub Actions dialect does not
run for `cli-tool` or `agent-system` repos. The declared capability for a job or a
target is `shell.exec`, because that is what running one actually requires; anything
further is left to T1/T2 rather than guessed.
"""

import ast
import json
import re

from .base import Candidate, Dialect
from .common import (
    block_children,
    collect_params,
    decorator_names,
    dedupe,
    first_str,
    humanize,
    slug,
)

_YAML_SUFFIXES = (".yml", ".yaml")
_COMPOSE_NAMES = ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml")
_MAKE_NAMES = ("Makefile", "makefile", "GNUmakefile")
_MAKE_TARGET = re.compile(r"^(?P<target>[A-Za-z0-9_][A-Za-z0-9_.\-/]*)\s*:(?!=)", re.MULTILINE)
_CRON_LINE = re.compile(
    r"^\s*(?:(?:@(?:reboot|yearly|annually|monthly|weekly|daily|hourly|midnight)\b)"
    r"|(?:\S+\s+){4}\S+)\s+(?P<command>\S.*)$"
)

_IMAGE_CAPABILITIES = (
    ("postgres", "data.write"),
    ("mysql", "data.write"),
    ("mariadb", "data.write"),
    ("mongo", "data.write"),
    ("redis", "cache.write"),
    ("memcached", "cache.write"),
    ("minio", "storage.object"),
    ("rabbitmq", "queue.publish"),
    ("kafka", "queue.publish"),
    ("nats", "queue.publish"),
    ("elasticsearch", "search.index"),
    ("opensearch", "search.index"),
)


class GitHubActionsDialect(Dialect):
    id = "github-actions"
    description = "GitHub Actions jobs"
    repo_classes = ("infra-config", "service-app", "library", "unknown")

    def match(self, snapshot, profile):
        candidates = []
        order = 0
        for rel in snapshot.iter_files(suffixes=_YAML_SUFFIXES):
            if not rel.startswith(".github/workflows/"):
                continue
            text = snapshot.read(rel)
            if not text or "jobs:" not in text:
                continue
            trigger = "scheduled" if re.search(r"(?m)^\s+schedule\s*:", text) else "event"
            for job, lineno in block_children(text, "jobs"):
                capabilities = ["shell.exec"]
                if any(word in job.lower() for word in ("deploy", "release", "ship", "publish")):
                    capabilities.append("deploy.op")
                candidates.append(
                    Candidate(
                        op_id=slug(f"job-{job}"),
                        name=humanize(job),
                        dialect=self.id,
                        declaration_kind="github-job",
                        loc=f"{rel}:{lineno}",
                        order_hint=order,
                        inputs=[],
                        detail=f"GitHub Actions job {job}",
                        capabilities=capabilities,
                        effect_hint="effectful-local",
                        trigger=trigger,
                    )
                )
                order += 1
        return dedupe(candidates)


class MakeTargetDialect(Dialect):
    id = "make-target"
    description = "Makefile targets"
    repo_classes = ("infra-config", "library", "service-app", "cli-tool", "unknown")

    def match(self, snapshot, profile):
        candidates = []
        order = 0
        for rel in snapshot.files:
            name = rel.rsplit("/", 1)[-1]
            if name not in _MAKE_NAMES and not name.endswith(".mk"):
                continue
            text = snapshot.read(rel)
            if not text:
                continue
            for match in _MAKE_TARGET.finditer(text):
                target = match.group("target")
                if target.startswith(".") or "%" in target or "=" in target:
                    continue
                lineno = text.count("\n", 0, match.start()) + 1
                candidates.append(
                    Candidate(
                        op_id=slug(f"make-{target}"),
                        name=humanize(target),
                        dialect=self.id,
                        declaration_kind="make-target",
                        loc=f"{rel}:{lineno}",
                        order_hint=order,
                        inputs=[],
                        detail=f"Make target {target}",
                        capabilities=["shell.exec"],
                        effect_hint="effectful-local",
                    )
                )
                order += 1
        return dedupe(candidates)


class PackageScriptDialect(Dialect):
    id = "package-scripts"
    description = "package.json scripts"
    repo_classes = ("service-app", "library", "cli-tool", "agent-system", "unknown")

    def match(self, snapshot, profile):
        candidates = []
        order = 0
        for rel in snapshot.find("package.json"):
            text = snapshot.read(rel)
            if not text:
                continue
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                continue
            scripts = data.get("scripts") if isinstance(data, dict) else None
            if not isinstance(scripts, dict):
                continue
            for script in sorted(scripts):
                candidates.append(
                    Candidate(
                        op_id=slug(f"script-{script}"),
                        name=humanize(script),
                        dialect=self.id,
                        declaration_kind="npm-script",
                        loc=f"{rel}:scripts.{script}",
                        order_hint=order,
                        inputs=[],
                        detail=f"package.json script {script}",
                        capabilities=["shell.exec"],
                        effect_hint="effectful-local",
                    )
                )
                order += 1
        return dedupe(candidates)


class ComposeServiceDialect(Dialect):
    id = "compose-services"
    description = "docker-compose services"
    repo_classes = ("infra-config", "service-app", "unknown")

    def match(self, snapshot, profile):
        candidates = []
        order = 0
        for rel in snapshot.iter_files(suffixes=_YAML_SUFFIXES):
            name = rel.rsplit("/", 1)[-1].lower()
            if name not in _COMPOSE_NAMES:
                continue
            text = snapshot.read(rel)
            if not text:
                continue
            lines = text.splitlines()
            for service, lineno in block_children(text, "services"):
                image = _service_image(lines, lineno)
                capabilities = ["container.op"]
                for marker, capability in _IMAGE_CAPABILITIES:
                    if marker in image:
                        capabilities.append(capability)
                candidates.append(
                    Candidate(
                        op_id=slug(f"svc-{service}"),
                        name=humanize(service),
                        dialect=self.id,
                        declaration_kind="compose-service",
                        loc=f"{rel}:{lineno}",
                        order_hint=order,
                        inputs=[],
                        detail=f"compose service {service}" + (f" ({image})" if image else ""),
                        capabilities=capabilities,
                        effect_hint="effectful-local",
                    )
                )
                order += 1
        return dedupe(candidates)


def _service_image(lines, lineno):
    """The `image:` value of the service whose key is at `lineno` (1-based)."""
    base = len(lines[lineno - 1]) - len(lines[lineno - 1].lstrip())
    for line in lines[lineno:]:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if indent <= base:
            break
        if stripped.startswith("image:"):
            return stripped.split(":", 1)[1].strip().strip("\"'").lower()
    return ""


class SystemdDialect(Dialect):
    id = "systemd-unit"
    description = "systemd units"
    repo_classes = ("infra-config", "service-app", "unknown")

    def match(self, snapshot, profile):
        candidates = []
        order = 0
        for rel in snapshot.iter_files(suffixes=(".service", ".timer", ".socket")):
            text = snapshot.read(rel)
            if not text or "[Unit]" not in text:
                continue
            name = rel.rsplit("/", 1)[-1].rsplit(".", 1)[0]
            suffix = rel.rsplit(".", 1)[-1]
            if suffix == "timer":
                capabilities = ["schedule.cron"]
                trigger = "scheduled"
            elif suffix == "socket":
                capabilities = []
                trigger = "manual"
            else:
                capabilities = ["shell.exec"] if "ExecStart=" in text else []
                trigger = "manual"
            candidates.append(
                Candidate(
                    op_id=slug(f"unit-{name}"),
                    name=humanize(name),
                    dialect=self.id,
                    declaration_kind=f"systemd-{suffix}",
                    loc=f"{rel}:1",
                    order_hint=order,
                    inputs=[],
                    detail=f"systemd {suffix} unit {name}",
                    capabilities=capabilities,
                    effect_hint="effectful-local" if capabilities else None,
                    trigger=trigger,
                )
            )
            order += 1
        return dedupe(candidates)


class CronDialect(Dialect):
    id = "crontab"
    description = "crontab entries"
    repo_classes = ("infra-config", "unknown")

    def match(self, snapshot, profile):
        candidates = []
        order = 0
        for rel in snapshot.files:
            name = rel.rsplit("/", 1)[-1].lower()
            if not (name.startswith("crontab") or name.endswith(".cron") or "/cron.d/" in rel):
                continue
            text = snapshot.read(rel)
            if not text:
                continue
            for index, line in enumerate(text.splitlines(), start=1):
                if not line.strip() or line.lstrip().startswith("#"):
                    continue
                match = _CRON_LINE.match(line)
                if not match:
                    continue
                command = match.group("command").split()[0]
                candidates.append(
                    Candidate(
                        op_id=slug(f"cron-{command}") or f"cron-entry-{index}",
                        name=humanize(f"cron {command}"),
                        dialect=self.id,
                        declaration_kind="crontab-entry",
                        loc=f"{rel}:{index}",
                        order_hint=order,
                        inputs=[],
                        detail=f"scheduled command {command}",
                        capabilities=["shell.exec"],
                        effect_hint="effectful-local",
                        trigger="scheduled",
                    )
                )
                order += 1
        return dedupe(candidates)


class OrchestratorDialect(Dialect):
    id = "orchestrator-dag"
    description = "Airflow/Prefect/Dagster DAG and task declarations"
    repo_classes = ("agent-system", "service-app", "library", "infra-config", "unknown")

    # Deliberately disjoint from the CLI dialect's decorators: a `@task` is already
    # claimed there, and claiming it twice would only inflate the candidate list.
    _DECORATORS = ("dag", "op", "asset", "schedule", "sensor", "graph", "materialize")

    def match(self, snapshot, profile):
        candidates = []
        order = 0
        for rel in snapshot.iter_files(suffixes=(".py",)):
            text = snapshot.read(rel)
            if not text or not any(f"@{name}" in text for name in self._DECORATORS):
                continue
            try:
                tree = ast.parse(text, filename=rel)
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                hit = next(
                    (n for n in decorator_names(node) if n in self._DECORATORS),
                    None,
                )
                if not hit:
                    continue
                candidates.append(
                    Candidate(
                        op_id=slug(f"{hit}-{node.name}"),
                        name=humanize(node.name),
                        dialect=self.id,
                        declaration_kind=f"{hit}-decorator",
                        loc=f"{rel}:{node.lineno}",
                        order_hint=order,
                        inputs=collect_params(node),
                        detail=f"@{hit} on {node.name}()",
                        body_range=(node.lineno, getattr(node, "end_lineno", node.lineno)),
                    )
                )
                order += 1
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                    continue
                if node.func.id not in ("DAG", "Flow"):
                    continue
                label = first_str(node)
                if not label:
                    continue
                candidates.append(
                    Candidate(
                        op_id=slug(f"dag-{label}"),
                        name=humanize(label),
                        dialect=self.id,
                        declaration_kind="dag-constructor",
                        loc=f"{rel}:{node.lineno}",
                        order_hint=order,
                        inputs=[],
                        detail=f"{node.func.id} {label}",
                    )
                )
                order += 1
        return dedupe(candidates)


__all__ = [
    "ComposeServiceDialect",
    "CronDialect",
    "GitHubActionsDialect",
    "MakeTargetDialect",
    "OrchestratorDialect",
    "PackageScriptDialect",
    "SystemdDialect",
]
