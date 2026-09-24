# MCP server

**Status: working, with limits that are stated rather than hidden.** `mcp/server.py` is a
standalone script — nothing in `src/r2s` imports it, and it changes no CLI behaviour. It is
the answer to the question `docs/PLAN.md` poses: can `r2s` expose a converted repo's
stand-in-backed operations to a harness that lacks the capabilities they need?

## The verdict

**Yes — and it now executes.** `server.py` speaks MCP over stdio (newline-delimited
JSON-RPC 2.0), answers `initialize` / `tools/list` / `tools/call` / `ping`, and exposes one
tool per operation the router bound to `script`. A tool call runs the operation's catalog
stand-in through `r2s.execute` and returns the artifacts it produced.

The limit is not the protocol, it is the stand-in. Two outcomes are honest rather than
bugs, and the server reports them distinctly instead of pretending:

| Outcome | What happens | What it means |
|---|---|---|
| **Runs** | The stand-in script executes; `executed: true`, real artifacts listed | The step is done, at the declared fidelity |
| **Cannot run** | `executed: false`, `status: unavailable`, the reason | No substitute exists; route the operation to `ask-user` |
| **Tried and failed** | `executed: false`, `status: failed`, the reason | A prerequisite exists but did not work; do not assume it ran |

`reasoning-without-sources` is the canonical "cannot run": it is a model call, `r2s` is
stdlib-only and offline, so there is no deterministic substitute and none is invented.
`gradient-card-1080p` is the canonical "needs a tool": without `ffmpeg` on `PATH` it
reports the missing tool and writes nothing.

A caller can always tell "ran" from "could not run". That distinction is the point of the
project, so it would be odd to lose it here.

## What works

- A harness loads the server and completes the MCP handshake.
- `tools/list` returns exactly the stand-in-backed operations. Native operations are not
  exposed (the harness already performs them); `ask-user` and `drop` operations are not
  exposed (they are not tools).
- `tools/call` **executes** the stand-in and returns `artifacts`, `notes`, `exit_code` and
  `duration_seconds`. The declared fidelity and caveat are returned alongside, so a caller
  can tell a real result from a reduced one.
- Where a stand-in has no executable form, the tool description and the result both say so,
  and the result's note routes the caller to `ask-user`.
- An undeclared input key is **rejected**, not ignored — a typo cannot silently produce a
  plausible artifact from the wrong arguments.
- Credentials are read from the process environment only. The server emits credential
  **names**, never values. A missing credential fails the call closed (`isError: true`)
  before the stand-in runs at all. A test asserts a sentinel secret never appears on stdout
  or stderr.
- Artifacts are confined to the operation's run directory; a script that reports a path
  outside it has that path discarded and the discarding is noted.
- Unknown tools return JSON-RPC `-32602`; unknown methods return `-32601`.

## What does not work

- **Not a generator.** `server.py` loads a routed report at start-up rather than being
  emitted per repo. Codegen — inlining the report and the needed scripts so the server is
  self-contained and version-pinned — is not built. The server shape is proven; the codegen
  is not.
- **No resources, prompts, or sampling.** Only the `tools` capability is declared.
- **No auth transport.** Credentials are environment variables, full stop. There is no
  OAuth flow, no per-call scoping, no vault integration.
- **No sandboxing.** A stand-in runs as a child process with the server's privileges. The
  scripts are r2s's own and reviewed, and repository code is never executed — but there is
  no OS-level isolation, so do not treat this as a security boundary against a hostile
  *stand-in*.
- **Stdlib only.** By project rule, so it hand-rolls the slice of JSON-RPC it needs rather
  than using an MCP SDK. Fine here; a product would use the SDK.

## What would make it real

1. **Codegen from the routed report**, inlining the report and the needed scripts so the
   emitted server is self-contained and version-pinned.
2. **Credential scoping beyond env vars** — the operation's declared credentials mapped to
   a real secret store, with the value never entering the model's context.
3. **OS-level isolation** for the stand-in subprocess, if this is ever pointed at scripts
   the project does not own.
4. **A dependency budget decision.** A real server is easier and safer on an MCP SDK. That
   is a project-level call, not a spike-level one, and it collides with the
   zero-dependency rule.

## Files

```
mcp/
├── server.py                       the server (stdlib, stdio JSON-RPC)
├── README.md                       this file
└── fixtures/
    ├── sample-inventory.json       hand-authored reviewed inventory (NOT from `r2s extract`)
    └── sample-report.json          real `r2s convert --json-out` output for the above
```

`sample-report.json` is genuine router output — produced by running
`r2s convert mcp/fixtures/sample-inventory.json --profile bare --json-out ...`. The
inventory it was produced from is hand-authored and labelled as such in each operation's
`notes`; it stands in for a fixture repo, and is not a claim about extraction.

## Run it

```bash
# from a checkout, so `r2s` is importable:
PYTHONPATH=src python mcp/server.py mcp/fixtures/sample-report.json
```

Then write JSON-RPC lines to stdin. A one-shot call:

```bash
printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"caption-audio","arguments":{"inputs":{"text":"One sentence. And another."}}}}' \
| PYTHONPATH=src python mcp/server.py mcp/fixtures/sample-report.json
```

Or drive it from the tests:

```bash
python -m unittest tests.test_metaskill
```

Set `R2S_RUN_DIR` to choose where artifacts are written; it defaults to a
`r2s-mcp-runs` directory under the system temp directory.
