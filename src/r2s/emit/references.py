"""The four reference files, plus the blocked-question text.

`references/` is one level deep and holds the detail that would blow the SKILL.md
budget: the full binding table with evidence, the stand-in caveats, the questions to
ask, and the gate list.
"""

import shutil
from pathlib import Path

from .. import execute as execute_mod
from ..util.io import write_text

# Gates are fail-closed, so the safe default is always "do not do the thing".
_SAFE_DEFAULT = "Do not proceed."

_GATE_QUESTION = {
    "publish": "publish this to an external system",
    "spend": "spend money",
    "irreversible": "take an action that cannot be undone",
    "legal": "take an action with legal or rights implications",
    "privacy": "handle personal data or change visibility",
}


def build_questions(blocked_rows):
    """Map operation id -> the question the skill will ask.

    I7's emitter half (`invariants.check_deferred_questions`) asserts that every gate
    deferred onto an ask-user operation appears in the question text. The gate names are
    written in upper case deliberately, so the check is unambiguous and a reader cannot
    miss what is being asked.
    """
    questions = {}
    for row in blocked_rows:
        gates = row.get("deferred_gates") or []
        needs = ", ".join(row["missing"]) or "a human decision"

        if gates:
            asks = " and ".join(_GATE_QUESTION.get(g, g) for g in gates)
            text = (
                f"`{row['id']}` needs {needs}. It would {asks}. "
                f"Gates: {', '.join(g.upper() for g in gates)}. {_SAFE_DEFAULT}"
            )
        else:
            text = (
                f"`{row['id']}` needs {needs}, which this harness cannot provide. {_SAFE_DEFAULT}"
            )
        questions[row["id"]] = text
    return questions


def _bindings_md(inventory, profile, rows, summary):
    lines = [
        "# Bindings",
        "",
        f"Resolved for the **{profile.name}** profile (`{profile.id}`).",
        "",
        "A binding is computed from the operation and the harness profile. It is never "
        "authored, and it differs per harness — the same repository resolves differently "
        "on a different profile.",
        "",
        "| Operation | Phase | Effect | Requires | Binding | Gates | Confidence |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in sorted(rows, key=lambda r: r["id"]):
        lines.append(
            f"| `{row['id']}` | {row['stage']} | {row['effect']} | "
            f"{', '.join(row['requires'])} | {row['binding']} | "
            f"{', '.join(row['gates']) or '-'} | {row['confidence']} |"
        )
    lines += [
        "",
        "## Evidence",
        "",
        "Every operation carries the evidence its requirements rest on. A requirement "
        "without evidence is a guess.",
        "",
    ]
    for row in sorted(rows, key=lambda r: r["id"]):
        lines.append(f"### `{row['id']}`")
        lines.append("")
        for item in row.get("evidence") or []:
            detail = item.get("detail") or ""
            lines.append(f"- `{item.get('source')}` — {item.get('loc')}: {detail}")
        if not row.get("evidence"):
            lines.append("- (no evidence recorded)")
        lines.append("")
    return "\n".join(lines)


def _degradation_md(rows, summary, standins):
    degraded = [r for r in rows if r["binding"] == "script" and r["stand_in_detail"]]
    lines = [
        "# Degradation",
        "",
        "A stand-in does the same job worse. Fidelity is declared data from the curated "
        "catalog, not a generated estimate, and is always below 1.0 — a substitute at "
        "1.0 would not be a substitute.",
        "",
    ]
    if not degraded:
        lines += ["Nothing is degraded on this harness.", ""]
        return "\n".join(lines)

    lines += [
        f"Coverage index: **{summary['coverage']}**.",
        "",
        "| Operation | Stand-in | Fidelity | Runs as | What it does instead | Caveat |",
        "|---|---|---|---|---|---|",
    ]
    for row in sorted(degraded, key=lambda r: r["id"]):
        detail = row["stand_in_detail"]
        lines.append(
            f"| `{row['id']}` | `{row['stand_in']}` | {detail['fidelity']} | "
            f"{_runs_as(row['stand_in'], standins)} | "
            f"{detail['desc']} | {detail.get('caveat', '')} |"
        )

    runnable, blocked = _executable_split(standins, {r["stand_in"] for r in degraded})
    lines += ["", "## Running these steps", ""]
    if runnable:
        lines += [
            "These steps have an executable stand-in in `scripts/`. Each takes a JSON "
            "job on stdin and writes its artifacts to the working directory:",
            "",
            "```",
            "python scripts/<name>.py   # job JSON on stdin, result JSON on stdout",
            "```",
            "",
            f"Included: {', '.join(f'`scripts/{name}`' for name in runnable)}.",
            "",
        ]
    if blocked:
        lines += [
            "These have **no** executable stand-in, and are reported rather than run:",
            "",
        ]
        for standin_id, reason in blocked:
            lines.append(f"- `{standin_id}` — {reason}")
        lines.append("")

    lines += [
        "> Degraded output must be reported as degraded. Presenting a stand-in as the "
        "intended result is the failure this table exists to prevent.",
        "",
    ]
    return "\n".join(lines)


def _runs_as(standin_id, standins):
    """The script name for a stand-in, or an explicit note that there is none."""
    try:
        entry = standins.get(standin_id)
    except Exception:
        return "not runnable"
    spec = execute_mod.exec_spec(entry)
    if spec is None:
        return "not runnable"
    return f"`scripts/{spec['script']}`"


def _executable_split(standins, standin_ids):
    """Split stand-in ids into (runnable script names, [(id, why-not)])."""
    runnable = []
    blocked = []
    for standin_id in sorted(standin_ids):
        try:
            entry = standins.get(standin_id)
        except Exception:
            blocked.append((standin_id, "not present in the catalog"))
            continue
        spec = execute_mod.exec_spec(entry)
        if spec is None:
            blocked.append((standin_id, execute_mod.unavailable_reason(entry)))
        else:
            runnable.append(spec["script"])
    return runnable, blocked


def _blocked_md(questions, rows):
    blocked = [r for r in rows if r["binding"] == "ask-user"]
    lines = [
        "# Blocked — ask before proceeding",
        "",
        "These operations cannot run on this harness. Each is asked rather than "
        "silently skipped, and each carries a safe default.",
        "",
    ]
    if not blocked:
        lines += ["Nothing is blocked on this harness.", ""]
        return "\n".join(lines)

    for row in sorted(blocked, key=lambda r: r["id"]):
        lines.append(f"## `{row['id']}`")
        lines.append("")
        lines.append(f"**Ask:** {questions.get(row['id'], '')}")
        lines.append("")
        if row["deferred_gates"]:
            lines.append(
                f"Gates deferred into this question: "
                f"{', '.join(g.upper() for g in row['deferred_gates'])}. They are not "
                f"enforced by code here — they are enforced by asking."
            )
            lines.append("")
        lines.append(f"Missing capabilities: {', '.join(row['missing']) or 'none'}")
        lines.append("")
    return "\n".join(lines)


def _gates_md(rows, summary):
    lines = [
        "# Gates",
        "",
        "Gates are fail-closed and profile-invariant. A stronger harness buys a better "
        "binding, never a weaker gate.",
        "",
        "| Gate | Meaning |",
        "|---|---|",
        "| `publish` | sends something to an external system |",
        "| `spend` | costs money |",
        "| `irreversible` | cannot be undone |",
        "| `legal` | rights, licensing or disclosure implications |",
        "| `privacy` | personal data or visibility changes |",
        "",
    ]
    gated = [r for r in rows if r["gates"]]
    if gated:
        lines += ["## Enforced on this harness", "", "| Operation | Gates |", "|---|---|"]
        for row in sorted(gated, key=lambda r: r["id"]):
            lines.append(f"| `{row['id']}` | {', '.join(row['gates'])} |")
        lines.append("")

    deferred = [r for r in rows if r["deferred_gates"]]
    if deferred:
        lines += [
            "## Deferred into questions",
            "",
            "These gates are not enforced by code because the operation cannot run on "
            "this harness. They are surfaced in the question instead — see "
            "`blocked.md`. A deferred gate is not a satisfied gate.",
            "",
            "| Operation | Deferred gates |",
            "|---|---|",
        ]
        for row in sorted(deferred, key=lambda r: r["id"]):
            lines.append(f"| `{row['id']}` | {', '.join(row['deferred_gates'])} |")
        lines.append("")

    if not gated and not deferred:
        lines += ["No operation carries a gate on this harness.", ""]
    return "\n".join(lines)


def write_all(references_dir, inventory, profile, rows, summary, questions, standins):
    references_dir = Path(references_dir)
    write_text(references_dir / "bindings.md", _bindings_md(inventory, profile, rows, summary))
    write_text(references_dir / "degradation.md", _degradation_md(rows, summary, standins))
    write_text(references_dir / "blocked.md", _blocked_md(questions, rows))
    write_text(references_dir / "gates.md", _gates_md(rows, summary))
    return references_dir


def write_scripts(skill_dir, rows, standins):
    """Copy only the stand-in scripts actually needed.

    A stand-in names its script in the catalog's `exec` block, and the file ships with
    r2s under the same licence. Repository code is never copied into an emitted skill --
    that is a licensing decision, and it is also the security boundary: the emitted
    skill runs our reviewed scripts, never the analysed repo's.

    A stand-in with no `exec` block contributes no script. `degradation.md` records why,
    so an absent script is visible rather than mysterious.
    """
    used = sorted({r["stand_in"] for r in rows if r["stand_in"]})
    if not used:
        return []

    copied = []
    scripts_dir = Path(skill_dir) / "scripts"
    for standin_id in used:
        spec = execute_mod.exec_spec(standins.get(standin_id))
        if spec is None:
            continue
        # Resolved through the executor's containment check rather than joined by hand.
        source = execute_mod.script_path(spec["script"])
        if not source.is_file():
            continue
        scripts_dir.mkdir(parents=True, exist_ok=True)
        target = scripts_dir / spec["script"]
        shutil.copyfile(source, target)
        copied.append(target.name)
    return copied
