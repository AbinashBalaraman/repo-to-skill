"""Shared helpers for the T0 declaration dialects.

Every dialect returns `Candidate` objects in a deterministic order, and every dialect
has to read some structured-but-not-quite-config format. These helpers keep both
contracts in one place: slugging, humanising, de-duplication, and small
indentation-aware scans for YAML-ish and Make-ish files.

There is deliberately no parser dependency here. The project is stdlib-only, so the
scans below are line-oriented and conservative: when a file is ambiguous they return
nothing rather than guessing. A missed declaration is visible in the diagnostics; a
fabricated one is not.
"""

import ast
import re

from ...util.io import slugify

_KEY_LINE = re.compile(r"^(?P<indent>[ \t]*)(?P<key>[^\s:#][^:#]*?)\s*:(?P<rest>.*)$")


def slug(value):
    return slugify(value)


def humanize(name):
    return name.replace("-", " ").replace("_", " ").strip().capitalize() or "Operation"


def dedupe(candidates):
    """Drop candidates whose op_id was already seen, keeping first occurrence."""
    seen = set()
    out = []
    for candidate in candidates:
        if not candidate.op_id or candidate.op_id in seen:
            continue
        seen.add(candidate.op_id)
        out.append(candidate)
    return out


def _indent_of(line):
    return len(line) - len(line.lstrip(" \t"))


def block_children(text, section, min_indent=0):
    """Yield (key, lineno) for the direct children of a top-level `section:` block.

    Only the immediate children are returned: `jobs:` yields job names, not the steps
    inside them. Comments and blank lines are skipped. A section that never appears
    yields nothing, which is the correct outcome for a file of a different shape.
    """
    lines = text.splitlines()
    out = []
    for index, line in enumerate(lines):
        match = _KEY_LINE.match(line)
        if not match or _indent_of(line) != min_indent:
            continue
        if match.group("key").strip().strip("\"'") != section:
            continue
        child_indent = None
        for offset in range(index + 1, len(lines)):
            child = lines[offset]
            stripped = child.strip()
            if not stripped or stripped.startswith("#"):
                continue
            child_match = _KEY_LINE.match(child)
            if not child_match:
                # A bare list item or scalar inside the block ends the key scan.
                if _indent_of(child) <= min_indent:
                    break
                continue
            indent = _indent_of(child)
            if indent <= min_indent:
                break
            if child_indent is None:
                child_indent = indent
            if indent == child_indent:
                key = child_match.group("key").strip().strip("\"'")
                if key:
                    out.append((key, offset + 1))
        break
    return out


def top_level_scalar(text, key):
    """Return the scalar value of a top-level `key: value` line, or None."""
    for line in text.splitlines():
        if _indent_of(line) != 0:
            continue
        match = _KEY_LINE.match(line)
        if match and match.group("key").strip().strip("\"'") == key:
            value = match.group("rest").strip()
            if value and not value.startswith("#"):
                return value.strip("\"'")
            return ""
    return None


def has_top_level_key(text, key):
    return top_level_scalar(text, key) is not None


def first_str(node):
    """First string literal positional argument of a call node, or None."""
    if not isinstance(node, ast.Call):
        return None
    for arg in node.args:
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            return arg.value
    return None


def collect_params(func_node):
    """Parameter names of a function definition, minus the implicit receiver."""
    args = func_node.args
    names = [a.arg for a in list(args.posonlyargs) + list(args.args) + list(args.kwonlyargs)]
    return [n for n in names if n not in ("self", "cls")]


def decorator_names(node):
    """Every decorator on a function, resolved to its final attribute or name."""
    names = []
    for decorator in getattr(node, "decorator_list", []):
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        if isinstance(target, ast.Attribute):
            names.append(target.attr)
        elif isinstance(target, ast.Name):
            names.append(target.id)
    return names
