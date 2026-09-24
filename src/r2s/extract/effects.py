"""T1: effect tracing.

From each entrypoint, walk the call graph only until an effect site, then stop. Do
not build a whole-program call graph -- it is expensive, fragile across languages,
and unnecessary, because an effect site is recognisable from a stable pattern set
regardless of what the repo is.

Effects map to capabilities through catalog/effects.json. That mapping is
repo-independent, which is the property that makes general extraction possible:
we recognise dependencies and call sites, not structure.

Honest limit: tracing is high-fidelity for Python via stdlib ast. Other languages
fall back to T0 declaration coverage plus T2 catalog detection, so they get *an*
inventory with weaker requirement attribution.
"""

import ast

from .. import config
from ..util.io import load_json


class EffectSite:
    def __init__(self, capability, effect, loc, detail, pattern_id):
        self.capability = capability
        self.effect = effect
        self.loc = loc
        self.detail = detail
        self.pattern_id = pattern_id

    def evidence(self):
        return {"source": "traced", "loc": self.loc, "detail": self.detail}

    def __repr__(self):
        return f"<EffectSite {self.capability} {self.loc}>"


class EffectCatalog:
    def __init__(self, data):
        self.patterns = data["patterns"]
        self.plumbing = set((data.get("plumbing") or {}).get("func", []))

    @classmethod
    def load(cls, path=None):
        return cls(load_json(path or config.EFFECTS_FILE))


# ---------------------------------------------------------------- matching


def _dotted_name(node):
    """Resolve an attribute chain to 'a.b.c', or None if not statically resolvable."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def _literal_mode(node):
    """Extract a file-open mode from the second positional arg or the mode= kwarg."""
    if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
        value = node.args[1].value
        return value if isinstance(value, str) else None
    for kw in node.keywords:
        if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
            value = kw.value.value
            return value if isinstance(value, str) else None
    return None


def _literal_verb(node):
    """Extract an HTTP method from a method= kwarg, where present."""
    for kw in node.keywords:
        if kw.arg in ("method", "verb") and isinstance(kw.value, ast.Constant):
            value = kw.value.value
            return value.upper() if isinstance(value, str) else None
    return None


_READ_STRING_PREFIXES = ("select", "with", "show", "describe", "explain", "pragma")
_READ_CALL_NAMES = ("select", "text", "query")


def _arg0_looks_like_a_read(node):
    """True when the first positional argument is recognisably a read.

    `session.execute(...)` is genuinely ambiguous: it runs `select` just as happily as
    `delete`. Guessing "read" would be fail-*open* -- a DELETE would be reported as
    read-only and lose its gate, which is the exact failure this project exists to
    prevent. Guessing "write" over-requires and blocks legitimate reads.

    So we look at the argument. `execute("select 1")` and `execute(select(Entry))` are
    reads and are recognised; anything else is left unmatched and surfaces for review,
    which is the honest outcome for an ambiguous call.
    """
    if not node.args:
        return False
    first = node.args[0]

    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return first.value.strip().lower().startswith(_READ_STRING_PREFIXES)

    if isinstance(first, ast.Call):
        dotted = _dotted_name(first.func)
        if dotted and dotted.split(".")[-1] in _READ_CALL_NAMES:
            return True

    return False


def match_call(node, catalog):
    """Return the first matching pattern result, else None."""
    dotted = _dotted_name(node.func)
    if dotted is None:
        return None
    parts = dotted.split(".")
    last = parts[-1]
    prefix = ".".join(parts[:-1])

    if last in catalog.plumbing and prefix == "":
        return None

    mode = _literal_mode(node) if last == "open" else None
    verb = _literal_verb(node)

    for pattern in catalog.patterns:
        m = pattern["match"]
        funcs = m.get("func", [])
        modules = m.get("module", [])

        if funcs and last not in funcs and dotted not in funcs:
            continue
        if modules and not any(prefix == mod or prefix.startswith(mod + ".") for mod in modules):
            continue
        if not funcs and not modules:
            continue

        if "mode_in" in m and (mode is None or mode not in m["mode_in"]):
            continue
        # An absent mode means the default, which is read.
        if "mode_not_in" in m and mode is not None and mode in m["mode_not_in"]:
            continue
        if m.get("mode_absent") and mode is not None:
            continue
        if "verb_in" in m and (verb is None or verb not in m["verb_in"]):
            continue
        if m.get("arg0_read") and not _arg0_looks_like_a_read(node):
            continue

        return pattern
    return None


# ---------------------------------------------------------------- tracing


class _FileIndex:
    """Function definitions and their call sites, for one module."""

    def __init__(self, tree):
        self.tree = tree
        self.functions = {}
        self.module_calls = []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.functions[node.name] = node
            else:
                self.module_calls.extend(n for n in ast.walk(node) if isinstance(n, ast.Call))

    def calls_in(self, func_node):
        return [n for n in ast.walk(func_node) if isinstance(n, ast.Call)]


def _called_locals(calls):
    names = set()
    for call in calls:
        dotted = _dotted_name(call.func)
        if dotted and "." not in dotted:
            names.add(dotted)
    return names


def trace_file(rel, text, catalog, max_depth=3):
    """Collect effect sites reachable from this file's entry surface."""
    try:
        tree = ast.parse(text, filename=rel)
    except SyntaxError:
        return []

    index = _FileIndex(tree)
    sites = []
    seen_funcs = set()

    def walk(calls, depth, origin):
        for call in calls:
            pattern = match_call(call, catalog)
            if pattern:
                sites.append(
                    EffectSite(
                        capability=pattern["capability"],
                        effect=pattern["effect"],
                        loc=f"{rel}:{call.lineno}",
                        detail=f"{_dotted_name(call.func)} matched {pattern['id']} (via {origin})",
                        pattern_id=pattern["id"],
                    )
                )
                continue  # stop at the effect site; do not descend into it

            dotted = _dotted_name(call.func)
            if (
                dotted
                and "." not in dotted
                and depth < max_depth
                and dotted in index.functions
                and dotted not in seen_funcs
            ):
                seen_funcs.add(dotted)
                walk(index.calls_in(index.functions[dotted]), depth + 1, dotted)

    walk(index.module_calls, 0, "module")

    # If nothing was reachable from module level, fall back to scanning every
    # top-level function. Looser, but a repo whose entry surface is only callable
    # from outside would otherwise trace to nothing at all.
    if not sites and index.functions:
        for name, node in sorted(index.functions.items()):
            walk(index.calls_in(node), 1, name)

    return sites


def trace(snapshot, entrypoints, catalog, max_files=200):
    """Trace effect sites across the repo's Python surface, entrypoints first."""
    by_loc = {}
    files = []

    for ep in entrypoints:
        if ep.loc.endswith(".py") and snapshot.exists(ep.loc):
            files.append(ep.loc)

    for rel in snapshot.iter_files(suffixes=(".py",)):
        if rel not in files:
            files.append(rel)
        if len(files) >= max_files:
            break

    for rel in files:
        text = snapshot.read(rel)
        if not text:
            continue
        for site in trace_file(rel, text, catalog):
            by_loc.setdefault(site.loc, site)

    return [by_loc[k] for k in sorted(by_loc)]
