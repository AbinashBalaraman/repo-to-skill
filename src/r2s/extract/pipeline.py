"""The extraction pipeline: evidence sources fused into a draft inventory.

Structure comes from T0 declared dialects, refined by T1 and T4.
Capabilities come from T1 traced effects plus T2 catalog matches, always.
Gates come from T3 documentation plus effect semantics, always.

They run concurrently and corroborate. Running them as a fallback ladder -- stop at
the first source that produces anything -- yields either an inventory with no
requirements or requirements with no structure.
"""

from .. import config
from ..util.io import slugify
from . import coalesce as coalesce_mod
from . import confidence as confidence_mod
from . import docs as docs_mod
from . import effects as effects_mod
from . import phases
from .dialects import registry as dialect_registry

GENERATIVE = "generative"

_EFFECT_RANK = {
    "read-local": 0,
    "read-external": 1,
    "effectful-local": 2,
    "effectful-external": 3,
}


def extract(snapshot, profile_result, vocab, providers, effects_catalog, standins, source=None):
    """Run every evidence source and return (draft_inventory, warnings)."""
    warnings = []
    source = source or {}

    # ---- T0: declared operations
    candidates, dialects_ran = dialect_registry.run(snapshot, profile_result)

    # ---- T1: traced effects
    sites = effects_mod.trace(snapshot, profile_result.entrypoints, effects_catalog)
    if not sites:
        warnings.append(
            "no effect sites were traced; requirements will rest on declaration and "
            "dependency evidence alone"
        )

    # ---- T2: provider catalog
    matches, unknown = providers.match(profile_result.manifests.get("ecosystems") or {})
    if unknown:
        warnings.append(
            f"{len(unknown)} dependency/ies not in the catalog "
            f"(e.g. {', '.join(n for _, n in unknown[:5])}); they are recorded as "
            f"unresolved rather than assumed to require nothing"
        )

    # ---- T3: documentation
    gate_signals, optional_signals = docs_mod.scan(snapshot)

    # ---- attribute
    unattributed = []
    if candidates:
        operations, unattributed = _operations_from_candidates(
            candidates, sites, matches, gate_signals, optional_signals, vocab, standins
        )
    else:
        operations = _operations_from_sites(sites, vocab, standins)
        if operations:
            warnings.append(
                f"no declaration dialect matched; {len(operations)} operation(s) were "
                f"derived from traced effects alone, so ordering is unknown"
            )

    if not operations and matches:
        operations = _operations_from_providers(matches, vocab, standins)
        if operations:
            warnings.append(
                "no declarations and no traced effects; operations were derived from "
                "the dependency surface alone, so ordering and attribution are weak"
            )

    if unattributed:
        warnings.append(
            f"{len(unattributed)} traced effect site(s) could not be attributed to any "
            f"operation: the file has several candidates with no function body to "
            f"attribute against. They are left unattributed rather than smeared across "
            f"all of them, which would have reported false confidence."
        )

    # ---- unresolved dependencies become one reviewable operation, never silence
    if unknown:
        operations.append(_unresolved_operation(unknown, vocab, standins))

    operations = [op for op in operations if op is not None]
    operations, coalesce_notes = coalesce_mod.coalesce(operations, profile_result.repo_class)
    warnings.extend(coalesce_notes)

    # Strip internal bookkeeping so the emitted artifact matches the schema exactly.
    # (`_order` used to be left on every operation, which both leaked an internal field
    # into the artifact and violated the schema's additionalProperties: false.)
    for op in operations:
        op.pop("_merged", None)
        op.pop("_dropped_as_plumbing", None)

    operations.sort(key=lambda o: o["id"])

    draft = {
        "schema_version": config.SCHEMA_VERSION,
        "source": source,
        "repo_class": profile_result.repo_class,
        "repo_class_confidence": profile_result.class_confidence,
        "inventory_status": "draft",
        "extraction": {
            "converter_version": config.CONVERTER_VERSION,
            "catalog_version": providers.version,
            "generated_at": None,
            "sources_run": _sources_run(dialects_ran, sites, matches, gate_signals),
            "truncated": snapshot.truncated,
        },
        "profile": None,
        "operations": operations,
        "diagnostics": {
            "dialects_ran": dialects_ran,
            "entrypoints": [e.to_dict() for e in profile_result.entrypoints],
            "effect_sites": len(sites),
            "providers_matched": [m.id for m in matches],
            "unresolved_dependencies": [f"{eco}:{name}" for eco, name in unknown],
            "doc_gate_signals": len(gate_signals),
            "class_evidence": [list(e) for e in profile_result.class_evidence],
        },
    }
    return draft, warnings


def _sources_run(dialects_ran, sites, matches, gate_signals):
    ran = []
    if dialects_ran:
        ran.append("T0")
    if sites:
        ran.append("T1")
    if matches:
        ran.append("T2")
    if gate_signals:
        ran.append("T3")
    return ran


# ---------------------------------------------------------------- attribution


def _claim_sites(candidates, sites):
    """Attribute effect sites to candidates by file and, where known, by body range.

    Returns (claims, weak_ids, unattributed):
      claims        op_id -> [EffectSite]
      weak_ids      op_ids whose attribution is positional, not structural
      unattributed  sites that could not honestly be assigned to any candidate

    Two subtleties, both of which used to be wrong:

    Nested body ranges. A module-level `main()` contains every command function, so a
    naive containment test double-claims and attributes every effect in the file to
    `main` as well as to the real owner. A site is assigned to the INNERMOST
    containing range instead.

    Positional leftovers. Sites outside every body range (argparse subparsers have no
    body) used to be smeared onto *every* candidate lacking a body range. With several
    such candidates that is simply wrong, and it reported high confidence while being
    wrong. Now: exactly one rangeless candidate takes them and is flagged weak; several
    candidates means the sites cannot be attributed and are returned as unattributed
    rather than guessed at.
    """
    by_file = {}
    for site in sites:
        rel = site.loc.split(":")[0]
        by_file.setdefault(rel, []).append(site)

    claims = {c.op_id: [] for c in candidates}
    weak = set()
    unattributed = []

    for rel, file_sites in by_file.items():
        file_candidates = [c for c in candidates if c.loc.startswith(rel + ":")]
        ranged = [c for c in file_candidates if getattr(c, "body_range", None)]
        rangeless = [c for c in file_candidates if not getattr(c, "body_range", None)]

        taken = set()
        for site in file_sites:
            line = int(site.loc.split(":")[1])
            containing = [c for c in ranged if c.body_range[0] <= line <= c.body_range[1]]
            if not containing:
                continue
            innermost = min(containing, key=lambda c: c.body_range[1] - c.body_range[0])
            claims[innermost.op_id].append(site)
            taken.add(site.loc)

        leftover = [s for s in file_sites if s.loc not in taken]
        if not leftover:
            continue
        if len(rangeless) == 1:
            target = rangeless[0]
            claims[target.op_id].extend(leftover)
            weak.add(target.op_id)
        elif rangeless:
            unattributed.extend(leftover)

    return claims, weak, unattributed


def _operations_from_candidates(
    candidates, sites, matches, gate_signals, optional_signals, vocab, standins
):
    claims, weak, unattributed = _claim_sites(candidates, sites)
    operations = []
    for candidate in candidates:
        claimed = claims.get(candidate.op_id, [])
        operations.append(
            _build_operation(
                candidate,
                claimed,
                matches,
                gate_signals,
                optional_signals,
                vocab,
                standins,
                weak_attribution=candidate.op_id in weak,
            )
        )
    return operations, unattributed


def _build_operation(
    candidate,
    sites,
    matches,
    gate_signals,
    optional_signals,
    vocab,
    standins,
    weak_attribution=False,
):
    traced_caps = sorted({s.capability for s in sites})
    effect = _dominant_effect(sites)

    evidence = [candidate.evidence()] + [s.evidence() for s in sites]

    provider_hits = (
        [m for m in matches if set(m.capabilities) & set(traced_caps)] if traced_caps else []
    )
    for match in provider_hits:
        evidence.append(match.evidence())

    requires = list(traced_caps)
    ambiguous = any(m.ambiguous for m in provider_hits)
    corroboration = len({m.id for m in provider_hits})

    # Never silently "requires nothing". If nothing was traced for this operation,
    # say so explicitly at low confidence rather than emitting an empty requirement.
    if not requires:
        requires = ["external.call"]
        evidence.append(
            {
                "source": "proposed",
                "loc": candidate.loc,
                "detail": "no effect traced for this operation; requires external.call "
                "as a placeholder pending review",
            }
        )
        ambiguous = True

    # Gates: provider hints plus documentation language that shares vocabulary.
    gate_set = []
    for match in provider_hits:
        gate_set.extend(match.gate_hints)
    doc_hits = docs_mod.gates_for_operation(candidate.name, candidate.detail, gate_signals)
    for hit in doc_hits:
        gate_set.append(hit.gate)
        evidence.append(hit.evidence())
    gates = sorted(set(gate_set))

    optional = candidate.optional_hint or any(
        s.gate == "optional" and _shares_vocabulary(candidate.name, s.snippet)
        for s in optional_signals
    )

    confidence, notes = confidence_mod.fuse(
        evidence,
        corroboration=corroboration,
        ambiguous=ambiguous,
        weak_attribution=weak_attribution,
    )
    gate_confidence = (
        confidence_mod.MEDIUM
        if any(m.gate_hints for m in provider_hits)
        else (confidence_mod.LOW if gates else None)
    )

    stage = phases.infer(candidate.name, effect, requires, gates)

    operation = {
        "id": candidate.op_id,
        "name": candidate.name,
        "stage": stage,
        "effect": effect,
        "requires": vocab.validate(requires, f"operation {candidate.op_id!r}"),
        "optional": bool(optional),
        "deterministic": _is_deterministic(requires, effect, vocab),
        "gate": gates,
        "confidence": confidence,
        "evidence": confidence_mod.merge_sources(evidence),
        "summary": candidate.detail,
        "credentials": _credentials(provider_hits),
        "stand_in": _standin_for(requires, standins),
        "trigger": "manual",
        "entrypoint": {"kind": candidate.declaration_kind, "loc": candidate.loc},
        "notes": "; ".join(notes) if notes else "",
    }
    if gate_confidence:
        operation["gate_confidence"] = gate_confidence
    return operation


def _operations_from_sites(sites, vocab, standins):
    """No declarations: group traced effects into operations by file and capability."""
    groups = {}
    for site in sites:
        rel = site.loc.split(":")[0]
        groups.setdefault((rel, site.capability), []).append(site)

    operations = []
    for (rel, capability), group in sorted(groups.items()):
        stem = rel.rsplit("/", 1)[-1].removesuffix(".py")
        op_id = slugify(f"{capability.replace('.', '-')}-{stem}")
        evidence = [s.evidence() for s in group]
        requires = [capability]
        effect = _dominant_effect(group)
        confidence, notes = confidence_mod.fuse(evidence)
        operation = {
            "id": op_id,
            "name": f"{capability} in {rel}",
            "stage": phases.infer(capability, effect, requires, []),
            "effect": effect,
            "requires": vocab.validate(requires, f"operation {op_id!r}"),
            "optional": False,
            "deterministic": _is_deterministic(requires, effect, vocab),
            "gate": [],
            "confidence": confidence,
            "evidence": confidence_mod.merge_sources(evidence),
            "summary": f"derived from {len(group)} traced effect site(s)",
            "credentials": [],
            "stand_in": _standin_for(requires, standins),
            "trigger": "manual",
            "entrypoint": {"kind": "traced", "loc": group[0].loc},
            "notes": "; ".join(notes) if notes else "",
        }
        operations.append(operation)
    return operations


def _operations_from_providers(matches, vocab, standins):
    operations = []
    for match in matches:
        capabilities = sorted(set(match.capabilities))
        if not capabilities:
            continue
        op_id = slugify(f"use-{match.id}")
        evidence = [match.evidence()]
        confidence, notes = confidence_mod.fuse(
            evidence, corroboration=1, ambiguous=match.ambiguous
        )
        operation = {
            "id": op_id,
            "name": f"Use {match.id}",
            "stage": phases.infer(match.id, match.effect, capabilities, match.gate_hints),
            "effect": match.effect or "effectful-external",
            "requires": vocab.validate(capabilities, f"operation {op_id!r}"),
            "optional": False,
            "deterministic": _is_deterministic(capabilities, match.effect, vocab),
            "gate": sorted(set(match.gate_hints)),
            "gate_confidence": confidence_mod.MEDIUM if match.gate_hints else None,
            "confidence": confidence,
            "evidence": confidence_mod.merge_sources(evidence),
            "summary": f"dependency {match.matched_on} ({match.ecosystem})",
            "credentials": _credentials([match]),
            "stand_in": _standin_for(capabilities, standins),
            "trigger": "manual",
            "entrypoint": {"kind": "dependency", "loc": "manifest"},
            "notes": "; ".join(notes) if notes else "",
        }
        operation = {k: v for k, v in operation.items() if v is not None}
        operations.append(operation)
    return operations


def _unresolved_operation(unknown, vocab, standins):
    evidence = [
        {
            "source": "inferred",
            "loc": "manifest",
            "detail": f"unrecognised dependency {eco}:{name}",
        }
        for eco, name in unknown[:20]
    ]
    return {
        "id": "unresolved-external-calls",
        "name": "Unresolved external calls",
        "stage": "transform",
        "effect": "effectful-external",
        "requires": vocab.validate(["external.call"], "operation 'unresolved-external-calls'"),
        "optional": True,
        "deterministic": False,
        "gate": [],
        "confidence": confidence_mod.LOW,
        "evidence": confidence_mod.merge_sources(evidence),
        "summary": f"{len(unknown)} dependency/ies outside the catalog",
        "credentials": [],
        "stand_in": None,
        "trigger": "manual",
        "entrypoint": {"kind": "dependency", "loc": "manifest"},
        "notes": "Review: these dependencies may require capabilities the catalog "
        "cannot name. They are deliberately not assumed to require nothing.",
    }


# ---------------------------------------------------------------- helpers


def _dominant_effect(sites):
    if not sites:
        return "effectful-external"
    best = max(sites, key=lambda s: _EFFECT_RANK.get(s.effect, 0))
    return best.effect


def _is_deterministic(capabilities, effect, vocab):
    for capability in capabilities:
        if vocab.has(capability) and vocab.kind(capability) == GENERATIVE:
            return False
    return effect in ("read-local", "effectful-local")


def _credentials(matches):
    out = []
    seen = set()
    for match in matches:
        for cred in match.credentials:
            key = (cred.get("name"), cred.get("kind"))
            if key in seen:
                continue
            seen.add(key)
            out.append(
                {
                    "name": cred.get("name"),
                    "kind": cred.get("kind", "other"),
                    "env": cred.get("env"),
                }
            )
    return out


def _standin_for(capabilities, standins):
    """A stand-in only where the curated catalog declares one. Never invented."""
    defaults = getattr(standins, "defaults", {}) or {}
    for capability in capabilities:
        standin_id = defaults.get(capability)
        if standin_id:
            return standin_id
    return None


def _shares_vocabulary(name, snippet):
    words = {w for w in name.lower().replace("-", " ").replace("_", " ").split() if len(w) > 3}
    return any(word in snippet.lower() for word in words)
