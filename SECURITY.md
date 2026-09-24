# Security policy

## Reporting a vulnerability

Report privately through GitHub Security Advisories:
**https://github.com/AbinashBalaraman/repo-to-skill/security/advisories/new**

Please do not open a public issue for a security problem.

Include, where you can: the version or commit, what you ran, what happened, and what
you expected. A minimal reproducer is worth more than a long description.

We aim to acknowledge within 72 hours and to ship a fix or a mitigation for confirmed
issues as soon as is reasonable.

## Threat model

`r2s` **reads untrusted input by design** — arbitrary third-party repositories — and
writes an artifact intended to be installed into an agent harness. The emitted
inventory is also the thing a user is most likely to attach to a bug report or commit
to a repository. Both facts shape the surfaces below.

## Implemented protections

These exist in the code today.

**Credential-bearing files are never read.** `source/snapshot.py` excludes `.env*`,
`*.pem`, `*.key`, `id_rsa`, `.npmrc`, `.pypirc`, `.netrc`, `credentials.json`,
`service-account.json`, `kubeconfig` and similar, by basename, before any read.
`Snapshot.read()` re-checks and refuses. See `config.SECRET_FILE_NAMES` and
`SECRET_FILE_SUFFIXES`.

**Raw repository text is never carried into an artifact.** Documentation scanning
detects *that* gate language was found and where — it does not copy the line. This was
a real vulnerability: a README line such as
``Publish with your token: `tool publish --token sk-live-...` `` previously put the
token into `inventory.draft.json`.

**Credential-shaped strings are redacted as a backstop.** `util/io.redact()` scrubs
common key shapes (OpenAI/Stripe `sk-*`, AWS `AKIA*`, GitHub `ghp_*`, Slack `xox*`,
Google `AIza*`, JWTs, `token=`/`password=` assignments). Defence in depth, not the
primary control.

**The snapshot walk is bounded and does not follow symlinks.** File, byte and time caps
are enforced *during* the walk with directory pruning, not after materialising the
tree. Symlinked files are skipped, so a symlink loop cannot hang the walk and a symlink
pointing outside the repository is not an exfiltration path.

**`--subdir` is contained.** A subdirectory that resolves outside the repository root
is refused.

**Git URLs are validated and passed safely.** `fetch_git` allowlists http(s), git, ssh
and scp-style URLs, rejects anything beginning with `-` (which git would read as an
option), validates the SHA against a hex pattern, and terminates option parsing with
`--` before positional arguments.

**Extraction is static.** No code from the repository under analysis is imported,
evaluated, or executed. The only subprocess is `git`, invoked as described above.
`subprocess` is never called with `shell=True`.

**Bundled data is validated at load.** Catalogs, stand-in definitions and harness
profiles are checked against the capability vocabulary on startup; an unknown capability
ID in a provider entry is a hard error rather than a silent widening of a requirement.

## Known gaps

Stated plainly, because a security policy that overstates its coverage is worse than one
that admits limits.

- **There is no secret-leak scanner over the emitted package.** The eval layer that
  would scan the final skill for leaked credentials (L3) is specified and not built.
  The controls above are exclusion and redaction, not detection.
- **Redaction is pattern-based.** An unusual credential format will not be caught by
  `redact()`. The primary control is that raw repository text is not emitted at all.
- **The emitter does not exist yet.** No skill package is produced, so the artifact most
  likely to leak has not yet been built. This must be revisited when M6 lands — the
  emitted `references/` files will contain repository-derived prose and will need their
  own scan.
- **`--catalog-extra` is not sandboxed.** Catalog data is validated for schema
  conformance and capability validity, but a catalog is trusted input: do not point it
  at a file you would not otherwise run.

## Out of scope

- The content, quality or licensing of the repositories you choose to convert.
- Capabilities a harness does not have. A skill that halts with a question when a
  harness lacks a tool is the design working, not a bug.
- The security posture of third-party MCP servers or harnesses you attach.
- Whether a repository you convert is trustworthy to *its* users. `r2s` reports what a
  repository does; it does not audit whether it should.
