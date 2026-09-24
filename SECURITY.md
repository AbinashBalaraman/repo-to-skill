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

**Execution is confined to r2s's own scripts.** `r2s run` and the MCP server execute a
stand-in, and a stand-in is a script that ships with `r2s` under the same MIT licence —
never repository code. `execute/` resolves the script path through a containment check,
invokes it with an argv list (`shell=True` is never used, so no input can become shell
syntax), and passes the job as JSON on stdin rather than as arguments, so there is no
argument-injection surface at all. Reported artifacts are resolved and checked to stay
inside the operation's working directory. Output is redacted and truncated before it
reaches a report or a model. A declared prerequisite that is missing, a timeout, or a
non-zero exit is an error: the executor never writes a placeholder artifact and calls it
success.

**Bundled data is validated at load.** Catalogs, stand-in definitions and harness
profiles are checked against the capability vocabulary on startup; an unknown capability
ID in a provider entry is a hard error rather than a silent widening of a requirement.
The stand-in catalog's `exec` contract is validated too: a declared script that is not
shipped, an input with an unknown type, or a stand-in that declares neither an `exec`
block nor a reason it cannot run, all fail at load rather than at the point of use.

## Known gaps

Stated plainly, because a security policy that overstates its coverage is worse than one
that admits limits.

- **The L3 secret scanner detects; it does not prevent.** `r2s scan <skill-dir>` and the
  L3 eval layer check an emitted package for credential-shaped strings, and the emitter
  runs the scan on what it just wrote. The controls that *prevent* leakage are upstream:
  credential files are never read, and raw repository text is never carried into
  evidence.
- **Redaction and detection are both pattern-based.** An unusual credential format will
  be caught by neither. The primary control remains that raw repository text is not
  emitted at all.
- **The emitter writes repository-derived prose into `references/`.** That path is new
  and is the most likely place for a leak to appear. It is scanned on emission and in
  CI, but the scan is only as good as its patterns.
- **Stand-ins run without OS-level isolation.** `execute/` invokes the script as a child
  process with the caller's privileges and environment. The scripts are reviewed and
  owned by this project, and repository code is never executed — but this is not a
  sandbox, and it should not be treated as one if `r2s` is ever pointed at stand-in
  scripts the project does not own. Credentials reach a stand-in through the inherited
  environment, which is how a step that needs a secret gets one; the executor never logs
  the environment and redacts the child's output.
- **The MCP server holds no secrets and leaks none, but has not been independently
  reviewed.** It executes stand-ins through `execute/` and emits credential *names* only;
  a missing credential fails the call closed before the stand-in runs. It remains a
  single-maintainer component.
- **`--catalog-extra` is not sandboxed.** Catalog data is validated for schema
  conformance and capability validity, but a catalog is trusted input: do not point it
  at a file you would not otherwise run. A catalog entry that declares an `exec` block
  names a script, and that script is executed — so a catalog is code-adjacent input in a
  way it was not before stand-ins became runnable.

## Out of scope

- The content, quality or licensing of the repositories you choose to convert.
- Capabilities a harness does not have. A skill that halts with a question when a
  harness lacks a tool is the design working, not a bug.
- The security posture of third-party MCP servers or harnesses you attach.
- Whether a repository you convert is trustworthy to *its* users. `r2s` reports what a
  repository does; it does not audit whether it should.
