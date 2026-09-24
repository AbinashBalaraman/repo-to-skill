"""T0 dialects: infrastructure-as-code declaration sites.

These repos have no application code at all. Their declarations *are* the operations:
a Terraform resource is a thing that will exist, a Kubernetes manifest is a thing that
will run, an Ansible play is a thing that will be applied. Nothing can be traced, so
the declaration is the whole evidence base -- which is why these dialects declare
capabilities directly from the resource/manifest kind instead of leaving every
operation as an unresolved external call.

The kind-to-capability mapping is curated and narrow. An unrecognised kind declares
nothing and falls through to the pipeline's unresolved-external-call path, which is
reviewable; a guessed capability is not.
"""

import re

from .base import Candidate, Dialect
from .common import dedupe, has_top_level_key, humanize, slug, top_level_scalar

_YAML_SUFFIXES = (".yml", ".yaml")

_TERRAFORM_CAPABILITIES = (
    ("s3", "storage.object"),
    ("bucket", "storage.object"),
    ("storage", "storage.object"),
    ("gcs", "storage.object"),
    ("blob", "storage.object"),
    ("filestore", "storage.object"),
    ("db_instance", "data.write"),
    ("rds", "data.write"),
    ("dynamodb", "data.write"),
    ("_sql", "data.write"),
    ("postgres", "data.write"),
    ("mysql", "data.write"),
    ("database", "data.write"),
    ("bigquery", "data.write"),
    ("spanner", "data.write"),
    ("redis", "cache.write"),
    ("elasticache", "cache.write"),
    ("memcache", "cache.write"),
    ("sqs", "queue.publish"),
    ("kinesis", "queue.publish"),
    ("pubsub", "queue.publish"),
    ("eventhub", "queue.publish"),
    ("sns", "notify.message"),
    ("ses", "notify.message"),
    ("lambda", "code.exec"),
    ("cloudfunctions", "code.exec"),
    ("cloudfunction", "code.exec"),
    ("batch", "code.exec"),
    ("instance", "code.exec"),
    ("kms", "secret.apikey"),
    ("secret", "secret.apikey"),
    ("vault", "secret.apikey"),
    ("ssm", "secret.apikey"),
    ("ecs", "container.op"),
    ("eks", "container.op"),
    ("container", "container.op"),
    ("docker", "container.op"),
    ("ecr", "container.op"),
    ("scheduler", "schedule.cron"),
    ("cron", "schedule.cron"),
    ("eventbridge", "schedule.cron"),
    ("cloudfront", "deploy.op"),
    ("cdn", "deploy.op"),
    ("api_gateway", "deploy.op"),
    ("apigateway", "deploy.op"),
    ("load_balancer", "deploy.op"),
    ("elasticsearch", "search.index"),
    ("opensearch", "search.index"),
    ("search", "search.index"),
)

_K8S_CAPABILITIES = {
    "deployment": "deploy.op",
    "statefulset": "deploy.op",
    "daemonset": "deploy.op",
    "replicaset": "deploy.op",
    "job": "deploy.op",
    "cronjob": "schedule.cron",
    "service": "deploy.op",
    "ingress": "deploy.op",
    "horizontalpodautoscaler": "deploy.op",
    "secret": "secret.apikey",
    "persistentvolumeclaim": "storage.object",
    "persistentvolume": "storage.object",
}

_TERRAFORM_BLOCK = re.compile(
    r"(?m)^\s*(?P<kind>resource|module|data)\s+"
    r"(?:(?P<type>\"[^\"]+\")\s+)?(?P<name>\"[^\"]+\")"
)
_HOSTS_LINE = re.compile(r"(?m)^\s*-?\s*hosts\s*:\s*(?P<hosts>\S.*)$")
_PLAY_NAME = re.compile(r"(?m)^\s*-?\s*name\s*:\s*(?P<name>\S.*)$")


class TerraformDialect(Dialect):
    id = "terraform"
    description = "Terraform resources, modules and data sources"
    repo_classes = ("infra-config", "library", "unknown")

    def match(self, snapshot, profile):
        candidates = []
        order = 0
        for rel in snapshot.iter_files(suffixes=(".tf",)):
            text = snapshot.read(rel)
            if not text or ("resource" not in text and "module" not in text and "data" not in text):
                continue
            for match in _TERRAFORM_BLOCK.finditer(text):
                kind = match.group("kind")
                name = match.group("name").strip('"')
                type_name = (match.group("type") or "").strip('"')
                label = f"{type_name} {name}".strip()
                capabilities = []
                if kind != "module":
                    capabilities = _terraform_capabilities(type_name)
                lineno = text.count("\n", 0, match.start()) + 1
                candidates.append(
                    Candidate(
                        op_id=slug(f"tf-{type_name}-{name}") or slug(f"tf-{name}"),
                        name=humanize(f"{type_name} {name}".strip()),
                        dialect=self.id,
                        declaration_kind=f"terraform-{kind}",
                        loc=f"{rel}:{lineno}",
                        order_hint=order,
                        inputs=[],
                        detail=f"Terraform {kind} {label}",
                        capabilities=capabilities,
                        effect_hint="effectful-external" if capabilities else None,
                    )
                )
                order += 1
        return dedupe(candidates)


def _terraform_capabilities(type_name):
    lowered = type_name.lower()
    for marker, capability in _TERRAFORM_CAPABILITIES:
        if marker in lowered:
            return [capability]
    return []


class KubernetesDialect(Dialect):
    id = "kubernetes"
    description = "Kubernetes manifests"
    repo_classes = ("infra-config", "service-app", "unknown")

    def match(self, snapshot, profile):
        candidates = []
        order = 0
        for rel in snapshot.iter_files(suffixes=_YAML_SUFFIXES):
            text = snapshot.read(rel)
            if not text or not has_top_level_key(text, "kind"):
                continue
            kind = (top_level_scalar(text, "kind") or "").strip()
            if not kind:
                continue
            name = _metadata_name(text) or rel.rsplit("/", 1)[-1].rsplit(".", 1)[0]
            capability = _K8S_CAPABILITIES.get(kind.lower())
            capabilities = [capability] if capability else []
            trigger = "scheduled" if kind.lower() == "cronjob" else "manual"
            candidates.append(
                Candidate(
                    op_id=slug(f"k8s-{kind}-{name}"),
                    name=humanize(f"{kind} {name}"),
                    dialect=self.id,
                    declaration_kind=f"kubernetes-{kind.lower()}",
                    loc=f"{rel}:1",
                    order_hint=order,
                    inputs=[],
                    detail=f"Kubernetes {kind} {name}",
                    capabilities=capabilities,
                    effect_hint="effectful-external" if capabilities else None,
                    trigger=trigger,
                )
            )
            order += 1
        return dedupe(candidates)


def _metadata_name(text):
    """The `name:` value directly under a top-level `metadata:` block."""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line != "metadata:":
            continue
        for following in lines[index + 1 :]:
            stripped = following.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if not following.startswith((" ", "\t")):
                break
            if stripped.startswith("name:"):
                return stripped.split(":", 1)[1].strip().strip("\"'")
        break
    return None


class AnsibleDialect(Dialect):
    id = "ansible"
    description = "Ansible playbooks and task files"
    repo_classes = ("infra-config", "unknown")

    def match(self, snapshot, profile):
        candidates = []
        order = 0
        for rel in snapshot.iter_files(suffixes=_YAML_SUFFIXES):
            text = snapshot.read(rel)
            if not text or has_top_level_key(text, "kind"):
                continue
            is_playbook = _HOSTS_LINE.search(text) is not None
            is_taskfile = bool(re.search(r"(?m)^\s*(?:- )?tasks\s*:", text)) and "/tasks/" in rel
            if not (is_playbook or is_taskfile):
                continue
            stem = rel.rsplit("/", 1)[-1].rsplit(".", 1)[0]
            if is_playbook:
                lines = text.splitlines()
                for match in _HOSTS_LINE.finditer(text):
                    lineno = text.count("\n", 0, match.start()) + 1
                    label = _preceding_play_name(lines, lineno) or f"{stem}-{lineno}"
                    candidates.append(_ansible_candidate(rel, lineno, label, "ansible-play", order))
                    order += 1
            else:
                candidates.append(_ansible_candidate(rel, 1, stem, "ansible-tasks", order))
                order += 1
        return dedupe(candidates)


def _ansible_candidate(rel, lineno, label, kind, order):
    return Candidate(
        op_id=slug(f"play-{label}") or slug(f"play-{lineno}"),
        name=humanize(label),
        dialect="ansible",
        declaration_kind=kind,
        loc=f"{rel}:{lineno}",
        order_hint=order,
        inputs=[],
        detail=f"Ansible {kind.split('-')[-1]} {label}",
        capabilities=["shell.exec"],
        effect_hint="effectful-local",
    )


def _preceding_play_name(lines, lineno):
    for index in range(lineno - 2, -1, -1):
        match = _PLAY_NAME.match(lines[index])
        if match:
            return match.group("name").strip().strip("\"'")
        if lines[index].strip() and not lines[index].lstrip().startswith("#"):
            break
    return None


__all__ = ["AnsibleDialect", "KubernetesDialect", "TerraformDialect"]
