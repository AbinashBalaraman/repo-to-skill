# repo-to-skill

[![CI](https://github.com/AbinashBalaraman/repo-to-skill/actions/workflows/ci.yml/badge.svg)](https://github.com/AbinashBalaraman/repo-to-skill/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![Dependencies](https://img.shields.io/badge/dependencies-0%20on%20py3.11%2B-brightgreen.svg)](pyproject.toml)

Convert any repository into a portable agent skill.

Existing converters (`Skill_Seekers`, `repo2skill`) emit **knowledge** — documentation about what a repo is. `r2s` emits **capability**: for every operation the repo performs, a decision about how the target harness will actually perform it. That decision layer is the **capability map**, and it is the point of this project.

**Status:** M0–M6 complete — extract, review, route, emit and run all work end to end. Extraction is Python-first (tracing is Python-only; declarations and the catalog are not). The MCP server executes stand-ins and reports honestly when it cannot.

## What it is

An app that produces skills. Four artifacts at four layers:

| Layer | Artifact | Form |
|---|---|---|
| Engine | `r2s` core | Python library |
| Interface | `r2s` CLI | console script |
| Convenience | meta-skill | `SKILL.md` driving the CLI |
| **Output** | one skill per converted repo | Agent Skills package |

It is not a skill itself. A skill can *describe* a capability; it cannot *deterministically analyze*. Static analysis, byte-stable output and calibrated confidence all require a program.

### The meta-skill

`SKILL.md` at the repository root is a thin meta-skill, so a user can say "convert this repo" from inside an agent harness. It drives the installed `r2s` CLI and cannot replace it: it chooses which command to run, in what order, and interprets the output, while the analysis itself — static parsing, deterministic output, calibrated confidence — stays in the program. It walks the real workflow, `extract` → review the generated `inventory.review.md` → edit into `inventory.json` → `convert`, and treats the review as mandatory, because the router refuses to route a draft inventory and that refusal is a design feature.

## Install

Zero runtime dependencies on Python 3.11+. On 3.10 the `tomli` backport is pulled in, because `tomllib` is stdlib only from 3.11.

```bash
pip install -e .
```

Or run from source without installing:

```bash
PYTHONPATH=src python -m r2s.cli <command>
```

## Use

```bash
r2s profiles                          # list harness capability profiles
r2s standins                          # list stand-ins; which can actually run here

r2s extract ./some-repo               # profile + extract a draft inventory
r2s extract --url https://github.com/owner/repo --sha <commit> --out ./out

r2s convert out/inventory.json --profile bare       # route against one harness
r2s convert out/inventory.json --all                # route against every profile
r2s convert out/inventory.json --profile bare --emit ./skills   # write a skill package

r2s run out/report.json --all --out ./artifacts     # execute the stand-in-backed steps
r2s scan ./skills/<skill>             # L3 secret scan over an emitted skill

r2s validate out/inventory.json       # schema + vocabulary check
r2s diff a/inventory.json b/inventory.json          # compare across SHAs
r2s catalog-lint ./some-repo          # dependencies missing from the catalog

r2s digest out/inventory.draft.json   # T4: the summary to hand to your own model
```

`extract` writes `inventory.draft.json` and `inventory.review.md`. Review the draft, edit it into `inventory.json`, then `convert`. `convert --emit` writes the skill package; `run` then executes it.

**The router consumes a reviewed inventory, never a draft.** `extract` is a first-class command rather than a hidden stage precisely so review stays a step you can take.

## How extraction works

Five evidence sources that run concurrently and corroborate. They answer *different* questions — running them as a fallback ladder yields either an inventory with no requirements or requirements with no structure.

| Source | Question | Signal |
|---|---|---|
| **T0** declared dialects | which operations, what order | 20 dialects: CLI subcommands, web routes, DAGs, CI jobs, tool registries, IaC, Rust/Go/Java |
| **T1** effect tracing | what each requires, effect class | calls to I/O boundaries, reached from an entrypoint |
| **T2** provider catalog | which capabilities the repo uses at all | manifests matched against `providers.json` — 37 providers, 6 ecosystems |
| **T3** docs | gates, intent, optionality | README, docstrings, `--help` prose |
| **T4** LLM proposal | gap-filling only | a file you produce from `r2s digest`; never auto-accepted, never raises confidence |

**Honest limit:** T1 is high-fidelity for Python via stdlib `ast`. Other languages get T0/T2 quality plus regex heuristics. "Any repo" means any repo gets *an* inventory; call-graph fidelity is Python-first.

Three rules that matter more than coverage:

1. **Unknown capability IDs hard-fail.** They are never silently treated as "missing" — that would hide a converter bug as a capability gap.
2. **Stand-ins must come from the curated catalog.** Inline definitions are rejected. An invented stand-in is indistinguishable from fabrication.
3. **Unknown dependencies never yield "requires nothing."** They become `external.call` at **low** confidence with a note saying the requirement is a placeholder. Silently assuming nothing is needed is the dangerous direction, and so is dressing a placeholder up as a finding.

## The capability map

Five bindings, ordered, first match wins:

`native → mcp → script → ask-user → drop`

Five gates, orthogonal: `publish`, `spend`, `irreversible`, `legal`, `privacy`. Gates attach only to executing bindings; on `ask-user` a gate is **deferred into the question**, never dropped.

Four rules that do the real work:

- **`drop` is legal only for optional operations.** A required operation with no path is `ask-user`.
- **`script` does double duty** — the repo's deterministic steps *and* the degraded stand-in. That is what keeps a pipeline running instead of stalling on the first capability gap.
- **Gates are profile-invariant.** A better harness buys a better binding, never a weaker gate.
- **`binding` is computed from `(operation, harness profile)`**, never authored.

### Same operation, four harnesses

From the `agent-system` fixture:

| Harness | `gen-scene-visuals` | `gen-narration` | `upload-video` gates |
|---|---|---|---|
| bare | script (0.25) | **ask-user — halts** | publish, irreversible, privacy |
| claude-code | script (0.25) | **ask-user — halts** | publish, irreversible, privacy |
| multimodal | native | mcp | publish, irreversible, privacy |
| upper bound | native | native | publish, irreversible, privacy |

`gen-narration` has no honest stand-in, so it halts rather than producing a silent video. That is the design working.

## Output contract

`r2s convert --emit <dir>` writes one skill package per harness profile.

```
<skill>/
├── SKILL.md                      pipeline; each step annotated with binding, gates, fidelity
├── references/
│   ├── bindings.md               resolved binding table, with the evidence behind each requirement
│   ├── degradation.md            what is degraded, which stand-in, declared fidelity, and how to run it
│   ├── blocked.md                ask-user questions with safe defaults
│   └── gates.md                  fail-closed gates
└── scripts/                      the stand-in scripts this skill actually needs, and no others
```

Conforms to `agentskills.io/specification`: `name` 1–64 chars kebab-case matching the folder, `description` 1–1024 chars, optional `license` / `compatibility` (≤500) / `metadata` (string→string) / `allowed-tools`. Body under 500 lines and ~5000 tokens; references one level deep. Validate with `skills-ref validate`, or with `r2s convert --emit` itself, which runs the spec check and the secret scan on what it just wrote.

**The skill declares its own capability profile at the top of `SKILL.md`.** A skill that does not state its assumptions cannot fail loudly, so it fails silently.

**The emitter refuses to emit from a draft inventory.** An unreviewed inventory has unconfirmed operations, and a skill built on those is confidently wrong rather than visibly incomplete. `--accept-unreviewed` overrides it and stamps the status into the skill, flagging every low-confidence operation inside.

**No repository code is copied.** The scripts in `scripts/` are r2s's own stand-ins, shipped under the same licence and copied verbatim — so emitting a skill never redistributes someone else's source, and the skill runs code that was reviewed here rather than code from the repo it describes. Each takes a JSON job on stdin and returns a JSON result:

```bash
echo '{"inputs": {"text": "..."}, "out_dir": "."}' | python scripts/srt_from_text.py
```

A stand-in with no executable form contributes no script, and `references/degradation.md` says which ones those are and why — an absent script is explained rather than mysterious.

## Eval

```
PYTHONPATH=src python -m eval        # all layers; exits non-zero on failure
PYTHONPATH=src python -m eval L3     # one layer
```

A layer with no input reports `skipped`, not `pass`. A layer that silently passes when it had nothing to check manufactures confidence, which is worse than not having it.

| Layer | What it does |
|---|---|
| **L1** | gate-survival invariants I1–I7 across every fixture × profile |
| **L2** | behavioural cases keyed to gate classes, instantiated per fixture |
| **L3** | secret-leak scan over freshly emitted skills |
| **L4** | Agent Skills spec validation, including the profile declaration |
| **L5** | extraction and emission determinism — byte-identical on re-run |

**L1 invariants** (also enforced by `r2s convert`, which exits non-zero on failure):

| # | Invariant |
|---|---|
| I1 | a gated operation on an executing binding keeps its gate |
| I2 | a required operation is never `drop` |
| I3 | an unsatisfied required operation with no stand-in is `ask-user` |
| I4 | every stand-in declares fidelity `< 1.0` |
| I5 | every dropped operation appears in the loss report |
| I6 | `publish` and `irreversible` never execute ungated |
| I7 | a gated operation routed to `ask-user` surfaces its gate in the question |

**L2 scoring is strict:** producing a plausible artefact when the correct behaviour is to halt is a failure, not a partial pass. A tool that quietly emits a broken skill is worse than one that refuses.

**L3 never prints the credential.** A report that echoes the secret is its own leak; findings carry the file, the line, and the credential type only.

## Layout

```
src/r2s/
  capability/    vocab, profiles, router, invariants, report   <- the novel component
  profiler/      manifests, entrypoints, classify, suitability
  extract/       pipeline, effects, catalog, docs, phases, confidence, coalesce, review
                 gapfill (T4) and dialects/ (twenty declaration dialects)
  emit/          SKILL.md and references/ rendering
  source/        snapshot, local
  validate/      schema, secrets (L3), spec (L4)
  data/          capabilities.json + catalog/ + harness-profiles/ + schemas/
                 (packaged; resolved via importlib.resources so an installed wheel works)
  cli.py
eval/            the L1-L5 harness, runnable as `python -m eval`
execute/         runs a catalog stand-in: argv-only, timeboxed, fail-closed
mcp/             the MCP server (executes stand-ins; reports when it cannot)
docs/            PLAN.md, prior-art.md
SKILL.md         the meta-skill that drives the CLI
fixtures/        cli-tool/feed-sync, agent-system/youtube-automation
                 (eval goldens, deliberately not packaged)
tests/
```

## Tests

```bash
python -m unittest discover -s tests
```

251 tests, stdlib only. They cover the vocabulary contract, routing, all seven invariants, schema rejection cases, the effect-matcher false positives that the fixture exposed, phase inference, the emitter contract, the secret scanner, the spec validator, the stand-in executor (including every fail-closed path), the T4 proposals contract, and end-to-end extraction on repos the system has never seen.

## What is built, and what is not

Against the milestone plan in `docs/PLAN.md` §7 — **M0–M6 are done** (fixtures and
schema, profiler, extraction, capability router, emitter and validators, frontends).
`extract → review → route → emit → run` works end to end.

Three things are worth stating plainly, because each is a limit rather than a gap:

- **The MCP server executes, but only what a stand-in can honestly do.** `mcp/server.py`
  is a stdio JSON-RPC server exposing one tool per stand-in-backed operation, and
  `tools/call` runs the catalog stand-in and returns the artifacts it produced. Where a
  stand-in cannot be run it says so and returns `executed: false` with the reason —
  `reasoning-without-sources` is a model call, and `r2s` is stdlib-only and offline, so
  there is no deterministic substitute and none is faked. See `mcp/README.md`.
- **Extraction covers 20 dialects across 6 package ecosystems.** Python CLI, Python and
  JS web routes, OpenAPI, protobuf, GitHub Actions, Make, package scripts,
  docker-compose, systemd, cron, orchestrators, agent tool registries,
  Terraform/Kubernetes/Ansible, and **Rust, Go and Java/Kotlin**. `providers.json` maps
  37 providers across `pypi`, `npm`, `cargo`, `go`, `maven` and `gradle`.
- **T4 (LLM gap-fill) is wired but off by default, and that is the design.** `r2s digest`
  produces a structured summary; you hand it to whatever model you already have; `r2s
  extract --llm-proposals` applies what comes back. `r2s` itself never calls a model,
  which is what keeps it deterministic, offline and safe to run over an untrusted repo.
  The contract around the proposals *is* validated — a proposal can never raise
  confidence, weaken a requirement or remove a gate (see `tests/test_gapfill.py`). What
  is not validated, and cannot be from here, is whether a given model produces good
  proposals.

Extraction is **Python-first**. Effect tracing uses stdlib `ast`, so it is Python-only:
other languages get declaration and catalog coverage but weaker call-graph fidelity, and
requirement attribution on them is correspondingly weaker. That is why every requirement
carries a confidence level, and why an operation whose requirement could not be
determined says `external.call` at **low** confidence with a note explaining that the
requirement is a placeholder — never a guess presented as a finding.

## Contributing

Read [CONTRIBUTING.md](CONTRIBUTING.md) first — the project has eight design rules that
are not negotiable, and a PR that breaks one will be asked to change however good the
intent. The short version: unknown capability IDs hard-fail, stand-ins come from the
catalog, unknown dependencies never yield "requires nothing", `drop` is legal only for
optional operations, gates are profile-invariant, extraction is deterministic and
static.

The most valuable contributions are to `extract/`: a new dialect, a catalog entry, or a
better confidence rule. The capability map itself is finished.

## Licence

[MIT](LICENSE).

## Design notes

Two documents explain why this is shaped the way it is:

- `docs/PLAN.md` — the plan this implements, including the four decisions that were
  deliberately made against the obvious choice (five bindings not three, gates as a
  separate axis, stand-ins declared rather than generated, evidence fusion rather than
  a fallback ladder).
- `docs/prior-art.md` — the prior-art review. Repo→skill is solved as
  *knowledge extraction* by existing tools; nothing emits *capability*. That gap is
  what this project addresses.

