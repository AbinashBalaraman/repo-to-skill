"""T0 dialect: Java and Kotlin declaration sites.

JVM sources are not effect-traced (T1 is stdlib `ast` over Python), so the declaration is
the operation and the capability is read off the declaration kind. Only two declaration
kinds genuinely imply a capability:

  @Scheduled                       -> schedule.cron  (and trigger "scheduled")
  @KafkaListener / @JmsListener    -> queue.consume

A Spring route handler declares nothing: serving a request is not in the vocabulary, and
an invented ID would hard-fail the validator, so those operations fall through to the
honest `external.call` placeholder the pipeline adds. `@RestController` / `@Controller`
are recognised as context: they decide the route's `declaration_kind` and its detail, and
a class-level `@RequestMapping` contributes the path prefix its methods are mounted under,
which is what Spring actually does.

`body_range` is recovered by `common.brace_body`, which knows about Java text blocks and
Kotlin raw strings.

Dependency coordinates -- `pom.xml` `<artifactId>` under `<dependencies>`, and the
`implementation "group:artifact:version"` lines in `build.gradle`/`build.gradle.kts` --
are read by `profiler.manifests.read_jvm_deps` so that T2 can match them. A dependency is
not an operation, so this dialect deliberately does not invent one.
"""

import re

from .base import Candidate, Dialect
from .common import brace_body, dedupe, humanize, slug

_SUFFIXES = (".java", ".kt")

_MAPPING = re.compile(
    r"@(?P<ann>Get|Post|Put|Delete|Patch|Request)Mapping\b[ \t]*(?:\((?P<args>[^)]*)\))?"
)
_CONTROLLER = re.compile(r"@(?:RestController|Controller)\b")
_LIFECYCLE = re.compile(
    r"@(?P<ann>Scheduled|KafkaListener|JmsListener|EventListener)\b[ \t]*(?:\((?P<args>[^)]*)\))?"
)
_CLASS_KEYWORD = re.compile(r"\b(?:class|interface|enum|record|object)\s+(?P<name>[A-Za-z_]\w*)")
_JAVA_MAIN = re.compile(
    r"\bstatic\s+void\s+main\s*\(\s*String\s*(?:\[\s*\]|\.\.\.)\s*[A-Za-z_]\w*\s*\)"
)
_KOTLIN_MAIN = re.compile(r"(?m)^[ \t]*fun\s+main\s*\(")
_STRING_ARG = re.compile(r'"([^"]*)"')
_REQUEST_METHOD = re.compile(r"RequestMethod\.(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)")
_IDENT_BEFORE_PAREN = re.compile(r"([A-Za-z_]\w*)\s*\(")

_CLASS_WINDOW = 400
_MEMBER_WINDOW = 400


class JvmDialect(Dialect):
    id = "jvm"
    description = "Java and Kotlin declaration sites: Spring controllers, listeners, main"
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
    class_positions = [match.start() for match in _CLASS_KEYWORD.finditer(text)]
    context_cache = {}

    def context_for(position):
        class_pos = _last_before(class_positions, position)
        if class_pos is None:
            return False, ""
        if class_pos not in context_cache:
            context_cache[class_pos] = _class_context(text, class_pos)
        return context_cache[class_pos]

    found = []
    for match in _MAPPING.finditer(text):
        member_kind, member_name, has_body = _member_after(text, match.end())
        if member_kind != "method":
            continue  # a class-level mapping is the base path, not an operation
        is_controller, base = context_for(match.start())
        path = _join_path(base, _annotation_path(match.group("args")))
        method = _annotation_method(match.group("ann"), match.group("args"))
        detail = f"Spring {method} route {path}"
        if is_controller:
            detail = f"Spring controller {method} route {path}"
        found.append(
            (
                match.start(),
                _candidate(
                    rel,
                    text,
                    match.start(),
                    op_id=slug(f"{method}-{path}")
                    or slug(f"route-{_line_of(text, match.start())}"),
                    name=f"{method} {path}",
                    kind="spring-controller-route" if is_controller else "spring-route",
                    detail=detail,
                    body_range=(
                        brace_body(text, match.start(), triple_quoted=True) if has_body else None
                    ),
                ),
            )
        )

    for match in _LIFECYCLE.finditer(text):
        member_kind, member_name, has_body = _member_after(text, match.end())
        if member_kind != "method":
            continue  # a listener or schedule with no member is not an operation
        ann = match.group("ann")
        label = _words(member_name) if member_name else f"line-{_line_of(text, match.start())}"
        if ann == "Scheduled":
            op_id = slug(f"scheduled-{label}")
            kind, capabilities, trigger = "spring-scheduled", ["schedule.cron"], "scheduled"
        elif ann in ("KafkaListener", "JmsListener"):
            op_id = slug(f"{ann.removesuffix('Listener').lower()}-{label}")
            kind, capabilities, trigger = (
                f"spring-{ann.removesuffix('Listener').lower()}-listener",
                ["queue.consume"],
                "manual",
            )
        else:
            op_id = slug(f"event-{label}")
            kind, capabilities, trigger = "spring-event-listener", [], "manual"
        found.append(
            (
                match.start(),
                _candidate(
                    rel,
                    text,
                    match.start(),
                    op_id=op_id,
                    name=humanize(label),
                    kind=kind,
                    detail=f"@{ann} on {member_name}()" if member_name else f"@{ann}",
                    capabilities=capabilities,
                    effect_hint="effectful-external" if capabilities else None,
                    trigger=trigger,
                    body_range=(
                        brace_body(text, match.start(), triple_quoted=True) if has_body else None
                    ),
                ),
            )
        )

    for match in _JAVA_MAIN.finditer(text):
        found.append(
            (
                match.start(),
                _candidate(
                    rel,
                    text,
                    match.start(),
                    op_id="main",
                    name="Main",
                    kind="java-main",
                    detail="public static void main entry point",
                    capabilities=["shell.exec"],
                    effect_hint="effectful-local",
                    body_range=brace_body(text, match.start(), triple_quoted=True),
                ),
            )
        )

    for match in _KOTLIN_MAIN.finditer(text):
        found.append(
            (
                match.start(),
                _candidate(
                    rel,
                    text,
                    match.start(),
                    op_id="main",
                    name="Main",
                    kind="kotlin-main",
                    detail="Kotlin main entry point",
                    capabilities=["shell.exec"],
                    effect_hint="effectful-local",
                    body_range=brace_body(text, match.start(), triple_quoted=True),
                ),
            )
        )

    found.sort(key=lambda item: item[0])
    return [candidate for _, candidate in found]


def _member_after(text, offset):
    """(kind, name, has_body) for the member an annotation is attached to.

    `kind` is "type" for a class/interface/record/object, "method" for a method or `fun`,
    and None when there is no member to attach to. `has_body` is False for an abstract
    member or a Kotlin expression body: those have no brace of their own, so scanning
    forward for one would attach the annotation to whatever member comes next -- which is
    how a `fun get(id: String) = id` route ends up claiming the body of the next function.
    """
    brace = text.find("{", offset)
    stoppers = [value for value in (text.find(";", offset), text.find("}", offset)) if value >= 0]
    stop = min(stoppers) if stoppers else -1

    if brace < 0 or (stop >= 0 and stop < brace):
        if stop < 0:
            return None, None, False
        head = text[offset:stop]
        has_body = False
    else:
        if brace - offset > _MEMBER_WINDOW:
            return None, None, False
        head = text[offset:brace]
        has_body = True

    class_match = _CLASS_KEYWORD.search(head)
    if class_match:
        return "type", class_match.group("name"), has_body
    matches = list(_IDENT_BEFORE_PAREN.finditer(head))
    if matches:
        return "method", matches[-1].group(1), has_body
    return None, None, False


def _class_context(text, class_pos):
    """(is_controller, base_path) for the class declared at `class_pos`."""
    prefix = _class_prefix(text, class_pos)
    is_controller = _CONTROLLER.search(prefix) is not None
    base = ""
    mappings = list(_MAPPING.finditer(prefix))
    if mappings and mappings[-1].group("ann") == "Request":
        base = _annotation_path(mappings[-1].group("args"))
    return is_controller, base


def _class_prefix(text, class_pos):
    """The annotation block attached to a class: everything since the previous member."""
    segment = text[max(0, class_pos - _CLASS_WINDOW) : class_pos]
    cut = max(segment.rfind("}"), segment.rfind(";"), segment.rfind("{"))
    return segment[cut + 1 :] if cut >= 0 else segment


def _annotation_path(args):
    if not args:
        return "/"
    match = _STRING_ARG.search(args)
    if match is None:
        return "/"
    value = match.group(1)
    return value if value.startswith("/") else f"/{value}"


def _annotation_method(ann, args):
    if ann != "Request":
        return ann.upper()
    if args:
        match = _REQUEST_METHOD.search(args)
        if match:
            return match.group(1)
    return "ANY"


def _join_path(base, path):
    if not base or base == "/":
        return path
    if not path or path == "/":
        return base
    return f"{base.rstrip('/')}/{path.lstrip('/')}"


def _last_before(positions, value):
    result = None
    for position in positions:
        if position >= value:
            break
        result = position
    return result


def _words(name):
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name)


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
    trigger="manual",
    body_range=None,
):
    return Candidate(
        op_id=op_id,
        name=name,
        dialect="jvm",
        declaration_kind=kind,
        loc=f"{rel}:{_line_of(text, offset)}",
        inputs=[],
        detail=detail,
        capabilities=list(capabilities or []),
        effect_hint=effect_hint,
        trigger=trigger,
        body_range=body_range,
    )


def _line_of(text, offset):
    return text.count("\n", 0, offset) + 1


__all__ = ["JvmDialect"]
