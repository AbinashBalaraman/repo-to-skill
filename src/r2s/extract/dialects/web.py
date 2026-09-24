"""T0 dialects: web and RPC declaration sites.

A service's operations are its routes. FastAPI/Flask/Django declare them with
decorators or urlconf calls, Express/Fastify/Nest with method chains or decorators, and
an OpenAPI/Swagger document or a `.proto` file declares them as data. All four are the
same operation shape: a named, addressable unit of work with an attached body.

Where a declaration is attached to a function the candidate carries a `body_range`, so
T1 can attribute effect sites to the right route by line containment rather than by
position. For a JS/TS handler the range is recovered by a small brace scanner -- not a
parser, but enough to bound the handler. An OpenAPI path or a Django urlconf entry has
no body in this repository, so it carries none and its attribution is flagged weak.
"""

import ast
import json
import re

from .base import Candidate, Dialect
from .common import collect_params, dedupe, first_str, has_top_level_key, humanize, slug

_ROUTE_METHODS = (
    "get",
    "post",
    "put",
    "patch",
    "delete",
    "head",
    "options",
    "trace",
    "route",
    "websocket",
)
# Only these objects are treated as routers. `@client.get(...)` in a test file is not a
# route declaration, and requiring a recognised receiver is what keeps it out.
_ROUTE_OBJECTS = frozenset(
    {
        "app",
        "application",
        "router",
        "routes",
        "api",
        "bp",
        "blueprint",
        "server",
        "web",
        "public",
        "admin",
        "fastapi",
        "flask",
    }
)

_JS_ROUTE = re.compile(
    r"\b(?P<obj>[A-Za-z_$][\w$]*)\s*\.\s*"
    r"(?P<method>get|post|put|patch|delete|head|options|all)\s*\(\s*"
    r"(?P<quote>['\"`])(?P<path>[^'\"`]+)(?P=quote)"
)
_NEST_ROUTE = re.compile(
    r"@(?P<method>Get|Post|Put|Patch|Delete|Head|Options|All)\s*\(\s*"
    r"(?:(?P<quote>['\"`])(?P<path>[^'\"`]*)(?P=quote))?"
)

_JS_SUFFIXES = (".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx")
_SPEC_NAMES = (
    "openapi.json",
    "openapi.yaml",
    "openapi.yml",
    "swagger.json",
    "swagger.yaml",
    "swagger.yml",
)
_HTTP_METHODS = ("get", "post", "put", "patch", "delete", "head", "options", "trace")


class WebPythonDialect(Dialect):
    id = "web-python"
    description = "FastAPI/Flask/Django route declarations"
    repo_classes = ("service-app", "library", "unknown", "agent-system")

    def match(self, snapshot, profile):
        candidates = []
        order = 0
        for rel in snapshot.iter_files(suffixes=(".py",)):
            text = snapshot.read(rel)
            if not text or not any(token in text for token in ("@", "path(", "re_path(", "url(")):
                continue
            try:
                tree = ast.parse(text, filename=rel)
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    hit = _route_from_decorators(node)
                    if hit:
                        path, method = hit
                        candidates.append(
                            Candidate(
                                op_id=slug(f"{method}-{path}") or slug(node.name),
                                name=f"{method} {path}",
                                dialect=self.id,
                                declaration_kind="route-decorator",
                                loc=f"{rel}:{node.lineno}",
                                order_hint=order,
                                inputs=collect_params(node),
                                detail=f"{method} route {path} handled by {node.name}()",
                                body_range=(node.lineno, getattr(node, "end_lineno", node.lineno)),
                            )
                        )
                        order += 1
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and (arg := _urlconf_target(node)):
                    view, name = arg
                    candidates.append(
                        Candidate(
                            op_id=slug(f"url-{name or view}"),
                            name=humanize(name or view),
                            dialect=self.id,
                            declaration_kind="django-urlconf",
                            loc=f"{rel}:{node.lineno}",
                            order_hint=order,
                            inputs=[],
                            detail=f"Django urlconf entry for {view}",
                        )
                    )
                    order += 1
        return dedupe(candidates)


def _route_from_decorators(node):
    """Return (path, METHOD) for a route decorator, else None."""
    for decorator in node.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        if not isinstance(target, ast.Attribute) or target.attr not in _ROUTE_METHODS:
            continue
        receiver = target.value
        receiver_name = receiver.id if isinstance(receiver, ast.Name) else None
        if receiver_name is not None and receiver_name not in _ROUTE_OBJECTS:
            continue
        path = first_str(decorator) or "/"
        method = target.attr.upper()
        if target.attr == "route":
            method = _route_method_kwarg(decorator) or "ANY"
        return path, method
    return None


def _route_method_kwarg(decorator):
    if not isinstance(decorator, ast.Call):
        return None
    for keyword in decorator.keywords:
        if keyword.arg == "methods" and isinstance(keyword.value, (ast.List, ast.Tuple)):
            names = [
                element.value.upper()
                for element in keyword.value.elts
                if isinstance(element, ast.Constant) and isinstance(element.value, str)
            ]
            if names:
                return "|".join(sorted(names))
    return None


def _urlconf_target(node):
    """Return (view_name, url_name) for a Django path()/re_path()/url() call."""
    if not isinstance(node.func, ast.Name) or node.func.id not in ("path", "re_path", "url"):
        return None
    if not node.args or not isinstance(node.args[0], ast.Constant):
        return None
    if not isinstance(node.args[0].value, str):
        return None
    view = None
    if len(node.args) > 1:
        second = node.args[1]
        if isinstance(second, ast.Name):
            view = second.id
        elif isinstance(second, ast.Attribute):
            view = second.attr
        elif isinstance(second, ast.Call) and isinstance(second.func, ast.Attribute):
            view = second.func.attr
    if not view or view in ("include", "static"):
        return None
    name = None
    for keyword in node.keywords:
        if (
            keyword.arg == "name"
            and isinstance(keyword.value, ast.Constant)
            and isinstance(keyword.value.value, str)
        ):
            name = keyword.value.value
    return view, name


class WebJsDialect(Dialect):
    id = "web-js"
    description = "Express/Fastify/Nest route declarations"
    repo_classes = ("service-app", "library", "unknown")

    def match(self, snapshot, profile):
        candidates = []
        order = 0
        for rel in snapshot.iter_files(suffixes=_JS_SUFFIXES):
            text = snapshot.read(rel)
            if not text:
                continue
            if not any(token in text for token in (".get(", ".post(", "@Get", "@Post", "@Put")):
                continue
            for match in _JS_ROUTE.finditer(text):
                if match.group("obj") not in _ROUTE_OBJECTS:
                    continue
                path = match.group("path")
                method = match.group("method").upper()
                candidates.append(
                    _js_candidate(
                        self.id, rel, text, match.end(), method, path, "route-call", order
                    )
                )
                order += 1
            for match in _NEST_ROUTE.finditer(text):
                path = match.group("path") or "/"
                method = match.group("method").upper()
                candidates.append(
                    _js_candidate(
                        self.id, rel, text, match.end(), method, path, "nest-decorator", order
                    )
                )
                order += 1
        return dedupe(candidates)


def _js_candidate(dialect_id, rel, text, offset, method, path, kind, order):
    start_line = text.count("\n", 0, offset) + 1
    body = _brace_body(text, offset)
    candidate = Candidate(
        op_id=slug(f"{method}-{path}"),
        name=f"{method} {path}",
        dialect=dialect_id,
        declaration_kind=kind,
        loc=f"{rel}:{start_line}",
        order_hint=order,
        inputs=[],
        detail=f"{method} route {path}",
    )
    if body:
        candidate.body_range = body
    return candidate


def _brace_body(text, offset):
    """Line range of the next balanced `{...}` block, or None.

    A bounded scan, not a parser: strings, template literals and comments are skipped so
    a brace inside a literal cannot unbalance the count. When the scan runs off the end
    of the file it returns None rather than a wrong range.
    """
    index = text.find("{", offset)
    if index < 0:
        return None
    depth = 0
    quote = None
    while index < len(text):
        char = text[index]
        if quote:
            if char == "\\":
                index += 2
                continue
            if char == quote:
                quote = None
        elif char in "\"'`":
            quote = char
        elif char == "/" and index + 1 < len(text) and text[index + 1] == "/":
            newline = text.find("\n", index)
            if newline < 0:
                return None
            index = newline
        elif char == "/" and index + 1 < len(text) and text[index + 1] == "*":
            close = text.find("*/", index + 2)
            if close < 0:
                return None
            index = close + 1
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                start = text.count("\n", 0, offset) + 1
                end = text.count("\n", 0, index) + 1
                return (start, end)
        index += 1
    return None


class OpenApiDialect(Dialect):
    id = "openapi-spec"
    description = "OpenAPI/Swagger specification documents"
    repo_classes = ("service-app", "infra-config", "library", "unknown")

    def match(self, snapshot, profile):
        candidates = []
        order = 0
        for rel in snapshot.iter_files(suffixes=(".json", ".yaml", ".yml")):
            name = rel.rsplit("/", 1)[-1].lower()
            if name not in _SPEC_NAMES and not (
                ("openapi" in name or "swagger" in name)
                and name.endswith((".json", ".yaml", ".yml"))
            ):
                continue
            text = snapshot.read(rel)
            if not text:
                continue
            for path, method, lineno in _spec_operations(rel, text):
                candidates.append(
                    Candidate(
                        op_id=slug(f"{method}-{path}"),
                        name=f"{method} {path}",
                        dialect=self.id,
                        declaration_kind="openapi-operation",
                        loc=f"{rel}:{lineno}",
                        order_hint=order,
                        inputs=[],
                        detail=f"OpenAPI operation {method} {path}",
                    )
                )
                order += 1
        return dedupe(candidates)


def _spec_operations(rel, text):
    """Yield (path, METHOD, lineno) from an OpenAPI document. Deterministic order."""
    out = []
    if rel.endswith(".json"):
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return out
        if not isinstance(data, dict):
            return out
        paths = data.get("paths")
        if not isinstance(paths, dict):
            return out
        lines = text.splitlines()
        for path in sorted(paths):
            methods = paths[path]
            if not isinstance(methods, dict):
                continue
            for method in sorted(methods):
                if method.lower() not in _HTTP_METHODS:
                    continue
                out.append((path, method.upper(), _line_of(lines, f'"{path}"')))
        return out

    if not (has_top_level_key(text, "openapi") or has_top_level_key(text, "swagger")):
        return out
    lines = text.splitlines()
    path_indent = None
    method_indent = None
    current = None
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("paths:"):
            path_indent = None
            method_indent = None
            current = None
            continue
        indent = len(line) - len(line.lstrip(" "))
        if path_indent is None:
            if stripped.startswith("/") and stripped.endswith(":"):
                path_indent = indent
                current = stripped[:-1]
            continue
        if indent <= path_indent:
            if stripped.startswith("/") and stripped.endswith(":"):
                current = stripped[:-1]
                method_indent = None
                continue
            break
        if current is None:
            continue
        if method_indent is None and indent > path_indent:
            method_indent = indent
        if indent == method_indent and stripped.endswith(":"):
            method = stripped[:-1]
            if method.lower() in _HTTP_METHODS:
                out.append((current, method.upper(), index + 1))
    return out


def _line_of(lines, needle):
    for index, line in enumerate(lines):
        if needle in line:
            return index + 1
    return 1


class ProtoServiceDialect(Dialect):
    id = "proto-service"
    description = "protobuf service and rpc declarations"
    repo_classes = ()

    _SERVICE = re.compile(r"^\s*service\s+(?P<name>\w+)\s*\{", re.MULTILINE)
    _RPC = re.compile(r"^\s*rpc\s+(?P<name>\w+)\s*\(", re.MULTILINE)

    def match(self, snapshot, profile):
        candidates = []
        order = 0
        for rel in snapshot.iter_files(suffixes=(".proto",)):
            text = snapshot.read(rel)
            if not text or "rpc" not in text:
                continue
            lines = text.splitlines()
            for match in self._RPC.finditer(text):
                lineno = text.count("\n", 0, match.start()) + 1
                service = _enclosing_service(lines, lineno)
                method = match.group("name")
                candidates.append(
                    Candidate(
                        op_id=slug(f"{service or 'rpc'}-{method}"),
                        name=humanize(f"{service} {method}".strip()),
                        dialect=self.id,
                        declaration_kind="proto-rpc",
                        loc=f"{rel}:{lineno}",
                        order_hint=order,
                        inputs=[],
                        detail=f"gRPC method {service}.{method}"
                        if service
                        else f"gRPC method {method}",
                    )
                )
                order += 1
        return dedupe(candidates)


def _enclosing_service(lines, lineno):
    name = None
    for index in range(lineno - 1, -1, -1):
        match = ProtoServiceDialect._SERVICE.match(lines[index])
        if match:
            name = match.group("name")
            break
    return name


__all__ = [
    "OpenApiDialect",
    "ProtoServiceDialect",
    "WebJsDialect",
    "WebPythonDialect",
]
