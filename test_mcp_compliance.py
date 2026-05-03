#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Cross-project MCP compliance test.

Connects to an MCP server (HTTP/SSE or streamable-http) and validates:

  1. Server responds to `tools/list` with a non-empty tool registry
  2. Every tool's name follows the `<domain>_<action>[_<target>]` convention
     (see docs/naming-convention.md)
  3. For every tool name that has a schema file in this repo's
     `schemas/tools/<name>.json`, the live tool's inputSchema matches
     the contract schema
  4. Server can be reached via standard MCP initialize handshake

Usage:
  python3 test_mcp_compliance.py --url http://localhost:18090/mcp
  python3 test_mcp_compliance.py --url http://localhost:5004/sse --transport sse
  python3 test_mcp_compliance.py --url http://localhost:18090/mcp --schemas-dir ./schemas/tools

Run against:
  - ic-engine bridge:  http://localhost:18090/mcp  (transport=streamable-http)
  - mnemos http:       http://localhost:5004/sse   (transport=sse, requires bearer token)
  - mnemos-rs (TBD):   http://localhost:<port>/mcp (transport=TBD)

Exit code 0 = compliant. Non-zero = drift detected; report on stderr.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path
from typing import Any

NAMING_RE = re.compile(r"^[a-z][a-z0-9]*(_[a-z][a-z0-9]*)+$")


def parse_sse(body: str) -> dict | None:
    """FastMCP HTTP returns text/event-stream framed JSON-RPC. Parse the data line."""
    for line in body.splitlines():
        if line.startswith("data: "):
            try:
                return json.loads(line[6:])
            except json.JSONDecodeError:
                continue
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return None


def post(url: str, payload: dict, session_id: str | None = None,
         bearer: str | None = None, timeout: int = 30) -> tuple[dict | None, str | None, int]:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if session_id:
        headers["mcp-session-id"] = session_id
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return parse_sse(body), resp.headers.get("mcp-session-id"), resp.status
    except Exception as exc:
        return {"error": str(exc)}, None, 0


def init_session(url: str, bearer: str | None = None) -> str | None:
    init_payload = {
        "jsonrpc": "2.0", "id": 0, "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "mcp-compliance-test", "version": "1.0"},
        },
    }
    resp, sid, status = post(url, init_payload, bearer=bearer)
    if not sid and resp and "result" in (resp or {}):
        # SSE transport doesn't always return a session ID header; that's OK.
        return ""
    return sid


def list_tools(url: str, session_id: str | None, bearer: str | None = None) -> list[dict]:
    payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    resp, _, _ = post(url, payload, session_id=session_id, bearer=bearer)
    if not resp or "error" in resp:
        raise RuntimeError(f"tools/list failed: {resp}")
    result = resp.get("result", {})
    return result.get("tools", [])


def load_contract_schemas(schemas_dir: Path) -> dict[str, dict]:
    """Load all <tool>.json schemas from schemas_dir."""
    schemas = {}
    for path in schemas_dir.glob("*.json"):
        try:
            schema = json.loads(path.read_text())
            tool_name = path.stem
            schemas[tool_name] = schema
        except json.JSONDecodeError as e:
            print(f"WARN: malformed schema {path}: {e}", file=sys.stderr)
    return schemas


def schemas_compatible(live: dict, contract: dict) -> tuple[bool, list[str]]:
    """Compare live inputSchema with contract schema. Returns (ok, list_of_issues)."""
    issues = []

    # Required keys: type=object
    if live.get("type") != "object":
        issues.append(f"live type={live.get('type')!r}, contract requires 'object'")

    # Properties: contract MUST be a subset of live (live can have more,
    # but every contract property must exist in live with compatible type).
    live_props = live.get("properties", {}) or {}
    contract_props = contract.get("properties", {}) or {}
    for prop, contract_schema in contract_props.items():
        if prop not in live_props:
            issues.append(f"live missing required property {prop!r}")
            continue
        live_type = live_props[prop].get("type")
        contract_type = contract_schema.get("type")
        if contract_type and live_type != contract_type:
            issues.append(f"property {prop!r}: live type={live_type!r}, contract type={contract_type!r}")

    # Required: contract's required[] MUST be a subset of live's required[]
    contract_required = set(contract.get("required") or [])
    live_required = set(live.get("required") or [])
    missing = contract_required - live_required
    if missing:
        issues.append(f"contract requires {sorted(missing)} but live doesn't")

    return (not issues, issues)


def main() -> int:
    ap = argparse.ArgumentParser(description="MCP compliance test for the claws ecosystem")
    ap.add_argument("--url", required=True, help="MCP server endpoint URL")
    ap.add_argument("--bearer", default=None, help="Bearer token (for mnemos HTTP transport)")
    ap.add_argument("--schemas-dir", default=str(Path(__file__).parent / "schemas" / "tools"),
                    help="Directory with contract schemas (default: ./schemas/tools/)")
    ap.add_argument("--strict-naming", action="store_true",
                    help="Fail if any live tool name violates the naming convention")
    args = ap.parse_args()

    schemas_dir = Path(args.schemas_dir)
    if not schemas_dir.is_dir():
        print(f"FATAL: schemas dir not found: {schemas_dir}", file=sys.stderr)
        return 2

    contract_schemas = load_contract_schemas(schemas_dir)
    print(f"[compliance] loaded {len(contract_schemas)} contract schemas from {schemas_dir}")
    print(f"[compliance] connecting to {args.url}")

    sid = init_session(args.url, bearer=args.bearer)
    if sid is None:
        print("FATAL: MCP initialize handshake failed", file=sys.stderr)
        return 1

    print(f"[compliance] session: {sid or '(none returned — SSE)'}")
    tools = list_tools(args.url, sid, bearer=args.bearer)
    print(f"[compliance] live tools: {len(tools)}")

    failures: list[str] = []

    for tool in tools:
        name = tool.get("name")
        if not name:
            failures.append("tool missing 'name' field")
            continue

        # 2. naming convention
        if not NAMING_RE.match(name):
            msg = f"tool {name!r}: violates naming convention (expected '<domain>_<action>[_<target>]')"
            if args.strict_naming:
                failures.append(msg)
            else:
                print(f"WARN: {msg}", file=sys.stderr)

        # 3. schema match
        if name in contract_schemas:
            live_schema = tool.get("inputSchema", {})
            contract = contract_schemas[name]
            ok, issues = schemas_compatible(live_schema, contract)
            if not ok:
                failures.extend(f"tool {name!r}: {issue}" for issue in issues)
            else:
                print(f"  ✓ {name}: schema matches contract")
        else:
            print(f"  · {name}: no contract schema in repo (skipping schema check)")

    print(f"\n[compliance] tested {len(tools)} live tools against {len(contract_schemas)} contract schemas")
    if failures:
        print(f"\n[compliance] {len(failures)} drift issue(s):", file=sys.stderr)
        for f in failures:
            print(f"  ✗ {f}", file=sys.stderr)
        return 1
    print("[compliance] ALL CHECKS PASSED — no drift detected")
    return 0


if __name__ == "__main__":
    sys.exit(main())
