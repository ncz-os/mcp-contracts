# mcp-contracts

Cross-project MCP contract for the claws-ecosystem (mnemos + ic-engine + mnemos-rs).

Per GRAEAE 2026-05-02 consultation: the only thing all three projects can
share without forcing premature abstraction is **the protocol contract**
(JSON tool schemas, naming convention, error envelope). This repo is the
single source of truth for those contracts. Each project consumes this
repo as a git submodule (or vendored copy synced on a schedule) and
validates its MCP surface against the schemas here.

## What's in here

- `projects/` — explicit project manifests listing required tools, optional feature-gated tools, and the project's schema directory.
- `schemas/tools/` — JSON Schema for each tool's `inputSchema`. MNEMOS contracts live in `schemas/tools/mnemos/`; the four ic-engine contracts remain in `schemas/tools/`.
- `schemas/errors/` — JSON Schema for the canonical error envelope returned in `ic_result`-style payloads.
- `docs/naming-convention.md` — `domain_action` tool naming rules.
- `docs/error-envelope.md` — error payload shape both Python and Rust services emit.
- `docs/transport-policy.md` — current transport choice per project (FastMCP streamable-http vs mcp.server.sse vs whatever Rust does), migration tickets.

## What's NOT in here

- Implementation code (each project ships its own — that's the point).
- Tool handlers — only schemas describing the tool's inputs / outputs.
- Auth/security primitives — those are project-specific (mnemos has multi-tenant; ic-engine is single-tenant; mnemos-rs TBD).

## Adding a tool

1. Add the tool's JSON Schema to the schema directory named by its project manifest.
2. Declare it in `required_tools`, or in `optional_tools` when a documented feature gate controls registration.
3. Add an entry to the project's TOOL_REGISTRY (in mnemos: `mnemos/mcp/tools/__init__.py`; in ic-engine: `bridge/investorclaw_bridge/mcp/tools/__init__.py`).
4. Run the compliance checker with the explicit project selection.

For bearer-protected transports, keep tokens out of shell history and process
argv:

```bash
MCP_BEARER_TOKEN=... python3 test_mcp_compliance.py --project mnemos --url http://localhost:5004/sse
```

For ic-engine:

```bash
python3 test_mcp_compliance.py --project ic-engine --url http://localhost:18090/mcp
```

Project selection is mandatory. This prevents a server from being compared with
an unrelated project's contracts. `--schemas-dir` remains available for an
explicit custom contract set; every schema in that directory is then required.

## Naming convention

`<domain>_<action>[_<target>]` — see `docs/naming-convention.md`. Examples: `portfolio_ask`, `portfolio_holdings`, `memory_create`, `memory_search`, `kg_create_triple`.

## Versioning

This repo is versioned via git tags. Both projects pin to a tag. Breaking changes to a tool schema → bump major. Adding a new optional field → bump minor. Adding a new tool → bump minor.


## Build infrastructure & partners

Continuous integration and package distribution for this project are generously
supported by our open-source infrastructure partners:

- **[GitLab](https://gitlab.com/)** — canonical source hosting and CI pipelines
  (format / lint / test gates), via the
  [GitLab for Open Source](https://about.gitlab.com/solutions/open-source/) program.
- **[Buildkite](https://buildkite.com/)** — CI/CD orchestration with hosted macOS
  and Linux agents, and our APT package registry host
  (`packages.buildkite.com/ncz-os/ncz`), via the
  [Buildkite Open Source](https://buildkite.com/pricing) program.

Thank you to both for backing open-source software.
