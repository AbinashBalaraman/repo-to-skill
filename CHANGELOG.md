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

### Not yet built

These are planned, not shipped. They were previously listed under "Added", which
implied they existed.

- Emitter for the output contract: `SKILL.md` plus `references/{bindings,degradation,blocked,gates}.md` (M6)
- Secret-leak scanner over the emitted package (L3)
- Spec conformance and budget validation via `skills-ref validate` (L4)
- SHA-diff stability check (L5)
- MCP synthesis spike (M8)
- Meta-skill frontend that drives the CLI from inside a harness (M7)
- Remaining extraction dialects and non-Python effect tracing (M4)
- T4 LLM gap-fill

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
