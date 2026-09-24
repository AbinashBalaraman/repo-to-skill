"""Dialect discovery and execution.

Ordering is deterministic: dialects run in a fixed order so the same repo always
yields the same candidate order, which the stability eval depends on. Ordering is by
`id`, so registration order does not matter -- but it does mean `id` is load-bearing:
when two dialects recognise the same site, the alphabetically first one wins, and the
loser's candidate is dropped rather than merged, because a duplicate `op_id` would be
a schema violation.

Each dialect declares the repo classes it fits. A dialect that runs where it does not
fit produces noise, not coverage, so `repo_classes` is a correctness control, not a
hint.
"""

from .agent_tools import ToolDecoratorDialect, ToolManifestDialect
from .cli_python import CliPythonDialect
from .infra import AnsibleDialect, KubernetesDialect, TerraformDialect
from .web import OpenApiDialect, ProtoServiceDialect, WebJsDialect, WebPythonDialect
from .workflow import (
    ComposeServiceDialect,
    CronDialect,
    GitHubActionsDialect,
    MakeTargetDialect,
    OrchestratorDialect,
    PackageScriptDialect,
    SystemdDialect,
)

_REGISTRY = [
    CliPythonDialect(),
    # Web and RPC declaration sites.
    WebPythonDialect(),
    WebJsDialect(),
    OpenApiDialect(),
    ProtoServiceDialect(),
    # Workflow and automation declaration sites.
    GitHubActionsDialect(),
    MakeTargetDialect(),
    PackageScriptDialect(),
    ComposeServiceDialect(),
    SystemdDialect(),
    CronDialect(),
    OrchestratorDialect(),
    # Agent tool registries.
    ToolDecoratorDialect(),
    ToolManifestDialect(),
    # Infrastructure as code.
    TerraformDialect(),
    KubernetesDialect(),
    AnsibleDialect(),
]


def register(dialect):
    """Add a dialect. Ordering is by id, so registration order does not matter."""
    _REGISTRY.append(dialect)


def all_dialects():
    return sorted(_REGISTRY, key=lambda d: d.id)


def run(snapshot, profile):
    """Run every dialect that fits the repo class. Returns (candidates, ran).

    `ran` lists only dialects that both fit the class and returned candidates, so the
    diagnostics say which evidence sources actually fired rather than which were
    merely considered.
    """
    candidates = []
    ran = []
    seen_ids = set()
    for dialect in all_dialects():
        if not dialect.fits(profile.repo_class):
            continue
        produced = dialect.match(snapshot, profile)
        fresh = [c for c in produced if c.op_id and c.op_id not in seen_ids]
        if not fresh:
            continue
        for candidate in fresh:
            seen_ids.add(candidate.op_id)
        ran.append(dialect.id)
        candidates.extend(fresh)
    return candidates, ran
