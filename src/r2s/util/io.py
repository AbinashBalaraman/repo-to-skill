"""JSON and filesystem helpers. Canonical output keeps diffs meaningful."""

import json
import re
from pathlib import Path

# Obvious credential shapes. This is a backstop, not a scanner: the real defence is
# that raw repository text is never carried into an artifact in the first place.
# Defence in depth, because the emitted inventory is exactly the thing a user
# attaches to a bug report or commits to a repository.
_SECRET_PATTERNS = (
    re.compile(r"\b(sk|pk|rk)-[A-Za-z0-9_\-]{16,}"),  # OpenAI/Stripe style
    re.compile(r"\bsk_live_[A-Za-z0-9]{16,}"),  # Stripe live
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),  # AWS access key id
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),  # GitHub tokens
    re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}"),  # Slack
    re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}"),  # Google API key
    re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"),  # JWT
    re.compile(r"(?i)\b(bearer|token|secret|password|passwd|api[_-]?key)\b\s*[:=]\s*\S{8,}"),
)

REDACTED = "[redacted]"


def redact(text):
    """Replace credential-shaped substrings. Returns text unchanged if not a string."""
    if not isinstance(text, str):
        return text
    out = text
    for pattern in _SECRET_PATTERNS:
        out = pattern.sub(REDACTED, out)
    return out


def contains_secret(text):
    """True if the text looks like it carries a credential."""
    if not isinstance(text, str):
        return False
    return any(pattern.search(text) for pattern in _SECRET_PATTERNS)


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def dump_json(path, data):
    """Write deterministic JSON: sorted keys, 2-space indent, trailing newline.

    Determinism matters because the eval diffs emitted artifacts across SHAs.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False)
    path.write_text(text + "\n", encoding="utf-8")
    return path


def write_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def read_text(path):
    return Path(path).read_text(encoding="utf-8", errors="replace")


def relpath(path, root):
    """Path relative to root, POSIX separators, or None if outside root."""
    try:
        return Path(path).resolve().relative_to(Path(root).resolve()).as_posix()
    except ValueError:
        return None


def slugify(text):
    """Lowercase kebab-case, matching the Agent Skills `name` constraint."""
    out = []
    prev_dash = False
    for ch in text.lower():
        if ch.isalnum():
            out.append(ch)
            prev_dash = False
        elif not prev_dash:
            out.append("-")
            prev_dash = True
    return "".join(out).strip("-")
