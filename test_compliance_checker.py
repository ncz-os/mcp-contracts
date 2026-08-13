import io
import itertools
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import test_mcp_compliance as checker


CONTRACT = {
    "type": "object",
    "properties": {"question": {"type": "string"}},
    "required": ["question"],
}


class ComplianceCheckerTests(unittest.TestCase):
    def run_main(self, schemas, tools):
        with tempfile.TemporaryDirectory() as directory:
            schema_dir = Path(directory)
            for name, schema in schemas.items():
                (schema_dir / f"{name}.json").write_text(json.dumps(schema))
            client = mock.Mock()
            client.session_id = "session"
            client.is_legacy_sse = False
            client.list_tools.return_value = tools
            argv = ["checker", "--url", "http://localhost/mcp", "--schemas-dir", directory]
            with mock.patch.object(checker, "MCPClient", return_value=client), \
                    mock.patch("sys.argv", argv), redirect_stdout(io.StringIO()), \
                    redirect_stderr(io.StringIO()):
                return checker.main()

    def run_project(self, project, tools, probe_payload=None):
        client = mock.Mock()
        client.session_id = "session"
        client.is_legacy_sse = False
        client.list_tools.return_value = tools
        client.call_tool.return_value = probe_payload or {
            "content": [{"type": "text", "text": json.dumps({"success": False, "error": "Unknown tool"})}]
        }
        argv = ["checker", "--url", "http://localhost/mcp", "--project", project]
        with mock.patch.object(checker, "MCPClient", return_value=client), \
                mock.patch("sys.argv", argv), redirect_stdout(io.StringIO()), \
                redirect_stderr(io.StringIO()):
            return checker.main()

    def test_legacy_sse_fallback_uses_advertised_message_endpoint(self):
        stream = io.BytesIO(
            b"event: endpoint\ndata: /messages/?session_id=abc\n\n"
            b"event: message\ndata: {\"jsonrpc\":\"2.0\",\"id\":0,\"result\":{\"protocolVersion\":\"2025-06-18\"}}\n\n"
            b"event: message\ndata: {\"jsonrpc\":\"2.0\",\"id\":1,\"result\":{\"tools\":[]}}\n\n"
        )
        sent = []

        def fake_post(url, payload, **kwargs):
            sent.append((url, payload))
            if len(sent) == 1:
                return {"error": "HTTP Error 405"}, None, 405
            return None, None, 202

        client = checker.MCPClient("http://localhost/sse")
        with mock.patch.object(checker, "post", side_effect=fake_post), \
                mock.patch.object(checker.urllib.request, "urlopen", return_value=stream) as urlopen:
            client.initialize()
            self.assertEqual(client.list_tools(), [])

        self.assertEqual(urlopen.call_args.args[0].method, "GET")
        self.assertEqual([item[0] for item in sent[1:]], [
            "http://localhost/messages/?session_id=abc",
            "http://localhost/messages/?session_id=abc",
            "http://localhost/messages/?session_id=abc",
        ])
        self.assertEqual(sent[2][1]["method"], "notifications/initialized")

    def test_streamable_client_sends_initialized_before_tools_list(self):
        responses = [
            ({"jsonrpc": "2.0", "id": 0, "result": {"protocolVersion": "2025-06-18"}}, "sid", 200),
            (None, None, 202),
            ({"jsonrpc": "2.0", "id": 1, "result": {"tools": []}}, None, 200),
        ]
        with mock.patch.object(checker, "post", side_effect=responses) as post:
            client = checker.MCPClient("http://localhost/mcp")
            client.initialize()
            client.list_tools()
        methods = [call.args[1]["method"] for call in post.call_args_list]
        self.assertEqual(methods, ["initialize", "notifications/initialized", "tools/list"])
        self.assertEqual(post.call_args_list[1].kwargs["session_id"], "sid")

    def test_initialize_requests_current_protocol_and_accepts_supported_legacy(self):
        response = ({
            "jsonrpc": "2.0", "id": 0,
            "result": {"protocolVersion": "2025-06-18"},
        }, "sid", 200)
        with mock.patch.object(checker, "post", side_effect=[response, (None, None, 202)]) as post:
            client = checker.MCPClient("http://localhost/mcp")
            client.initialize()
        initialize = post.call_args_list[0].args[1]
        self.assertEqual(initialize["params"]["protocolVersion"], "2025-11-25")
        self.assertEqual(client.protocol_version, "2025-06-18")

    def test_initialize_accepts_current_protocol_selection(self):
        response = ({
            "jsonrpc": "2.0", "id": 0,
            "result": {"protocolVersion": "2025-11-25"},
        }, "sid", 200)
        with mock.patch.object(checker, "post", side_effect=[response, (None, None, 202)]) as post:
            client = checker.MCPClient("http://localhost/mcp")
            client.initialize()
        self.assertEqual(client.protocol_version, "2025-11-25")
        self.assertEqual(post.call_args_list[1].kwargs["protocol_version"], "2025-11-25")

    def test_initialize_rejects_unsupported_protocol_before_initialized(self):
        response = ({"jsonrpc": "2.0", "id": 0, "result": {"protocolVersion": "2099-01-01"}}, "sid", 200)
        with mock.patch.object(checker, "post", return_value=response) as post:
            with self.assertRaisesRegex(RuntimeError, "unsupported MCP protocol"):
                checker.MCPClient("http://localhost/mcp").initialize()
        self.assertEqual(post.call_count, 1)

    def test_parse_sse_accepts_optional_space_and_multiline_data(self):
        expected = {"jsonrpc": "2.0", "id": 1}
        self.assertEqual(checker.parse_sse('data:{"jsonrpc":"2.0","id":1}\n\n'), expected)
        self.assertEqual(checker.parse_sse('data: {"jsonrpc":"2.0",\ndata: "id":1}\n\n'), expected)

    def test_streamable_http_selects_response_matching_request_id(self):
        body = (
            b'data: {"jsonrpc":"2.0","method":"notifications/progress","params":{}}\n\n'
            b'data: {"jsonrpc":"2.0","id":99,"method":"sampling/createMessage","params":{}}\n\n'
            b'data: {"jsonrpc":"2.0","id":1,"result":{"tools":[]}}\n\n'
        )
        response = mock.MagicMock()
        response.read.return_value = body
        response.headers.get.return_value = "sid"
        response.status = 200
        opened = mock.MagicMock()
        opened.__enter__.return_value = response
        with mock.patch.object(checker.urllib.request, "urlopen", return_value=opened):
            message, session_id, status = checker.post(
                "http://localhost/mcp",
                {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            )
        self.assertEqual(message, {"jsonrpc": "2.0", "id": 1, "result": {"tools": []}})
        self.assertEqual((session_id, status), ("sid", 200))

    def test_tools_list_follows_pagination(self):
        pages = [
            {"jsonrpc": "2.0", "id": 1, "result": {
                "tools": [{"name": "memory_create"}], "nextCursor": "page-2",
            }},
            {"jsonrpc": "2.0", "id": 1, "result": {
                "tools": [{"name": "memory_search"}],
            }},
        ]
        client = checker.MCPClient("http://localhost/mcp")
        with mock.patch.object(client, "send", side_effect=pages) as send:
            self.assertEqual(
                [tool["name"] for tool in client.list_tools()],
                ["memory_create", "memory_search"],
            )
        self.assertEqual(send.call_args_list[0].args[0]["params"], {})
        self.assertEqual(send.call_args_list[1].args[0]["params"], {"cursor": "page-2"})

    def test_tools_list_rejects_repeated_cursor_and_duplicate_name(self):
        repeated_cursor = [
            {"result": {"tools": [{"name": "memory_create"}], "nextCursor": "same"}},
            {"result": {"tools": [{"name": "memory_search"}], "nextCursor": "same"}},
        ]
        client = checker.MCPClient("http://localhost/mcp")
        with mock.patch.object(client, "send", side_effect=repeated_cursor):
            with self.assertRaisesRegex(RuntimeError, "repeated cursor"):
                client.list_tools()

        duplicate_name = [
            {"result": {"tools": [{"name": "memory_create"}], "nextCursor": "next"}},
            {"result": {"tools": [{"name": "memory_create"}]}},
        ]
        with mock.patch.object(client, "send", side_effect=duplicate_name):
            with self.assertRaisesRegex(RuntimeError, "duplicate tool name"):
                client.list_tools()

    def test_tools_list_has_a_configurable_page_limit(self):
        pages = [
            {"result": {"tools": [{"name": "memory_create"}], "nextCursor": "next"}},
            {"result": {"tools": [{"name": "memory_search"}]}},
        ]
        limited_client = checker.MCPClient("http://localhost/mcp", max_tool_pages=1)
        with mock.patch.object(limited_client, "send", side_effect=pages) as send:
            with self.assertRaisesRegex(RuntimeError, "configured 1-page limit"):
                limited_client.list_tools()
        self.assertEqual(send.call_count, 1)

        client = checker.MCPClient("http://localhost/mcp", max_tool_pages=2)
        with mock.patch.object(client, "send", side_effect=pages):
            self.assertEqual(
                [tool["name"] for tool in client.list_tools()],
                ["memory_create", "memory_search"],
            )

        with self.assertRaisesRegex(ValueError, "at least 1"):
            checker.MCPClient("http://localhost/mcp", max_tool_pages=0)

    def test_schema_comparison_rejects_extra_required_field_and_constraint(self):
        extra_required = json.loads(json.dumps(CONTRACT))
        extra_required["properties"]["tenant_id"] = {"type": "string"}
        extra_required["required"].append("tenant_id")
        narrowed = json.loads(json.dumps(CONTRACT))
        narrowed["properties"]["question"]["minLength"] = 1
        self.assertFalse(checker.schemas_compatible(extra_required, CONTRACT)[0])
        self.assertFalse(checker.schemas_compatible(narrowed, CONTRACT)[0])

    def test_schema_comparison_ignores_annotations(self):
        live = json.loads(json.dumps(CONTRACT))
        live["title"] = "Generated title"
        live["properties"]["question"]["description"] = "Generated description"
        self.assertTrue(checker.schemas_compatible(live, CONTRACT)[0])

    def test_schema_comparison_rejects_default_drift(self):
        live = {"type": "string", "default": "private"}
        contract = {"type": "string", "default": "facts"}
        self.assertFalse(checker.schemas_compatible(live, contract)[0])

    def test_schema_comparison_normalizes_order_insensitive_keywords(self):
        live = {
            "type": ["null", "string"],
            "enum": ["semantic", "lexical"],
            "anyOf": [{"maxLength": 5}, {"minLength": 1}],
        }
        contract = {
            "anyOf": [{"minLength": 1}, {"maxLength": 5}],
            "enum": ["lexical", "semantic"],
            "type": ["string", "null"],
        }
        self.assertTrue(checker.schemas_compatible(live, contract)[0])

    def test_schema_comparison_normalizes_object_defaults(self):
        self.assertTrue(checker.schemas_compatible(
            {"type": "object"},
            {"type": "object", "properties": {}, "required": [], "additionalProperties": True},
        )[0])

    def test_empty_registry_fails(self):
        self.assertEqual(self.run_main({"portfolio_ask": CONTRACT}, []), 1)

    def test_missing_canonical_tool_fails(self):
        tools = [{"name": "portfolio_ask", "inputSchema": CONTRACT}]
        self.assertEqual(self.run_main({"portfolio_ask": CONTRACT, "portfolio_setup": CONTRACT}, tools), 1)

    def test_tool_without_contract_fails(self):
        tool = {"name": "memory_create", "inputSchema": {"type": "object", "properties": {}}}
        self.assertEqual(self.run_main({"portfolio_ask": CONTRACT}, [tool]), 1)

    def test_invalid_name_fails_by_default(self):
        tool = {"name": "Bad-Tool", "inputSchema": CONTRACT}
        self.assertEqual(self.run_main({"Bad-Tool": CONTRACT}, [tool]), 1)

    def test_malformed_and_empty_schema_directories_are_fatal(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "broken.json").write_text("{")
            with self.assertRaises(ValueError):
                checker.load_contract_schemas(Path(directory))
            (Path(directory) / "broken.json").write_text("[]")
            with self.assertRaises(ValueError):
                checker.load_contract_schemas(Path(directory))
        self.assertEqual(self.run_main({}, []), 2)

    def test_project_manifests_cover_all_mnemos_optional_extra_combinations(self):
        root = Path(checker.__file__).parent
        manifest, schemas_dir = checker.load_project("mnemos", root)
        schemas = checker.load_contract_schemas(schemas_dir)
        required = manifest["required_tools"]
        optional = manifest["optional_tools"]
        self.assertEqual(set(schemas), set(required) | set(optional))
        for enabled in itertools.chain.from_iterable(itertools.combinations(optional, size) for size in range(7)):
            names = [*required, *enabled]
            tools = [{"name": name, "inputSchema": schemas[name]} for name in names]
            self.assertEqual(self.run_project("mnemos", tools), 0, enabled)

    def test_ic_engine_manifest_requires_all_four_tools(self):
        root = Path(checker.__file__).parent
        manifest, schemas_dir = checker.load_project("ic-engine", root)
        schemas = checker.load_contract_schemas(schemas_dir)
        tools = [{"name": name, "inputSchema": schemas[name]} for name in manifest["required_tools"]]
        self.assertEqual(self.run_project("ic-engine", tools), 0)
        self.assertEqual(self.run_project("ic-engine", tools[:-1]), 1)

    def test_project_selection_is_required_without_custom_schemas(self):
        argv = ["checker", "--url", "http://localhost/mcp"]
        with mock.patch("sys.argv", argv), redirect_stdout(io.StringIO()), \
                redirect_stderr(io.StringIO()):
            self.assertEqual(checker.main(), 2)

    def test_error_envelope_validation_covers_both_forms(self):
        root = Path(checker.__file__).parent / "schemas" / "errors"
        form_a = json.loads((root / "tool_error_envelope.json").read_text())
        form_b = json.loads((root / "ic_result_envelope.json").read_text())
        self.assertEqual(checker.validate_error_envelope({"success": False, "error": "safe"}, form_a), [])
        self.assertTrue(checker.validate_error_envelope({"error": "unsafe shape"}, form_a))
        self.assertEqual(checker.validate_error_envelope(
            {"ic_result": {"script": "ask.py", "exit_code": 1}}, form_b), [])
        self.assertTrue(checker.validate_error_envelope(
            {"ic_result": {"script": "ask.py", "exit_code": 999}}, form_b))

    def test_mnemos_live_error_probe_rejects_malformed_envelope(self):
        root = Path(checker.__file__).parent
        manifest, schemas_dir = checker.load_project("mnemos", root)
        schemas = checker.load_contract_schemas(schemas_dir)
        tools = [{"name": name, "inputSchema": schemas[name]} for name in manifest["required_tools"]]
        malformed = {"content": [{"type": "text", "text": json.dumps({"error": "missing success"})}]}
        self.assertEqual(self.run_project("mnemos", tools, malformed), 1)


if __name__ == "__main__":
    unittest.main()
