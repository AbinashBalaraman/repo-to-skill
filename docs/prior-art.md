# Repo → Skill and Harness → API: prior art review

Research date: 2026-09-23

Two separate questions were asked:

- **A.** Does anything already convert a GitHub repo into an agent skill?
- **B.** Does anything already expose a coding harness's LLM as a callable API?

Short answer: **yes to both, and both are more mature than the plan assumed.** What does *not* exist is the specific thing the plan actually cared about — the capability map.

---

## Layer A — Repo → Skill

| Project | Stars | What it actually does | Notes |
|---|---|---|---|
| [yusufkaraaslan/Skill_Seekers](https://github.com/yusufkaraaslan/Skill_Seekers) | **15.0k** | 18 source types (docs sites, GitHub repos, local codebases, PDF/DOCX/EPUB, notebooks, OpenAPI, YouTube, Confluence, Notion, chat exports) → structured knowledge → `SKILL.md`. 22 export targets (`claude`, `gemini`, `openai`, `opencode`, `kimi`, `deepseek`, `qwen`, RAG/vector stores). Ships an MCP server with 40 tools. MIT, v3.9.0, 3,900+ tests. | The doc/knowledge extraction layer is **solved**. `skill-seekers create facebook/react` is a one-liner. |
| [zhangyanxs/repo2skill](https://github.com/zhangyanxs/repo2skill) | 245 | A **skill**, not a CLI: no npm install, no API keys, zero dependencies. Say "convert this repo to a skill: <url>" and your own harness LLM analyses GitHub/GitLab/Gitee or a local dir and emits an 18+ section `SKILL.md`. 10+ mirror rotation, exponential backoff, batch mode. Works in OpenCode + Claude Code. | This is essentially steps 1–4 of the proposed plan, **already shipped, and already in the shape the plan wanted.** |
| [GBSOSS/-mcp-to-skill-converter](https://github.com/GBSOSS/-mcp-to-skill-converter) | 159 | Reverse direction: MCP server → Claude Skill, claims ~90% context savings. | Interesting for the *compression* framing, not the conversion. |
| [lordpardonme/skill-converter](https://github.com/lordpardonme/skill-converter) | small | Converts any agent/skill file into another tool's format (Claude Code agent, Cursor rule, GitHub link, raw markdown). | Format interoperability, not capability transfer. |
| bjornslib/mcp-to-uber-skills-converter · Myst4ke/mcp-to-skills-converter · Dwsy/mcp-to-skill | 0–2 | More MCP→skill variants. | Ecosystem here is thin and fragmented. |
| Shyft repo-to-claude-skill · SkillAgent · Apify Repo-to-Claude-Skill-Converter · agent-skills.cc | hosted/SaaS | Same job as a service. | Not self-hosted; credential/trust question. |

**Key observation:** every one of these produces a **documentation/knowledge** skill. The output is "the agent can now *talk about* this repo," not "the agent can now *do what this repo did*."

---

## Layer B — Harness → API

| Project | Stars | What it actually does |
|---|---|---|
| [router-for-me/CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI) | **53.0k**, MIT, 4,029 commits | Wraps Antigravity/Gemini, ChatGPT Codex, Claude Code, Grok Build, Muse Code, Davin → **OpenAI / Gemini / Claude / Codex-compatible endpoints**. OAuth login, multi-account round-robin, streaming + non-streaming + WebSocket, function calling, multimodal input, Go SDK for embedding. Huge fork/derivative ecosystem (VibeProxy, Quotio, ProxyPal, 9Router, OmniRoute, Panopticon, Claude Dialects, …). |
| [chironn/AIClient-2-API](https://github.com/chironn/AIClient-2-API) | ~8.7k | Simulates Gemini CLI / Antigravity / Qwen Code / Kiro clients → OpenAI-compatible API. |
| Claudex · claude-code-proxy · CCProxy | varies | Mostly the **opposite** direction — make Claude Code *consume* other providers. Claudex is the one that turns the CLI into an OpenAI-compatible API. |

**Official, sanctioned routes (prefer these):**

- **Claude Agent SDK** (Python + TypeScript) — runs the Claude Code binary as a library. You get the same agent loop, built-in tools, permissions, hooks, subagents, sessions, MCP, and it auto-loads Skills/commands/memory from `.claude/`. Note: third parties may **not** offer claude.ai login/rate limits through it; you must use API-key auth.
- **OpenCode server + SDK** — `opencode serve` exposes an **OpenAPI 3.1** endpoint (`/doc`) with sessions, messages, tools, MCP, permissions, and an SSE event stream. The SDK is generated from that spec. This is the cleanest open-source "drive a harness programmatically" story.
- **Headless CLI** — `claude -p --output-format json` from any language.

---

## Layer C — The portability standard (this is the important part)

- **[agentskills.io](https://agentskills.io)** — the Agent Skills format, originated at Anthropic, now an **open standard** with **45+ supporting products**: Claude Code, Codex/ChatGPT, Gemini CLI, GitHub Copilot, VS Code, Cursor, Amp, Roo Code, Kiro, Trae, Goose, OpenCode, OpenHands, Letta, JetBrains Junie, and more. Required: `SKILL.md` with `name` + `description`. Optional: `scripts/`, `references/`, `assets/`. Loads by progressive disclosure (Discovery → Activation → Execution). **There is no central registry/marketplace.**
- **[MCP](https://modelcontextprotocol.io)** — the other portability layer: **tools**, not knowledge.
- [anthropics/skills](https://github.com/anthropics/skills) — 177.8k stars, contains `spec/`, `template/`, and notably a skill for **generating MCP servers**.

---

## The honest gap

1. **Nobody does the "capability map."** Both repo2skill and Skill_Seekers stop at knowledge. Neither emits a skill that says *"for the image step, call the harness's native image tool with this prompt; if the harness has none, ask the user for a key."* The `{harness-native | ask-user | script-adapter}` taxonomy — the genuinely novel part of the plan — has **no prior art** found.
2. **A skill can describe a capability but cannot provide one.** If the harness has no image/video model, the skill can only degrade to asking the user. So "use any model, in any harness" works for the *reasoning and prompt* layer, not the *generation* layer.
3. **Repo → skill is mostly repo → docs.** That is fine for libraries and CLIs, weak for apps whose value is orchestration plus gates — which is exactly the YouTube-automation case.
4. **The reverse pipeline is more mature and probably closer to the goal.** Repo → **MCP server** has real tooling: `openapi-mcp-generator` (npm), `mcp-generator` / `openapi-to-mcp` (PyPI), `mcp-server-openapi` (Go), AWS Labs OpenAPI MCP Server, .NET `OpenApiMcpNet`, plus Anthropic's own `mcp-builder` skill. MCP yields **callable capability that any harness can use** — the actual "in any harness" property.

---

## Recommendation

- **Don't build `fetch.py` / `distill.py` from scratch.** repo2skill already implements that shape as a skill; Skill_Seekers already implements the extraction layer with 3,900+ tests and an MIT licence. Fork one.
- **Build the differentiator, not the pipeline.** `capability-map.py` + the ask-user inventory + an eval asserting the gates survive compression. That is the only part with no prior art.
- **Target MCP as the action layer instead of `scripts/`.** Portable across 45+ harnesses rather than one, and it already has generators.
- **Pin upstream to a tag/SHA** when converting; single-maintainer risk is real (repo2skill: 5 commits, 1 open issue).
- **For the harness-API half, use CLIProxyAPI** (53k stars, MIT, Go SDK) if you want the free/subscription tier behind an OpenAI-compatible endpoint, and the **Claude Agent SDK** or **opencode server** if you want a sanctioned, supported integration.
