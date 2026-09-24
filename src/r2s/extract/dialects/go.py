"""T0 dialect: Go declaration sites.

Go is not effect-traced (T1 is stdlib `ast` over Python), so the declaration is the
operation and the capability is read off the declaration kind. A `func main`, a cobra
command and an exported command function all declare `shell.exec` -- running one is
running a process, which is the same call the Make and CI dialects make. An HTTP route
handler declares nothing: serving a request is not in the capability vocabulary, and an
invented ID would hard-fail the validator.

`body_range` is recovered by `common.brace_body`, which skips backtick raw strings and
comments so a `{` inside either cannot end the range early.

The declaration sites recognised are `func main`, exported top-level command functions,
cobra commands, `net/http` handlers and gin/echo/chi/fiber routes. The go.mod module path
and its `require` block are read by `profiler.manifests.read_go_deps`, which feeds T2 --
a module requirement is not an operation, so this dialect does not invent one.
"""

import re

from .base import Candidate, Dialect
from .common import brace_body, dedupe, humanize, slug

_SUFFIXES = (".go",)

_FUNC = re.compile(
    r"(?m)^func\s+(?P<name>[A-Za-z_]\w*)\s*(?:\[[^\]]*\])?\s*"
    r"\((?P<params>[^)]*)\)\s*(?P<ret>[^{]{0,120})"
)
_COBRA_LITERAL = re.compile(r"&cobra\.Command\s*\{")
_COBRA_USE = re.compile(r"\bUse\s*:\s*\"([^\"]*)\"")
_COBRA_SHORT = re.compile(r"\bShort\s*:\s*\"([^\"]*)\"")

_HTTP_HANDLE = re.compile(r"(?P<obj>[A-Za-z_]\w*)\.Handle(?:Func)?\(\s*\"(?P<path>[^\"]*)\"")
_ROUTER_METHOD = re.compile(
    r"(?P<obj>[A-Za-z_]\w*)\.(?P<method>"
    r"GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS|TRACE|CONNECT|Any|"
    r"Get|Post|Put|Patch|Delete|Head|Options|All"
    r")\s*\(\s*\"(?P<path>[^\"]+)\""
)
# Only these receivers are treated as routers. `client.Get("/x")` is an HTTP call, not a
# route, and requiring a recognised receiver is what keeps it out -- the same guard the
# JS web dialect uses.
_ROUTE_OBJECTS = frozenset(
    {
        "r",
        "router",
        "e",
        "app",
        "api",
        "g",
        "group",
        "mux",
        "srv",
        "server",
        "engine",
        "fiber",
        "public",
        "admin",
        "v1",
        "routes",
    }
)


class GoDialect(Dialect):
    id = "go"
    description = "Go declaration sites: main, cobra commands, net/http and router handlers"
    repo_classes = (
        "cli-tool",
        "service-app",
        "library",
        "infra-config",
        "agent-system",
        "unknown",
    )

    def match(self, snapshot, profile):
        candidates = []
        for rel in snapshot.iter_files(suffixes=_SUFFIXES):
            text = snapshot.read(rel)
            if not text:
                continue
            candidates.extend(_source_candidates(rel, text))
        unique = dedupe(candidates)
        for order, candidate in enumerate(unique):
            candidate.order_hint = order
        return unique


def _source_candidates(rel, text):
    found = []
    found.extend(_func_candidates(rel, text))
    found.extend(_cobra_candidates(rel, text))
    found.extend(_route_candidates(rel, text))
    found.sort(key=lambda item: item[0])
    return [candidate for _, candidate in found]


def _func_candidates(rel, text):
    out = []
    for match in _FUNC.finditer(text):
        name = match.group("name")
        params = match.group("params")
        ret = match.group("ret")
        if name == "main":
            kind, detail = "go-main", "main entry point"
        elif _looks_like_command(name, params, ret):
            kind, detail = "go-func", f"exported command function {name}()"
        else:
            continue
        out.append(
            (
                match.start(),
                _candidate(
                    rel,
                    text,
                    match.start(),
                    op_id=slug(name),
                    name=humanize(name),
                    kind=kind,
                    detail=detail,
                    capabilities=["shell.exec"],
                    effect_hint="effectful-local",
                    body_range=brace_body(text, match.start(), raw_ticks=True),
                ),
            )
        )
    return out


def _looks_like_command(name, params, ret):
    """A top-level exported function that reads as a command, not a library helper.

    A command is exported, does not hand back a cobra command (that is the constructor
    for a command declared elsewhere), and either takes no arguments or takes the cobra
    `(cmd, args)` pair. Anything else is a library function, and inventing an operation
    for it would be noise rather than coverage.
    """
    if not name[:1].isupper():
        return False
    if "*cobra.Command" in ret:
        return False
    stripped = params.strip()
    if not stripped:
        return True
    return stripped.startswith("cmd *cobra.Command")


def _cobra_candidates(rel, text):
    out = []
    for match in _COBRA_LITERAL.finditer(text):
        span = brace_body(text, match.start(), raw_ticks=True)
        if span is None:
            continue
        body = "\n".join(text.splitlines()[span[0] - 1 : span[1]])
        use = _COBRA_USE.search(body)
        if use is None:
            continue
        label = use.group(1).split()[0] if use.group(1).split() else ""
        if not label:
            continue
        short = _COBRA_SHORT.search(body)
        detail = f"cobra command {label}"
        if short:
            detail = f"{detail} — {short.group(1)}"
        out.append(
            (
                match.start(),
                _candidate(
                    rel,
                    text,
                    match.start(),
                    op_id=slug(label),
                    name=humanize(label),
                    kind="cobra-command",
                    detail=detail,
                    capabilities=["shell.exec"],
                    effect_hint="effectful-local",
                    body_range=span,
                ),
            )
        )
    return out


def _route_candidates(rel, text):
    out = []
    for match in _HTTP_HANDLE.finditer(text):
        path = match.group("path")
        out.append(
            (
                match.start(),
                _candidate(
                    rel,
                    text,
                    match.start(),
                    op_id=slug(f"http-{path}") or slug(f"http-{match.group('obj')}"),
                    name=f"HTTP {path}",
                    kind="http-handle",
                    detail=f"net/http handler for {path or '/'}",
                ),
            )
        )
    for match in _ROUTER_METHOD.finditer(text):
        if match.group("obj") not in _ROUTE_OBJECTS:
            continue
        path = match.group("path")
        if not path.startswith("/"):
            continue
        method = match.group("method").upper()
        out.append(
            (
                match.start(),
                _candidate(
                    rel,
                    text,
                    match.start(),
                    op_id=slug(f"{method}-{path}"),
                    name=f"{method} {path}",
                    kind="router-route",
                    detail=f"{method} route {path}",
                ),
            )
        )
    return out


def _candidate(
    rel,
    text,
    offset,
    *,
    op_id,
    name,
    kind,
    detail,
    capabilities=None,
    effect_hint=None,
    body_range=None,
):
    lineno = text.count("\n", 0, offset) + 1
    return Candidate(
        op_id=op_id,
        name=name,
        dialect="go",
        declaration_kind=kind,
        loc=f"{rel}:{lineno}",
        inputs=[],
        detail=detail,
        capabilities=list(capabilities or []),
        effect_hint=effect_hint,
        body_range=body_range,
    )


__all__ = ["GoDialect"]
