"""The capability map: route each operation against a harness profile.

Ported from the validated prototype. Three defects fixed relative to it:

  1. `halted` / `lost` / `all_required_operations_executable` now consider only
     required operations. The prototype counted dropped *optional* operations as
     failure.
  2. `deferred_gates` is now emitted alongside `gates`. Previously a gated operation
     routed to `ask-user` silently lost its gate, because `gates_for` returned [].
  3. Unknown capability IDs and non-catalog stand-ins are now hard errors raised by
     the caller's vocabulary/catalog validation, not silently treated as missing.
"""

AUTO_BINDINGS = ("native", "mcp", "script")
ALL_BINDINGS = ("native", "mcp", "script", "ask-user", "drop")

# Coverage index weights. Deliberately coarse; documented wherever it is printed.
COVERAGE_WEIGHT = {"native": 1.0, "mcp": 0.9, "ask-user": 0.5, "drop": 0.0}
COVERAGE_LABEL = (
    "coverage index (heuristic: native 1.0, mcp 0.9, script = declared fidelity, "
    "ask-user 0.5, drop 0.0)"
)


def route(operation, profile):
    """First match wins. Returns (binding, standin_id_used_or_None)."""
    requires = operation["requires"]

    if all(profile.has_native(c) for c in requires):
        return "native", None

    if all(profile.has(c) for c in requires) and any(profile.has_mcp(c) for c in requires):
        return "mcp", None

    standin_id = operation.get("stand_in")
    if standin_id:
        return "script", standin_id

    if operation.get("optional"):
        return "drop", None

    return "ask-user", None


def gates_for(operation, binding):
    """Return (enforced_gates, deferred_gates).

    Gates attach only to executing bindings. On `ask-user` the operation cannot
    execute, so the gate is DEFERRED into the question the skill asks rather than
    dropped -- it must still be surfaced there. On `drop` nothing runs, so the gate
    is moot but the loss is reported.
    """
    declared = list(operation.get("gate", []))
    if binding in AUTO_BINDINGS:
        return declared, []
    if binding == "ask-user":
        return [], declared
    return [], []


def coverage_weight(binding, standin_fidelity):
    if binding == "script":
        return standin_fidelity if standin_fidelity is not None else 0.5
    return COVERAGE_WEIGHT.get(binding, 0.0)


def resolve(operations, profile, vocab, standins):
    """Resolve every operation against one profile.

    Validates capability IDs and stand-in references as it goes: both raise rather
    than degrade, so a converter bug can never masquerade as a capability gap.
    """
    rows = []
    for operation in operations:
        op_id = operation["id"]
        vocab.validate(operation["requires"], f"operation {op_id!r} requires")

        binding, standin_id = route(operation, profile)
        gates, deferred = gates_for(operation, binding)

        standin = None
        fidelity = None
        if binding == "script" and standin_id:
            standin = standins.validate(standin_id, f"operation {op_id!r}")
            fidelity = standin["fidelity"]

        rows.append(
            {
                "id": op_id,
                "name": operation["name"],
                "stage": operation["stage"],
                "effect": operation["effect"],
                "requires": list(operation["requires"]),
                "optional": bool(operation.get("optional", False)),
                "deterministic": bool(operation.get("deterministic", False)),
                "confidence": operation.get("confidence"),
                "binding": binding,
                "gates": gates,
                "deferred_gates": deferred,
                "stand_in": standin_id if binding == "script" else None,
                "stand_in_detail": standin,
                "coverage": coverage_weight(binding, fidelity),
                "missing": [c for c in operation["requires"] if not profile.has(c)],
                "credentials": list(operation.get("credentials", [])),
            }
        )
    return rows


def summarise(rows):
    """Roll up a resolved set. Required vs optional is tracked separately throughout:
    dropping an optional operation is not a failure, and reporting it as one was the
    prototype's bug."""
    required = [r for r in rows if not r["optional"]]
    optional = [r for r in rows if r["optional"]]

    def ids(seq, binding):
        return [r["id"] for r in seq if r["binding"] == binding]

    gates = [g for r in rows for g in r["gates"]]
    deferred = [g for r in rows for g in r["deferred_gates"]]
    covered = sum(r["coverage"] for r in required)

    return {
        "operations": len(rows),
        "required": len(required),
        "optional": len(optional),
        "bindings": {b: sum(1 for r in rows if r["binding"] == b) for b in ALL_BINDINGS},
        "coverage": round(covered / len(required), 3) if required else 0.0,
        "gates": sorted(set(gates)),
        "gate_count": len(gates),
        "deferred_gates": sorted(set(deferred)),
        "halted": ids(required, "ask-user"),
        "halted_optional": ids(optional, "ask-user"),
        "lost": ids(required, "drop"),
        "lost_optional": ids(optional, "drop"),
        "degraded": [
            {
                "id": r["id"],
                "stand_in": r["stand_in"],
                "fidelity": r["stand_in_detail"]["fidelity"],
                "instead": r["stand_in_detail"]["desc"],
            }
            for r in rows
            if r["binding"] == "script" and r["stand_in_detail"]
        ],
        # Renamed from `runs_end_to_end`: "end to end" is pipeline-shaped and inert
        # for a library, where the concept does not apply.
        "all_required_operations_executable": not any(
            r["binding"] in ("ask-user", "drop") for r in required
        ),
    }
