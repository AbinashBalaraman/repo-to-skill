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
    """Gate hints for one operation, when its siblings are not known.

    Kept for callers that hold a single operation. The pipeline uses `associate`, which
    can see every operation and so can tell a mention from a subject.
    """
    mapping, _ = associate([("_", operation_name)], gate_signals, limit=limit)
    return mapping.get("_", [])


# Vocabulary that identifies the *subject* of a gate sentence. A gate is attributed to
# the operation the gate language names; the hint table is what lets a `publish` gate
# find an operation called `deploy` even though the words differ.
_GATE_NAME_HINTS = {
    "publish": (
        "publish",
        "upload",
        "release",
        "ship",
        "deploy",
        "post",
        "send",
        "export",
        "submit",
        "notify",
        "email",
        "announce",
    ),
    "spend": ("charge", "bill", "pay", "cost", "budget", "spend", "purchase", "order", "invoice"),
    "irreversible": (
        "delete",
        "drop",
        "destroy",
        "wipe",
        "purge",
        "reset",
        "migrate",
        "truncate",
        "remove",
        "rollback",
    ),
    "legal": (
        "rights",
        "license",
        "licence",
        "copyright",
        "consent",
        "attribution",
        "disclose",
        "provenance",
        "compliance",
        "terms",
    ),
    "privacy": ("privacy", "personal", "pii", "gdpr", "redact", "anonymize", "anonymise", "scrub"),
}

# Function words that carry no operation identity. They are dropped so a summary line
# like "the status command never prints them" cannot be matched by the word "command".
_STOPWORDS = frozenset(
    {
        "command",
        "commands",
        "with",
        "from",
        "that",
        "this",
        "into",
        "each",
        "then",
        "your",
        "have",
        "will",
        "when",
        "also",
        "only",
        "over",
        "more",
        "than",
        "them",
        "they",
        "there",
        "their",
        "which",
        "using",
        "used",
        "operation",
        "operations",
    }
)


def _operation_tokens(name):
    """Identity tokens of an operation name. Deliberately not the declaration detail.

    The declaration detail is boilerplate ("@command on fetch()"), and matching gate
    language against it was the source of most of the noise this module used to emit:
    the word "command" appears in half the prose of a typical README.
    """
    tokens = set()
    for token in re.split(r"\W+", (name or "").lower()):
        if len(token) > 3 and token not in _STOPWORDS:
            tokens.add(token)
    return tokens


def _names_in_line(line, operations):
    """Operations whose name token appears in the line, in a deterministic order."""
    lowered = line.lower()
    hits = []
    for op_id, name in operations:
        for token in sorted(_operation_tokens(name)):
            if re.search(rf"\b{re.escape(token)}", lowered):
                hits.append((op_id, name))
                break
    return hits


def _gate_matches_name(gate, name):
    tokens = _operation_tokens(name)
    return any(
        token.startswith(hint) or hint.startswith(token)
        for token in tokens
        for hint in _GATE_NAME_HINTS.get(gate, ())
    )


def associate(operations, gate_signals, limit=6):
    """Attribute gate language to operations. Returns (mapping, unattributed).

    `operations` is an iterable of (op_id, name); `mapping` maps op_id to a list of
    DocSignal, deduplicated by gate and capped at `limit`.

    The rule is: a gate is attributed to the operation the gate language *names*. If a
    gate's own vocabulary matches an operation's name, that operation claims it -- this
    is what keeps a `publish` gate on the `publish` operation. Otherwise every operation
    named on the line claims it, which is the deliberately fail-closed direction: a
    spurious gate at low confidence is reviewable, a missed one is not.

    A signal that names no operation at all is returned as unattributed rather than
    smeared across the whole inventory. The pipeline reports those, so nothing is
    silently dropped; the difference from before is that "we found gate language and
    could not tell you where it belongs" is now distinguishable from "this operation
    is gated".
    """
    operations = list(operations)
    mapping = {op_id: [] for op_id, _ in operations}
    unattributed = []

    for signal in gate_signals:
        named = _names_in_line(signal.snippet, operations)
        if not named:
            unattributed.append(signal)
            continue
        owners = [op for op in named if _gate_matches_name(signal.gate, op[1])] or named
        for op_id, _ in owners:
            bucket = mapping[op_id]
            if len(bucket) >= limit or any(hit.gate == signal.gate for hit in bucket):
                continue
            bucket.append(signal)
    return mapping, unattributed
