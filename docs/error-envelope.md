# Canonical Error Envelope

When an MCP tool fails (anywhere in the claws ecosystem), the response
MUST include a structured error envelope. This is what the agentic-cobol
harness (and any other test client) uses to detect failure modes.

## Two equivalent forms

### Form A: tool result with success=false

```json
{
  "success": false,
  "error": "Resource not found",
  "error_class": "HTTPStatusError"
}
```

Used by mnemos's `execute_tool()` dispatcher (mnemos/mcp/tools/__init__.py).
Returned as the tool's MCP `CallToolResult.content[0].text` payload.

### Form B: ic_result envelope with non-zero exit_code

```json
{
  "ic_result": {
    "script": "<script_name>.py",
    "exit_code": 1,
    "duration_ms": 1234
  }
}
```

Used by ic-engine bridge. Emitted as the LAST line of `stdout` from the
ic-engine subprocess, parsed by `_run_ic_engine()` and returned in the
tool result's `ic_result` field.

## Verification line (text form)

For agents that quote tool output back to the user, the canonical
verification line format is:

```
Verification: ic-engine ask completed (exit_code: 0, hmac: <12-char-hmac>)
```

Or for non-ic-engine services:

```
Verification: <service-name> <action> completed (exit_code: 0)
```

The cobol harness's V4_VERIFICATION_RE regex parses this to determine
whether the agent actually invoked the bridge. Substitute the actual
exit_code and (where applicable) hmac from the structured envelope.

## What MUST NOT happen

- **Don't return arbitrary HTTP status codes as tool errors** — wrap in
  the structured envelope above. The cobol harness can't differentiate
  agent-runtime errors from tool errors otherwise.
- **Don't use service-specific error formats** — converge on the two
  forms above. mnemos uses Form A; ic-engine uses Form B; mnemos-rs
  should pick one (probably Form A since it's MCP-side, not subprocess-side).
- **Don't include sensitive data in `error` strings** — full stack traces
  go to logs, not the tool result. The agent's LLM may surface `error`
  text to end users.

## Examples by failure class

| Failure | Form A (mnemos) | Form B (ic-engine) |
|---|---|---|
| Tool not found | `{success: false, error: "Unknown tool: X"}` | (n/a — bridge tools are static) |
| Auth missing | `{success: false, error: "authenticated user required"}` | (n/a — single-tenant) |
| Rate limit | `{success: false, error: "Rate limit exceeded"}` | `{ic_result: {script, exit_code: 1}, narrative: "rate limited"}` |
| Backend timeout | `{success: false, error: "Tool execution failed"}` | `{ic_result: {script, exit_code: 124, duration_ms: 240000}, narrative: "..."}` |
| Invalid input | `{success: false, error: "Invalid tool input"}` | bridge raises HTTPException(400) at REST layer |
| Resource not found | `{success: false, error: "Resource not found"}` | (n/a) |
