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


def brace_body(
    text,
    offset,
    *,
    raw_ticks=False,
    lifetimes=False,
    raw_hashes=False,
    triple_quoted=False,
):
    """Line range of the balanced `{...}` block at or after `offset`, or None.

    A bounded scan, not a parser -- but a *careful* one. A naive brace counter mis-ranges
    on any real C-family source: a brace inside a string literal, a comment or a char
    literal unbalances the count and the body ends early or runs past the end of the
    function, which then smears T1's effect sites across the wrong operations. So strings,
    char literals and comments are skipped rather than counted.

    `raw_ticks`      Go raw strings, delimited by backticks and spanning lines.
    `raw_hashes`     Rust raw strings: r"...", r#"..."#, r##"..."## and br#"..."#.
    `lifetimes`      Rust lifetimes (`'a`, `'static`, `'_`) must not be mistaken for
                     char literals -- treating `'a` as an opening quote and scanning for
                     the closing one swallows the rest of the declaration.
    `triple_quoted`  Java text blocks and Kotlin raw strings (\"\"\"...\"\"\").

    The returned range starts on the line of `offset`, so callers pass the declaration's
    first line. When the block never closes the scan returns None: a wrong range is worse
    than none, because T1 attributes effect sites by containment.
    """
    index = offset
    depth = 0
    opened = False
    length = len(text)
    while index < length:
        char = text[index]
        if char == "/" and index + 1 < length:
            following = text[index + 1]
            if following == "/":
                newline = text.find("\n", index)
                if newline < 0:
                    return None
                index = newline + 1
                continue
            if following == "*":
                end = _skip_block_comment(text, index)
                if end is None:
                    return None
                index = end
                continue
        if raw_ticks and char == "`":
            end = text.find("`", index + 1)
            if end < 0:
                return None
            index = end + 1
            continue
        if triple_quoted and text.startswith('"""', index):
            end = text.find('"""', index + 3)
            if end < 0:
                return None
            index = end + 3
            continue
        if raw_hashes and char in "rb":
            end = _skip_rust_raw_string(text, index)
            if end is not None:
                index = end
                continue
        if char == '"':
            end = _skip_escaped(text, index, '"')
            if end is None:
                return None
            index = end
            continue
        if char == "'":
            end = _skip_single_quote(text, index, lifetimes)
            if end is None:
                return None
            index = end
            continue
        if char == "{":
            depth += 1
            opened = True
        elif char == "}" and opened:
            depth -= 1
            if depth == 0:
                start_line = text.count("\n", 0, offset) + 1
                end_line = text.count("\n", 0, index) + 1
                return (start_line, end_line)
        index += 1
    return None


def _skip_block_comment(text, index):
    """Index just past the `*/` that closes the block comment opening at `index`."""
    depth = 1
    cursor = index + 2
    length = len(text)
    while cursor < length:
        if text.startswith("/*", cursor):
            # Rust block comments nest; treating a non-nesting language as nesting is
            # harmless, because the inner `/*` would be a syntax error there.
            depth += 1
            cursor += 2
        elif text.startswith("*/", cursor):
            depth -= 1
            cursor += 2
            if depth == 0:
                return cursor
        else:
            cursor += 1
    return None


def _skip_escaped(text, index, quote):
    """Index just past the closing `quote` of a string opening at `index`."""
    cursor = index + 1
    length = len(text)
    while cursor < length:
        char = text[cursor]
        if char == "\\":
            cursor += 2
            continue
        if char == quote:
            return cursor + 1
        cursor += 1
    return None


def _skip_rust_raw_string(text, index):
    """Index just past a Rust raw string starting at `index`, or None if there is none."""
    length = len(text)
    start = index
    if text[start] == "b":
        if start + 1 >= length or text[start + 1] != "r":
            return None
        start += 1
    elif text[start] != "r":
        return None
    # A raw string's `r` is a word of its own; `error` or `my_r` must not be read as one.
    if index > 0 and (text[index - 1].isalnum() or text[index - 1] == "_"):
        return None
    cursor = start + 1
    hashes = 0
    while cursor < length and text[cursor] == "#":
        hashes += 1
        cursor += 1
    if cursor >= length or text[cursor] != '"':
        return None
    closing = '"' + "#" * hashes
    end = text.find(closing, cursor + 1)
    if end < 0:
        return None
    return end + len(closing)


def _skip_single_quote(text, index, lifetimes):
    """Index just past a char literal (or lifetime) opening at `index`."""
    length = len(text)
    cursor = index + 1
    if cursor >= length:
        return None
    if text[cursor] == "\\":
        # An escape: '\n', '\'', '\u{7F}'. Scan to the closing quote so the braces
        # inside a unicode escape are skipped rather than counted.
        cursor += 2
        while cursor < length:
            char = text[cursor]
            if char == "\\":
                cursor += 2
                continue
            if char == "'":
                return cursor + 1
            cursor += 1
        return None
    if lifetimes:
        if cursor + 1 < length and text[cursor + 1] == "'":
            return cursor + 2  # 'a'
        # A lifetime has no closing quote. Consume its name and carry on, instead of
        # scanning for a quote that does not exist.
        while cursor < length and (text[cursor].isalnum() or text[cursor] == "_"):
            cursor += 1
        return cursor
    while cursor < length:
        char = text[cursor]
        if char == "\\":
            cursor += 2
            continue
        if char == "'":
            return cursor + 1
        cursor += 1
    return None
