# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Because a release changes the emitted artifact and the vocabulary, note that:

- **Minor** — new dialects, catalog entries, capabilities, or harness profiles.
  Existing inventories keep validating.
- **Major** — the capability vocabulary changes incompatibly, an invariant is
  weakened, or an existing inventory stops validating.

## [Unreleased]

### Added

- **The emitter (M6).** `r2s convert --emit <dir>` writes an Agent Skills package:
  `SKILL.md` plus `references/{bindings,degradation,blocked,gates}.md`. It refuses to
  emit from a draft inventory unless `--accept-unreviewed` is passed, in which case the
  status is stamped into the skill and every low-confidence operation is flagged inside
  it. It also runs the spec check and the secret scan on what it just wrote, rather
  than trusting the writer.
- **I7's emitter half.** `blocked.md` generates a question per halted operation that
  surfaces every deferred gate by name, and the emitter asserts it via
  `invariants.check_deferred_questions`. Previously the invariant existed but nothing
  produced the questions it checks.
- **Twelve extraction dialects (M2 completion).** Python CLI, Python and JS web routes,
  OpenAPI, protobuf services, GitHub Actions, Make targets, package scripts,
  docker-compose, systemd units, cron, orchestrators (Airflow/Prefect/Dagster), agent
  tool registries, and Terraform/K8s/Ansible. Config-only and IaC repos are now
  extractable: their declarations *are* the operations.
- **T4 LLM gap-fill** (`extract/gapfill.py`), off by default, consuming a structured
  digest of T0–T3 output rather than a whole-repo dump, always `confidence: low`, never
  auto-accepted.
- **The eval harness** (`python -m eval`), runnable as a CI gate and exiting non-zero
  on failure. L1 invariants, L2 behavioural, L3 secret scan, L4 spec validation, L5
  determinism. A layer with no input reports `skipped`, not `pass`.
- **L3 secret-leak scanner** (`validate/secrets.py`, `r2s scan`). Detects OpenAI/Stripe
  `sk-*`, AWS `AKIA*`, GitHub `ghp_*`, Slack `xox*`, Google `AIza*`, JWTs, PEM private
  key blocks, SendGrid, Twilio and generic `token=`/`password=` assignments. It never
  prints the credential — a report that echoes the secret is its own leak.
- **L4 Agent Skills spec validation** (`validate/spec.py`), including a check that the
  skill declares its own capability profile.
- **The meta-skill** (`SKILL.md` at the repo root) that drives the CLI from inside a
  harness.
- **An MCP synthesis spike** (`mcp/server.py`) — a real stdio JSON-RPC server exposing
  one tool per stand-in-backed operation. It does not execute; see Known limits.

### Known limits

- **The MCP server does not execute.** A catalog stand-in is a curated *description*
  with declared fidelity, not a runnable script, so `tools/call` returns the routing
  decision with `executed: false`. Making it real needs executable stand-ins, codegen,
  and a decision on the SDK-versus-zero-dependency trade-off.
- **Extraction breadth is incomplete.** Rust, Go and Java declaration sites are not
  covered. Effect tracing remains Python-only via stdlib `ast`.
- **T4 is unproven.** It exists and is off by default; it has not been validated against
  a gold standard.

### Fixed

- **`session.execute(...)` was guessable in the wrong direction.** It was briefly mapped
  to `data.query`, which would have reported a `DELETE` as read-only and let it lose its
  gate — fail-*open* in a fail-closed design. It is now matched only when the argument
  is recognisably a read (`execute("select ...")`, `execute(select(...))`); anything
  else is left unmatched and surfaces for review.
- **`stripe.Charge.create(...)` and similar resource-style calls were not traced.**
  Added receiver-scoped patterns, so a payment call is now reported as `payment.charge`
  rather than falling through to the `external.call` placeholder.
- **The L3 scanner leaked the credential in its own report.** The excerpt appended a
  mask *after* the first 40 characters of the line, so a secret appearing early was
  printed in full. It now masks the matched span in place. Caught by the scanner's own
  test, which is why that test exists.

### Security

- **Fixed a credential leak into the emitted artifact.** Documentation scanning copied
  the raw matching line into the evidence `detail`, so a README line such as
  ``Publish with your token: `tool publish --token sk-live-...` `` put the token into
  `inventory.draft.json` — the file a user attaches to a bug report. Evidence now
  records that gate language was found and where, never what it said.
- **Fixed an attribution bug that over-reported confidence.** `_claim_sites` smeared
  every effect site not covered by a function body onto *all* candidates lacking a body
  range, so with several argparse subparsers in one file every site was attributed to
  every one of them at high confidence. Positional leftovers are now assigned to at
  most one candidate and capped at `medium` confidence; nested body ranges resolve to
  the innermost owner so a module-level `main()` no longer double-claims.
- Credential-bearing files (`.env*`, `*.pem`, `*.key`, `id_rsa`, `.npmrc`, `.pypirc`,
  `.netrc`, `credentials.json`, `kubeconfig`, and others) are now excluded before any
  read, and `Snapshot.read()` re-checks.
- Added `util/io.redact()` as a defence-in-depth backstop for credential-shaped strings.
- `--subdir` is now contained to the repository root; a path escaping it is refused.
- `fetch_git` now allowlists URL schemes, rejects a leading `-` (which git would parse
  as an option), validates the SHA, and terminates option parsing with `--`.
- The snapshot walk no longer follows symlinks, and its file/byte/time caps are enforced
  during the walk with directory pruning instead of after materialising every path.
- Bundled catalogs, stand-in definitions and harness profiles are now validated against
  the capability vocabulary at load. An unknown capability ID in a provider entry is a
  hard error rather than a silent widening of a requirement.

### Fixed

- `r2s convert` always exited `0`, including when an invariant failed, so CI could not
  gate on it. It now exits `1` when any invariant fails.
- `ProfileError` escaped `main()`'s handler as a raw traceback.
- `_order` leaked into the emitted artifact and violated the inventory schema's
  `additionalProperties: false`.
- Runtime data (`catalog/`, `harness-profiles/`, `schemas/`) was resolved relative to the
  repository root, which works in a source checkout and breaks under any real install.
  All runtime data now lives inside the package and resolves through
  `importlib.resources`, so an installed wheel works.
- `CATALOG_VERSION` was a hand-maintained constant that had already drifted from the
  catalog's actual version field; it is gone.


## [0.1.0] — 2026-09-24

First working version. End to end from a repository to a routed capability report.
Extraction is Python-first; MCP synthesis and the emitter are not built.

### Added

- **Capability map.** Five ordered bindings (`native → mcp → script → ask-user → drop`)
  and five orthogonal gates (`publish`, `spend`, `irreversible`, `legal`, `privacy`).
  Routing is a function of `(operation, harness profile)`, so the same repository
  resolves differently per harness.
- **Deferred gates.** A gated operation routed to `ask-user` surfaces its gate in the
  question asked, via `deferred_gates`. Previously this case could not be represented
  at all and the gate was silently lost.
- **Gate-survival invariants I1–I7**, run in CI on every fixture inventory and on every routed report.
- **Vocabulary v2** — 47 capability IDs across four kinds, extending the original
  media-heavy set with what arbitrary repos need (`queue.publish`, `db.migrate`,
  `payment.charge`, `vector.search`, `external.call`, and others).
- **Provider catalog** — ~30 packages mapped to capabilities, credentials, effect class
  and gate hints, with explicit `ambiguous` marking for packages that import-only
  detection cannot resolve (`boto3`, `replicate`, `sqlalchemy`, `google-api-python-client`).
- **Curated stand-in library** with declared fidelity and a three-type taxonomy
  (`reducing`, `deferred`, `no-op`).
- **Extraction.** Evidence fusion rather than a fallback ladder: T0 declared dialects,
  T1 effect tracing via stdlib `ast`, T2 provider catalog, T3 documentation and gate
  language. T4 (LLM proposal) is specified and not built.
- **Repo profiler** — manifests, entrypoint discovery, five-class classification
  (`library`, `cli-tool`, `service-app`, `agent-system`, `infra-config`), and a hybrid
  suitability gate that refuses, or falls back to a knowledge-only skill.
- **Phase vocabulary** — `acquire | prepare | transform | verify | deliver | observe`,
  chosen because gates cluster predictably by phase.
- **CLI** — `profiles`, `extract`, `convert`, `validate`, `diff`, `catalog-lint`.
- **Fixtures** — `cli-tool/feed-sync` (a repo the system had not seen) and
  `agent-system/youtube-automation` (18 operations).
- **66 tests**, stdlib `unittest` only.

### Changed

- `runs_end_to_end` renamed to `all_required_operations_executable`, and it now
  considers only required operations. It previously counted a dropped *optional*
  operation as failure.
- `halted` and `lost` split into required and optional, which had the same defect.
- Snapshot file ordering is now sorted on the relative path strings. `Path` objects
  compare case-insensitively on Windows and case-sensitively elsewhere, so ordering
  differed by platform and would have broken stability checks silently.

### Fixed

- `requests.get` matched the cache-read pattern, so an HTTP fetch could be reported as
  requiring `cache.read`. Generic verbs are now scoped by receiver.
- `os.environ.get` and `dict.get` matched the cache-read pattern for the same reason.
- `session.execute(select(...))` matched the write pattern, so a read-only query could
  be reported as requiring `data.write`. Bare `execute` is now treated as ambiguous and
  left unmatched.
- `@click.group()` was treated as an operation. Container decorators are now skipped.
- A spurious documentation-derived `publish` gate reclassified `acquire` operations as
  `deliver`. Verbs are now consulted before gates in phase inference.
- Unknown capability IDs were silently treated as "missing" and routed to `ask-user` or
  `drop`, hiding converter bugs as capability gaps. They are now hard errors.

[Unreleased]: https://github.com/AbinashBalaraman/repo-to-skill/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/AbinashBalaraman/repo-to-skill/releases/tag/v0.1.0
