"""Human-readable and machine-readable rendering of a resolved capability map."""

from .router import COVERAGE_LABEL


def render_table(rows, width_id=26):
    head = f"{'operation':{width_id}} {'stage':10} {'effect':18} {'binding':9} {'gates':24} note"
    lines = [head, "-" * len(head)]
    for row in rows:
        if row["binding"] == "script" and row["stand_in_detail"]:
            note = f"stand-in {row['stand_in']} f={row['stand_in_detail']['fidelity']}"
        elif row["binding"] == "ask-user":
            note = f"needs {', '.join(row['missing']) or 'human'}"
        elif row["binding"] == "drop":
            note = "optional, lost"
        else:
            note = ""
        gates = ",".join(row["gates"]) or (
            "deferred:" + ",".join(row["deferred_gates"]) if row["deferred_gates"] else "-"
        )
        lines.append(
            f"{row['id']:{width_id}} {row['stage']:10} {row['effect']:18} "
            f"{row['binding']:9} {gates:24} {note}"
        )
    return "\n".join(lines)


def render_summary(summary, profile):
    out = []
    out.append(f"  {profile.name}  ({profile.id})")
    out.append(f"  {COVERAGE_LABEL}")
    out.append(
        f"  coverage {summary['coverage']}   operations {summary['operations']}   "
        f"required {summary['required']}   optional {summary['optional']}"
    )
    out.append(f"  bindings {summary['bindings']}")
    out.append(
        "  all required operations executable: "
        f"{'yes' if summary['all_required_operations_executable'] else 'no'}"
    )
    if summary["halted"]:
        out.append(f"  halts for human input: {', '.join(summary['halted'])}")
    if summary["lost"]:
        out.append(f"  lost (required): {', '.join(summary['lost'])}")
    if summary["lost_optional"]:
        out.append(f"  lost (optional): {', '.join(summary['lost_optional'])}")
    for d in summary["degraded"]:
        out.append(f"  degraded: {d['id']} via {d['stand_in']} at fidelity {d['fidelity']}")
    if summary["deferred_gates"]:
        out.append(f"  gates deferred into questions: {', '.join(summary['deferred_gates'])}")
    out.append(
        f"  gates enforced: {', '.join(summary['gates']) or '-'}  ({summary['gate_count']} total)"
    )
    return "\n".join(out)


def build_report(inventory, profile, rows, summary, invariant_failures=None, warnings=None):
    """The machine-readable report the emitter consumes."""
    return {
        "schema_version": inventory.get("schema_version"),
        "source": inventory.get("source"),
        "repo_class": inventory.get("repo_class"),
        "inventory_status": inventory.get("inventory_status"),
        "profile": profile.to_dict(),
        "summary": summary,
        "rows": rows,
        "invariants": {
            "passed": not invariant_failures,
            "failures": invariant_failures or [],
        },
        "warnings": warnings or [],
    }


def render_report_text(report):
    lines = []
    lines.append("=" * 78)
    lines.append(f"  {report['profile']['name']}")
    lines.append(
        f"  source: {report['source'].get('url') or report['source'].get('kind')}"
        f"  class: {report['repo_class']}  status: {report['inventory_status']}"
    )
    lines.append("=" * 78)
    lines.append(render_table(report["rows"]))
    lines.append("")
    lines.append(render_summary(report["summary"], _ProfileView(report["profile"])))
    if report["warnings"]:
        lines.append("")
        for w in report["warnings"]:
            lines.append(f"  WARNING: {w}")
    lines.append("")
    if report["invariants"]["passed"]:
        lines.append("  INVARIANTS: PASS (I1-I7)")
    else:
        lines.append("  INVARIANTS: FAIL")
        for f in report["invariants"]["failures"]:
            lines.append(f"    - {f}")
    return "\n".join(lines)


class _ProfileView:
    """Adapter so render_summary can take a plain dict from a report."""

    def __init__(self, data):
        self.id = data["id"]
        self.name = data["name"]
