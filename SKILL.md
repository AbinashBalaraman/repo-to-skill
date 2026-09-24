---
name: repo-to-skill
description: Converts a repository into a portable agent skill by driving the installed r2s command line. Use when the user asks to convert, inventory, or analyse a repository's operations and capability requirements for an agent harness, or asks which of a repo's steps a given harness can actually perform. Produces a reviewed operation inventory and a routed capability report; it does not write the final skill package itself.
license: MIT
compatibility: Requires Python 3.10+ and the `r2s` console script on PATH (pip install repo-to-skill). Drives the installed CLI; it does not bundle or replace it. Local paths need no network. A git URL source needs network access to clone.
metadata:
  version: "0.1.0"
  drives: r2s
  upstream: https://github.com/AbinashBalaraman/repo-to-skill
---

# repo-to-skill

Drive `r2s` to turn a repository into a capability inventory and a routed report:
for every operation the repo performs, a decision about how the target harness will
actually perform it.

## This skill drives a CLI. It cannot replace it.

`r2s` is a program, and the analysis it performs cannot be done by a skill alone.
Static parsing, deterministic byte-stable output and calibrated confidence are
properties of code, not of prose. This skill is a thin frontend: it decides *which*
`r2s` command to run, in *what order*, and it interprets the output. Everything that
matters is computed by the installed CLI.

**If `r2s` is not on PATH, stop and say so.** Do not attempt to reproduce its analysis
by reading the repository yourself — a hand-rolled guess at the operation inventory is
exactly the fabrication this project exists to prevent.

## Before you start

```bash
r2s --version          # confirm the CLI is installed
r2s profiles           # see the harness profiles you can route against
```

If `r2s` is missing, tell the user to install it (`pip install repo-to-skill`) or run
it from source (`PYTHONPATH=src python -m r2s <command>`). Do not proceed without it.

## The workflow: extract → review → convert

There are three stages, and the middle one is **not optional**.

### 1. Extract a draft

```bash
r2s extract ./path/to/repo --out ./r2s-out
# or from a pinned git source:
r2s extract --url https://github.com/owner/repo --sha <commit> --out ./r2s-out
```

This writes two files into the output directory:

- `inventory.draft.json` — the machine-readable draft, `inventory_status: "draft"`.
- `inventory.review.md` — the human-readable review. Every operation is shown with the
  evidence behind it: which declaration site, which traced effect, which provider match,
  which documentation gate.

Extraction is deterministic and static. The same repo twice gives byte-identical
output, and the repository under analysis is parsed, never executed.

### 2. Review the draft — the step that must not be skipped

**Read `inventory.review.md` with the user.** This is where a human decides whether the
extracted inventory is true. Check for the things extraction cannot know:

- **Operations that should not be there.** A test helper or an internal function
  mistaken for a real operation.
- **Operations that are missing.** A capability the repo uses that was not attributed,
  often on non-Python code where effect tracing is weaker.
- **Wrong gates.** `publish`, `spend`, `irreversible`, `legal`, `privacy` are load-bearing:
  a missing gate is a missing safety check.
- **Low-confidence rows.** Anything at `confidence: low` — especially the
  `unresolved-external-calls` row, which is what the converter emits instead of
  assuming an unrecognised dependency "requires nothing".
- **Stand-ins.** If an operation is degraded to a stand-in, confirm the stand-in is
  honest for this repo.

Then edit `inventory.draft.json` into `inventory.json` and set
`"inventory_status": "reviewed"`. Keep the draft; it is the record of what the
converter said before you changed it.

### 3. Convert — route the reviewed inventory

```bash
r2s convert r2s-out/inventory.json --profile bare     # one harness
r2s convert r2s-out/inventory.json --all              # every profile
r2s convert r2s-out/inventory.json --profile bare --json-out r2s-out/report.json
```

The output is the capability map: one row per operation, with its binding
(`native → mcp → script → ask-user → drop`), its enforced or deferred gates, and — where
it is degraded — the stand-in and its declared fidelity.

**The router refuses a draft.** `r2s convert` exits non-zero with
`inventory_status is 'draft'. Review it first, or pass --accept-unreviewed.` This
refusal is a design feature, not an obstacle. Do not reach for `--accept-unreviewed`
to get past it. That flag exists for deliberate throwaway runs and stamps the output
`accepted-unreviewed` so the shortcut is visible; it is the wrong default and should be
the user's explicit choice, never the agent's convenience.

## Reading the result

A few properties of the report are the whole point, and you should surface them:

- **`ask-user` means the operation halts.** It is not a failure of the tool; it is the
  honest answer when a required capability has no path. `gen-narration` on a bare
  harness halts rather than producing a silent video.
- **Gates are profile-invariant.** A better harness buys a better binding, never a
  weaker gate. A gate on an `ask-user` row is *deferred into the question*
  (`deferred_gates`), never dropped.
- **`drop` is legal only for optional operations.** A required operation that would
  otherwise drop becomes `ask-user`.
- **Coverage is a heuristic**, and the report says so. `script` counts as the stand-in's
  declared fidelity, not as 1.0.
- **Exit code 1 is a signal.** `convert` returns non-zero when an invariant fails or an
  inventory is invalid; check it and report it, do not swallow it.

## Supporting commands

```bash
r2s validate r2s-out/inventory.json                  # schema + vocabulary check
r2s diff a/inventory.json b/inventory.json           # what changed across two SHAs
r2s catalog-lint ./path/to/repo                      # dependencies missing from the catalog
```

Use `validate` before `convert` when an inventory has been edited by hand — it catches
schema and vocabulary mistakes with a clearer message than the router would.

## What does not exist yet

Be honest with the user about the current state:

- **Skill-package emission exists but is young.** `r2s convert --emit <dir>` writes a
  skill package. It is new in 0.1.0, so treat an emitted skill as a draft to review
  rather than a finished artefact — and if `r2s` reports `spec: OK / profile declared:
  OK / secret scan: clean`, that means the checks passed, not that the inventory was
  right.
- **Extraction is Python-first.** Non-Python repos get declaration and catalog coverage
  but weaker call-graph fidelity, so requirement attribution on them is weaker. Say so
  rather than presenting the inventory as equally reliable.
- **T4 (LLM gap-filling) is off by default and unproven.** If it is ever enabled, its
  proposals must never be auto-accepted.
- **MCP synthesis is a spike, not a feature.** The generated server loads and answers
  `tools/list` and `tools/call`, but it does not execute — it returns the routing
  decision. Do not promise a working MCP server.

## Limits

- This skill orchestrates; it does not analyse. Never present its own reading of a repo
  as equivalent to an `r2s` inventory.
- Never invent a stand-in, a gate, or a capability ID. Stand-ins come from the curated
  catalog; an invented one is indistinguishable from fabrication.
- Do not edit `inventory.review.md` and call it review. The review is a judgement about
  the inventory's truth, recorded in `inventory.json` with `inventory_status: reviewed`.
