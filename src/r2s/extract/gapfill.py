"""T4: LLM gap-fill, as a contract rather than a dependency.

T4 fills gaps in the inventory that T0-T3 could not close -- an operation with no
traced effect, a gate the documentation implies but never states. It is the one
evidence source that is not derived from the repository, so it is fenced off hard:

  * **Off by default.** Nothing runs unless a proposals file is supplied explicitly.
  * **No model dependency.** `r2s` never calls a model, opens a socket, or reads an API
    key. `digest()` produces the compact structured summary a user feeds to whatever
    model they already have; `load_proposals()` reads what comes back. That keeps the
    converter stdlib-only, deterministic and offline, which is the whole reason it can
    be run over an untrusted repository.
  * **Structured, not a repo dump.** The digest contains operation ids, names,
    requirements, gates, confidence and the sources that ran. It does not contain file
    contents, so a model is never handed the repository.
  * **Never auto-accepted.** An applied proposal is `proposed` evidence, the operation's
    confidence is forced to LOW, and the operation carries a review note. A proposal
    cannot raise confidence, only lower it.

If a proposal cannot be validated against the vocabulary it is rejected loudly rather
than applied: an unknown capability ID is a hard error everywhere else in this project
and it must not become a soft one here.
"""

import json
from pathlib import Path

from ..util.io import redact

PROPOSALS_KIND = "r2s.proposals"
_MAX_RATIONALE = 200


def digest(draft):
    """A compact, structured summary of T0-T3 output, safe to hand to a model.

    Deliberately contains no repository text: only the normalised inventory, so the
    model sees the same shape the reviewer does and nothing else.
    """
    return {
        "kind": "r2s.digest",
        "repo_class": draft.get("repo_class"),
        "sources_run": (draft.get("extraction") or {}).get("sources_run", []),
        "tracing": (draft.get("diagnostics") or {}).get("tracing", {}),
        "operations": [
            {
                "id": op["id"],
                "name": op["name"],
                "stage": op["stage"],
                "effect": op["effect"],
                "requires": list(op["requires"]),
                "gate": list(op.get("gate", [])),
                "confidence": op["confidence"],
                "summary": op.get("summary", ""),
                "evidence_sources": sorted(
                    {item.get("source") for item in op.get("evidence", []) if item.get("source")}
                ),
            }
            for op in draft.get("operations", [])
        ],
    }


def load_proposals(source):
    """Read a proposals file or accept an already-parsed list.

    Shape:
        {"kind": "r2s.proposals",
         "proposals": [{"op_id": "fetch", "requires": ["http.request"],
                        "gate": ["spend"], "rationale": "..."}]}
    """
    if source is None:
        return []
    if isinstance(source, (list, tuple)):
        data = {"proposals": list(source)}
    elif isinstance(source, (str, Path)):
        text = Path(source).read_text(encoding="utf-8")
        data = json.loads(text)
    elif isinstance(source, dict):
        data = source
    else:
        raise ValueError("llm_proposals must be a path, a dict or a list of proposals")

    proposals = data.get("proposals") if isinstance(data, dict) else None
    if not isinstance(proposals, list):
        raise ValueError("proposals file must contain a 'proposals' list")

    cleaned = []
    for index, proposal in enumerate(proposals):
        if not isinstance(proposal, dict):
            raise ValueError(f"proposal {index} is not an object")
        op_id = proposal.get("op_id")
        if not isinstance(op_id, str) or not op_id:
            raise ValueError(f"proposal {index} is missing a string op_id")
        cleaned.append(
            {
                "op_id": op_id,
                "requires": _string_list(proposal.get("requires"), f"proposal {index}"),
                "gate": _string_list(proposal.get("gate"), f"proposal {index}"),
                "rationale": str(proposal.get("rationale") or "")[:_MAX_RATIONALE],
            }
        )
    return cleaned


def _string_list(value, where):
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{where}: requires/gate must be lists of strings")
    return value


def apply(operations, proposals, vocab):
    """Apply proposals to already-coalesced operations. Returns (operations, notes).

    A proposal for an operation that no longer exists (merged away by coalescing, or
    misspelled) is reported rather than dropped silently.
    """
    if not proposals:
        return operations, []

    by_id = {op["id"]: op for op in operations}
    notes = []
    applied = 0
    unknown_ops = []

    for proposal in proposals:
        operation = by_id.get(proposal["op_id"])
        if operation is None:
            unknown_ops.append(proposal["op_id"])
            continue

        requires = sorted(set(operation["requires"]) | set(proposal["requires"]))
        # An unknown capability is a hard error, here as everywhere else.
        operation["requires"] = vocab.validate(
            requires, f"T4 proposal for operation {operation['id']!r}"
        )

        gates = sorted(set(operation.get("gate", [])) | set(proposal["gate"]))
        if gates:
            operation["gate"] = gates
            operation["gate_confidence"] = "low"

        rationale = redact(proposal["rationale"])
        operation["evidence"] = _with_proposal(
            operation.get("evidence", []),
            operation["entrypoint"]["loc"],
            rationale,
        )
        # A model proposal can never raise confidence, and is never auto-accepted.
        operation["confidence"] = "low"
        operation["notes"] = _append_note(
            operation.get("notes", ""),
            "T4 proposal applied (proposed evidence only, low confidence): confirm or "
            "remove before the inventory is reviewed",
        )
        applied += 1

    if applied:
        notes.append(
            f"T4: {applied} LLM proposal(s) applied as low-confidence, unconfirmed "
            f"evidence; every affected operation needs human confirmation"
        )
    if unknown_ops:
        notes.append(
            f"T4: {len(unknown_ops)} proposal(s) referenced an operation that does not "
            f"exist after coalescing ({', '.join(sorted(unknown_ops)[:5])}); they were "
            f"not applied"
        )
    return operations, notes


def _with_proposal(evidence, loc, rationale):
    detail = "T4 LLM proposal (unconfirmed)"
    if rationale:
        detail = f"{detail}: {rationale}"
    return [*evidence, {"source": "proposed", "loc": loc, "detail": detail}]


def _append_note(existing, note):
    return f"{existing}; {note}" if existing else note


__all__ = ["PROPOSALS_KIND", "apply", "digest", "load_proposals"]
