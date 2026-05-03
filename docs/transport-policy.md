# Transport Policy

Each project picks ONE primary MCP transport. Anti-drift comes from the
contract (this repo), not from forcing all projects onto the same Python
class hierarchy.

## Current state (2026-05-02)

| Project | Primary transport | Library | Notes |
|---|---|---|---|
| **mnemos** (Python) | SSE (Server-Sent Events) | `mcp.server.sse.SseServerTransport` | Spec-compliant MCP HTTP transport. Used by ChatGPT Pro Developer Mode + remote MCP clients. |
| **ic-engine** bridge (Python) | streamable-http (FastMCP) | `mcp.server.fastmcp.FastMCP.streamable_http_app()` | Higher-level abstraction. 30/30 cobol validation across openclaw + zeroclaw + hermes. |
| **mnemos-rs** (Rust) | TBD | (no canonical Rust MCP SDK yet) | Will need to implement MCP protocol from spec; pick SSE to match mnemos. |

## Migration tickets

- **ic-engine bridge → SSE**: ic-engine should eventually migrate from FastMCP
  streamable_http_app() to `mcp.server.sse.SseServerTransport` to align with
  mnemos. Switching means revalidating all 4 surfaces (ic-engine direct +
  zeroclaw + openclaw + hermes). Tracked separately.
- **mnemos-rs MCP transport**: implement SSE in Rust. Reference implementation:
  v5 mnemos's `mnemos/mcp/http.py` (693 lines, well-commented). Match the
  bearer-token middleware + SSE framing.

## Why two transports today

The MCP spec defines stdio + SSE. FastMCP's streamable-http is a different
HTTP transport that's easier to wire from Python decorators. ic-engine
shipped on FastMCP because it was the fastest path to a working bridge.
v5 mnemos shipped on SSE because it's the spec-compliant choice.

Switching ic-engine to SSE today would require revalidating the agentic
cobol harness across all three runtimes (openclaw 2026.4.29, zeroclaw v0.7.4,
hermes nousresearch/latest), each of which has its own MCP-client quirks.
The 30/30 result on streamable-http is hard-won. We accept the temporary
divergence; the contract (tool names, schemas, error envelope) is what
matters for cross-project compatibility.

## What MUST be the same regardless of transport

These are the cross-project invariants — shared via this repo:

1. **Tool names** (`<domain>_<action>` format; see `naming-convention.md`).
2. **Tool inputSchemas** (one JSON Schema file per tool in `schemas/tools/`).
3. **Error envelope** (Form A or Form B; see `error-envelope.md`).
4. **Verification line text format** (the cobol harness regex parses this).

Both projects' compliance test (`test_mcp_compliance.py` in each repo)
validates these invariants against a live MCP server. Same test, same
schemas, different transports. That's the contract.
