#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Cross-project MCP compliance test.

Connects to an MCP server (HTTP/SSE or streamable-http) and validates:

  1. Server responds to `tools/list` with a non-empty tool registry
  2. Every tool's name follows the `<domain>_<action>[_<target>]` convention
     (see docs/naming-convention.md)
  3. Every required project tool is live, every live tool is declared by the
     project, and each inputSchema matches that project's contract
  4. Server can be reached via standard MCP initialize handshake

Usage:
  python3 test_mcp_compliance.py --project ic-engine --url http://localhost:18090/mcp
  python3 test_mcp_compliance.py --url http://localhost:18090/mcp --schemas-dir ./schemas/tools
  MCP_BEARER_TOKEN=... python3 test_mcp_compliance.py --project mnemos --url http://localhost:5004/sse

Run against:
  - ic-engine bridge:  http://localhost:18090/mcp  (transport=streamable-http)
  - mnemos http:       http://localhost:5004/sse   (transport=sse, requires bearer token)
  - mnemos-rs (TBD):   http://localhost:<port>/mcp (transport=TBD)

Exit code 0 = compliant. Non-zero = drift detected; report on stderr.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

NAMING_RE = re.compile(r"^[a-z][a-z0-9]*(_[a-z][a-z0-9]*)+$")
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
PROTOCOL_VERSION = "2025-06-18"
SUPPORTED_PROTOCOL_VERSIONS = frozenset({PROTOCOL_VERSION})
SCHEMA_ANNOTATIONS = {
    "$comment", "$schema", "deprecated", "description", "examples",
    "readOnly", "title", "writeOnly",
}
ORDER_INSENSITIVE_ARRAYS = frozenset({"allOf", "anyOf", "enum", "oneOf", "required", "type"})


def parse_sse(body: str) -> dict | None:
    """Return the first JSON object in an SSE body, accepting optional data whitespace."""
    data_lines: list[str] = []
    for line in [*body.splitlines(), ""]:
        if line.startswith("data:"):
            value = line[5:]
            data_lines.append(value[1:] if value.startswith(" ") else value)
        elif not line and data_lines:
            try:
                return json.loads("\n".join(data_lines))
            except json.JSONDecodeError:
                data_lines = []
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return None


def post(url: str, payload: dict, session_id: str | None = None,
         bearer: str | None = None, timeout: int = 30,
         protocol_version: str | None = None) -> tuple[dict | None, str | None, int]:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if session_id:
        headers["mcp-session-id"] = session_id
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    if protocol_version:
        headers["MCP-Protocol-Version"] = protocol_version
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = read_capped_body(resp)
            return parse_sse(body), resp.headers.get("mcp-session-id"), resp.status
    except urllib.error.HTTPError as exc:
        return {"error": str(exc)}, None, exc.code
    except Exception as exc:
        return {"error": str(exc)}, None, 0


def read_capped_body(resp: Any) -> str:
    body = resp.read(MAX_RESPONSE_BYTES + 1)
    if len(body) > MAX_RESPONSE_BYTES:
        raise RuntimeError(f"MCP response exceeded {MAX_RESPONSE_BYTES} bytes")
    return body.decode("utf-8", errors="replace")


def read_sse_event(resp: Any) -> tuple[str, str]:
    """Read one event from an open SSE response without consuming the stream."""
    event_type = "message"
    data_lines: list[str] = []
    total = 0
    while True:
        raw = resp.readline(MAX_RESPONSE_BYTES + 1)
        if not raw:
            raise RuntimeError("SSE stream closed before a complete event arrived")
        total += len(raw)
        if total > MAX_RESPONSE_BYTES:
            raise RuntimeError(f"MCP SSE event exceeded {MAX_RESPONSE_BYTES} bytes")
        line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
        if not line:
            if data_lines:
                return event_type, "\n".join(data_lines)
            event_type = "message"
            continue
        if line.startswith("event:"):
            event_type = line[6:].lstrip()
        elif line.startswith("data:"):
            value = line[5:]
            data_lines.append(value[1:] if value.startswith(" ") else value)


class MCPClient:
    """Small Streamable HTTP client with the specified legacy SSE fallback."""

    def __init__(self, url: str, bearer: str | None = None, timeout: int = 30):
        self.url = url
        self.bearer = bearer
        self.timeout = timeout
        self.session_id: str | None = None
        self.protocol_version: str | None = None
        self._sse_response: Any | None = None
        self._legacy_message_url: str | None = None

    @property
    def is_legacy_sse(self) -> bool:
        return self._legacy_message_url is not None

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.bearer}"} if self.bearer else {}

    def _connect_legacy_sse(self) -> None:
        headers = {"Accept": "text/event-stream", **self._auth_headers()}
        req = urllib.request.Request(self.url, headers=headers, method="GET")
        self._sse_response = urllib.request.urlopen(req, timeout=self.timeout)
        event_type, endpoint = read_sse_event(self._sse_response)
        if event_type != "endpoint":
            raise RuntimeError(f"legacy SSE stream's first event was {event_type!r}, not 'endpoint'")
        message_url = urljoin(self.url, endpoint)
        base = urlparse(self.url)
        target = urlparse(message_url)
        if (target.scheme, target.netloc) != (base.scheme, base.netloc):
            raise RuntimeError("legacy SSE message endpoint must use the server URL's origin")
        self._legacy_message_url = message_url

    def _legacy_send(self, payload: dict) -> dict | None:
        assert self._legacy_message_url and self._sse_response
        resp, _, status = post(
            self._legacy_message_url, payload, bearer=self.bearer, timeout=self.timeout,
            protocol_version=self.protocol_version,
        )
        if status < 200 or status >= 300:
            raise RuntimeError(f"legacy SSE message POST failed: {resp}")
        if "id" not in payload:
            return None
        while True:
            _, data = read_sse_event(self._sse_response)
            try:
                message = json.loads(data)
            except json.JSONDecodeError:
                continue
            if message.get("id") == payload["id"]:
                return message

    def send(self, payload: dict, allow_legacy_fallback: bool = False) -> dict | None:
        if self.is_legacy_sse:
            return self._legacy_send(payload)
        resp, sid, status = post(
            self.url, payload, session_id=self.session_id, bearer=self.bearer,
            timeout=self.timeout, protocol_version=self.protocol_version,
        )
        if allow_legacy_fallback and 400 <= status < 500:
            self._connect_legacy_sse()
            return self._legacy_send(payload)
        if status < 200 or status >= 300 or (resp and "error" in resp):
            raise RuntimeError(f"MCP request failed: {resp}")
        if sid:
            self.session_id = sid
        if "id" in payload and not resp:
            raise RuntimeError("MCP request returned no JSON-RPC response")
        return resp

    def initialize(self) -> None:
        payload = {
            "jsonrpc": "2.0", "id": 0, "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "mcp-compliance-test", "version": "1.0"},
            },
        }
        response = self.send(payload, allow_legacy_fallback=True)
        if not response or "result" not in response:
            raise RuntimeError(f"initialize failed: {response}")
        selected_version = response["result"].get("protocolVersion", PROTOCOL_VERSION)
        if selected_version not in SUPPORTED_PROTOCOL_VERSIONS:
            raise RuntimeError(f"server selected unsupported MCP protocol version {selected_version!r}")
        self.protocol_version = selected_version
        self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def list_tools(self) -> list[dict]:
        response = self.send({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
        if not response or "result" not in response:
            raise RuntimeError(f"tools/list failed: {response}")
        return response["result"].get("tools", [])

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict:
        response = self.send({
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        })
        if not response or "result" not in response:
            raise RuntimeError(f"tools/call failed: {response}")
        return response["result"]

    def close(self) -> None:
        if self._sse_response is not None:
            self._sse_response.close()
            self._sse_response = None


def load_contract_schemas(schemas_dir: Path) -> dict[str, dict]:
    """Load all <tool>.json schemas from schemas_dir."""
    schemas = {}
    for path in schemas_dir.glob("*.json"):
        try:
            schema = json.loads(path.read_text())
            if not isinstance(schema, dict):
                raise ValueError(f"contract schema {path} must be a JSON object")
            tool_name = path.stem
            schemas[tool_name] = schema
        except (OSError, json.JSONDecodeError) as e:
            raise ValueError(f"cannot load contract schema {path}: {e}") from e
    return schemas


def load_project(project: str, root: Path) -> tuple[dict[str, Any], Path]:
    path = root / "projects" / f"{project}.json"
    try:
        manifest = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot load project manifest {path}: {exc}") from exc
    if not isinstance(manifest, dict):
        raise ValueError(f"project manifest {path} must be a JSON object")
    required = manifest.get("required_tools")
    optional = manifest.get("optional_tools", [])
    schema_dir_value = manifest.get("schemas_dir")
    if not isinstance(required, list) or not all(isinstance(v, str) for v in required):
        raise ValueError(f"project manifest {path} has invalid required_tools")
    if not isinstance(optional, list) or not all(isinstance(v, str) for v in optional):
        raise ValueError(f"project manifest {path} has invalid optional_tools")
    if set(required) & set(optional) or len(set(required + optional)) != len(required + optional):
        raise ValueError(f"project manifest {path} has duplicate tool declarations")
    if not isinstance(schema_dir_value, str):
        raise ValueError(f"project manifest {path} has invalid schemas_dir")
    schemas_dir = (path.parent / schema_dir_value).resolve()
    repository_root = root.resolve()
    if schemas_dir != repository_root and repository_root not in schemas_dir.parents:
        raise ValueError(f"project manifest {path} resolves schemas_dir outside the repository")
    return manifest, schemas_dir


def schemas_compatible(live: dict, contract: dict) -> tuple[bool, list[str]]:
    """Require equal validation semantics while ignoring annotation-only keywords."""
    def validation_schema(value: Any, parent_key: str | None = None) -> Any:
        if isinstance(value, dict):
            normalized = {
                key: validation_schema(item, key)
                for key, item in value.items()
                if key not in SCHEMA_ANNOTATIONS
            }
            if normalized.get("type") == "object":
                normalized.setdefault("properties", {})
                normalized.setdefault("required", [])
                normalized.setdefault("additionalProperties", True)
            if isinstance(normalized.get("required"), list):
                normalized["required"] = sorted(normalized["required"])
            return normalized
        if isinstance(value, list):
            normalized_items = [validation_schema(item) for item in value]
            if parent_key in ORDER_INSENSITIVE_ARRAYS:
                return sorted(normalized_items, key=lambda item: json.dumps(item, sort_keys=True))
            return normalized_items
        return value

    if validation_schema(live) == validation_schema(contract):
        return True, []
    return False, ["live inputSchema validation rules differ from the canonical contract"]


def decode_tool_payload(result: dict) -> dict:
    content = result.get("content")
    if not isinstance(content, list):
        raise ValueError("tools/call result has no content array")
    for item in content:
        if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str):
            try:
                value = json.loads(item["text"])
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                return value
    raise ValueError("tools/call result has no JSON-object text content")


def validate_error_envelope(value: Any, schema: dict, path: str = "$") -> list[str]:
    """Validate the checked-in error schemas' Draft-07 keyword subset."""
    issues: list[str] = []
    expected_type = schema.get("type")
    type_checks = {
        "object": lambda v: isinstance(v, dict),
        "string": lambda v: isinstance(v, str),
        "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
        "boolean": lambda v: isinstance(v, bool),
    }
    if expected_type in type_checks and not type_checks[expected_type](value):
        return [f"{path}: expected {expected_type}"]
    if "const" in schema and value != schema["const"]:
        issues.append(f"{path}: expected constant {schema['const']!r}")
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                issues.append(f"{path}: missing required field {key!r}")
        for key, child in schema.get("properties", {}).items():
            if key in value:
                issues.extend(validate_error_envelope(value[key], child, f"{path}.{key}"))
    if isinstance(value, int) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            issues.append(f"{path}: below minimum")
        if "maximum" in schema and value > schema["maximum"]:
            issues.append(f"{path}: above maximum")
    if isinstance(value, str) and "pattern" in schema and not re.search(schema["pattern"], value):
        issues.append(f"{path}: does not match required pattern")
    return issues


def main() -> int:
    ap = argparse.ArgumentParser(description="MCP compliance test for the claws ecosystem")
    ap.add_argument("--url", required=True, help="MCP server endpoint URL")
    ap.add_argument("--project", help="Project manifest name from ./projects (required unless --schemas-dir is used)")
    ap.add_argument("--bearer", default=None,
                    help="Deprecated: bearer token value. Prefer --bearer-env to avoid argv leaks.")
    ap.add_argument("--bearer-env", default="MCP_BEARER_TOKEN",
                    help="Environment variable containing the bearer token (default: MCP_BEARER_TOKEN)")
    ap.add_argument("--schemas-dir", default=None,
                    help="Legacy/custom schema directory; cannot be combined with --project")
    ap.add_argument("--strict-naming", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--allow-invalid-names", action="store_true",
                    help="Warn instead of failing for legacy tool names")
    args = ap.parse_args()

    url_error = validate_endpoint_url(args.url)
    if url_error:
        print(f"FATAL: {url_error}", file=sys.stderr)
        return 2
    bearer = resolve_bearer(args)

    root = Path(__file__).parent
    manifest: dict[str, Any] | None = None
    if args.project and args.schemas_dir:
        print("FATAL: --project and --schemas-dir are mutually exclusive", file=sys.stderr)
        return 2
    if args.project:
        try:
            manifest, schemas_dir = load_project(args.project, root)
        except ValueError as exc:
            print(f"FATAL: {exc}", file=sys.stderr)
            return 2
    elif args.schemas_dir:
        schemas_dir = Path(args.schemas_dir)
    else:
        print("FATAL: select an explicit --project (or provide --schemas-dir for a custom contract)", file=sys.stderr)
        return 2
    if not schemas_dir.is_dir():
        print(f"FATAL: schemas dir not found: {schemas_dir}", file=sys.stderr)
        return 2

    try:
        contract_schemas = load_contract_schemas(schemas_dir)
    except ValueError as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        return 2
    if not contract_schemas:
        print(f"FATAL: no contract schemas found in {schemas_dir}", file=sys.stderr)
        return 2
    required_names = set(manifest["required_tools"] if manifest else contract_schemas)
    optional_names = set(manifest.get("optional_tools", []) if manifest else [])
    declared_names = required_names | optional_names
    missing_schema_names = declared_names - set(contract_schemas)
    extra_schema_names = set(contract_schemas) - declared_names
    if missing_schema_names or extra_schema_names:
        details = []
        if missing_schema_names:
            details.append(f"missing schemas: {', '.join(sorted(missing_schema_names))}")
        if extra_schema_names:
            details.append(f"undeclared schemas: {', '.join(sorted(extra_schema_names))}")
        print(f"FATAL: project contract is inconsistent ({'; '.join(details)})", file=sys.stderr)
        return 2
    print(f"[compliance] loaded {len(contract_schemas)} contract schemas from {schemas_dir}")
    print(f"[compliance] connecting to {args.url}")

    client = MCPClient(args.url, bearer=bearer)
    try:
        client.initialize()
        tools = client.list_tools()
        probe_result = None
        if manifest and manifest.get("error_probe"):
            probe = manifest["error_probe"]
            probe_result = client.call_tool(probe["tool"], probe.get("arguments", {}))
    except Exception as exc:
        print(f"FATAL: MCP communication failed: {exc}", file=sys.stderr)
        return 1
    finally:
        client.close()

    session = client.session_id or ("legacy SSE" if client.is_legacy_sse else "none returned")
    print(f"[compliance] session: {session}")
    print(f"[compliance] live tools: {len(tools)}")

    failures: list[str] = []
    if not tools:
        failures.append("server returned an empty tool registry")

    live_names = {tool.get("name") for tool in tools if tool.get("name")}
    for name in sorted(required_names - live_names):
        failures.append(f"required tool {name!r}: not advertised by server")
    for name in sorted(live_names - declared_names):
        failures.append(f"tool {name!r}: not declared by the selected project")

    for tool in tools:
        name = tool.get("name")
        if not name:
            failures.append("tool missing 'name' field")
            continue

        # 2. naming convention
        if not NAMING_RE.match(name):
            msg = f"tool {name!r}: violates naming convention (expected '<domain>_<action>[_<target>]')"
            if args.allow_invalid_names:
                print(f"WARN: {msg}", file=sys.stderr)
            else:
                failures.append(msg)

        # 3. schema match
        if name in contract_schemas:
            live_schema = tool.get("inputSchema", {})
            contract = contract_schemas[name]
            ok, issues = schemas_compatible(live_schema, contract)
            if not ok:
                failures.extend(f"tool {name!r}: {issue}" for issue in issues)
            else:
                print(f"  ✓ {name}: schema matches contract")
    if manifest and manifest.get("error_probe"):
        probe = manifest["error_probe"]
        error_schema_path = root / "schemas" / "errors" / probe["schema"]
        try:
            error_schema = json.loads(error_schema_path.read_text())
            payload = decode_tool_payload(probe_result or {})
            envelope_issues = validate_error_envelope(payload, error_schema)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            envelope_issues = [str(exc)]
        failures.extend(f"error-envelope probe: {issue}" for issue in envelope_issues)

    print(f"\n[compliance] tested {len(tools)} live tools against {len(contract_schemas)} contract schemas")
    if failures:
        print(f"\n[compliance] {len(failures)} drift issue(s):", file=sys.stderr)
        for f in failures:
            print(f"  ✗ {f}", file=sys.stderr)
        return 1
    print("[compliance] ALL CHECKS PASSED — no drift detected")
    return 0


def validate_endpoint_url(url: str) -> str | None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return f"--url must be http or https, got {parsed.scheme!r}"
    if not parsed.netloc:
        return "--url must include a host"
    if parsed.username or parsed.password:
        return "--url must not contain embedded credentials"
    return None


def resolve_bearer(args: argparse.Namespace) -> str | None:
    if args.bearer:
        print(
            "WARN: --bearer exposes the token in process argv; use --bearer-env instead",
            file=sys.stderr,
        )
        return args.bearer
    env_name = args.bearer_env.strip()
    return os.environ.get(env_name) if env_name else None


if __name__ == "__main__":
    sys.exit(main())
