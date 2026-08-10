import io
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

    def test_schema_comparison_normalizes_object_defaults(self):
        self.assertTrue(checker.schemas_compatible(
            {"type": "object"},
            {"type": "object", "properties": {}, "required": [], "additionalProperties": True},
        )[0])

    def test_empty_registry_fails(self):
        self.assertEqual(self.run_main({"portfolio_ask": CONTRACT}, []), 1)

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


if __name__ == "__main__":
    unittest.main()
