# Contributing

Thanks for looking. This project has an unusual shape, so a few minutes here will save
you time.

## Setup

No runtime dependencies. Python 3.10+.

```bash
git clone https://github.com/abinashbalaraman/repo-to-skill
cd r2s
python -m pip install -e ".[dev]"
```

Run the suite:

```bash
python -m unittest discover -s tests
ruff check src tests && ruff format --check src tests
```

There is no test framework dependency. Tests are stdlib `unittest` and the project
intends to stay dependency-free at runtime — please don't add one without discussing it
first.

## The one thing to understand first

**The capability map is the easy part. The operation inventory is the hard part.**

Routing is ~40 lines of ordered matching in `capability/router.py`, and it is done.
The genuine difficulty is `extract/` — turning an arbitrary repository into a normalised
list of operations that each declare the minimum capabilities they need. If the
inventory is wrong, perfect routing routes the wrong things.

So most valuable contributions are to extraction: a new dialect, a catalog entry, a
better confidence rule.

## Design rules that are not negotiable

These are load-bearing. A PR that breaks one will be asked to change, however good the
intent.

1. **Unknown capability IDs hard-fail.** Never treat one as "missing". Doing so hides a
   converter bug as a capability gap. See `capability/vocab.py`.
2. **Stand-ins come from `src/r2s/data/catalog/stand-ins/index.json`.** Inline stand-in definitions
   are rejected by the validator. An invented stand-in is indistinguishable from
   fabrication.
3. **Unknown dependencies never yield "requires nothing".** They become `external.call`
   at low confidence plus a review flag. Silently assuming nothing is needed is the
   dangerous direction.
4. **`drop` is legal only for optional operations.** A required operation with no path
   is `ask-user`.
5. **Gates are profile-invariant.** A better harness buys a better binding, never a
   weaker gate. If your change lets a gate disappear on some profile, it is wrong.
6. **Gates attach only to executing bindings.** On `ask-user` a gate is *deferred into
   the question* (`deferred_gates`), never dropped. Invariant I7 enforces this.
7. **Extraction is deterministic.** Same input twice, byte-identical output. If you add
   anything that calls a model, it must be off by default and cached.
8. **Extraction is static.** Parse, never execute. Do not import, `eval`, or shell out
   to code from the repository under analysis.

## How to add a dialect

A dialect is a plugin that recognises one family of declaration sites. This is how the
extractor generalises across repo types.

1. Subclass `Dialect` in `extract/dialects/base.py`.
2. Set `id`, `description`, `repo_classes`.
3. Implement `match(snapshot, profile)` returning `Candidate` objects in deterministic
   order.
4. Where a declaration is attached to a function, set `body_range` — T1 uses it to
   attribute effect sites to the right operation by line containment. Without it,
   effects get smeared across every operation in the file.
5. Register it in `extract/dialects/registry.py`.
6. Add a fixture under `fixtures/<class>/<name>/` and assert on properties, not exact
   operation identity.

## How to add a catalog entry

Edit `src/r2s/data/catalog/providers.json`:

```json
{
  "id": "my-service",
  "match": { "pypi": ["my-service"], "npm": ["my-service"] },
  "capabilities": ["http.request"],
  "credentials": [{ "name": "MY_SERVICE_KEY", "kind": "apikey", "env": "MY_SERVICE_KEY" }],
  "effect": "effectful-external",
  "gate_hints": ["spend"]
}
```

**If the package is ambiguous, say so.** `boto3` could be storage, database, queue or
notification. Mark it `"ambiguous": true` with an `ambiguous_note`. Import-only
detection then lowers confidence rather than requiring all four — over-requiring
wrongly blocks operations.

## How to add a capability

Capabilities live in `src/r2s/data/capabilities.json`. Three tests before adding one:

- **Probeable** — a harness profile must be able to declare it. If it can't, it isn't a
  capability.
- **Coarse** — roughly one ID per harness tool. If two IDs would always be satisfied by
  the same tool, they are one capability.
- **Non-overlapping** — if it's a special case of an existing ID, extend that one.

Changing the vocabulary invalidates every inventory written against the old set, so
this is done early and deliberately, not casually.

## Adding a harness profile

Profiles live in `src/r2s/data/harness-profiles/`. Where the harness exposes tool enumeration, set
`probe` to the command and `expiry` to a date. A profile that is assumed rather than
probed is a user declaration, and the emitted skill records it so the user can correct
it.

## Invariants

`capability/invariants.py` enforces seven gate-survival properties. They run in CI and
on every fixture inventory and every routed report. If your change alters one:

- Say which, and why, in the PR description.
- Add or update a test in `tests/test_r2s.py` that would have caught the old behaviour.

I7 in particular exists because a gate on an `ask-user` operation was previously
reported as satisfied when it had only been deferred. Don't reintroduce that.

## Commit and PR conventions

Conventional Commits (`feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`). Keep
the subject imperative and under ~72 characters.

For PRs: fill in the checklist, and state explicitly whether any binding or gate
behaviour changed.

## Reporting bugs

Use the issue templates. The single most useful attachment is the `inventory.review.md`
that `r2s extract` produces — it shows the evidence behind every operation, which is
usually enough to localise the problem.

Security issues go through [SECURITY.md](SECURITY.md), not the issue tracker.

## Licence

By contributing you agree your contributions are licensed under the MIT Licence.
