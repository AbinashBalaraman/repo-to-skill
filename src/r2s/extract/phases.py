"""Phase inference.

The vocabulary is `acquire | prepare | transform | verify | deliver | observe`. It is
total, orthogonal and domain-neutral -- but the reason it earns its keep is that gates
cluster predictably by phase: `deliver` concentrates publish/irreversible/legal,
`verify` concentrates human gates, `observe` is read-mostly. Phase therefore predicts
where the gates are, which is useful to the emitter and to a reviewer scanning the
inventory.

Recurrence is orthogonal to lifecycle position, so scheduling is a `trigger`, not a
phase.
"""

PHASES = ("acquire", "prepare", "transform", "verify", "deliver", "observe")

_VERBS = {
    "acquire": (
        "fetch",
        "read",
        "get",
        "load",
        "download",
        "collect",
        "ingest",
        "import",
        "pull",
        "sync",
        "scrape",
        "crawl",
        "receive",
        "query",
        "search",
        "list",
        "discover",
        "resolve",
        "scan",
    ),
    "prepare": (
        "init",
        "setup",
        "configure",
        "config",
        "install",
        "provision",
        "bootstrap",
        "migrate",
        "seed",
        "auth",
        "login",
        "connect",
        "mount",
        "clone",
        "plan",
        "compile",
        "build",
        "bundle",
        "parse",
        "normalize",
        "normalise",
        "clean",
        "preprocess",
        "chunk",
        "embed",
        "index",
    ),
    "transform": (
        "generate",
        "create",
        "write",
        "compose",
        "render",
        "transform",
        "convert",
        "translate",
        "summarize",
        "summarise",
        "extract",
        "classify",
        "analyze",
        "analyse",
        "process",
        "compute",
        "calculate",
        "merge",
        "split",
        "edit",
        "modify",
        "update",
        "enrich",
        "annotate",
        "assemble",
        "encode",
        "train",
    ),
    "verify": (
        "validate",
        "verify",
        "check",
        "test",
        "lint",
        "audit",
        "review",
        "inspect",
        "assert",
        "approve",
        "confirm",
        "sanitize",
        "sanitise",
        "compare",
        "diff",
        "score",
        "grade",
        "moderate",
    ),
    "deliver": (
        "publish",
        "upload",
        "deploy",
        "release",
        "send",
        "post",
        "submit",
        "push",
        "deliver",
        "ship",
        "notify",
        "email",
        "sms",
        "announce",
        "export",
        "emit",
        "charge",
        "bill",
        "pay",
        "transfer",
    ),
    "observe": (
        "monitor",
        "report",
        "log",
        "track",
        "metric",
        "metrics",
        "analytics",
        "stats",
        "statistics",
        "status",
        "health",
        "watch",
        "alert",
        "dashboard",
        "record",
        "summarise_usage",
        "usage",
    ),
}

# Effects that constrain the phase regardless of the verb.
_READ_EFFECTS = ("read-local", "read-external")


def infer(name, effect=None, capabilities=None, gates=None):
    """Infer a phase.

    Verbs are consulted before gates. A gate is a strong signal, but T3 deliberately
    over-proposes gates so review can prune -- and a spurious `publish` gate must not
    be allowed to reclassify an `acquire` operation as `deliver`.
    """
    capabilities = capabilities or []
    gates = set(gates or [])
    words = _words(name)

    for phase in PHASES:
        if words & set(_VERBS[phase]):
            return phase

    # No verb matched: a gate is now the best remaining signal.
    if "publish" in gates:
        return "deliver"
    if "spend" in gates and "payment.charge" in capabilities:
        return "deliver"

    # Fall back to effect class.
    if effect in _READ_EFFECTS:
        return "acquire"
    if effect == "effectful-local":
        return "transform"
    if effect == "effectful-external":
        return "deliver"
    return "transform"


def _words(name):
    out = set()
    for token in name.lower().replace("-", " ").replace("_", " ").split():
        if len(token) > 2:
            out.add(token)
            if token.endswith("s") and len(token) > 4:
                out.add(token[:-1])
    return out


def validate(phase):
    return phase in PHASES
