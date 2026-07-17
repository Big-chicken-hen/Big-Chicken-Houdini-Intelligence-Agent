from __future__ import annotations

import copy
import io
import json
import sys
import threading
import time
import unittest
from pathlib import Path
from typing import Any, Mapping
from unittest import mock


REPOSITORY_ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))
sys.path.insert(0, str(REPOSITORY_ROOT / "services" / "houdini_mcp"))

from hia_core.houdini_contract import (  # noqa: E402
    B2_READ_ONLY_TOOLS,
    EXPECTED_TOOLS,
    ContractError,
    SchemaRegistry,
    canonical_json_sha256,
)
from hia_houdini_mcp.adapter import (  # noqa: E402
    B2_READ_ONLY_TOOL_NAMES,
    B2_READ_ONLY_TOOL_PERMISSIONS,
    CancellationHandoff,
    FROZEN_TOOL_NAMES,
    FROZEN_TOOL_PERMISSIONS,
    MCP_PROTOCOL_VERSION,
    BridgeTransportError,
    HoudiniMCPAdapter,
)
from hia_houdini_mcp import stdio as stdio_module  # noqa: E402
from hia_houdini_mcp.stdio import (  # noqa: E402
    MAX_JSONL_BYTES,
    decode_jsonl_frame,
    run_stdio,
)


TOOLS = (
    "houdini_scene_info",
    "houdini_node_type_info",
    "houdini_graph_validate",
    "houdini_graph_apply",
    "houdini_graph_verify",
)
NODE_TYPE_SELECTORS = [
    {"context": "Object", "name": "geo"},
    {"context": "Sop", "name": "box"},
    {"context": "Sop", "name": "transform"},
    {"context": "Sop", "name": "merge"},
    {"context": "Sop", "name": "null"},
]


class RecordingRegistry:
    tool_names = TOOLS

    @staticmethod
    def permission_level(name: str) -> str:
        return FROZEN_TOOL_PERMISSIONS[name]

    def __init__(self) -> None:
        self.inputs: list[tuple[str, dict[str, Any]]] = []
        self.outputs: list[tuple[str, dict[str, Any], dict[str, Any]]] = []

    def tool_descriptors(self) -> tuple[dict[str, Any], ...]:
        return tuple(
            {
                "name": name,
                "description": f"Frozen {name}",
                "inputSchema": {
                    "type": "object",
                    "additionalProperties": False,
                },
                "annotations": {
                    "readOnlyHint": name != "houdini_graph_apply",
                    "destructiveHint": False,
                    "idempotentHint": True,
                    "openWorldHint": False,
                },
            }
            for name in TOOLS
        )

    def validate_input(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        if arguments.get("invalid"):
            raise ContractError("SCHEMA_INVALID", "Input did not match schema")
        self.inputs.append((name, arguments))
        return arguments

    def validate_output(
        self,
        name: str,
        request: dict[str, Any],
        result: dict[str, Any],
    ) -> dict[str, Any]:
        if result.get("contract_mismatch"):
            raise ContractError("CONTRACT_MISMATCH", "Output contract mismatch")
        self.outputs.append((name, request, result))
        return result


class B2RecordingRegistry(RecordingRegistry):
    tool_names = B2_READ_ONLY_TOOLS

    @staticmethod
    def permission_level(name: str) -> str:
        return B2_READ_ONLY_TOOL_PERMISSIONS[name]

    def tool_descriptors(self) -> tuple[dict[str, Any], ...]:
        return tuple(
            {
                "name": name,
                "description": f"Read-only {name}",
                "inputSchema": {
                    "type": "object",
                    "additionalProperties": False,
                },
                "annotations": {
                    "readOnlyHint": True,
                    "destructiveHint": False,
                    "idempotentHint": True,
                    "openWorldHint": False,
                },
            }
            for name in self.tool_names
        )


class RecordingTransport:
    def __init__(self, result: Mapping[str, Any] | None = None) -> None:
        self.result = dict(result or {"ok": True, "value": "机"})
        self.calls: list[tuple[str, dict[str, Any], int | str]] = []
        self.cancelled: list[int | str] = []
        self.error: BridgeTransportError | None = None
        self.close_count = 0

    def call_tool(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
        *,
        rpc_request_id: int | str,
        cancellation_handoff: CancellationHandoff,
    ) -> Mapping[str, Any]:
        claimed = cancellation_handoff.claim_submission(
            lambda: self.calls.append((tool_name, dict(arguments), rpc_request_id))
        )
        if not claimed:
            raise BridgeTransportError(
                "CANCELLED",
                "The MCP tool call was cancelled before transport submission",
            )
        if self.error is not None:
            raise self.error
        return dict(self.result)

    def cancel(self, rpc_request_id: int | str) -> None:
        self.cancelled.append(rpc_request_id)

    def close(self) -> None:
        self.close_count += 1


class PreparingB2Transport(RecordingTransport):
    """Test double for the real transport's semantic/full-envelope split."""

    def __init__(self, registry: SchemaRegistry) -> None:
        super().__init__()
        self.registry = registry
        self.prepared: list[tuple[str, dict[str, Any], int | str]] = []
        self.prepare_handoffs: list[CancellationHandoff] = []
        self.call_handoffs: list[CancellationHandoff] = []

    def prepare_arguments(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
        *,
        rpc_request_id: int | str,
        cancellation_handoff: CancellationHandoff,
    ) -> Mapping[str, Any]:
        self.prepare_handoffs.append(cancellation_handoff)
        self.prepared.append((tool_name, dict(arguments), rpc_request_id))
        full = scene_info_arguments()
        if tool_name == "houdini_scene_info":
            full["include_graph_summaries"] = arguments["include_graph_summaries"]
        elif tool_name == "houdini_node_type_info":
            full.pop("include_graph_summaries")
            full["node_types"] = copy.deepcopy(arguments["node_types"])
        else:
            raise AssertionError(f"unexpected B2 tool: {tool_name}")
        return full

    def call_tool(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
        *,
        rpc_request_id: int | str,
        cancellation_handoff: CancellationHandoff,
    ) -> Mapping[str, Any]:
        self.call_handoffs.append(cancellation_handoff)
        claimed = cancellation_handoff.claim_submission(
            lambda: self.calls.append((tool_name, dict(arguments), rpc_request_id))
        )
        if not claimed:
            raise BridgeTransportError("CANCELLED", "Cancelled before submission")
        return {
            "ok": False,
            "request_id": arguments["request_id"],
            "thread_id": arguments["thread_id"],
            "turn_id": arguments["turn_id"],
            "hip_session_id": arguments["hip_session_id"],
            "base_scene_revision": arguments["base_scene_revision"],
            "scene_revision": arguments["base_scene_revision"],
            "idempotency_key": arguments["idempotency_key"],
            "result": None,
            "warnings": [],
            "structured_error": {
                "code": "HOUDINI_UNAVAILABLE",
                "message": "The read capability is unavailable",
                "details": [{"key": "retryable", "value": False}],
            },
        }


class BlockingTransport(RecordingTransport):
    def __init__(self, *, release_on_cancel: bool) -> None:
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()
        self.finished = threading.Event()
        self.release_on_cancel = release_on_cancel

    def call_tool(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
        *,
        rpc_request_id: int | str,
        cancellation_handoff: CancellationHandoff,
    ) -> Mapping[str, Any]:
        claimed = cancellation_handoff.claim_submission(
            lambda: self.calls.append((tool_name, dict(arguments), rpc_request_id))
        )
        if not claimed:
            raise BridgeTransportError(
                "CANCELLED",
                "The MCP tool call was cancelled before transport submission",
            )
        self.started.set()
        self.release.wait(5.0)
        self.finished.set()
        return {"ok": False, "cancelled": rpc_request_id in self.cancelled}

    def cancel(self, rpc_request_id: int | str) -> None:
        super().cancel(rpc_request_id)
        if self.release_on_cancel:
            self.release.set()


class CountingTransport(RecordingTransport):
    def __init__(self, expected: int) -> None:
        super().__init__()
        self.expected = expected
        self.done = threading.Event()
        self._lock = threading.Lock()

    def call_tool(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
        *,
        rpc_request_id: int | str,
        cancellation_handoff: CancellationHandoff,
    ) -> Mapping[str, Any]:
        result = super().call_tool(
            tool_name,
            arguments,
            rpc_request_id=rpc_request_id,
            cancellation_handoff=cancellation_handoff,
        )
        with self._lock:
            if len(self.calls) >= self.expected:
                self.done.set()
        return result


class BlockingCancelTransport(BlockingTransport):
    def __init__(self) -> None:
        super().__init__(release_on_cancel=False)
        self.cancel_started = threading.Event()
        self.cancel_release = threading.Event()

    def cancel(self, rpc_request_id: int | str) -> None:
        RecordingTransport.cancel(self, rpc_request_id)
        self.cancel_started.set()
        self.cancel_release.wait(5.0)


class HandoffRaceTransport(RecordingTransport):
    """Pause before registration so cancellation can win deterministically."""

    def __init__(self) -> None:
        super().__init__()
        self.entered_call = threading.Event()
        self.allow_registration = threading.Event()

    def call_tool(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
        *,
        rpc_request_id: int | str,
        cancellation_handoff: CancellationHandoff,
    ) -> Mapping[str, Any]:
        self.entered_call.set()
        self.allow_registration.wait(2.0)
        claimed = cancellation_handoff.claim_submission(
            lambda: self.calls.append((tool_name, dict(arguments), rpc_request_id))
        )
        if not claimed:
            raise BridgeTransportError(
                "CANCELLED",
                "Cancellation won before transport registration",
            )
        return dict(self.result)


class CoordinatedInput:
    """A binary readline source that can wait before yielding selected frames."""

    def __init__(
        self,
        frames: list[bytes],
        waits: dict[int, threading.Event] | None = None,
    ) -> None:
        self._frames = frames
        self._waits = waits or {}
        self._index = 0

    def readline(self, _limit: int = -1) -> bytes:
        wait = self._waits.get(self._index)
        if wait is not None:
            wait.wait(2.0)
        if self._index >= len(self._frames):
            return b""
        value = self._frames[self._index]
        self._index += 1
        return value


def initialize(adapter: HoudiniMCPAdapter, request_id: int = 1) -> dict[str, Any]:
    response = adapter.handle_message(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "initialize",
            "params": {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "offline-test", "version": "1"},
            },
        }
    )
    assert response is not None
    adapter.handle_message(
        {"jsonrpc": "2.0", "method": "notifications/initialized"}
    )
    return response


def scene_info_arguments(**extra: Any) -> dict[str, Any]:
    arguments: dict[str, Any] = {
        "request_id": "scene-request-1",
        "thread_id": "thread-1",
        "turn_id": "turn-1",
        "hip_session_id": "hip-session-1",
        "base_scene_revision": 7,
        "idempotency_key": "scene-info-key-0001",
        "deadline_ms": 1000,
        "permission_level": "scene_read",
        "include_graph_summaries": True,
    }
    arguments.update(extra)
    return arguments


class HoudiniMCPAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.transport = RecordingTransport()
        self.registry = RecordingRegistry()
        self.diagnostics: list[str] = []
        self.adapter = HoudiniMCPAdapter(
            self.transport,
            registry=self.registry,  # type: ignore[arg-type]
            diagnostic_sink=self.diagnostics.append,
        )

    def test_initialize_is_pinned_and_tools_list_exactly_five(self) -> None:
        response = initialize(self.adapter)
        self.assertEqual(MCP_PROTOCOL_VERSION, response["result"]["protocolVersion"])
        self.assertEqual(
            {"tools": {"listChanged": False}},
            response["result"]["capabilities"],
        )
        self.assertEqual("0.1.0", response["result"]["serverInfo"]["version"])
        listed = self.adapter.handle_message(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
        )
        self.assertEqual(TOOLS, tuple(tool["name"] for tool in listed["result"]["tools"]))

    def test_explicit_b2_profile_lists_and_dispatches_only_two_read_tools(self) -> None:
        transport = RecordingTransport()
        registry = B2RecordingRegistry()
        adapter = HoudiniMCPAdapter.b2_read_only(
            transport,
            registry=registry,  # type: ignore[arg-type]
        )
        initialized = initialize(adapter)
        self.assertEqual("0.2.0", initialized["result"]["serverInfo"]["version"])
        listed = adapter.handle_message(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
        )
        self.assertEqual(
            B2_READ_ONLY_TOOL_NAMES,
            tuple(item["name"] for item in listed["result"]["tools"]),
        )
        self.assertTrue(
            all(
                item["annotations"]["readOnlyHint"]
                for item in listed["result"]["tools"]
            )
        )

        response = adapter.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "houdini_scene_info",
                    "arguments": {"request_id": "b2-read"},
                },
            }
        )
        self.assertFalse(response["result"]["isError"])
        self.assertEqual(
            [("houdini_scene_info", {"request_id": "b2-read"}, 3)],
            transport.calls,
        )

    def test_b2_graph_tools_are_rejected_before_transport(self) -> None:
        transport = RecordingTransport()
        adapter = HoudiniMCPAdapter.b2_read_only(
            transport,
            registry=B2RecordingRegistry(),  # type: ignore[arg-type]
        )
        initialize(adapter)
        for request_id, name in enumerate(
            (
                "houdini_graph_validate",
                "houdini_graph_apply",
                "houdini_graph_verify",
            ),
            start=20,
        ):
            with self.subTest(tool=name):
                response = adapter.handle_message(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "method": "tools/call",
                        "params": {"name": name, "arguments": {}},
                    }
                )
                self.assertEqual(
                    "TOOL_NOT_ALLOWED", response["error"]["data"]["code"]
                )
        self.assertEqual([], transport.calls)

    def test_real_b2_registry_produces_exact_two_closed_descriptors(self) -> None:
        registry = SchemaRegistry.b2_read_only()
        adapter = HoudiniMCPAdapter.b2_read_only(
            RecordingTransport(), registry=registry
        )
        initialize(adapter)
        response = adapter.handle_message(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
        )
        descriptors = response["result"]["tools"]
        self.assertEqual(
            B2_READ_ONLY_TOOLS,
            tuple(item["name"] for item in descriptors),
        )
        for descriptor in descriptors:
            self.assertEqual("object", descriptor["inputSchema"]["type"])
            self.assertFalse(descriptor["inputSchema"]["additionalProperties"])
            self.assertEqual(
                {
                    "readOnlyHint": True,
                    "destructiveHint": False,
                    "idempotentHint": True,
                    "openWorldHint": False,
                },
                descriptor["annotations"],
            )

    def test_b2_constructor_rejects_the_b1_registry(self) -> None:
        with self.assertRaises(ValueError):
            HoudiniMCPAdapter.b2_read_only(
                RecordingTransport(), registry=SchemaRegistry()
            )

    def test_initialize_accepts_bounded_ignored_capabilities_and_title(self) -> None:
        adapter = HoudiniMCPAdapter(
            RecordingTransport(), registry=RecordingRegistry()
        )
        response = adapter.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {"sampling": {}, "roots": {"listChanged": True}},
                    "_meta": {"progressToken": "bounded-and-ignored"},
                    "clientInfo": {
                        "name": "offline-test",
                        "version": "1",
                        "title": "Offline Test Client",
                    },
                },
            }
        )
        self.assertEqual(MCP_PROTOCOL_VERSION, response["result"]["protocolVersion"])

    def test_tools_list_accepts_only_initial_null_cursor(self) -> None:
        initialize(self.adapter)
        accepted = self.adapter.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/list",
                "params": {
                    "cursor": None,
                    "_meta": {"progressToken": "bounded"},
                },
            }
        )
        self.assertIn("tools", accepted["result"])
        meta_only = self.adapter.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/list",
                "params": {"_meta": {}},
            }
        )
        self.assertIn("tools", meta_only["result"])
        for request_id, params in ((3, {"cursor": "next"}), (4, {"extra": None})):
            rejected = self.adapter.handle_message(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": "tools/list",
                    "params": params,
                }
            )
            self.assertEqual("INVALID_PARAMS", rejected["error"]["data"]["code"])

    def test_initialize_unbounded_or_unknown_client_fields_fail_closed(self) -> None:
        invalid_params = (
            {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {"oversized": "x" * 20_000},
                "clientInfo": {"name": "offline-test", "version": "1"},
            },
            {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {
                    "name": "offline-test",
                    "version": "1",
                    "unknown": True,
                },
            },
        )
        for index, params in enumerate(invalid_params, start=1):
            with self.subTest(index=index):
                adapter = HoudiniMCPAdapter(
                    RecordingTransport(), registry=RecordingRegistry()
                )
                response = adapter.handle_message(
                    {
                        "jsonrpc": "2.0",
                        "id": index,
                        "method": "initialize",
                        "params": params,
                    }
                )
                self.assertEqual(-32602, response["error"]["code"])
                self.assertFalse(adapter.initialized)

    def test_real_transport_projects_semantic_schema_and_injects_full_envelope(self) -> None:
        registry = SchemaRegistry.b2_read_only()
        transport = PreparingB2Transport(registry)
        adapter = HoudiniMCPAdapter.b2_read_only(transport, registry=registry)
        initialize(adapter)
        listed = adapter.handle_message(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
        )
        descriptors = {
            item["name"]: item["inputSchema"]
            for item in listed["result"]["tools"]
        }
        self.assertEqual(
            ["include_graph_summaries"],
            descriptors["houdini_scene_info"]["required"],
        )
        self.assertEqual(
            ["node_types"], descriptors["houdini_node_type_info"]["required"]
        )
        self.assertEqual(
            {"include_graph_summaries"},
            set(descriptors["houdini_scene_info"]["properties"]),
        )

        response = adapter.handle_message(
            {
                "jsonrpc": "2.0",
                "id": "semantic-1",
                "method": "tools/call",
                "params": {
                    "name": "houdini_scene_info",
                    "arguments": {"include_graph_summaries": False},
                    "_meta": {"progressToken": "ignored-but-bounded"},
                },
            }
        )
        self.assertTrue(response["result"]["isError"])
        self.assertEqual(
            json.loads(response["result"]["content"][0]["text"]),
            response["result"]["structuredContent"],
        )
        self.assertIn(
            "outputSchema",
            next(
                item
                for item in listed["result"]["tools"]
                if item["name"] == "houdini_scene_info"
            ),
        )
        self.assertEqual(
            [("houdini_scene_info", {"include_graph_summaries": False}, "semantic-1")],
            transport.prepared,
        )
        self.assertEqual(1, len(transport.prepare_handoffs))
        self.assertIs(transport.prepare_handoffs[0], transport.call_handoffs[0])
        self.assertEqual("hip-session-1", transport.calls[0][1]["hip_session_id"])

        injected = adapter.handle_message(
            {
                "jsonrpc": "2.0",
                "id": "semantic-2",
                "method": "tools/call",
                "params": {
                    "name": "houdini_scene_info",
                    "arguments": {
                        "include_graph_summaries": False,
                        "hip_session_id": "model-forged",
                    },
                },
            }
        )
        self.assertEqual("SCHEMA_INVALID", injected["error"]["data"]["code"])
        self.assertEqual(1, len(transport.prepared))

    def test_node_type_tools_list_schema_is_fully_inlined_for_the_model(self) -> None:
        registry = SchemaRegistry.b2_read_only()
        adapter = HoudiniMCPAdapter.b2_read_only(
            PreparingB2Transport(registry), registry=registry
        )
        initialize(adapter)
        listed = adapter.handle_message(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
        )
        descriptor = next(
            item
            for item in listed["result"]["tools"]
            if item["name"] == "houdini_node_type_info"
        )
        model_schema = descriptor["inputSchema"]
        encoded_schema = json.dumps(model_schema, sort_keys=True)
        self.assertNotIn('"$ref"', encoded_schema)
        self.assertNotIn('"$defs"', encoded_schema)
        self.assertEqual({"node_types"}, set(model_schema["properties"]))
        self.assertEqual(["node_types"], model_schema["required"])
        self.assertFalse(model_schema["additionalProperties"])
        self.assertIn("context", model_schema["description"])
        self.assertIn("name", model_schema["description"])
        self.assertEqual(
            [{"node_types": NODE_TYPE_SELECTORS}], model_schema["examples"]
        )

        frozen_schema = registry.get_input_schema("houdini_node_type_info")
        inlined = model_schema["properties"]["node_types"]["items"]
        self.assertEqual(frozen_schema["$defs"]["nodeType"], inlined)
        self.assertIn(
            "exactly context and name",
            model_schema["properties"]["node_types"]["description"],
        )
        self.assertEqual(2, len(inlined["oneOf"]))
        object_branch, sop_branch = inlined["oneOf"]
        for branch in (object_branch, sop_branch):
            self.assertEqual(["context", "name"], branch["required"])
            self.assertFalse(branch["additionalProperties"])
            self.assertEqual({"context", "name"}, set(branch["properties"]))
        self.assertEqual("Object", object_branch["properties"]["context"]["const"])
        self.assertEqual("geo", object_branch["properties"]["name"]["const"])
        self.assertEqual("Sop", sop_branch["properties"]["context"]["const"])
        self.assertEqual(
            ["box", "transform", "merge", "null"],
            sop_branch["properties"]["name"]["enum"],
        )

    def test_five_node_type_objects_pass_semantic_validation_and_prepare(self) -> None:
        registry = SchemaRegistry.b2_read_only()
        transport = PreparingB2Transport(registry)
        adapter = HoudiniMCPAdapter.b2_read_only(transport, registry=registry)
        initialize(adapter)
        semantic_arguments = {"node_types": copy.deepcopy(NODE_TYPE_SELECTORS)}

        response = adapter.handle_message(
            {
                "jsonrpc": "2.0",
                "id": "node-types-valid",
                "method": "tools/call",
                "params": {
                    "name": "houdini_node_type_info",
                    "arguments": semantic_arguments,
                },
            }
        )

        self.assertNotIn("error", response)
        self.assertTrue(response["result"]["isError"])
        self.assertEqual(
            [
                (
                    "houdini_node_type_info",
                    semantic_arguments,
                    "node-types-valid",
                )
            ],
            transport.prepared,
        )
        self.assertEqual(1, len(transport.calls))
        submitted_name, submitted, submitted_id = transport.calls[0]
        self.assertEqual("houdini_node_type_info", submitted_name)
        self.assertEqual("node-types-valid", submitted_id)
        self.assertEqual(NODE_TYPE_SELECTORS, submitted["node_types"])
        self.assertEqual("hip-session-1", submitted["hip_session_id"])
        self.assertEqual(7, submitted["base_scene_revision"])
        self.assertEqual("scene_read", submitted["permission_level"])
        self.assertNotIn("include_graph_summaries", submitted)

    def test_invalid_node_type_semantics_fail_before_prepare(self) -> None:
        invalid_arguments = (
            {"node_types": ["Sop/box"]},
            {"node_types": [{"context": "Sop", "type": "box"}]},
            {
                "node_types": [
                    {"context": "Sop", "name": "box", "unexpected": True}
                ]
            },
            {"node_types": [{"context": "Object", "name": "box"}]},
            {"node_types": [{"context": "Sop", "name": "geo"}]},
            {"node_types": [{"context": "Sop", "name": "xform"}]},
            {
                "node_types": [{"context": "Sop", "name": "box"}],
                "request_id": "model-forged",
            },
        )
        registry = SchemaRegistry.b2_read_only()
        transport = PreparingB2Transport(registry)
        adapter = HoudiniMCPAdapter.b2_read_only(transport, registry=registry)
        initialize(adapter)

        for index, arguments in enumerate(invalid_arguments, start=1):
            with self.subTest(index=index):
                response = adapter.handle_message(
                    {
                        "jsonrpc": "2.0",
                        "id": f"node-types-invalid-{index}",
                        "method": "tools/call",
                        "params": {
                            "name": "houdini_node_type_info",
                            "arguments": arguments,
                        },
                    }
                )
                self.assertEqual(-32602, response["error"]["code"])
                self.assertEqual(
                    "SCHEMA_INVALID", response["error"]["data"]["code"]
                )
                self.assertEqual([], transport.prepared)
                self.assertEqual([], transport.calls)

    def test_prepared_node_type_envelope_still_requires_frozen_validation(self) -> None:
        registry = SchemaRegistry.b2_read_only()

        class InvalidPreparedEnvelopeTransport(PreparingB2Transport):
            def prepare_arguments(
                self,
                tool_name: str,
                arguments: Mapping[str, Any],
                *,
                rpc_request_id: int | str,
                cancellation_handoff: CancellationHandoff,
            ) -> Mapping[str, Any]:
                full = dict(
                    super().prepare_arguments(
                        tool_name,
                        arguments,
                        rpc_request_id=rpc_request_id,
                        cancellation_handoff=cancellation_handoff,
                    )
                )
                full["permission_level"] = "scene_write"
                return full

        transport = InvalidPreparedEnvelopeTransport(registry)
        adapter = HoudiniMCPAdapter.b2_read_only(transport, registry=registry)
        initialize(adapter)
        response = adapter.handle_message(
            {
                "jsonrpc": "2.0",
                "id": "node-types-invalid-envelope",
                "method": "tools/call",
                "params": {
                    "name": "houdini_node_type_info",
                    "arguments": {"node_types": copy.deepcopy(NODE_TYPE_SELECTORS)},
                },
            }
        )
        self.assertEqual(-32602, response["error"]["code"])
        self.assertEqual("SCHEMA_INVALID", response["error"]["data"]["code"])
        self.assertEqual(1, len(transport.prepared))
        self.assertEqual([], transport.calls)

    def test_model_projection_does_not_mutate_frozen_b2_contract(self) -> None:
        registry = SchemaRegistry.b2_read_only()
        HoudiniMCPAdapter.b2_read_only(PreparingB2Transport(registry), registry=registry)
        frozen_schema = registry.get_input_schema("houdini_node_type_info")
        self.assertEqual(
            "bb5ec5547c00534d7135973190a6781fb6a40934d480c8b99923c3af0d305628",
            registry.manifest_digest,
        )
        self.assertEqual(
            "d468c8f602a5b012daa76ba4dda62ec4819942abfeb064cb30425a6fa5828730",
            canonical_json_sha256(frozen_schema),
        )
        self.assertEqual(
            {"$ref": "#/$defs/nodeType"},
            frozen_schema["properties"]["node_types"]["items"],
        )
        self.assertIn("nodeType", frozen_schema["$defs"])

    def test_real_schema_registry_produces_exact_five_mcp_descriptors(self) -> None:
        registry = SchemaRegistry()
        adapter = HoudiniMCPAdapter(self.transport, registry=registry)
        initialize(adapter)
        response = adapter.handle_message(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
        )
        descriptors = response["result"]["tools"]
        self.assertEqual(TOOLS, tuple(tool["name"] for tool in descriptors))
        for tool in descriptors:
            self.assertIsInstance(tool["inputSchema"], dict)
            self.assertEqual("object", tool["inputSchema"]["type"])
            self.assertEqual(
                tool["name"] != "houdini_graph_apply",
                tool["annotations"]["readOnlyHint"],
            )
            self.assertEqual(
                "scene_write"
                if tool["name"] == "houdini_graph_apply"
                else "scene_read",
                registry.permission_level(tool["name"]),
            )

    def test_adapter_rejects_registry_policy_drift_at_construction(self) -> None:
        class FourToolRegistry(RecordingRegistry):
            tool_names = TOOLS[:-1]

        class WrongPermissionRegistry(RecordingRegistry):
            @staticmethod
            def permission_level(name: str) -> str:
                return "scene_write" if name == "houdini_graph_validate" else (
                    FROZEN_TOOL_PERMISSIONS[name]
                )

        class WrongAnnotationRegistry(RecordingRegistry):
            def tool_descriptors(self) -> tuple[dict[str, Any], ...]:
                descriptors = list(super().tool_descriptors())
                descriptors[2]["annotations"]["readOnlyHint"] = False
                return tuple(descriptors)

        class ExtraAnnotationRegistry(RecordingRegistry):
            def tool_descriptors(self) -> tuple[dict[str, Any], ...]:
                descriptors = list(super().tool_descriptors())
                descriptors[0]["annotations"]["extraHint"] = False
                return tuple(descriptors)

        for registry in (
            FourToolRegistry(),
            WrongPermissionRegistry(),
            WrongAnnotationRegistry(),
            ExtraAnnotationRegistry(),
        ):
            with self.subTest(registry=type(registry).__name__):
                with self.assertRaises(ValueError):
                    HoudiniMCPAdapter(self.transport, registry=registry)  # type: ignore[arg-type]

    def test_frozen_tool_constants_match_general_graph_contract(self) -> None:
        self.assertEqual(TOOLS, FROZEN_TOOL_NAMES)
        self.assertEqual(EXPECTED_TOOLS, FROZEN_TOOL_NAMES)
        self.assertEqual(
            {"houdini_graph_apply"},
            {
                name
                for name, permission in FROZEN_TOOL_PERMISSIONS.items()
                if permission == "scene_write"
            },
        )
        self.assertEqual("scene_read", FROZEN_TOOL_PERMISSIONS["houdini_graph_validate"])

    def test_tools_call_validates_both_sides_and_returns_mcp_content(self) -> None:
        initialize(self.adapter)
        response = self.adapter.handle_message(
            {
                "jsonrpc": "2.0",
                "id": "rpc-2",
                "method": "tools/call",
                "params": {
                    "name": "houdini_scene_info",
                    "arguments": {"request_id": "scene-1"},
                },
            }
        )
        self.assertEqual(
            [("houdini_scene_info", {"request_id": "scene-1"}, "rpc-2")],
            self.transport.calls,
        )
        self.assertEqual(1, len(self.registry.inputs))
        self.assertEqual(1, len(self.registry.outputs))
        self.assertFalse(response["result"]["isError"])
        content = json.loads(response["result"]["content"][0]["text"])
        self.assertEqual({"ok": True, "value": "机"}, content)

    def test_cancellation_wins_atomic_handoff_before_apply_submission(self) -> None:
        transport = HandoffRaceTransport()
        adapter = HoudiniMCPAdapter(
            transport,
            registry=RecordingRegistry(),  # type: ignore[arg-type]
        )
        initialize(adapter)
        response: list[dict[str, Any] | None] = []

        worker = threading.Thread(
            target=lambda: response.append(
                adapter.handle_message(
                    {
                        "jsonrpc": "2.0",
                        "id": "apply-race",
                        "method": "tools/call",
                        "params": {
                            "name": "houdini_graph_apply",
                            "arguments": {"graph": "generic"},
                        },
                    }
                )
            )
        )
        worker.start()
        self.assertTrue(transport.entered_call.wait(1.0))
        adapter.cancel_request("apply-race")
        transport.allow_registration.set()
        worker.join(1.0)

        self.assertFalse(worker.is_alive())
        self.assertEqual([], transport.calls)
        self.assertEqual(["apply-race"], transport.cancelled)
        self.assertEqual("CANCELLED", response[0]["error"]["data"]["code"])

    def test_schema_invalid_and_unknown_tool_never_reach_transport(self) -> None:
        initialize(self.adapter)
        for request_id, name, arguments, expected in (
            (2, "houdini_scene_info", {"invalid": True}, "SCHEMA_INVALID"),
            (3, "arbitrary_python", {}, "TOOL_NOT_ALLOWED"),
        ):
            with self.subTest(expected=expected):
                response = self.adapter.handle_message(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "method": "tools/call",
                        "params": {"name": name, "arguments": arguments},
                    }
                )
                self.assertEqual(expected, response["error"]["data"]["code"])
        self.assertEqual([], self.transport.calls)

    def test_real_registry_rejects_unknown_tool_argument_field(self) -> None:
        transport = RecordingTransport()
        adapter = HoudiniMCPAdapter(transport, registry=SchemaRegistry())
        initialize(adapter)
        response = adapter.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 9,
                "method": "tools/call",
                "params": {
                    "name": "houdini_scene_info",
                    "arguments": scene_info_arguments(unexpected_field=True),
                },
            }
        )
        self.assertEqual("SCHEMA_INVALID", response["error"]["data"]["code"])
        self.assertEqual([], transport.calls)

    def test_tools_list_returns_a_copy_of_frozen_descriptors(self) -> None:
        initialize(self.adapter)
        first = self.adapter.handle_message(
            {"jsonrpc": "2.0", "id": 10, "method": "tools/list"}
        )
        first["result"]["tools"][0]["name"] = "mutated"
        second = self.adapter.handle_message(
            {"jsonrpc": "2.0", "id": 11, "method": "tools/list"}
        )
        self.assertEqual(TOOLS, tuple(tool["name"] for tool in second["result"]["tools"]))

    def test_bridge_transport_error_is_structured_without_token_logging(self) -> None:
        initialize(self.adapter)
        secret = "bearer-secret-must-not-be-logged"
        self.transport.error = BridgeTransportError(
            "QUEUE_FULL", "The bounded scene queue is full", {"capacity": 32}
        )
        response = self.adapter.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "houdini_scene_info",
                    "arguments": {"request_id": secret},
                },
            }
        )
        self.assertEqual(-32000, response["error"]["code"])
        self.assertEqual("QUEUE_FULL", response["error"]["data"]["code"])
        self.assertNotIn(secret, "\n".join(self.diagnostics))

    def test_unknown_request_is_method_not_found_and_notification_is_ignored(self) -> None:
        request = self.adapter.handle_message(
            {"jsonrpc": "2.0", "id": 1, "method": "unknown/request"}
        )
        notification = self.adapter.handle_message(
            {"jsonrpc": "2.0", "method": "secret-bearer-value"}
        )
        self.assertEqual(-32601, request["error"]["code"])
        self.assertIsNone(notification)
        self.assertEqual(
            ["UNKNOWN_REQUEST_REJECTED", "UNKNOWN_NOTIFICATION_IGNORED"],
            self.diagnostics,
        )

    def test_tools_are_unavailable_before_initialized_notification(self) -> None:
        response = self.adapter.handle_message(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        )
        self.assertEqual("NOT_INITIALIZED", response["error"]["data"]["code"])

    def test_cancellation_notification_delegates_without_response(self) -> None:
        response = self.adapter.handle_message(
            {
                "jsonrpc": "2.0",
                "method": "notifications/cancelled",
                "params": {
                    "requestId": "rpc-7",
                    "reason": "user stopped",
                    "_meta": {"progressToken": 7},
                },
            }
        )
        self.assertIsNone(response)
        self.assertEqual(["rpc-7"], self.transport.cancelled)

    def test_initialized_notification_accepts_only_bounded_meta(self) -> None:
        adapter = HoudiniMCPAdapter(
            RecordingTransport(), registry=RecordingRegistry()
        )
        response = adapter.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"},
                },
            }
        )
        self.assertIn("result", response)
        self.assertIsNone(
            adapter.handle_message(
                {
                    "jsonrpc": "2.0",
                    "method": "notifications/initialized",
                    "params": {"_meta": {"progressToken": "ready"}},
                }
            )
        )
        self.assertTrue(adapter.initialized)

    def test_malformed_known_notification_is_ignored_without_crashing(self) -> None:
        response = self.adapter.handle_message(
            {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
                "params": {"unexpected": "bearer-secret"},
            }
        )
        self.assertIsNone(response)
        self.assertEqual(["INVALID_NOTIFICATION_IGNORED"], self.diagnostics)
        self.assertNotIn("bearer-secret", "\n".join(self.diagnostics))

    def test_invalid_envelopes_fail_closed(self) -> None:
        for message in (
            {"jsonrpc": "1.0", "id": 1, "method": "ping"},
            {"jsonrpc": "2.0", "id": True, "method": "ping"},
            {"jsonrpc": "2.0", "id": 1, "method": "ping", "extra": 1},
            {"jsonrpc": "2.0", "id": 1, "method": "ping", "params": []},
        ):
            with self.subTest(message=message):
                response = self.adapter.handle_message(message)
                self.assertEqual(-32600, response["error"]["code"])
                if message.get("id") is True:
                    self.assertIsNone(response["id"])


class HoudiniMCPStdioTests(unittest.TestCase):
    def make_adapter(self) -> HoudiniMCPAdapter:
        return HoudiniMCPAdapter(
            RecordingTransport(),
            registry=RecordingRegistry(),  # type: ignore[arg-type]
        )

    def run_frames(self, frames: bytes) -> tuple[int, list[dict[str, Any]], str]:
        stdout = io.BytesIO()
        stderr = io.StringIO()
        status = run_stdio(
            self.make_adapter(),
            input_stream=io.BytesIO(frames),
            output_stream=stdout,
            diagnostic_stream=stderr,
        )
        responses = [json.loads(line) for line in stdout.getvalue().splitlines()]
        return status, responses, stderr.getvalue()

    def test_clean_jsonl_session_emits_only_protocol_json_on_stdout(self) -> None:
        frames = b"\n".join(
            json.dumps(message, separators=(",", ":")).encode("utf-8")
            for message in (
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": MCP_PROTOCOL_VERSION,
                        "capabilities": {},
                        "clientInfo": {"name": "test", "version": "1"},
                    },
                },
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "ping"},
            )
        ) + b"\n"
        status, responses, stderr = self.run_frames(frames)
        self.assertEqual(0, status)
        self.assertEqual([1, 2], [response["id"] for response in responses])
        self.assertEqual("", stderr)

    def test_duplicate_key_nonfinite_invalid_utf8_and_overdepth_terminate(self) -> None:
        overdeep = b'{"jsonrpc":"2.0","id":1,"method":"ping","params":' + (
            b'{"x":' * 33 + b"0" + b"}" * 33
        ) + b"}\n"
        cases = (
            b'{"jsonrpc":"2.0","id":1,"id":2,"method":"ping"}\n',
            b'{"jsonrpc":"2.0","id":1,"method":"ping","params":{"x":NaN}}\n',
            b'{"jsonrpc":"2.0","id":1,"method":"\xff"}\n',
            overdeep,
        )
        for frame in cases:
            with self.subTest(frame=frame[:40]):
                status, responses, stderr = self.run_frames(frame)
                self.assertEqual(1, status)
                self.assertEqual(-32700, responses[0]["error"]["code"])
                self.assertIn("INVALID_JSONL_FRAME", stderr)
                self.assertNotIn(frame.decode("utf-8", errors="ignore"), stderr)

    def test_oversized_frame_terminates_without_processing_following_request(self) -> None:
        oversized = b"{" + b" " * MAX_JSONL_BYTES + b"}\n"
        later = b'{"jsonrpc":"2.0","id":9,"method":"ping"}\n'
        status, responses, stderr = self.run_frames(oversized + later)
        self.assertEqual(1, status)
        self.assertEqual(1, len(responses))
        self.assertEqual("FRAME_TOO_LARGE", responses[0]["error"]["data"]["code"])
        self.assertIn("FRAME_TOO_LARGE", stderr)

    def test_decoder_accepts_utf8_and_rejects_non_object_root(self) -> None:
        value = decode_jsonl_frame('{"message":"机"}\n'.encode("utf-8"))
        self.assertEqual({"message": "机"}, value)
        with self.assertRaises(ValueError):
            decode_jsonl_frame(b"[]\n")

    def test_decoder_and_runner_fail_closed_on_recursion_error(self) -> None:
        ultra_deep = (
            b'{"value":' + (b"[" * 1500) + b"0" + (b"]" * 1500) + b"}\n"
        )
        with self.assertRaises(stdio_module.ProtocolSessionError):
            decode_jsonl_frame(ultra_deep)

        with mock.patch.object(
            stdio_module,
            "strict_json_loads",
            side_effect=RecursionError("synthetic deep input"),
        ):
            with self.assertRaises(stdio_module.ProtocolSessionError):
                decode_jsonl_frame(b'{"jsonrpc":"2.0"}\n')

        with mock.patch.object(
            stdio_module,
            "decode_jsonl_frame",
            side_effect=RecursionError("synthetic depth walk"),
        ):
            status, responses, stderr = self.run_frames(
                b'{"jsonrpc":"2.0","id":1,"method":"ping"}\n'
            )
        self.assertEqual(1, status)
        self.assertEqual(-32700, responses[0]["error"]["code"])
        self.assertEqual("INVALID_JSONL_FRAME", responses[0]["error"]["data"]["code"])
        self.assertIn("INVALID_JSONL_FRAME", stderr)
        self.assertNotIn("Traceback", stderr)

    def test_standalone_main_fails_closed_with_fixed_missing_environment_diagnostic(self) -> None:
        stderr = io.StringIO()
        registry = SchemaRegistry.b2_read_only()
        with mock.patch.object(stdio_module, "_runtime_identity_matches", return_value=True):
            with mock.patch.object(
                stdio_module.SchemaRegistry,
                "b2_read_only",
                return_value=registry,
            ):
                with mock.patch.object(
                    stdio_module.LoopbackBridgeTransport,
                    "from_environment",
                    side_effect=BridgeTransportError(
                        "BRIDGE_CONFIGURATION_MISSING",
                        "secret environment details must not be printed",
                    ),
                ) as factory:
                    with mock.patch.object(stdio_module, "serve") as serve_mock:
                        with mock.patch.object(stdio_module.sys, "stderr", stderr):
                            status = stdio_module.main()
        self.assertEqual(2, status)
        self.assertEqual(
            "hia-houdini-mcp: B2C_BRIDGE_CONFIGURATION_MISSING\n",
            stderr.getvalue(),
        )
        self.assertNotIn("secret", stderr.getvalue())
        factory.assert_called_once_with(manifest_digest=registry.manifest_digest)
        serve_mock.assert_not_called()

    def test_standalone_main_composes_environment_transport_with_stdio(self) -> None:
        transport = RecordingTransport()
        registry = SchemaRegistry.b2_read_only()
        with mock.patch.object(stdio_module, "_runtime_identity_matches", return_value=True):
            with mock.patch.object(
                stdio_module.SchemaRegistry,
                "b2_read_only",
                return_value=registry,
            ):
                with mock.patch.object(
                    stdio_module.LoopbackBridgeTransport,
                    "from_environment",
                    return_value=transport,
                ) as factory:
                    with mock.patch.object(
                        stdio_module, "serve", return_value=0
                    ) as serve_mock:
                        status = stdio_module.main()
        self.assertEqual(0, status)
        factory.assert_called_once_with(manifest_digest=registry.manifest_digest)
        serve_mock.assert_called_once_with(transport, registry=registry)

    def test_standalone_main_rejects_runtime_identity_with_fixed_diagnostic(self) -> None:
        stderr = io.StringIO()
        with mock.patch.object(stdio_module, "_runtime_identity_matches", return_value=False):
            with mock.patch.object(
                stdio_module.LoopbackBridgeTransport, "from_environment"
            ) as factory:
                with mock.patch.object(stdio_module.sys, "stderr", stderr):
                    status = stdio_module.main()
        self.assertEqual(2, status)
        self.assertEqual(
            "hia-houdini-mcp: B2C_RUNTIME_IDENTITY_INVALID\n",
            stderr.getvalue(),
        )
        factory.assert_not_called()

    def test_runtime_identity_uses_project_root_and_active_python(self) -> None:
        valid = {
            stdio_module.PROJECT_ROOT_ENV: str(REPOSITORY_ROOT),
            stdio_module.EXPECTED_PYTHON_ENV: sys.executable,
        }
        self.assertTrue(stdio_module._runtime_identity_matches(valid))
        wrong_root = dict(valid)
        wrong_root[stdio_module.PROJECT_ROOT_ENV] = str(REPOSITORY_ROOT / "tests")
        self.assertFalse(stdio_module._runtime_identity_matches(wrong_root))
        wrong_python = dict(valid)
        wrong_python[stdio_module.EXPECTED_PYTHON_ENV] = str(
            REPOSITORY_ROOT / "README.md"
        )
        self.assertFalse(stdio_module._runtime_identity_matches(wrong_python))

    def test_blocking_call_does_not_prevent_cancellation_notification(self) -> None:
        transport = BlockingTransport(release_on_cancel=True)
        adapter = HoudiniMCPAdapter(
            transport,
            registry=RecordingRegistry(),  # type: ignore[arg-type]
        )
        frames = [
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": MCP_PROTOCOL_VERSION,
                        "capabilities": {},
                        "clientInfo": {"name": "test", "version": "1"},
                    },
                }
            ).encode() + b"\n",
            b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n',
            b'{"jsonrpc":"2.0","id":"call-1","method":"tools/call",'
            b'"params":{"name":"houdini_scene_info","arguments":{}}}\n',
            b'{"jsonrpc":"2.0","method":"notifications/cancelled",'
            b'"params":{"requestId":"call-1","reason":"stop"}}\n',
            b"",
        ]
        source = CoordinatedInput(frames, waits={3: transport.started})
        stdout = io.BytesIO()
        status = run_stdio(
            adapter,
            input_stream=source,  # type: ignore[arg-type]
            output_stream=stdout,
            diagnostic_stream=io.StringIO(),
        )
        self.assertEqual(0, status)
        self.assertIn("call-1", transport.cancelled)
        self.assertTrue(transport.finished.wait(0.5))
        for line in stdout.getvalue().splitlines():
            self.assertIsInstance(json.loads(line), dict)

    def test_parallel_call_responses_are_complete_json_lines(self) -> None:
        transport = CountingTransport(expected=2)
        adapter = HoudiniMCPAdapter(
            transport,
            registry=RecordingRegistry(),  # type: ignore[arg-type]
        )
        messages = [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"},
                },
            },
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {
                "jsonrpc": "2.0",
                "id": "call-a",
                "method": "tools/call",
                "params": {"name": "houdini_scene_info", "arguments": {}},
            },
            {
                "jsonrpc": "2.0",
                "id": "call-b",
                "method": "tools/call",
                "params": {"name": "houdini_graph_verify", "arguments": {}},
            },
        ]
        frames = [
            json.dumps(message, separators=(",", ":")).encode() + b"\n"
            for message in messages
        ]
        frames.append(b"")
        source = CoordinatedInput(frames, waits={len(messages): transport.done})
        stdout = io.BytesIO()
        status = run_stdio(
            adapter,
            input_stream=source,  # type: ignore[arg-type]
            output_stream=stdout,
            diagnostic_stream=io.StringIO(),
        )
        self.assertEqual(0, status)
        responses = [json.loads(line) for line in stdout.getvalue().splitlines()]
        response_ids = {response["id"] for response in responses}
        self.assertIn("call-a", response_ids)
        self.assertIn("call-b", response_ids)

    def test_eof_shutdown_joins_normally_cancellable_call_worker(self) -> None:
        transport = BlockingTransport(release_on_cancel=True)
        adapter = HoudiniMCPAdapter(
            transport,
            registry=RecordingRegistry(),  # type: ignore[arg-type]
        )
        frames = [
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": MCP_PROTOCOL_VERSION,
                        "capabilities": {},
                        "clientInfo": {"name": "test", "version": "1"},
                    },
                }
            ).encode()
            + b"\n",
            b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n',
            b'{"jsonrpc":"2.0","id":"closing","method":"tools/call",'
            b'"params":{"name":"houdini_scene_info","arguments":{}}}\n',
            b"",
        ]
        source = CoordinatedInput(frames, waits={3: transport.started})
        status = run_stdio(
            adapter,
            input_stream=source,  # type: ignore[arg-type]
            output_stream=io.BytesIO(),
            diagnostic_stream=io.StringIO(),
        )
        self.assertEqual(0, status)
        self.assertTrue(transport.finished.is_set())
        self.assertIn("closing", transport.cancelled)
        self.assertEqual(1, transport.close_count)

        adapter.shutdown()
        self.assertEqual(1, transport.close_count)

    def test_eof_shutdown_is_bounded_when_transport_ignores_cancel(self) -> None:
        transport = BlockingCancelTransport()
        adapter = HoudiniMCPAdapter(
            transport,
            registry=RecordingRegistry(),  # type: ignore[arg-type]
        )
        frames = [
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": MCP_PROTOCOL_VERSION,
                        "capabilities": {},
                        "clientInfo": {"name": "test", "version": "1"},
                    },
                }
            ).encode() + b"\n",
            b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n',
            b'{"jsonrpc":"2.0","id":"blocked","method":"tools/call",'
            b'"params":{"name":"houdini_scene_info","arguments":{}}}\n',
            b"",
        ]
        source = CoordinatedInput(frames, waits={3: transport.started})
        started = time.monotonic()
        status = run_stdio(
            adapter,
            input_stream=source,  # type: ignore[arg-type]
            output_stream=io.BytesIO(),
            diagnostic_stream=io.StringIO(),
        )
        elapsed = time.monotonic() - started
        transport.release.set()
        transport.cancel_release.set()
        self.assertEqual(0, status)
        self.assertLess(elapsed, 1.0)
        self.assertTrue(transport.cancel_started.is_set())
        self.assertIn("blocked", transport.cancelled)


if __name__ == "__main__":
    unittest.main()
