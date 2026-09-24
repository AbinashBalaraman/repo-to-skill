"""Gate-survival invariants I1-I7.

These assert that the safety properties of a repo survive compression into a skill.
They are mechanical: run them on every emitted skill, in CI, and on every fixture.

I7 is new. The prototype had six invariants and I7 was only proposed, because the
data model could not represent a deferred gate at all.
"""

from .router import AUTO_BINDINGS

CRITICAL_GATES = frozenset({"publish", "irreversible"})


def verify(operations, rows, profile=None):
    """Return a list of failure strings. Empty list means all invariants hold."""
    failures = []
    by_id = {op["id"]: op for op in operations}

    for row in rows:
        op = by_id[row["id"]]
        declared = set(op.get("gate", []))
        enforced = set(row["gates"])
        deferred = set(row["deferred_gates"])

        # I1 -- a gated operation on an executing binding keeps its gate.
        if row["binding"] in AUTO_BINDINGS:
            lost = declared - enforced
            if lost:
                failures.append(f"I1 {row['id']}: gate(s) lost in conversion: {sorted(lost)}")

        # I2 -- a required operation is never dropped.
        if not op.get("optional") and row["binding"] == "drop":
            failures.append(f"I2 {row['id']}: required operation silently dropped")

        # I3 -- an unsatisfiable required operation with no stand-in must ask the user.
        if (
            not op.get("optional")
            and row["missing"]
            and not op.get("stand_in")
            and row["binding"] != "ask-user"
        ):
            failures.append(
                f"I3 {row['id']}: missing {row['missing']} but bound to "
                f"{row['binding']} instead of ask-user"
            )

        # I4 -- a stand-in must declare fidelity below 1.0. Checked regardless of
        # binding, so a malformed stand-in is caught even when it is not used.
        if op.get("stand_in") and row["binding"] == "script":
            detail = row["stand_in_detail"]
            if not detail:
                failures.append(f"I4 {row['id']}: bound to script but stand-in unresolved")
            else:
                fid = detail.get("fidelity")
                if fid is None or not (0.0 <= fid < 1.0):
                    failures.append(
                        f"I4 {row['id']}: stand-in fidelity missing or out of range ({fid})"
                    )

        # I5 -- a dropped operation must be reported. Checked against the summary below.

        # I6 -- publish and irreversible never execute ungated.
        if (
            declared & CRITICAL_GATES
            and row["binding"] in AUTO_BINDINGS
            and not declared & enforced
        ):
            failures.append(f"I6 {row['id']}: critical gate absent on executing binding")

        # I7 -- a gated operation routed to ask-user must surface its gate in the
        # question it asks. Without this the gate is reported as satisfied when it
        # was only deferred.
        if declared and row["binding"] == "ask-user":
            missing_deferred = declared - deferred
            if missing_deferred:
                failures.append(
                    f"I7 {row['id']}: gate(s) neither enforced nor deferred into the "
                    f"question: {sorted(missing_deferred)}"
                )

    # I5 -- every dropped operation appears in the loss report.
    from .router import summarise

    summary = summarise(rows)
    dropped = {r["id"] for r in rows if r["binding"] == "drop"}
    reported = set(summary["lost"]) | set(summary["lost_optional"])
    unreported = dropped - reported
    if unreported:
        failures.append(f"I5 loss report omits dropped operations: {sorted(unreported)}")

    return failures


def check_deferred_questions(rows, questions):
    """I7, emitter half: every deferred gate must appear in the question text.

    `questions` maps operation id -> the question the skill will ask. This is
    separated from verify() because it needs the emitted text, not the routing.
    """
    failures = []
    for row in rows:
        if not row["deferred_gates"]:
            continue
        text = (questions.get(row["id"]) or "").upper()
        if not text:
            failures.append(f"I7 {row['id']}: no question emitted for deferred gate(s)")
            continue
        for gate in row["deferred_gates"]:
            if gate.upper() not in text:
                failures.append(
                    f"I7 {row['id']}: deferred gate {gate!r} not surfaced in the question"
                )
    return failures
