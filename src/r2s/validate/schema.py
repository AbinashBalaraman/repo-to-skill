"""Targeted inventory validation.

This is deliberately NOT a general JSON Schema engine. It checks the constraints that
actually matter for this pipeline, in the order that produces the most useful error:

  - required fields and types
  - enum membership for stage, effect, gate, confidence
  - capability IDs against the vocabulary (hard fail, never "missing")
  - stand-in IDs against the curated catalog (hard fail, never invented)
  - gate_confidence present whenever gates are declared

schemas/inventory.schema.json remains the normative contract; this is the enforcement
used by the CLI and the eval.
"""

import re

from .. import execute
from ..capability.vocab import VocabError

ID_PATTERN = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

TOP_REQUIRED = (
    "schema_version",
    "source",
    "repo_class",
    "inventory_status",
    "extraction",
    "operations",
)

OP_REQUIRED = (
    "id",
    "name",
    "stage",
    "effect",
    "requires",
    "optional",
    "deterministic",
    "gate",
    "confidence",
    "evidence",
)

STAGES = {"acquire", "prepare", "transform", "verify", "deliver", "observe"}
EFFECTS = {"read-local", "read-external", "effectful-local", "effectful-external"}
GATES = {"publish", "spend", "irreversible", "legal", "privacy"}
CONFIDENCE = {"high", "medium", "low"}
REPO_CLASSES = {"library", "cli-tool", "service-app", "agent-system", "infra-config", "unknown"}
STATUSES = {"draft", "reviewed", "accepted-unreviewed"}
EVIDENCE_SOURCES = {"declared", "traced", "inferred", "documented", "proposed"}
TRIGGERS = {"manual", "scheduled", "event"}
CREDENTIAL_KINDS = {"apikey", "oauth", "token", "password", "other"}
# reducing -- does the same job worse; deferred -- stages locally and hands off;
# no-op -- does nothing and reports the gap.
STANDIN_TYPES = {"reducing", "deferred", "no-op"}


def validate_inventory(inventory, vocab, standins):
    """Return a list of failure strings. Empty means valid."""
    failures = []

    for key in TOP_REQUIRED:
        if key not in inventory:
            failures.append(f"missing top-level field: {key}")
    if failures:
        return failures

    if inventory["repo_class"] not in REPO_CLASSES:
        failures.append(f"unknown repo_class: {inventory['repo_class']!r}")
    if inventory["inventory_status"] not in STATUSES:
        failures.append(f"unknown inventory_status: {inventory['inventory_status']!r}")
    if not isinstance(inventory["operations"], list):
        failures.append("operations must be a list")
        return failures

    seen_ids = set()
    for index, operation in enumerate(inventory["operations"]):
        where = f"operations[{index}]"
        if not isinstance(operation, dict):
            failures.append(f"{where}: not an object")
            continue
        op_id = operation.get("id", where)
        where = f"operation {op_id!r}"

        for key in OP_REQUIRED:
            if key not in operation:
                failures.append(f"{where}: missing field {key}")
        if any(f"{where}: missing" in f for f in failures):
            continue

        if not ID_PATTERN.match(operation["id"]):
            failures.append(f"{where}: id is not kebab-case: {operation['id']!r}")
        if operation["id"] in seen_ids:
            failures.append(f"{where}: duplicate id")
        seen_ids.add(operation["id"])

        if operation["stage"] not in STAGES:
            failures.append(f"{where}: unknown stage {operation['stage']!r}")
        if operation["effect"] not in EFFECTS:
            failures.append(f"{where}: unknown effect {operation['effect']!r}")
        if operation["confidence"] not in CONFIDENCE:
            failures.append(f"{where}: unknown confidence {operation['confidence']!r}")

        for gate in operation["gate"]:
            if gate not in GATES:
                failures.append(f"{where}: unknown gate {gate!r}")
        if operation["gate"] and "gate_confidence" not in operation:
            failures.append(f"{where}: gates declared but no gate_confidence")
        if operation.get("gate_confidence") and operation["gate_confidence"] not in CONFIDENCE:
            failures.append(f"{where}: unknown gate_confidence {operation['gate_confidence']!r}")

        if not isinstance(operation["requires"], list) or not operation["requires"]:
            failures.append(f"{where}: requires must be a non-empty list")
        else:
            try:
                vocab.validate(operation["requires"], where)
            except VocabError as exc:
                failures.append(str(exc))

        evidence = operation["evidence"]
        if not isinstance(evidence, list) or not evidence:
            failures.append(f"{where}: evidence must be a non-empty list")
        else:
            for item in evidence:
                if item.get("source") not in EVIDENCE_SOURCES:
                    failures.append(f"{where}: unknown evidence source {item.get('source')!r}")
                if not item.get("loc"):
                    failures.append(f"{where}: evidence entry missing loc")

        standin = operation.get("stand_in")
        if standin is not None:
            if not isinstance(standin, str):
                failures.append(
                    f"{where}: stand_in must be a catalog ID string; inline stand-in "
                    f"definitions are rejected by design"
                )
            else:
                try:
                    standins.validate(standin, where)
                except VocabError as exc:
                    failures.append(str(exc))

        trigger = operation.get("trigger")
        if trigger is not None and trigger not in TRIGGERS:
            failures.append(f"{where}: unknown trigger {trigger!r}")

    return failures


def validate_profile(profile_dict):
    """Validate a harness profile. Returns a list of failure strings."""
    failures = []
    for key in ("id", "name", "native", "mcp"):
        if key not in profile_dict:
            failures.append(f"profile missing field: {key}")
    if failures:
        return failures
    if not ID_PATTERN.match(profile_dict["id"]):
        failures.append(f"profile id is not kebab-case: {profile_dict['id']!r}")
    for key in ("native", "mcp"):
        if not isinstance(profile_dict[key], list):
            failures.append(f"profile {key} must be a list")
        elif len(set(profile_dict[key])) != len(profile_dict[key]):
            failures.append(f"profile {key} contains duplicates")
    return failures


def validate_catalog(catalog, vocab):
    """Validate the provider catalog.

    Catalogs are trusted input that drives routing, so a bad entry is not a cosmetic
    problem: an unknown capability ID here would either blow up later or silently
    widen a requirement. Every referenced capability must exist in the vocabulary.
    """
    failures = []
    seen = set()
    for index, provider in enumerate(catalog.providers):
        pid = provider.get("id", f"[{index}]")
        where = f"provider {pid!r}"

        if not provider.get("id"):
            failures.append(f"{where}: missing id")
        elif provider["id"] in seen:
            failures.append(f"{where}: duplicate provider id")
        else:
            seen.add(provider["id"])

        if not provider.get("match"):
            failures.append(f"{where}: missing match block")
        for ecosystem, names in (provider.get("match") or {}).items():
            if not isinstance(names, list) or not names:
                failures.append(f"{where}: match.{ecosystem} must be a non-empty list")

        for capability in provider.get("capabilities", []):
            if not vocab.has(capability):
                failures.append(f"{where}: unknown capability {capability!r}")

        effect = provider.get("effect")
        if effect is not None and effect not in EFFECTS:
            failures.append(f"{where}: unknown effect {effect!r}")

        for gate in provider.get("gate_hints", []):
            if gate not in GATES:
                failures.append(f"{where}: unknown gate hint {gate!r}")

        for credential in provider.get("credentials", []):
            if not credential.get("name"):
                failures.append(f"{where}: credential missing name")
            if credential.get("kind") not in CREDENTIAL_KINDS:
                failures.append(f"{where}: unknown credential kind {credential.get('kind')!r}")

        if provider.get("ambiguous") and not provider.get("ambiguous_note"):
            failures.append(f"{where}: marked ambiguous without an ambiguous_note")

    fallback = catalog.fallback or {}
    capability = fallback.get("capability")
    if capability and not vocab.has(capability):
        failures.append(f"catalog fallback: unknown capability {capability!r}")

    return failures


def validate_standin_catalog(standins, vocab):
    """Validate the curated stand-in catalog and its capability defaults."""
    failures = []
    seen = set()
    for entry in standins.all():
        sid = entry.get("id", "?")
        where = f"stand-in {sid!r}"

        if not entry.get("id"):
            failures.append(f"{where}: missing id")
        elif sid in seen:
            failures.append(f"{where}: duplicate stand-in id")
        else:
            seen.add(sid)

        if entry.get("type") not in STANDIN_TYPES:
            failures.append(f"{where}: unknown type {entry.get('type')!r}")

        capability = entry.get("capability")
        if capability and not vocab.has(capability):
            failures.append(f"{where}: unknown capability {capability!r}")

        fidelity = entry.get("fidelity")
        if not isinstance(fidelity, (int, float)):
            failures.append(f"{where}: fidelity must be numeric")
        elif not (0.0 <= fidelity < 1.0):
            failures.append(f"{where}: fidelity {fidelity} must be >= 0.0 and < 1.0")

        if not entry.get("desc"):
            failures.append(f"{where}: missing desc")

        failures.extend(_validate_exec(entry, where))

    for capability, standin_id in (standins.defaults or {}).items():
        if not vocab.has(capability):
            failures.append(f"stand-in default: unknown capability {capability!r}")
        if standin_id not in standins.ids:
            failures.append(
                f"stand-in default for {capability!r} references unknown id {standin_id!r}"
            )

    return failures


def _validate_exec(entry, where):
    """Validate a stand-in's `exec` contract, or require a stated reason there is none.

    Two failure modes this exists to prevent, both silent without it:

      * an entry declares a script that is not shipped, so the stand-in looks runnable
        and fails at the point of use;
      * an entry declares neither `exec` nor `exec_unavailable`, so a caller cannot tell
        "this cannot be run" from "somebody forgot".

    Declared inputs are checked for shape only. Their *values* are validated at run time
    by the executor, which is the single authority on what an input may be.
    """
    failures = []
    spec = execute.exec_spec(entry)

    if spec is None:
        declared = entry.get("exec_unavailable")
        if not isinstance(declared, str) or not declared.strip():
            failures.append(
                f"{where}: declares no usable `exec` block and gives no "
                f"`exec_unavailable` reason. A stand-in that cannot be run must say why."
            )
        return failures

    if entry.get("exec_unavailable"):
        failures.append(
            f"{where}: declares both `exec` and `exec_unavailable`; it either runs or it "
            f"does not, and saying both is a contradiction"
        )

    try:
        path = execute.script_path(spec["script"])
    except execute.ExecError as exc:
        failures.append(f"{where}: {exc}")
        return failures

    if not path.is_file():
        failures.append(f"{where}: script {spec['script']!r} is not shipped with r2s")

    for tool in spec["requires_tools"]:
        if not isinstance(tool, str) or not tool.strip():
            failures.append(f"{where}: requires_tools must be non-empty strings")

    for name, declaration in sorted(spec["inputs"].items()):
        if not isinstance(declaration, dict):
            failures.append(f"{where}: input {name!r} must be an object")
            continue
        kind = declaration.get("type")
        if kind not in ("string", "number", "integer"):
            failures.append(f"{where}: input {name!r} has unknown type {kind!r}")
        elif "default" in declaration and not _default_matches(declaration["default"], kind):
            failures.append(f"{where}: input {name!r} default is not a {kind}")

    return failures


def _default_matches(value, kind):
    """Whether a declared input default has the type its declaration claims.

    `bool` is excluded from the numeric kinds on purpose: in JSON `true` is not a number,
    and accepting it would let a default of `true` reach a script expecting a duration.
    """
    if kind == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if kind == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == "string":
        return isinstance(value, str)
    return True
