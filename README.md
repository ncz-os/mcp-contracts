# mcp-contracts

Cross-project MCP contract for the claws-ecosystem (mnemos + ic-engine + mnemos-rs).

Per GRAEAE 2026-05-02 consultation: the only thing all three projects can
share without forcing premature abstraction is **the protocol contract**
(JSON tool schemas, naming convention, error envelope). This repo is the
single source of truth for those contracts. Each project consumes this
repo as a git submodule (or vendored copy synced on a schedule) and
validates its MCP surface against the schemas here.

## What's in here

- `schemas/tools/` — JSON Schema for each tool's `inputSchema`. Filename = tool name. Each tool MUST conform to its schema; the cross-project compliance test (`test_mcp_compliance.py`, lives in each project) validates this.
- `schemas/errors/` — JSON Schema for the canonical error envelope returned in `ic_result`-style payloads.
- `docs/naming-convention.md` — `domain_action` tool naming rules.
- `docs/error-envelope.md` — error payload shape both Python and Rust services emit.
- `docs/transport-policy.md` — current transport choice per project (FastMCP streamable-http vs mcp.server.sse vs whatever Rust does), migration tickets.

## What's NOT in here

- Implementation code (each project ships its own — that's the point).
- Tool handlers — only schemas describing the tool's inputs / outputs.
- Auth/security primitives — those are project-specific (mnemos has multi-tenant; ic-engine is single-tenant; mnemos-rs TBD).

## Adding a tool

1. Add `schemas/tools/<tool_name>.json` with the JSON Schema inputSchema.
2. Add an entry to the project's TOOL_REGISTRY (in mnemos: `mnemos/mcp/tools/__init__.py`; in ic-engine: `bridge/investorclaw_bridge/mcp/tools/__init__.py`).
3. Run the project's `test_mcp_compliance.py` to verify the live MCP server returns matching schemas.

## Naming convention

`<domain>_<action>[_<target>]` — see `docs/naming-convention.md`. Examples: `portfolio_ask`, `portfolio_holdings`, `memory_create`, `memory_search`, `kg_create_triple`.

## Versioning

This repo is versioned via git tags. Both projects pin to a tag. Breaking changes to a tool schema → bump major. Adding a new optional field → bump minor. Adding a new tool → bump minor.
