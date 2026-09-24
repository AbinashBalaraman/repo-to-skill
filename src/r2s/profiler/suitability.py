"""Suitability gate.

Hybrid policy. Three outcomes, never a broken skill:

  suitable       -- operations were extracted; proceed with a full capability skill
  knowledge-only -- no operations, but real documentation or API surface exists;
                    emit a knowledge skill via Skill_Seekers, clearly labelled as
                    having no capability map
  not-suitable   -- no operations and no substantial content; refuse with reasons

Criteria are deliberately few and empirical. A long a-priori rubric rejects good
repos and admits bad ones; these grow from fixture failures instead.
"""

REFUSE = "not-suitable"
KNOWLEDGE = "knowledge-only"
SUITABLE = "suitable"


class Verdict:
    def __init__(self, status, reasons, evidence=None):
        self.status = status
        self.reasons = reasons
        self.evidence = evidence or []

    @property
    def ok(self):
        return self.status != REFUSE

    def to_dict(self):
        return {"status": self.status, "reasons": self.reasons, "evidence": self.evidence}

    def render_markdown(self, source=None):
        lines = ["# Not suitable for capability conversion", ""]
        if source:
            lines += [f"Source: `{source}`", ""]
        lines.append(
            "This repository was not converted. The reasons are recorded here "
            "rather than emitted as a broken skill."
        )
        lines.append("")
        lines.append("## Reasons")
        lines.append("")
        for reason in self.reasons:
            lines.append(f"- {reason}")
        lines.append("")
        if self.evidence:
            lines.append("## Evidence")
            lines.append("")
            for item in self.evidence:
                lines.append(f"- {item}")
            lines.append("")
        lines.append("## What would change this")
        lines.append("")
        lines.append(
            "A discoverable entrypoint, or a declared workflow surface "
            "(CLI subcommands, HTTP routes, a DAG, CI jobs, a tool registry), "
            "would make this repo convertible."
        )
        return "\n".join(lines)


def assess(snapshot, manifests, entrypoints, repo_class, operations):
    """Decide the outcome. `operations` is the extracted inventory, possibly empty."""
    reasons = []
    evidence = []

    if operations:
        return Verdict(SUITABLE, ["operations extracted"], evidence)

    reasons.append("no operations could be extracted from this repository")

    # Is there real content worth capturing even without operations?
    doc_files = [
        f
        for f in snapshot.files
        if f.lower().endswith((".md", ".rst", ".txt")) or f.lower().startswith("docs/")
    ]
    code_files = [
        f for f in snapshot.files if f.endswith((".py", ".js", ".ts", ".go", ".rs", ".rb", ".java"))
    ]

    has_substance = len(doc_files) >= 3 or len(code_files) >= 10

    if repo_class == "unknown" and not entrypoints and not has_substance:
        reasons.append("no entrypoint, no manifest and no substantial content")
        evidence.append(
            f"{len(snapshot.files)} files scanned, {len(code_files)} source files, "
            f"{len(doc_files)} documentation files"
        )
        return Verdict(REFUSE, reasons, evidence)

    if not entrypoints:
        reasons.append("no discoverable entrypoint, so effects cannot be traced")

    if not manifests.get("languages"):
        reasons.append("no recognised language manifest")

    if has_substance:
        evidence.append(
            f"{len(code_files)} source files and {len(doc_files)} documentation "
            f"files present -- enough to emit a knowledge skill"
        )
        return Verdict(
            KNOWLEDGE,
            reasons + ["documentation or API surface is substantial enough to be worth capturing"],
            evidence,
        )

    evidence.append(f"only {len(snapshot.files)} files scanned; nothing substantial to capture")
    return Verdict(REFUSE, reasons + ["no substantial content to fall back on"], evidence)
