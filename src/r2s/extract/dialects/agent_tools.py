"""T0 dialects: agent tool registries.

An agent system's operation surface is its tools, and tools are declared either as a
decorator on the function that implements them or as a manifest the runtime reads.
The decorator form is traced like any other Python function, so T1 attributes its
effects. The manifest form declares only that a tool exists; what it needs is left
unresolved rather than guessed, and shows up as a review item.
"""

import ast
import json

from .base import Candidate, Dialect
from .common import collect_params, decorator_names, dedupe, humanize, slug

_TOOL_DECORATORS = ("tool", "function_tool", "register_tool")
_MANIFEST_HINTS = ("tool", "mcp", "manifest", "capabilities", "schema", "registry")


class ToolDecoratorDialect(Dialect):
    id = "agent-tools"
    description = "@tool decorators and function-tool registrations"
    repo_classes = ("agent-system", "library", "service-app", "unknown")

    def match(self, snapshot, profile):
        candidates = []
        order = 0
        for rel in snapshot.iter_files(suffixes=(".py",)):
            text = snapshot.read(rel)
            if not text or "tool" not in text:
                continue
            try:
                tree = ast.parse(text, filename=rel)
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                hit = next((n for n in decorator_names(node) if n in _TOOL_DECORATORS), None)
                if not hit:
                    continue
                candidates.append(
                    Candidate(
                        op_id=slug(f"tool-{node.name}"),
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
        return dedupe(candidates)


class ToolManifestDialect(Dialect):
    id = "tool-manifest"
    description = "JSON tool manifests (MCP and function-tool schemas)"
    repo_classes = ("agent-system", "library", "infra-config", "unknown")

    def match(self, snapshot, profile):
        candidates = []
        order = 0
        for rel in snapshot.iter_files(suffixes=(".json",)):
            name = rel.rsplit("/", 1)[-1].lower()
            if not any(hint in name for hint in _MANIFEST_HINTS):
                continue
            text = snapshot.read(rel)
            if not text or "tools" not in text:
                continue
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                continue
            for tool_name in _tool_names(data):
                candidates.append(
                    Candidate(
                        op_id=slug(f"tool-{tool_name}"),
                        name=humanize(tool_name),
                        dialect=self.id,
                        declaration_kind="tool-manifest",
                        loc=f"{rel}:tools.{tool_name}",
                        order_hint=order,
                        inputs=[],
                        detail=f"tool {tool_name} declared in a manifest",
                    )
                )
                order += 1
        return dedupe(candidates)


def _tool_names(data):
    """Names of tools declared in a manifest document, in sorted order."""
    tools = None
    if isinstance(data, dict) and isinstance(data.get("tools"), list):
        tools = data["tools"]
    elif isinstance(data, list):
        tools = data
    if not tools:
        return []
    names = set()
    for entry in tools:
        if isinstance(entry, dict):
            candidate = entry.get("name")
            if isinstance(candidate, str) and candidate.strip():
                names.add(candidate.strip())
    return sorted(names)


__all__ = ["ToolDecoratorDialect", "ToolManifestDialect"]
