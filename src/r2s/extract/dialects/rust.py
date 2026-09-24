"""T0 dialect: Rust declaration sites.

Rust has no traceable code here -- T1 is stdlib `ast` over Python, and there is no
stdlib Rust parser -- so, exactly as for the infrastructure dialects, the declaration
*is* the operation and the capability is read off the declaration kind rather than
traced from a body. Where a declaration is attached to a function the candidate carries
a `body_range`, recovered by a brace scanner (`common.brace_body`) that knows about
Rust's raw strings, lifetimes and nested block comments. A naive counter mis-ranges on
`format!("{{}}")` and on `fn f<'a>(...)`, which would smear any future attribution across
the wrong operations.

Capabilities are declared only where the declaration kind genuinely implies one:

  clap command / bin / main   -> shell.exec   (running it is running a process, which is
                               the same call the Make and CI dialects make)
  HTTP route handler          -> nothing      (serving a request is not in the vocabulary;
                               an invented capability would be worse than the honest
                               external.call placeholder the pipeline adds)

The declaration sites recognised are clap's derive commands, axum/actix-web/rocket
routes, `fn main`, and Cargo.toml `[[bin]]` targets and `[workspace] members`.
"""

import re

from .base import Candidate, Dialect
from .common import brace_body, dedupe, humanize, slug

_SUFFIXES = (".rs",)

_DERIVE = re.compile(r"#\[\s*derive\s*\((?P<names>[^)]*)\)\s*\]")
_ITEM = re.compile(
    r"(?m)^[ \t]*(?:pub(?:\([^)]*\))?\s+)?(?P<kind>struct|enum)\s+(?P<name>[A-Za-z_]\w*)"
)
_COMMAND_ATTR = re.compile(r"#\[\s*command\s*\((?P<args>[^\]]*)\)\s*\]")
_COMMAND_NAME = re.compile(r"\bname\s*=\s*\"([^\"]*)\"")
_COMMAND_ABOUT = re.compile(r"\babout\s*=\s*\"([^\"]*)\"")

_ROUTE_ATTR = re.compile(
    r"#\[\s*(?P<method>get|post|put|patch|delete|head|options)\s*\(\s*"
    r"(?P<quote>[\"'])(?P<path>[^\"']*)(?P=quote)"
)
_FN_DECL = re.compile(
    r"(?m)^[ \t]*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?(?:const\s+)?(?:unsafe\s+)?"
    r"(?:extern\s+\"[^\"]*\"\s+)?fn\s+(?P<name>[A-Za-z_]\w*)"
)
_AXUM_ROUTE = re.compile(
    r"\.route\(\s*(?P<quote>[\"'])(?P<path>[^\"']+)(?P=quote)\s*,\s*(?P<handler>[^;\n]{0,300})"
)
_METHOD_CALL = re.compile(r"\b(get|post|put|patch|delete|head|options|trace)\s*\(")
_AXUM_SERVICE = re.compile(r"\.service\(\s*(?P<name>[A-Za-z_]\w*)\s*\)")
_MAIN_FN = re.compile(r"(?m)^[ \t]*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?fn\s+main\s*\(")
_ASYNC_MAIN = re.compile(r"#\[\s*tokio::main\s*\]")

_CARGO_BIN_HEADER = re.compile(r"(?m)^\[\[bin\]\]\s*$")
_CARGO_TABLE_HEADER = re.compile(r"(?m)^\[")
_CARGO_BIN_NAME = re.compile(r'(?m)^\s*name\s*=\s*"([^"]+)"')
_CARGO_WORKSPACE = re.compile(r"(?m)^\[workspace\]\s*$(?P<body>.*?)(?=^\[|\Z)", re.DOTALL)
_CARGO_MEMBERS = re.compile(r"members\s*=\s*\[(?P<items>[^\]]*)\]")


class RustDialect(Dialect):
    id = "rust"
    description = "Rust declaration sites: clap commands, HTTP routes, cargo targets"
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
        for rel in snapshot.find("Cargo.toml"):
            text = snapshot.read(rel)
            if not text:
                continue
            candidates.extend(_manifest_candidates(rel, text))
        unique = dedupe(candidates)
        for order, candidate in enumerate(unique):
            candidate.order_hint = order
        return unique


def _source_candidates(rel, text):
    found = []
    found.extend(_clap_candidates(rel, text))
    found.extend(_route_candidates(rel, text))
    found.extend(_entry_candidates(rel, text))
    found.sort(key=lambda item: item[0])
    return [candidate for _, candidate in found]


def _clap_candidates(rel, text):
    out = []
    for derive in _DERIVE.finditer(text):
        names = {name.strip() for name in derive.group("names").split(",")}
        item = _ITEM.search(text, derive.end())
        if item is None or item.start() - derive.end() > 400:
            continue
        kind = item.group("kind")
        item_name = item.group("name")
        if "Parser" in names and kind == "struct":
            label = _command_label(text, derive.start(), item.start()) or item_name
            out.append(
                (
                    derive.start(),
                    _candidate(
                        rel,
                        text,
                        derive.start(),
                        op_id=slug(label),
                        name=humanize(_words(label)),
                        kind="clap-parser",
                        detail=f"clap Parser {item_name}",
                        capabilities=["shell.exec"],
                        effect_hint="effectful-local",
                    ),
                )
            )
        elif "Subcommand" in names and kind == "enum":
            span = brace_body(text, item.start(), raw_hashes=True, lifetimes=True)
            if span is None:
                continue
            for variant, about, override, lineno in _enum_variants(text, *span):
                label = override or variant
                position = _index_of_line(text, lineno)
                out.append(
                    (
                        position,
                        _candidate(
                            rel,
                            text,
                            position,
                            op_id=slug(label),
                            name=humanize(_words(label)),
                            kind="clap-subcommand",
                            detail=_detail("clap subcommand", label, about),
                            capabilities=["shell.exec"],
                            effect_hint="effectful-local",
                        ),
                    )
                )
    return out


def _route_candidates(rel, text):
    out = []
    for match in _ROUTE_ATTR.finditer(text):
        handler = _FN_DECL.search(text, match.end())
        if handler is None or handler.start() - match.end() > 2000:
            continue
        path = match.group("path") or "/"
        method = match.group("method").upper()
        out.append(
            (
                match.start(),
                _candidate(
                    rel,
                    text,
                    match.start(),
                    op_id=slug(f"{method}-{path}") or slug(handler.group("name")),
                    name=f"{method} {path}",
                    kind="route-attribute",
                    detail=f"{method} route {path} handled by {handler.group('name')}()",
                    body_range=brace_body(text, handler.start(), raw_hashes=True, lifetimes=True),
                ),
            )
        )
    for match in _AXUM_ROUTE.finditer(text):
        path = match.group("path")
        methods = sorted(
            {m.group(1).upper() for m in _METHOD_CALL.finditer(match.group("handler"))}
        )
        for method in methods:
            out.append(
                (
                    match.start(),
                    _candidate(
                        rel,
                        text,
                        match.start(),
                        op_id=slug(f"{method}-{path}"),
                        name=f"{method} {path}",
                        kind="axum-route",
                        detail=f"{method} route {path}",
                    ),
                )
            )
    for match in _AXUM_SERVICE.finditer(text):
        name = match.group("name")
        out.append(
            (
                match.start(),
                _candidate(
                    rel,
                    text,
                    match.start(),
                    op_id=slug(f"service-{name}"),
                    name=humanize(_words(name)),
                    kind="axum-service",
                    detail=f"axum service {name}",
                ),
            )
        )
    return out


def _entry_candidates(rel, text):
    out = []
    for match in _MAIN_FN.finditer(text):
        is_async = _ASYNC_MAIN.search(text, max(0, match.start() - 200), match.start()) is not None
        out.append(
            (
                match.start(),
                _candidate(
                    rel,
                    text,
                    match.start(),
                    op_id=_main_id(rel),
                    name="Main",
                    kind="rust-async-main" if is_async else "rust-main",
                    detail="async main entry point" if is_async else "main entry point",
                    capabilities=["shell.exec"],
                    effect_hint="effectful-local",
                    body_range=brace_body(text, match.start(), raw_hashes=True, lifetimes=True),
                ),
            )
        )
    return out


def _main_id(rel):
    """A per-crate id for `fn main`.

    A workspace has one `main` per binary, so a bare "main" would collapse them all into
    a single candidate. The crate directory disambiguates: `crates/audit/src/main.rs`
    becomes `audit-main`, while a root `src/main.rs` stays `main`.
    """
    parts = rel.split("/")
    stem = parts[-1].removesuffix(".rs")
    if stem != "main":
        return slug(f"main-{stem}")
    if "src" in parts:
        index = parts.index("src")
        if index > 0:
            return slug(f"{parts[index - 1]}-main")
    return "main"


def _manifest_candidates(rel, text):
    out = []
    for name in _cargo_bins(text):
        out.append(
            _candidate(
                rel,
                text,
                0,
                op_id=slug(f"bin-{name}"),
                name=humanize(_words(name)),
                kind="cargo-bin",
                detail=f"Cargo bin target {name}",
                capabilities=["shell.exec"],
                effect_hint="effectful-local",
            )
        )
    for member in _cargo_workspace_members(text):
        leaf = member.rstrip("/").rsplit("/", 1)[-1]
        out.append(
            _candidate(
                rel,
                text,
                0,
                op_id=slug(f"member-{leaf}"),
                name=humanize(_words(leaf)),
                kind="cargo-workspace-member",
                detail=f"Cargo workspace member {member}",
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
    return Candidate(
        op_id=op_id,
        name=name,
        dialect="rust",
        declaration_kind=kind,
        loc=f"{rel}:{_line_of(text, offset)}",
        inputs=[],
        detail=detail,
        capabilities=list(capabilities or []),
        effect_hint=effect_hint,
        body_range=body_range,
    )


def _detail(kind, label, about):
    return f"{kind} {label} — {about}" if about else f"{kind} {label}"


def _command_label(text, floor, item_start):
    """The `name = "..."` of the `#[command(...)]` attached to an item, if any."""
    for start, end in ((floor, item_start), (max(0, floor - 400), floor)):
        matches = list(_COMMAND_ATTR.finditer(text, start, end))
        if matches:
            name = _COMMAND_NAME.search(matches[-1].group("args"))
            return name.group(1) if name else None
    return None


def _enum_variants(text, start_line, end_line):
    """(variant, about, name_override, lineno) for the top-level variants of an enum."""
    lines = text.splitlines()
    body = lines[start_line - 1 : end_line]
    out = []
    pending_name = None
    pending_about = None
    for offset, raw_line in enumerate(body):
        line = raw_line.strip()
        if not line or line.startswith("//"):
            continue
        while line.startswith("#["):
            close = line.find("]")
            if close < 0:
                break
            attribute = line[: close + 1]
            if attribute.startswith("#[command"):
                name = _COMMAND_NAME.search(attribute)
                about = _COMMAND_ABOUT.search(attribute)
                pending_name = (name.group(1) if name else None) or pending_name
                pending_about = (about.group(1) if about else None) or pending_about
            line = line[close + 1 :].strip()
        if not line:
            continue
        match = re.match(r"(?P<name>[A-Za-z_]\w*)\s*(?:[({,]|$)", line)
        if not match or not match.group("name")[:1].isupper():
            continue
        out.append((match.group("name"), pending_about, pending_name, start_line + offset))
        pending_name = None
        pending_about = None
    return out


def _cargo_bins(text):
    headers = list(_CARGO_BIN_HEADER.finditer(text))
    out = []
    for index, header in enumerate(headers):
        end = headers[index + 1].start() if index + 1 < len(headers) else len(text)
        body = text[header.end() : end]
        stop = _CARGO_TABLE_HEADER.search(body)
        if stop:
            body = body[: stop.start()]
        match = _CARGO_BIN_NAME.search(body)
        if match:
            out.append(match.group(1))
    return out


def _cargo_workspace_members(text):
    section = _CARGO_WORKSPACE.search(text)
    if section is None:
        return []
    members = _CARGO_MEMBERS.search(section.group("body"))
    if members is None:
        return []
    return re.findall(r'"([^"]+)"', members.group("items"))


def _line_of(text, offset):
    return text.count("\n", 0, offset) + 1


def _index_of_line(text, lineno):
    lines = text.splitlines(keepends=True)
    return sum(len(line) for line in lines[: lineno - 1])


def _words(name):
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name)


__all__ = ["RustDialect"]
