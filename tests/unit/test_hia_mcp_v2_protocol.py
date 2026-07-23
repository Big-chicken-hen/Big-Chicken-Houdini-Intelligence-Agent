from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from typing import Any, Mapping


REPOSITORY_ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "services" / "hia_mcp_v2"))

from hia_mcp_v2.adapter import HiaMcpAdapter, MCP_PROTOCOL_VERSION  # noqa: E402
from hia_mcp_v2.stdio import run_bytes  # noqa: E402
from hia_mcp_v2.tools import CAPABILITY_MATRIX, TOOL_NAMES  # noqa: E402


class FakeTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any], int | str]] = []
        self.cancelled: list[int | str] = []
        self.closed = False

    def call(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
        *,
        request_id: int | str,
        cancellation: Any,
    ) -> Mapping[str, Any]:
        self.calls.append((tool_name, dict(arguments), request_id))
        if tool_name == "hia_execute_hom":
            return {
                "ok": True,
                "result": {"built": True},
                "stdout": "built network\n",
                "warnings": [],
                "errors": [],
                "created_or_changed_paths": ["/obj/hia_asset"],
                "revision": 4,
                "dirty": True,
            }
        if tool_name == "hia_capture_viewport":
            return {
                "ok": True,
                "result": {"path": ".runtime/cache/screenshots/test.png"},
                "warnings": [],
                "errors": [],
                "image": {"mime_type": "image/png", "data_base64": "aW1hZ2U="},
            }
        return {"ok": True, "result": {"tool": tool_name}, "warnings": [], "errors": []}

    def cancel(self, request_id: int | str) -> None:
        self.cancelled.append(request_id)

    def close(self) -> None:
        self.closed = True


def rpc(request_id: int, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    value: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        value["params"] = params
    return value


def initialize(adapter: HiaMcpAdapter) -> None:
    response = adapter.handle_message(
        rpc(
            1,
            "initialize",
            {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1"},
            },
        )
    )
    assert response is not None and "result" in response


class HiaMcpV2ProtocolTests(unittest.TestCase):
    def test_initialize_identifies_the_independent_server(self) -> None:
        adapter = HiaMcpAdapter(FakeTransport())
        response = adapter.handle_message(
            rpc(
                1,
                "initialize",
                {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"},
                },
            )
        )
        self.assertEqual("hia_mcp_v2", response["result"]["serverInfo"]["name"])
        self.assertEqual(MCP_PROTOCOL_VERSION, response["result"]["protocolVersion"])

    def test_tools_list_is_capability_led_and_matches_the_matrix(self) -> None:
        adapter = HiaMcpAdapter(FakeTransport())
        initialize(adapter)
        response = adapter.handle_message(rpc(2, "tools/list", {}))
        names = [item["name"] for item in response["result"]["tools"]]
        matrix_names = {
            name
            for capability in CAPABILITY_MATRIX
            for name in capability["tools"]
        }
        self.assertEqual(list(TOOL_NAMES), names)
        self.assertEqual(set(TOOL_NAMES), matrix_names)
        self.assertEqual(16, len(names))
        self.assertNotIn("hia_create_node", names)
        self.assertNotIn("hia_set_parameter", names)
        self.assertNotIn("hia_connect_nodes", names)
        self.assertTrue(all(name.startswith("hia_") for name in names))

        search_tool = next(
            item for item in response["result"]["tools"] if item["name"] == "hia_search_node_types"
        )
        search_description = search_tool["description"].casefold()
        self.assertIn("high-signal query", search_description)
        self.assertIn("wait for its result", search_description)
        self.assertIn("do not fan out", search_description)
        self.assertIn("blindly retry", search_description)

        help_tool = next(
            item for item in response["result"]["tools"] if item["name"] == "hia_node_help"
        )
        help_description = help_tool["description"].casefold()
        self.assertIn("node_path", help_description)
        self.assertIn("category plus a bare node_type", help_description)
        self.assertIn('node_type="category/name"', help_description)

        execute_tool = next(
            item for item in response["result"]["tools"] if item["name"] == "hia_execute_hom"
        )
        execute_description = execute_tool["description"].casefold()
        execute_properties = execute_tool["inputSchema"]["properties"]
        self.assertIn("targeted", execute_description)
        self.assertIn("must not be retried automatically", execute_description)
        self.assertIn("checkpoint", execute_description)
        self.assertIn("diff_paths", execute_properties)
        self.assertIn("checkpoint_label", execute_properties)

        codex_response = adapter.handle_message(
            rpc(3, "tools/list", {"_meta": {"progressToken": "inventory"}})
        )
        self.assertEqual(
            list(TOOL_NAMES),
            [item["name"] for item in codex_response["result"]["tools"]],
        )

    def test_execute_hom_is_one_batch_transport_dispatch(self) -> None:
        transport = FakeTransport()
        adapter = HiaMcpAdapter(transport)
        initialize(adapter)
        response = adapter.handle_message(
            rpc(
                2,
                "tools/call",
                {
                    "name": "hia_execute_hom",
                    "arguments": {
                        "script": "node = hou.node('/obj').createNode('geo')\nhia_result = node.path()",
                        "capture_diff": True,
                    },
                },
            )
        )
        self.assertEqual(1, len(transport.calls))
        self.assertEqual("hia_execute_hom", transport.calls[0][0])
        structured = response["result"]["structuredContent"]
        self.assertTrue(structured["ok"])
        self.assertEqual(["/obj/hia_asset"], structured["created_or_changed_paths"])
        self.assertEqual(4, structured["revision"])
        self.assertTrue(structured["dirty"])

    def test_context_inspect_help_and_capture_share_the_transport_contract(self) -> None:
        transport = FakeTransport()
        adapter = HiaMcpAdapter(transport)
        initialize(adapter)
        for request_id, name, arguments in (
            (2, "hia_context", {"include_graph": True}),
            (3, "hia_inspect", {"paths": ["/obj/geo1"], "views": ["parameters", "errors"]}),
            (4, "hia_node_help", {"category": "Sop", "node_type": "anything-installed"}),
            (5, "hia_capture_viewport", {"mode": "viewport"}),
        ):
            response = adapter.handle_message(
                rpc(request_id, "tools/call", {"name": name, "arguments": arguments})
            )
            self.assertFalse(response["result"]["isError"])
            if name == "hia_capture_viewport":
                self.assertEqual("image", response["result"]["content"][1]["type"])
        self.assertEqual(4, len(transport.calls))

    def test_capability_search_is_local_and_does_not_dispatch(self) -> None:
        transport = FakeTransport()
        adapter = HiaMcpAdapter(transport)
        initialize(adapter)
        response = adapter.handle_message(
            rpc(
                2,
                "tools/call",
                {"name": "hia_search_capabilities", "arguments": {"query": "Solaris"}},
            )
        )
        capabilities = response["result"]["structuredContent"]["result"]["capabilities"]
        self.assertEqual("solaris_usd_understanding", capabilities[0]["domain"])
        self.assertEqual([], transport.calls)

    def test_cancel_before_dispatch_returns_an_honest_limit(self) -> None:
        transport = FakeTransport()
        adapter = HiaMcpAdapter(transport)
        initialize(adapter)
        adapter.handle_message(
            {
                "jsonrpc": "2.0",
                "method": "notifications/cancelled",
                "params": {"requestId": 7, "reason": "test"},
            }
        )
        response = adapter.handle_message(
            rpc(7, "tools/call", {"name": "hia_context", "arguments": {}})
        )
        structured = response["result"]["structuredContent"]
        self.assertTrue(response["result"]["isError"])
        self.assertEqual("CANCELLED_BEFORE_EXECUTION", structured["structured_error"]["code"])
        self.assertFalse(structured["structured_error"]["details"]["interruptible_after_submission"])
        self.assertEqual([], transport.calls)

    def test_invalid_fields_are_rejected_before_transport(self) -> None:
        transport = FakeTransport()
        adapter = HiaMcpAdapter(transport)
        initialize(adapter)
        response = adapter.handle_message(
            rpc(
                2,
                "tools/call",
                {"name": "hia_context", "arguments": {"node_type_allowlist": ["box"]}},
            )
        )
        self.assertEqual(-32602, response["error"]["code"])
        self.assertEqual("INVALID_ARGUMENTS", response["error"]["data"]["code"])
        self.assertEqual([], transport.calls)

    def test_capture_output_path_cannot_escape_the_owned_cache(self) -> None:
        transport = FakeTransport()
        adapter = HiaMcpAdapter(transport)
        initialize(adapter)
        response = adapter.handle_message(
            rpc(
                2,
                "tools/call",
                {
                    "name": "hia_capture_viewport",
                    "arguments": {"output_path": "..\\outside.png"},
                },
            )
        )
        self.assertEqual(-32602, response["error"]["code"])
        self.assertEqual("INVALID_ARGUMENTS", response["error"]["data"]["code"])
        self.assertEqual([], transport.calls)

    def test_stdio_initialize_list_and_call(self) -> None:
        transport = FakeTransport()
        transcript = [
            rpc(
                1,
                "initialize",
                {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "stdio-test", "version": "1"},
                },
            ),
            {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
            rpc(2, "tools/list", {}),
            rpc(3, "tools/call", {"name": "hia_context", "arguments": {}}),
        ]
        payload = b"".join(
            json.dumps(item, separators=(",", ":")).encode("utf-8") + b"\n"
            for item in transcript
        )
        status, output, diagnostics = run_bytes(payload, HiaMcpAdapter(transport))
        messages = [json.loads(line) for line in output.splitlines()]
        by_id = {item["id"]: item for item in messages}
        self.assertEqual(0, status)
        self.assertEqual("", diagnostics)
        self.assertEqual("hia_mcp_v2", by_id[1]["result"]["serverInfo"]["name"])
        self.assertEqual(list(TOOL_NAMES), [item["name"] for item in by_id[2]["result"]["tools"]])
        self.assertFalse(by_id[3]["result"]["isError"])


if __name__ == "__main__":
    unittest.main()
