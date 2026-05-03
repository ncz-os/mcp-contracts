# MCP Tool Naming Convention

All MCP tools across the claws ecosystem (mnemos, ic-engine, mnemos-rs)
follow this convention to keep tool names predictable across runtimes
and to satisfy upstream MCP / OpenAI tool-name validation.

## Format

```
<domain>_<action>[_<target>]
```

- **`<domain>`**: short noun for the tool's subject area. Examples:
  `portfolio`, `memory`, `kg`, `dag`, `kronos`, `holdings`.
- **`<action>`**: verb for what the tool does. Examples: `ask`, `create`,
  `search`, `delete`, `get`, `update`, `list`, `refresh`, `setup`, `forecast`.
- **`<target>`** *(optional)*: noun specifying what the action operates on.
  Use only when `<domain>_<action>` is ambiguous. Examples: `kg_create_triple`,
  `dag_diff_commits`, `memory_get_stats`.

## Rules

1. **lowercase + underscores only** — no dots, hyphens, mixed case. Some
   agent runtimes (zeroclaw v0.7.x at one point) reject tool names with dots.
2. **Domain prefix is required** — `ask` alone is too generic; use
   `portfolio_ask`. Keeps tools from different services from colliding when
   an agent registers multiple MCP servers.
3. **Verb is the action** — not `portfolio_holdings_get`. Use `portfolio_holdings`
   (action implied) or `portfolio_get_holdings`.
4. **Target is the object of the action** — `kg_create_triple` (action=create,
   target=triple). Don't use target as another action: `kg_triple_create` is wrong.
5. **No version suffixes** — version the schema, not the tool name. Don't
   ship `portfolio_ask_v2`; bump `mcp-contracts` schema version instead.

## Current registry (as of 2026-05-02)

### ic-engine (mnemos-os/mnemos-ic-runtime)

| Tool | Status |
|---|---|
| `portfolio_ask` | shipped (v4.0) |
| `portfolio_holdings` | shipped (v4.0) |
| `portfolio_refresh` | shipped (v4.0) |
| `portfolio_setup` | shipped (v4.0) |

### mnemos (mnemos-os/mnemos-production v5.0.0)

| Tool | Status |
|---|---|
| `search_memories` | shipped |
| `update_memory` | shipped |
| `get_memory` | shipped |
| `create_memory` | shipped |
| `delete_memory` | shipped |
| `list_memories` | shipped |
| `get_stats` | shipped |
| `kg_create_triple` | shipped |
| `kg_search` | shipped |
| `kg_timeline` | shipped |
| `update_triple` | shipped |
| `delete_triple` | shipped |
| `bulk_create_memories` | shipped |
| `log_memory` | shipped (DAG) |
| `branch_memory` | shipped (DAG) |
| `diff_memory_commits` | shipped (DAG) |
| `checkout_memory` | shipped (DAG) |
| `recommend_model` | shipped (PANTHEON) |
| `pantheon_list_models` | shipped (PANTHEON) |
| `pantheon_route_explain` | shipped (PANTHEON) |
| `kronos_anomalies` | shipped |
| `kronos_forecast` | shipped |

### mnemos-rs (Rust)

No MCP yet. When added, must use the same naming convention and reuse
`memory_*` / `kg_*` / `dag_*` names where applicable (Rust impl of the
same conceptual tools).

## Inconsistencies to clean up later

- `search_memories` vs `get_memory` — plural for list, singular for get-by-id. **Acceptable.**
- `update_memory` vs `update_triple` — both follow `<domain>_<action>_<target>`. **Acceptable.**
- `get_stats` — domain prefix missing. Should be `mnemos_get_stats` or `memory_get_stats`. **Tech debt — fix in mnemos v5.1.**
