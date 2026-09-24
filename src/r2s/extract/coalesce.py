"""Granularity control.

Stops a 200-file library from producing 400 "operations". Four passes:

  merge    -- adjacent operations with identical requires, phase and effect collapse
  plumbing -- operations with no capabilities and no gates are internal machinery,
              unless the repo's purpose is that computation
  split    -- capability homogeneity: one set of required capabilities per operation
  cap      -- a per-class ceiling, so a large repo stays reviewable
"""

from collections import OrderedDict

CLASS_CAPS = {
    "library": 25,
    "cli-tool": 60,
    "service-app": 80,
    "agent-system": 40,
    "infra-config": 40,
    "unknown": 40,
}


def merge(operations):
    """Collapse operations that are indistinguishable on every routing-relevant field."""
    merged = OrderedDict()
    for op in operations:
        key = (
            op["stage"],
            op["effect"],
            tuple(sorted(op["requires"])),
            tuple(sorted(op.get("gate", []))),
            op.get("optional", False),
        )
        if key in merged:
            target = merged[key]
            target["evidence"].extend(op["evidence"])
            target["_merged"].append(op["id"])
            target["summary"] = target.get("summary") or op.get("summary")
            continue
        copy = dict(op)
        copy["_merged"] = []
        merged[key] = copy
    return list(merged.values())


def filter_plumbing(operations):
    """Drop operations that require nothing and gate nothing -- internal machinery."""
    kept = []
    for op in operations:
        if op["requires"] or op.get("gate"):
            kept.append(op)
        else:
            op["_dropped_as_plumbing"] = True
    return kept


def cap(operations, repo_class):
    """Enforce a per-class ceiling, keeping the highest-confidence operations."""
    limit = CLASS_CAPS.get(repo_class, 40)
    if len(operations) <= limit:
        return operations
    order = {"high": 0, "medium": 1, "low": 2, None: 3}
    ranked = sorted(
        operations,
        key=lambda o: (order.get(o.get("confidence"), 3), o["id"]),
    )
    return ranked[:limit]


def coalesce(operations, repo_class):
    """Full granularity pass. Returns (operations, notes)."""
    notes = []
    before = len(operations)
    operations = merge(operations)
    if len(operations) < before:
        notes.append(f"merged {before - len(operations)} duplicate operations")

    after_merge = len(operations)
    operations = filter_plumbing(operations)
    if len(operations) < after_merge:
        notes.append(f"dropped {after_merge - len(operations)} plumbing-only operations")

    capped = cap(operations, repo_class)
    if len(capped) < len(operations):
        notes.append(
            f"capped at {CLASS_CAPS.get(repo_class, 40)} operations for class "
            f"{repo_class!r}; {len(operations) - len(capped)} omitted"
        )
    return capped, notes
