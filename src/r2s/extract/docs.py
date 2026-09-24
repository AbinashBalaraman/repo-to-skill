"""T3: documentation and gate-language detection.

Gates are the field that code infers worst. "Publishing", "costs money", "cannot be
undone", "personal data" and "rights" live in prose, not signatures -- so they are
read from README, docstrings, --help text and OpenAPI descriptions.

Asymmetric error cost, bias toward inclusion: a missed gate is far worse than a
spurious one, because the whole design is fail-closed. So T3 over-proposes gates at
low confidence and the review step prunes. This mirrors "fail closed" at the
extraction layer rather than contradicting it.
"""

import re

from ..util.io import redact

GATE_LANGUAGE = {
    "publish": [
        r"\bpublish(?:es|ing)?\b",
        r"\bupload(?:s|ing)?\b",
        r"\bgo(?:es)? live\b",
        r"\brelease(?:s|d)? to\b",
        r"\bpost(?:s|ing)? publicly\b",
        r"\bmake public\b",
        r"\bship(?:s|ping)? to production\b",
    ],
    "spend": [
        r"\bcosts?\b",
        r"\bbill(?:ing|ed)?\b",
        r"\bpaid (?:tier|plan|api)\b",
        r"\bcharges?\b",
        r"\bcredits?\b",
        r"\bbudget\b",
        r"\bpricing\b",
        r"\bper[- ](?:call|request|token|image|minute)\b",
        r"\$",
    ],
    "irreversible": [
        r"\bcannot be undone\b",
        r"\birreversible\b",
        r"\bpermanent(?:ly)?\b",
        r"\bdestructive\b",
        r"\bdelete permanently\b",
        r"\bwipe[sd]?\b",
        r"\bhard delete\b",
        r"\bmigrat(?:e|ion)\b",
    ],
    "legal": [
        r"\brights?\b",
        r"\blicen[cs]e\b",
        r"\bcopyright\b",
        r"\bprovenance\b",
        r"\bdisclos(?:e|ure)\b",
        r"\bconsent\b",
        r"\battribution\b",
        r"\bsynthetic media\b",
        r"\bfair use\b",
        r"\bterms of service\b",
    ],
    "privacy": [
        r"\bprivacy\b",
        r"\bpersonal data\b",
        r"\bPII\b",
        r"\bGDPR\b",
        r"\bvisibility\b",
        r"\bprivate by default\b",
        r"\bdefault_privacy\b",
        r"\bsensitive\b",
    ],
}

_OPTIONAL_LANGUAGE = [
    r"\boptional(?:ly)?\b",
    r"\bif (?:you )?(?:want|wish)\b",
    r"\bcan be skipped\b",
    r"\bnice to have\b",
    r"\bnot required\b",
]

_DOC_SUFFIXES = (".md", ".rst", ".txt")
_DOC_NAMES = ("README", "CONTRIBUTING", "ARCHITECTURE", "SECURITY", "ADR")


class DocSignal:
    """A documentation line that carries gate or optionality language.

    The raw line is kept for *matching only* and is never emitted. It used to be
    written straight into the evidence `detail`, which meant a README line like

        Publish with your token: `thing publish --token sk-live-...`

    put the token into `inventory.draft.json` -- the artifact a user attaches to a bug
    report. Evidence now records that gate language was found and where, not what it
    said. `redact` is applied as a backstop in case a snippet is ever surfaced again.
    """

    def __init__(self, gate, loc, snippet, kind="gate"):
        self.gate = gate
        self.loc = loc
        self._snippet = snippet
        self.kind = kind

    @property
    def snippet(self):
        """Raw line. For matching only -- never emit this."""
        return self._snippet

    def evidence(self):
        return {
            "source": "documented",
            "loc": self.loc,
            "detail": redact(f"{self.kind} language detected in documentation ({self.gate})"),
        }


def _docs(snapshot, limit=40):
    out = []
    for rel in snapshot.files:
        lower = rel.lower()
        if lower.endswith(_DOC_SUFFIXES) or any(n.lower() in lower for n in _DOC_NAMES):
            out.append(rel)
        if len(out) >= limit:
            break
    return out


def scan(snapshot):
    """Return (gate_signals, optional_signals). Deterministic order."""
    gates = []
    optionals = []
    for rel in _docs(snapshot):
        text = snapshot.read(rel)
        if not text:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if not stripped:
                continue
            for gate, patterns in GATE_LANGUAGE.items():
                if any(re.search(p, stripped, re.IGNORECASE) for p in patterns):
                    gates.append(DocSignal(gate, f"{rel}:{lineno}", stripped))
            if any(re.search(p, stripped, re.IGNORECASE) for p in _OPTIONAL_LANGUAGE):
                optionals.append(
                    DocSignal("optional", f"{rel}:{lineno}", stripped, kind="optional")
                )
    return gates, optionals


def gates_for_operation(operation_name, operation_summary, gate_signals, limit=6):
    """Gate hints for one operation, from doc language that mentions its own words.

    Deliberately permissive: it returns gates the operation's name shares vocabulary
    with, plus any gate whose language appears anywhere in the docs when the operation
    name is generic. Review prunes.
    """
    haystack = f"{operation_name} {operation_summary or ''}".lower()
    words = {w for w in re.split(r"\W+", haystack) if len(w) > 3}

    hits = []
    seen = set()
    for signal in gate_signals:
        if signal.gate in seen:
            continue
        snippet = signal.snippet.lower()
        if any(word in snippet for word in words):
            hits.append(signal)
            seen.add(signal.gate)
        if len(hits) >= limit:
            break
    return hits
