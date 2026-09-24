"""L4: Agent Skills specification validation for an emitted skill.

Normative source: `agentskills.io/specification`. The rules enforced here are the ones
that actually break a skill when violated -- a name that does not match its folder, a
description over the limit, a body over the token budget, a `references/` tree nested
more than one level deep.

The frontmatter parser is deliberately minimal: flat `key: value` pairs plus one level
of nesting for `metadata`. That is the entire subset the emitter writes, and writing a
general YAML parser here would mean either a dependency (the project is stdlib-only) or
a large amount of untested code. If the emitter ever emits richer frontmatter, this
parser must grow with it -- it fails loudly rather than guessing.
"""

import re
from pathlib import Path

from .. import config

KEBAB = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

REQUIRED_KEYS = ("name", "description")
OPTIONAL_KEYS = ("license", "compatibility", "metadata", "allowed-tools")
KNOWN_KEYS = REQUIRED_KEYS + OPTIONAL_KEYS

# Rough token estimate. No tokeniser is available without a dependency, and the spec
# budget is approximate by nature, so characters/4 is the honest approximation.
CHARS_PER_TOKEN = 4


class FrontmatterError(Exception):
    pass


def parse_frontmatter(text):
    """Return (frontmatter dict, body). Raises FrontmatterError if malformed.

    Supports flat `key: value` and one level of nesting (`metadata:` then indented
    `key: value`). Anything deeper is a loud error, not a silent misparse.
    """
    if not text.startswith("---"):
        raise FrontmatterError("SKILL.md must open with a '---' frontmatter block")

    lines = text.splitlines()
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        raise FrontmatterError("frontmatter block is not closed with '---'") from None

    front: dict = {}
    current_map: dict | None = None
    for offset, raw in enumerate(lines[1:end], start=2):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indented = raw[0] in " \t"
        stripped = raw.strip()

        if ":" not in stripped:
            raise FrontmatterError(f"line {offset}: expected 'key: value', got {stripped!r}")

        key, _, value = stripped.partition(":")
        key = key.strip()
        value = value.strip().strip('"').strip("'")

        if indented:
            if current_map is None:
                raise FrontmatterError(
                    f"line {offset}: indented entry {key!r} has no parent key. Only one "
                    f"level of nesting is supported."
                )
            current_map[key] = value
            continue

        if not value:
            # A bare key opens a nested map.
            current_map = {}
            front[key] = current_map
            continue

        current_map = None
        front[key] = value

    body = "\n".join(lines[end + 1 :]).strip()
    return front, body


def validate_skill(skill_dir, max_lines=None, max_tokens=None):
    """Validate an emitted skill directory. Returns a list of failure strings."""
    skill_dir = Path(skill_dir)
    failures = []

    if not skill_dir.is_dir():
        return [f"not a directory: {skill_dir}"]

    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        return [f"missing SKILL.md in {skill_dir}"]

    text = skill_md.read_text(encoding="utf-8", errors="replace")
    try:
        front, body = parse_frontmatter(text)
    except FrontmatterError as exc:
        return [f"SKILL.md frontmatter: {exc}"]

    # --- required keys
    for key in REQUIRED_KEYS:
        if key not in front:
            failures.append(f"frontmatter: missing required key {key!r}")

    # --- unknown keys are a spec violation, not a warning
    for key in front:
        if key not in KNOWN_KEYS:
            failures.append(
                f"frontmatter: unknown key {key!r}; the spec allows only {list(KNOWN_KEYS)}"
            )

    # --- name
    name = front.get("name", "")
    if name:
        if not KEBAB.match(name):
            failures.append(f"name {name!r} is not kebab-case")
        if len(name) > config.NAME_MAX_CHARS:
            failures.append(f"name is {len(name)} chars, limit {config.NAME_MAX_CHARS}")
        if name != skill_dir.name:
            failures.append(
                f"name {name!r} does not match the folder name {skill_dir.name!r}; the "
                f"spec requires them to be identical"
            )
    else:
        failures.append("name is empty")

    # --- description
    description = front.get("description", "")
    if not description:
        failures.append("description is empty")
    elif len(description) > config.DESCRIPTION_MAX_CHARS:
        failures.append(
            f"description is {len(description)} chars, limit {config.DESCRIPTION_MAX_CHARS}"
        )

    # --- compatibility
    compatibility = front.get("compatibility")
    if isinstance(compatibility, str) and len(compatibility) > config.COMPATIBILITY_MAX_CHARS:
        failures.append(
            f"compatibility is {len(compatibility)} chars, limit {config.COMPATIBILITY_MAX_CHARS}"
        )

    # --- metadata must be a string->string map
    metadata = front.get("metadata")
    if metadata is not None and not isinstance(metadata, dict):
        failures.append("metadata must be a map of string keys to string values")
    elif isinstance(metadata, dict):
        for key, value in metadata.items():
            if not isinstance(value, str):
                failures.append(f"metadata.{key} must be a string")

    # --- body budgets
    line_limit = max_lines or config.SKILL_MAX_LINES
    token_limit = max_tokens or config.SKILL_MAX_TOKENS
    body_lines = len(body.splitlines())
    if body_lines > line_limit:
        failures.append(f"body is {body_lines} lines, limit {line_limit}")

    approx_tokens = len(body) // CHARS_PER_TOKEN
    if approx_tokens > token_limit:
        failures.append(
            f"body is ~{approx_tokens} tokens, limit {token_limit} "
            f"(approximated at {CHARS_PER_TOKEN} chars/token)"
        )

    # --- references/ must be one level deep
    references = skill_dir / "references"
    if references.is_dir():
        for path in sorted(references.rglob("*")):
            if not path.is_file():
                continue
            depth = len(path.relative_to(references).parts)
            if depth > 1:
                failures.append(
                    f"references/{path.relative_to(references).as_posix()} is nested "
                    f"{depth} levels deep; the spec allows one"
                )

    # --- nothing unexpected at the top level
    allowed_top = {"SKILL.md", "references", "scripts", "assets"}
    for entry in sorted(skill_dir.iterdir()):
        if entry.name not in allowed_top:
            failures.append(
                f"unexpected entry {entry.name!r} at the skill root; the spec allows "
                f"{sorted(allowed_top)}"
            )

    return failures


def validate_profile_declaration(body):
    """The skill must state its own capability profile.

    Rationale from the plan: a skill that does not state its assumptions cannot fail
    loudly, so it fails silently. This checks the declaration is present at all; the
    emitter owns its exact wording.
    """
    lowered = body.lower()
    if "profile" not in lowered:
        return ["body does not declare the harness capability profile it was resolved for"]
    if "native" not in lowered and "mcp" not in lowered:
        return [
            "body declares a profile but not what the harness has natively or reaches "
            "via MCP; a reader cannot tell what the skill assumes"
        ]
    return []
