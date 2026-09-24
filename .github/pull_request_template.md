## What this changes

<!-- One or two sentences. Link the issue if there is one. -->

## Why

<!-- The problem being solved. If it changes routing, gates, or the vocabulary,
     say which invariant or design rule is involved. -->

## Checklist

- [ ] `python -m unittest discover -s tests` passes
- [ ] `ruff check src tests` and `ruff format --check src tests` pass
- [ ] `python -m r2s validate fixtures/agent-system/youtube-automation/expected/inventory.json` passes
- [ ] `python -m r2s convert fixtures/agent-system/youtube-automation/expected/inventory.json --all` reports `INVARIANTS: PASS (I1-I7)`

## If this touches the design

<!-- Delete this section if it does not. -->

- [ ] No invariant (I1–I7) was weakened. If one changed, the reasoning is below.
- [ ] Gates remain profile-invariant: a better harness still cannot weaken a gate.
- [ ] Any new stand-in is in `catalog/stand-ins/index.json`, not defined inline.
- [ ] Any new capability ID is probeable by a harness profile and coarse enough to be one tool.
- [ ] Any new provider catalog entry that is ambiguous is marked `ambiguous` with a note.

## Breaking changes

<!-- Does an existing inventory stop validating? Does a binding change? -->
