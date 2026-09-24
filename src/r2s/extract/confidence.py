"""Evidence fusion and confidence.

Confidence must be *derived from evidence agreement*, never hand-assigned, or it
becomes decoration. It is also per-field: an operation's existence can be high
confidence while its gate is low, because those rest on different evidence.

The rules below are deliberately strict in one direction -- `inferred` or
`documented` alone can never reach `high`, and any conflict forces `low`. Without
that, everything drifts to `high` and the review step becomes useless.
"""

# Relative strength. Higher wins ties; only the presence of a source matters.
WEIGHTS = {"declared": 4, "traced": 3, "inferred": 2, "documented": 1, "proposed": 0}

HIGH = "high"
MEDIUM = "medium"
LOW = "low"


def sources_of(evidence):
    return {e.get("source") for e in evidence or []}


def fuse(
    evidence,
    corroboration=0,
    ambiguous=False,
    conflicts=None,
    weak_attribution=False,
    placeholder=False,
):
    """Return (confidence, notes).

    corroboration     -- number of independent provider matches agreeing.
    ambiguous         -- a matched provider cannot be resolved by import alone.
    conflicts         -- list of conflicting source names, if any.
    weak_attribution  -- the effect sites behind this operation were assigned by
                         position rather than by structure, so they may belong to a
                         different operation. Caps confidence at MEDIUM: a confident
                         wrong requirement is worse than an uncertain right one.
    placeholder       -- the requirement set is the `external.call` placeholder, because
                         nothing was traced and nothing was declared. Forces LOW.
    """
    notes = []
    sources = sources_of(evidence)

    if not sources:
        return LOW, ["no evidence"]

    if conflicts:
        return LOW, [f"conflicting evidence from {sorted(conflicts)}"]

    # A proposed (LLM) signal is never trusted, alone or in company.
    if sources == {"proposed"}:
        return LOW, ["proposed only; must be confirmed by a human"]

    if placeholder:
        # Checked before the `declared` branch on purpose. A declaration evidences that
        # the operation EXISTS; it says nothing about what the operation requires. Letting
        # it raise confidence here is exactly how a placeholder becomes a confident wrong
        # requirement -- and the note it used to emit blamed an "ambiguous provider
        # match" that had not occurred.
        return LOW, ["requirement is a placeholder; nothing was traced or declared"]

    def cap(result, reason):
        """Apply the weak-attribution ceiling."""
        if not weak_attribution:
            return result, notes
        if result == HIGH:
            return MEDIUM, [reason]
        return result, notes

    if "declared" in sources:
        if ambiguous:
            return MEDIUM, ["declared, but the matched provider is ambiguous"]
        return cap(HIGH, "attribution is positional, not structural")

    if "traced" in sources and ("inferred" in sources or "declared" in sources):
        if ambiguous:
            return MEDIUM, ["traced plus an ambiguous provider match"]
        return cap(HIGH, "attribution is positional, not structural")

    if "traced" in sources:
        return MEDIUM, []

    if "inferred" in sources:
        if ambiguous:
            return LOW, ["ambiguous provider match; import alone cannot resolve it"]
        if corroboration >= 2:
            return cap(HIGH, f"{corroboration} independent providers agree")
        return MEDIUM, []

    if "documented" in sources:
        return LOW, ["documentation only; not corroborated by code"]

    return LOW, []


def merge_sources(*evidence_lists):
    """Flatten evidence lists, dropping exact duplicates, keeping deterministic order."""
    seen = set()
    out = []
    for evidence_list in evidence_lists:
        for item in evidence_list or []:
            key = (item.get("source"), item.get("loc"), item.get("detail"))
            if key in seen:
                continue
            seen.add(key)
            out.append(item)
    order = {"declared": 0, "traced": 1, "inferred": 2, "documented": 3, "proposed": 4}
    return sorted(out, key=lambda e: (order.get(e.get("source"), 9), e.get("loc") or ""))
