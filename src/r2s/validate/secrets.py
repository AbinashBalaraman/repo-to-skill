"""L3: secret-leak scanner over an emitted skill.

This is a *detector*, not a preventer. Prevention lives upstream: `source/snapshot.py`
never reads credential files, `extract/docs.py` never copies raw repository text into
evidence, and `util/io.redact()` is a backstop. Those controls were added after a real
leak was found and reproduced -- a README line containing a token reached
`inventory.draft.json`.

A scanner is still needed because the emitter writes repository-derived prose into
`references/`, and that path is new. `SECURITY.md` previously had to admit this layer
did not exist; it does now.

Two rules that matter:

  Never print the credential. A report that echoes the secret is its own leak. Findings
  carry the file, the line, and the credential *type* -- never the value.

  Noisy is better than quiet. A false positive costs a reviewer a few seconds. A missed
  key costs someone their account.
"""

import re
from pathlib import Path

# (name, pattern). Order matters only for reporting precedence.
PATTERNS = (
    (
        "openai-or-stripe-key",
        re.compile(r"\b(?:sk|pk|rk)-(?:proj-|live-|test-)?[A-Za-z0-9_\-]{16,}"),
    ),
    ("aws-access-key-id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    (
        "aws-secret-access-key",
        re.compile(r"(?i)\baws_?(?:secret_?)?access_?key\b\s*[:=]\s*[\"']?([A-Za-z0-9/+=]{40})"),
    ),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("slack-token", re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}")),
    ("google-api-key", re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}")),
    ("pem-private-key", re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----")),
    ("sendgrid-key", re.compile(r"\bSG\.[A-Za-z0-9_\-]{16,}\.[A-Za-z0-9_\-]{16,}")),
    ("twilio-key", re.compile(r"\bSK[0-9a-fA-F]{32}\b")),
    (
        "generic-assignment",
        re.compile(
            r"(?i)\b(?:api[_-]?key|secret|token|password|passwd|auth[_-]?token|access[_-]?token)"
            r"\b\s*[:=]\s*[\"']?([^\s\"'#]{12,})"
        ),
    ),
)

# Placeholders and obvious non-secrets. Without these the generic-assignment rule fires
# on every documented example and the report becomes unreadable.
_ALLOWLIST = re.compile(
    r"(?i)^(?:\$\{?[A-Za-z_]|os\.environ|process\.env|your[_-]|my[_-]|xxx+$|<[^>]+>$|"
    r"\.\.\.$|redacted|example|placeholder|changeme|dummy|fake|test[_-]?key$|"
    r"[A-Z_]{4,}$|none$|null$|true$|false$)"
)

TEXT_SUFFIXES = (".md", ".json", ".txt", ".yaml", ".yml", ".py", ".toml", ".sh", ".js", ".ts")


class Finding:
    def __init__(self, path, line, kind, excerpt):
        self.path = path
        self.line = line
        self.kind = kind
        self.excerpt = excerpt

    def __str__(self):
        # The excerpt is already redacted; the value is never carried here.
        return f"{self.path}:{self.line}: {self.kind} ({self.excerpt})"

    def to_dict(self):
        return {"path": self.path, "line": self.line, "kind": self.kind, "excerpt": self.excerpt}


def _redacted_excerpt(line, match):
    """Mask the matched span *in place*, then trim for display.

    An earlier version appended a mask after the first 40 characters of the line, which
    leaked the whole secret whenever it appeared early -- the excerpt is the part a
    human reads, so it must never contain the value. Replacing in place keeps the
    surrounding context (which is what makes the finding locatable) while removing the
    secret itself, and `replace` handles a value appearing more than once.
    """
    value = match.group(0)
    head = value[:4] if len(value) > 8 else ""
    masked = f"{head}…[{len(value)} chars]"
    return line.replace(value, masked).strip()[:100]


def scan_text(text, path="<string>"):
    """Scan one document. Returns a list of Findings."""
    findings = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        for kind, pattern in PATTERNS:
            match = pattern.search(line)
            if not match:
                continue
            candidate = match.group(match.lastindex) if match.lastindex else match.group(0)
            if _ALLOWLIST.match(candidate or ""):
                continue
            findings.append(Finding(path, lineno, kind, _redacted_excerpt(line, match)))
            break  # one finding per line is enough; avoid stacking patterns
    return findings


def scan_directory(root, suffixes=TEXT_SUFFIXES):
    """Scan every text file under a directory. Deterministic order."""
    root = Path(root)
    if not root.exists():
        raise FileNotFoundError(f"nothing to scan at {root}")

    findings = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or not path.name.endswith(suffixes):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = path.relative_to(root).as_posix()
        findings.extend(scan_text(text, rel))
    return findings


def format_findings(findings, root=None):
    """Human-readable report. Never includes a credential value."""
    if not findings:
        return "L3 secret scan: clean (no credential-shaped strings found)"
    lines = [f"L3 secret scan: {len(findings)} finding(s)"]
    for finding in findings:
        location = f"{root}/{finding.path}" if root else finding.path
        lines.append(f"  {location}:{finding.line}  {finding.kind}")
        lines.append(f"      {finding.excerpt}")
    lines.append("")
    lines.append("  Values are masked by design: a report that echoed the secret")
    lines.append("  would be a leak in its own right.")
    return "\n".join(lines)
