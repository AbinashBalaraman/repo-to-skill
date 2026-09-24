# MCP synthesis spike

**Status: a working server with a documented limit — not a feature.** This directory is
a spike, explicitly outside the v1 path (`docs/PLAN.md` M5/M8). Nothing here is imported
by `src/r2s`, and nothing here changes the CLI.

## The question

Can `r2s` synthesise an MCP server for a repo whose operations need capabilities a bare
harness lacks?

## The verdict

**Yes at the protocol level, no at the capability level — and the gap is the interesting
part.**

`r2s` can emit a server that a harness loads and calls. `server.py` here speaks MCP over
stdio (newline-delimited JSON-RPC 2.0), answers `initialize` / `tools/list` / `tools/call`
/ `ping`, and exposes one tool per operation the router bound to `script` — the
stand-in-backed operations. That much genuinely works and is covered by
`tests/test_metaskill.py`.

What it **cannot** do is perform the capability. A catalog stand-in is a curated
*description* with a declared fidelity ("render the title on a gradient card via FFmpeg"),
not an executable script. The scripts come from the emitter, which does not exist yet.
So a tool call returns the routing decision — binding, stand-in, fidelity, caveat,
required capability, credential names — with `"executed": false` and a note saying so. It
refuses to pretend it ran.

That is the honest result of the spike: **an MCP server for a converted repo can expose
the *degradation decision* today; it can expose the *capability* only once stand-ins have
executable implementations.** The fallback the plan already names stays correct —
`effectful-external` defaults to `ask-user`, and MCP remains an upgrade path rather than
a dependency.

## What works

- A harness loads the server and completes the MCP handshake.
- `tools/list` returns exactly the stand-in-backed operations. Native operations are not
  exposed (the harness already performs them); `ask-user` and `drop` operations are not
  exposed (they are not tools).
- `tools/call` returns a well-formed result with the declared plan and `executed: false`.
- Credentials are read from the process environment only. The server emits credential
  **names**, never values. A missing credential fails the call closed (`isError: true`)
  naming the missing variable. A test asserts a sentinel secret never appears on stdout
  or stderr.
- Unknown tools return JSON-RPC `-32602`; unknown methods return `-32601`.

## What does not work

- **No execution.** The tools report; they do not run. There is no code to run.
- **No generator.** `server.py` is a parameterised server that loads a report at
  start-up. A real generator would inline the report into the emitted server; this spike
  proves the server shape, not the codegen.
- **No resources, prompts, or sampling.** Only the `tools` capability is declared.
- **No auth transport.** Credentials are environment variables, full stop. There is no
  OAuth flow, no per-call scoping, no vault integration.
- **Stdlib only.** By project rule the spike adds no dependency, so it hand-rolls the
  small slice of JSON-RPC it needs rather than using an MCP SDK. That is fine for a
  spike and wrong for a product.

## What would make it real

1. **Executable stand-ins.** Each catalog stand-in gains a runnable implementation
   (a script, or a call into a tool the harness provides). Without this, there is nothing
   for a tool to do.
2. **Codegen from the routed report**, inlining the report and the needed scripts so the
   emitted server is self-contained and version-pinned.
3. **Credential scoping beyond env vars** — the operation's declared credentials mapped to
   a real secret store, with the value never entering the model's context.
4. **A dependency budget decision.** A real server is easier and safer on an MCP SDK.
   That is a project-level call, not a spike-level one, and it collides with the
   zero-dependency rule.

## Files

```
mcp/
├── server.py                       the spike server (stdlib, stdio JSON-RPC)
├── README.md                       this file
└── fixtures/
    ├── sample-inventory.json       hand-authored reviewed inventory (NOT from `r2s extract`)
    └── sample-report.json          real `r2s convert --json-out` output for the above
```

`sample-report.json` is genuine router output — produced by running
`r2s convert mcp/fixtures/sample-inventory.json --profile bare --json-out ...`. The
inventory it was produced from is hand-authored and labelled as such in each operation's
`notes`; it is a stand-in for a fixture repo that does not exist yet, not a claim about
extraction.

## Run it

```bash
python mcp/server.py mcp/fixtures/sample-report.json
```

Then write JSON-RPC lines to stdin. Or drive it from the tests:

```bash
python -m unittest tests.test_metaskill
```
