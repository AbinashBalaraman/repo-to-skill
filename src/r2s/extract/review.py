"""Review report generation.

The review tool *is* the labelling tool -- the same loop that produces goldens for the
eval is a product feature. So the report is ordered by what needs attention, never
alphabetically: low confidence first, then gates, then the rest. A reviewer who reads
only the top of this file has seen the parts most likely to be wrong.
"""

ATTENTION_ORDER = {"low": 0, "medium": 1, "high": 2, None: 3}


def _needs_attention(operation):
    if operation["confidence"] == "low":
        return True
    if operation.get("gate"):
        return True
    return operation["requires"] == ["external.call"]


def build_review(draft, warnings=None):
    warnings = warnings or []
    operations = list(draft["operations"])
    diagnostics = draft.get("diagnostics", {})

    ranked = sorted(
        operations,
        key=lambda o: (
            ATTENTION_ORDER.get(o["confidence"], 3),
            0 if o.get("gate") else 1,
            o["id"],
        ),
    )

    low = [o for o in operations if o["confidence"] == "low"]
    gated = [o for o in operations if o.get("gate")]
    placeholder = [o for o in operations if o["requires"] == ["external.call"]]

    lines = []
    lines.append("# Inventory review")
    lines.append("")
    source = draft.get("source") or {}
    lines.append(f"Source: `{source.get('url') or source.get('kind') or 'unknown'}`")
    if source.get("sha"):
        lines.append(f"Pinned: `{source['sha']}`")
    lines.append(
        f"Repo class: **{draft.get('repo_class')}** ({draft.get('repo_class_confidence')})"
    )
    lines.append(f"Status: **{draft.get('inventory_status')}**")
    lines.append(f"Operations: **{len(operations)}**")
    lines.append("")

    if diagnostics.get("class_evidence"):
        lines.append(
            "Class evidence: "
            + "; ".join(
                f"{src}: {detail}"
                for src, _, detail in [(e[0], e[1], e[2]) for e in diagnostics["class_evidence"]]
            )
        )
        lines.append("")

    if warnings:
        lines.append("## Warnings")
        lines.append("")
        for warning in warnings:
            lines.append(f"- {warning}")
        lines.append("")

    lines.append("## Needs attention")
    lines.append("")
    lines.append(f"- **{len(low)}** operation(s) at low confidence")
    lines.append(f"- **{len(gated)}** operation(s) carry gates")
    lines.append(
        f"- **{len(placeholder)}** operation(s) have no traced requirement "
        f"and hold `external.call` as a placeholder"
    )
    if diagnostics.get("unresolved_dependencies"):
        lines.append(
            f"- **{len(diagnostics['unresolved_dependencies'])}** dependency/ies "
            f"outside the catalog: "
            f"{', '.join(diagnostics['unresolved_dependencies'][:8])}"
        )
    lines.append("")

    lines.append("## Operations, most uncertain first")
    lines.append("")
    lines.append("| op | stage | effect | requires | gate | conf | evidence |")
    lines.append("|---|---|---|---|---|---|---|")
    for operation in ranked:
        gates = ", ".join(operation.get("gate") or []) or "-"
        requires = ", ".join(operation["requires"])
        sources = ", ".join(sorted({e["source"] for e in operation.get("evidence", [])}))
        lines.append(
            f"| `{operation['id']}` | {operation['stage']} | {operation['effect']} | "
            f"{requires} | {gates} | {operation['confidence']} | {sources} |"
        )
    lines.append("")

    lines.append("## How to confirm this inventory")
    lines.append("")
    lines.append(
        "Edit the draft JSON to produce `inventory.json`, then re-run the router "
        "against it. Confirmation is mandatory when the inventory contains any "
        "low-confidence operation, any `external.call` placeholder, or any "
        "unresolved dependency."
    )
    lines.append("")
    lines.append(
        "Re-extraction merges rather than clobbers: reviewed decisions win "
        "unless the underlying evidence changed."
    )
    return "\n".join(lines)


def merge_reviewed(draft, previous):
    """Re-extraction merge. Reviewed decisions win unless their evidence changed.

    `previous` is a reviewed inventory. An operation present in both is kept as
    reviewed unless the draft's evidence for it differs, in which case the draft
    wins and the change is reported.
    """
    if not previous:
        return draft, []

    changes = []
    old_by_id = {op["id"]: op for op in previous.get("operations", [])}
    merged = []

    for operation in draft["operations"]:
        old = old_by_id.get(operation["id"])
        if old is None:
            changes.append(f"new: {operation['id']}")
            merged.append(operation)
            continue

        old_evidence = {e.get("loc") for e in old.get("evidence", [])}
        new_evidence = {e.get("loc") for e in operation.get("evidence", [])}
        if old_evidence == new_evidence:
            kept = dict(old)
            kept["confidence"] = old.get("confidence", operation["confidence"])
            merged.append(kept)
        else:
            changes.append(f"evidence changed, draft wins: {operation['id']}")
            merged.append(operation)

    for op_id in old_by_id:
        if op_id not in {op["id"] for op in draft["operations"]}:
            changes.append(f"disappeared from the repo: {op_id}")

    draft = dict(draft)
    draft["operations"] = merged
    return draft, changes
