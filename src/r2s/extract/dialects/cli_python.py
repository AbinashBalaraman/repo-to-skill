"""T0 dialect: Python CLI declarations.

Covers click/typer command decorators and argparse subparsers. Chosen as the first
dialect because CLI declarations are the cleanest, most explicit operation surface in
any ecosystem, and because `cli-tool` is the class with the least ambiguity.

Candidates carry a `body_range` where the declaration is attached to a function, so
that T1 effect sites can be attributed to the right operation by line containment.
Without that, effects would have to be smeared across every operation in the file.
"""

import ast

from ...util.io import slugify
from .base import Candidate, Dialect

_COMMAND_DECORATORS = ("command", "task", "job", "flow")
# Group/container decorators. A click group is a container for commands, not an
# operation in its own right, so a function decorated with one is skipped.
_GROUP_DECORATORS = ("group", "cli", "app", "add_typer")


class CliPythonDialect(Dialect):
    id = "cli-python"
    description = "Python CLI subcommands (click, typer, argparse)"
    repo_classes = ("cli-tool", "library", "agent-system", "service-app", "unknown")

    def match(self, snapshot, profile):
        candidates = []
        order = 0
        for rel in snapshot.iter_files(suffixes=(".py",)):
            text = snapshot.read(rel)
            if not text:
                continue
            if not any(tok in text for tok in ("add_parser", "add_argument", "@", "command")):
                continue
            try:
                tree = ast.parse(text, filename=rel)
            except SyntaxError:
                continue

            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    hit = self._from_decorators(node)
                    if hit:
                        name, kind, detail = hit
                        candidates.append(
                            Candidate(
                                op_id=slugify(name),
                                name=_humanize(name),
                                dialect=self.id,
                                declaration_kind=kind,
                                loc=f"{rel}:{node.lineno}",
                                order_hint=order,
                                inputs=_collect_params(node),
                                detail=detail,
                            )
                        )
                        candidates[-1].body_range = (
                            node.lineno,
                            getattr(node, "end_lineno", node.lineno),
                        )
                        order += 1

            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "add_parser"
                    and (arg := _first_str(node))
                ):
                    candidates.append(
                        Candidate(
                            op_id=slugify(arg),
                            name=_humanize(arg),
                            dialect=self.id,
                            declaration_kind="argparse-subparser",
                            loc=f"{rel}:{node.lineno}",
                            order_hint=order,
                            inputs=_collect_string_args(node),
                            detail="argparse subparser",
                        )
                    )
                    order += 1

        return _dedupe(candidates)

    @staticmethod
    def _from_decorators(func_node):
        decorator_names = set()
        for decorator in func_node.decorator_list:
            target = decorator.func if isinstance(decorator, ast.Call) else decorator
            if isinstance(target, ast.Attribute):
                decorator_names.add(target.attr)
            elif isinstance(target, ast.Name):
                decorator_names.add(target.id)

        if decorator_names & set(_GROUP_DECORATORS):
            return None

        for decorator in func_node.decorator_list:
            target = decorator
            if isinstance(target, ast.Call):
                arg = _first_str(target)
                name = arg or func_node.name
                attr = target.func
                attr_name = (
                    attr.attr
                    if isinstance(attr, ast.Attribute)
                    else (attr.id if isinstance(attr, ast.Name) else "")
                )
                if attr_name in _COMMAND_DECORATORS:
                    return name, f"{attr_name}-decorator", f"@{attr_name} on {func_node.name}()"
            elif isinstance(target, ast.Attribute) and target.attr in _COMMAND_DECORATORS:
                return (
                    func_node.name,
                    f"{target.attr}-decorator",
                    f"@{target.attr} on {func_node.name}()",
                )
        return None


def _first_str(node):
    for arg in node.args:
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            return arg.value
    return None


def _collect_string_args(node):
    return [a.value for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]


def _collect_params(func_node):
    args = func_node.args
    names = [a.arg for a in list(args.posonlyargs) + list(args.args) + list(args.kwonlyargs)]
    return [n for n in names if n not in ("self", "cls")]


def _humanize(name):
    return name.replace("-", " ").replace("_", " ").strip().capitalize() or "Operation"


def _dedupe(candidates):
    seen = set()
    out = []
    for candidate in candidates:
        if not candidate.op_id or candidate.op_id in seen:
            continue
        seen.add(candidate.op_id)
        out.append(candidate)
    return out
