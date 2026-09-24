"""Repo classification.

The class is not metadata -- it selects the extraction strategy. It decides where to
look for operations and what an operation looks like, which is why it is a first-class
profiler output rather than a label attached afterwards.
"""

# Dependencies that imply a class, checked against the T2 ecosystems map.
AGENT_MARKERS = {
    "langgraph",
    "langchain",
    "langchain-core",
    "crewai",
    "autogen",
    "pyautogen",
    "agents",
    "openai-agents",
    "smolagents",
    "llama-index",
    "llama-index-core",
    "haystack-ai",
    "semantic-kernel",
    "dspy",
    "dspy-ai",
}
WEB_MARKERS = {
    "fastapi",
    "flask",
    "django",
    "starlette",
    "aiohttp",
    "tornado",
    "bottle",
    "sanic",
    "falcon",
    "quart",
    "express",
    "fastify",
    "koa",
    "nestjs",
    "hapi",
    "restify",
    "sails",
    "meteor",
}
CLI_MARKERS = {
    "click",
    "typer",
    "argparse",
    "docopt",
    "fire",
    "rich-click",
    "cleo",
    "commander",
    "yargs",
    "oclif",
    "meow",
    "cac",
    "minimist",
    "inquirer",
    "prompt-toolkit",
    "textual",
}
INFRA_MARKERS = {"ansible", "ansible-core", "terraform", "pulumi", "boto3-cdk"}


def _dep_set(manifests):
    deps = set()
    for names in (manifests.get("ecosystems") or {}).values():
        deps.update(names)
    return deps


def classify(snapshot, manifests, entrypoints):
    """Return (repo_class, confidence, evidence list)."""
    deps = _dep_set(manifests)
    evidence = []

    has_agent = bool(deps & AGENT_MARKERS)
    has_web = bool(deps & WEB_MARKERS)
    has_cli_dep = bool(deps & CLI_MARKERS)
    has_infra = bool(deps & INFRA_MARKERS)

    declared_cli = [e for e in entrypoints if e.kind == "console-script" and e.source == "declared"]
    node_bins = [e for e in entrypoints if e.kind == "cli" and e.source == "declared"]
    has_server_ep = any(e.kind == "server" for e in entrypoints)
    has_make = manifests.get("has_make")

    # Order matters: the most specific structural signal wins.
    if has_agent:
        evidence.append(
            ("declared", "manifests", f"agent framework dep: {sorted(deps & AGENT_MARKERS)}")
        )
        return "agent-system", "high", evidence

    if has_web or has_server_ep:
        detail = sorted(deps & WEB_MARKERS) or [e.loc for e in entrypoints if e.kind == "server"]
        evidence.append(("declared", "manifests", f"web/server signal: {detail}"))
        return "service-app", "high" if has_web else "medium", evidence

    if declared_cli or node_bins or has_cli_dep:
        detail = [e.name for e in declared_cli + node_bins] or sorted(deps & CLI_MARKERS)
        evidence.append(("declared", "entrypoints", f"cli signal: {detail}"))
        return "cli-tool", "high" if (declared_cli or node_bins) else "medium", evidence

    # Infra last among the positives: a repo can have .tf files and still be a library.
    code_files = [
        f for f in snapshot.files if f.endswith((".py", ".js", ".ts", ".go", ".rs", ".rb", ".java"))
    ]
    if has_infra and len(code_files) < 10:
        evidence.append(("declared", "manifests", "infra tooling with little application code"))
        return "infra-config", "medium", evidence

    if manifests.get("languages") and code_files:
        evidence.append(
            (
                "inferred",
                "manifests",
                f"package metadata, no cli/web/agent signal ({len(code_files)} code files)",
            )
        )
        return "library", "medium", evidence

    if has_make or manifests.get("has_docker") or manifests.get("has_ci"):
        evidence.append(("inferred", "manifests", "automation config present, no application code"))
        return "infra-config", "low", evidence

    evidence.append(("inferred", "snapshot", "no class signal found"))
    return "unknown", "low", evidence
