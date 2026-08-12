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
from hia_mcp_v2.tools import CAPABILITY_MATRIX, TOOL_NAMES, TOOL_SPECS  # noqa: E402


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
            result = {
                "ok": True,
                "result": {"path": ".runtime/cache/screenshots/test.png"},
                "warnings": [],
                "errors": [],
            }
            if arguments.get("frames"):
                result["images"] = [
                    {"mime_type": "image/png", "data_base64": "ZnJhbWUx"},
                    {"mime_type": "image/png", "data_base64": "ZnJhbWUy"},
                ]
            else:
                result["image"] = {
                    "mime_type": "image/png",
                    "data_base64": "aW1hZ2U=",
                }
            return result
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
    def test_capture_tool_describes_quality_and_display_boundary(self) -> None:
        capture = next(
            spec for spec in TOOL_SPECS if spec.name == "hia_capture_viewport"
        )
        description = capture.description.casefold()
        self.assertIn("documented flipbook path", description)
        self.assertIn("first/last image content", description)
        self.assertIn("capture integrity", description)
        self.assertIn("never composition", description)
        self.assertIn("unverified os hdr", description)

    def test_adapter_emits_every_bounded_sequence_image(self) -> None:
        transport = FakeTransport()
        adapter = HiaMcpAdapter(transport)
        initialize(adapter)

        response = adapter.handle_message(
            rpc(
                2,
                "tools/call",
                {
                    "name": "hia_capture_viewport",
                    "arguments": {"mode": "flipbook", "frames": [1, 48]},
                },
            )
        )

        content = response["result"]["content"]
        self.assertEqual(["text", "image", "image"], [item["type"] for item in content])
        self.assertNotIn("images", response["result"]["structuredContent"])

    def test_geometry_summary_does_not_claim_spatial_acceptance(self) -> None:
        geometry = next(
            spec for spec in TOOL_SPECS if spec.name == "hia_geometry_summary"
        )
        description = geometry.description.casefold()

        self.assertIn("not proof of intersection", description)
        self.assertIn("endpoint clearance", description)
        self.assertIn("short read-only hom batch", description)

    def test_execute_hom_describes_staged_complex_asset_guidance(self) -> None:
        execute = next(spec for spec in TOOL_SPECS if spec.name == "hia_execute_hom")
        description = execute.description.casefold()

        self.assertIn("one semantic stage", description)
        self.assertIn("one coherent subsystem", description)
        self.assertIn("never pack primary form", description)
        self.assertIn("one all-asset script", description)

    def test_adapter_initialization_does_not_invite_one_complex_asset_batch(
        self,
    ) -> None:
        adapter = HiaMcpAdapter(FakeTransport())
        self.addCleanup(adapter.shutdown)

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

        assert response is not None
        instructions = response["result"]["instructions"].casefold()
        self.assertIn("bounded semantic authoring batches", instructions)
        self.assertIn("real scene and visual review", instructions)
        self.assertIn("lack of native subagents", instructions)

    def test_capability_discovery_describes_staged_complex_asset_writes(self) -> None:
        capability = next(
            spec for spec in TOOL_SPECS if spec.name == "hia_search_capabilities"
        )
        description = capability.description.casefold()

        self.assertIn("split complex asset writes", description)
        self.assertIn("bounded semantic stages", description)
        self.assertIn("review the real scene and images", description)

    def test_effect_experiment_is_one_bounded_domain_neutral_tool(self) -> None:
        experiment = next(
            spec
            for spec in TOOL_SPECS
            if spec.name == "hia_run_effect_experiment"
        )
        properties = experiment.input_schema["properties"]

        self.assertEqual("effect_experiment", experiment.domain)
        self.assertFalse(experiment.read_only)
        self.assertEqual(2, properties["candidates"]["minItems"])
        self.assertEqual(3, properties["candidates"]["maxItems"])
        self.assertEqual(6, properties["sample_frames"]["maxItems"])
        self.assertEqual(
            ["contact_sheet"],
            properties["capture_mode"]["enum"],
        )
        self.assertEqual(300, properties["timeout_seconds"]["maximum"])
        self.assertIsInstance(
            properties["baseline"]["properties"]["parameters"][
                "additionalProperties"
            ],
            dict,
        )
        self.assertIn("cache-reset button", experiment.description)
        self.assertIn("never scores", experiment.description)
        self.assertNotIn("effectspec", properties)

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
        matrix_names = [capability["tools"][0] for capability in CAPABILITY_MATRIX]
        self.assertEqual(list(TOOL_NAMES), names)
        self.assertEqual(list(TOOL_NAMES), matrix_names)
        self.assertEqual(len(TOOL_SPECS), len(CAPABILITY_MATRIX))
        for spec, capability in zip(TOOL_SPECS, CAPABILITY_MATRIX, strict=True):
            self.assertEqual([spec.name], capability["tools"])
            self.assertEqual(spec.domain, capability["domain"])
            self.assertEqual(spec.description, capability["description"])
            self.assertEqual(
                list(spec.input_schema.get("properties", {})),
                capability["parameters"],
            )
            self.assertEqual(list(spec.aliases), capability["aliases"])
        self.assertEqual(18, len(names))
        self.assertNotIn("hia_create_node", names)
        self.assertNotIn("hia_set_parameter", names)
        self.assertNotIn("hia_connect_nodes", names)
        self.assertTrue(all(name.startswith("hia_") for name in names))
        self.assertEqual(1, names.count("hia_local_help_search"))

        context_tool = next(
            item for item in response["result"]["tools"] if item["name"] == "hia_context"
        )
        context_properties = context_tool["inputSchema"]["properties"]
        self.assertIn("include_context_pack", context_properties)
        self.assertNotIn(
            "default",
            context_properties["include_context_pack"],
        )
        self.assertEqual(4, context_properties["knowledge_queries"]["maxItems"])
        self.assertEqual(
            32768,
            context_properties["context_pack_max_bytes"]["maximum"],
        )
        self.assertIn("strict byte budget", context_tool["description"].casefold())

        search_tool = next(
            item for item in response["result"]["tools"] if item["name"] == "hia_search_node_types"
        )
        search_description = search_tool["description"].casefold()
        self.assertIn("queries batch", search_description)
        self.assertIn("reuse its merged results", search_description)
        self.assertIn("fanning out parallel searches", search_description)
        self.assertIn("blindly retry", search_description)
        self.assertEqual(
            16,
            search_tool["inputSchema"]["properties"]["queries"]["maxItems"],
        )

        help_tool = next(
            item for item in response["result"]["tools"] if item["name"] == "hia_node_help"
        )
        help_description = help_tool["description"].casefold()
        self.assertIn("requests to batch", help_description)
        self.assertIn("node_path", help_description)
        self.assertIn("category plus a bare node_type", help_description)
        self.assertIn('node_type="category/name"', help_description)
        self.assertIn("shared defaults", help_description)
        self.assertEqual(
            16,
            help_tool["inputSchema"]["properties"]["requests"]["maxItems"],
        )

        local_help_tool = next(
            item
            for item in response["result"]["tools"]
            if item["name"] == "hia_local_help_search"
        )
        local_help_properties = local_help_tool["inputSchema"]["properties"]
        self.assertEqual(
            ["houdini", "project", "user", "memory"],
            local_help_properties["sources"]["items"]["enum"],
        )
        self.assertEqual(4, local_help_properties["sources"]["maxItems"])
        self.assertEqual(
            [
                "builtin_official_workflow",
                "community_tutorial",
                "user_document",
                "user_transcript",
                "thread_export",
                "project_memory",
            ],
            local_help_properties["source_kinds"]["items"]["enum"],
        )
        self.assertEqual(6, local_help_properties["source_kinds"]["maxItems"])
        self.assertIn("card_id", local_help_properties)
        self.assertIn("canonical_id", local_help_properties)
        self.assertEqual(
            ["lexical", "vector", "hybrid"],
            local_help_properties["mode"]["enum"],
        )
        self.assertEqual("lexical", local_help_properties["mode"]["default"])
        self.assertEqual(16, local_help_properties["queries"]["maxItems"])
        self.assertIn("refresh", local_help_properties)
        self.assertIn(
            "sqlite fts5",
            local_help_tool["description"].casefold(),
        )
        self.assertIn(
            "refresh=false is strictly read-only",
            local_help_tool["description"].casefold(),
        )
        self.assertIn(
            "queries",
            local_help_properties,
        )
        local_help_description = local_help_tool["description"].casefold()
        self.assertIn("qwen only encodes text", local_help_description)
        self.assertIn("degrades completely to lexical", local_help_description)
        self.assertIn("each query's own lexical candidates", local_help_description)
        self.assertIn("index reports complete", local_help_description)
        self.assertIn("independent cli operation", local_help_description)
        self.assertIn("single local-knowledge search entry", local_help_description)
        self.assertIn("does not gate hia_execute_hom", local_help_description)
        self.assertIn("backing source group", local_help_description)
        self.assertEqual(
            ["compact", "full", "diagnostic"],
            local_help_properties["response_format"]["enum"],
        )
        self.assertEqual(
            "compact",
            local_help_properties["response_format"]["default"],
        )
        self.assertEqual(4096, local_help_properties["max_bytes"]["minimum"])
        self.assertEqual(262144, local_help_properties["max_bytes"]["maximum"])

        memory_tool = next(
            item
            for item in response["result"]["tools"]
            if item["name"] == "hia_project_memory"
        )
        memory_properties = memory_tool["inputSchema"]["properties"]
        self.assertEqual(
            ["record", "search", "list", "delete", "supersede"],
            memory_properties["action"]["enum"],
        )
        self.assertEqual(
            ["decision", "preference", "asset", "lesson", "workflow"],
            memory_properties["memory_type"]["enum"],
        )
        self.assertEqual(["action"], memory_tool["inputSchema"]["required"])
        self.assertEqual(128, memory_properties["memory_id"]["maxLength"])
        self.assertEqual(512, memory_properties["title"]["maxLength"])
        self.assertEqual(65_536, memory_properties["body"]["maxLength"])
        self.assertEqual(32, memory_properties["tags"]["maxItems"])
        self.assertEqual(128, memory_properties["tags"]["items"]["maxLength"])
        self.assertEqual(256, memory_properties["scope"]["maxLength"])
        self.assertEqual(256, memory_properties["source_thread_id"]["maxLength"])
        self.assertEqual(256, memory_properties["source_turn_id"]["maxLength"])
        self.assertEqual(100, memory_properties["limit"]["maximum"])
        self.assertEqual("lexical", memory_properties["mode"]["default"])
        self.assertNotIn("model", memory_properties)
        self.assertNotIn("model_id", memory_properties)
        self.assertNotIn("profile", memory_properties)
        self.assertFalse(memory_tool["annotations"]["readOnlyHint"])
        self.assertTrue(memory_tool["annotations"]["destructiveHint"])
        memory_description = memory_tool["description"].casefold()
        self.assertIn("nothing is saved automatically", memory_description)
        self.assertIn("qwen only encodes text", memory_description)
        self.assertIn("defaults to lexical", memory_description)
        self.assertIn("requested/active embedding profiles", memory_description)

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
        self.assertIn("task", execute_properties)
        self.assertIn("mutable_root", execute_properties)
        self.assertIn("protected_paths", execute_properties)
        self.assertIn("expected_outputs", execute_properties)
        self.assertIn("expected_deletions", execute_properties)
        self.assertEqual(
            1_024,
            execute_properties["expected_deletions"]["maxItems"],
        )
        self.assertIn("fresh_validation", execute_properties)
        self.assertIn("require_scene_change", execute_properties)
        self.assertIn("checks", execute_properties)
        self.assertIn("semantic_checks", execute_properties)
        self.assertIn("not an ir", execute_description)
        self.assertIn("native houdini nodes", execute_description)
        self.assertIn("suitable existing nodes", execute_description)
        self.assertIn("undo group", execute_description)
        self.assertIn("does not block execution", execute_description)
        self.assertIn("parent in expected_deletions", execute_description)
        self.assertIn("do not enumerate internal child nodes", execute_description)
        self.assertIn("external file/render", execute_description)
        self.assertIn("requested validation failures", execute_description)
        self.assertIn("do not become tool errors or trigger undo", execute_description)
        self.assertIn("only a verified rolled-back batch", execute_description)
        self.assertIn("never repeat the identical failing batch", execute_description)
        self.assertIn("dirty mismatch or unavailability", execute_description)
        self.assertIn("advisory for subsequent goal work", execute_description)
        self.assertIn("critical-path existence and node-error checks", execute_description)
        self.assertIn("different runtime is rejected before submission", execute_description)
        self.assertIn("newer executor source file on disk is only advisory", execute_description)
        self.assertIn("write continues", execute_description)
        self.assertNotIn(
            "failed requested postconditions are undone",
            execute_description,
        )
        self.assertIn("before/after local-network facts", execute_description)
        self.assertIn("not an execution gate", execute_description)
        self.assertIn("bare successful script", execute_description)
        self.assertIn("fully observed structural assertions", execute_description)

        inspect_tool = next(
            item for item in response["result"]["tools"] if item["name"] == "hia_inspect"
        )
        inspect_views = inspect_tool["inputSchema"]["properties"]["views"]
        self.assertIn(
            "evidence",
            inspect_views["items"]["enum"],
        )
        self.assertEqual(len(inspect_views["items"]["enum"]), inspect_views["maxItems"])
        self.assertIn(
            "subjective quality",
            inspect_tool["description"].casefold(),
        )

        validate_tool = next(
            item for item in response["result"]["tools"] if item["name"] == "hia_validate"
        )
        validate_properties = validate_tool["inputSchema"]["properties"]
        self.assertEqual(
            [
                "node_errors",
                "empty_output",
                "critical_paths",
                "geometry_summary",
                "changed_scope",
                "semantic_expectations",
            ],
            validate_properties["checks"]["items"]["enum"],
        )
        self.assertIn("protected_paths", validate_properties)
        self.assertIn("semantic_checks", validate_properties)
        self.assertIn("spatial intersection", validate_tool["description"].casefold())
        self.assertIn(
            "unsupported node categories",
            validate_tool["description"].casefold(),
        )

        context_tool = next(
            item for item in response["result"]["tools"] if item["name"] == "hia_context"
        )
        self.assertIn(
            "include_runtime_capabilities",
            context_tool["inputSchema"]["properties"],
        )

        capture_tool = next(
            item for item in response["result"]["tools"] if item["name"] == "hia_capture_viewport"
        )
        capture_description = capture_tool["description"].casefold()
        capture_properties = capture_tool["inputSchema"]["properties"]
        self.assertIn("derive from the live viewport", capture_description)
        self.assertIn("at most 24 frames", capture_description)
        self.assertIn("requested versus actual frames", capture_description)
        self.assertIn("camera lock", capture_description)
        self.assertNotIn("default", capture_properties["width"])
        self.assertNotIn("default", capture_properties["height"])
        self.assertIn("frame", capture_properties)
        self.assertIn("frames", capture_properties)
        self.assertIn("frame_step", capture_properties)
        self.assertIn("validation_paths", capture_properties)
        self.assertEqual(24, capture_properties["frames"]["maxItems"])
        self.assertTrue(capture_properties["return_image"]["default"])
        self.assertIn("more than 24", capture_properties["frame_range"]["description"])
        self.assertTrue(capture_tool["annotations"]["readOnlyHint"])

        codex_response = adapter.handle_message(
            rpc(3, "tools/list", {"_meta": {"progressToken": "inventory"}})
        )
        self.assertEqual(
            list(TOOL_NAMES),
            [item["name"] for item in codex_response["result"]["tools"]],
        )

    def test_node_help_limit_is_explicit_and_reports_the_allowed_maximum(self) -> None:
        transport = FakeTransport()
        adapter = HiaMcpAdapter(transport)
        initialize(adapter)
        listed = adapter.handle_message(rpc(2, "tools/list", {}))
        help_tool = next(
            item for item in listed["result"]["tools"] if item["name"] == "hia_node_help"
        )
        request_limit = help_tool["inputSchema"]["properties"]["requests"]["items"][
            "properties"
        ]["limit"]

        self.assertEqual(500, request_limit["maximum"])
        self.assertIn("at most 500 parameters", request_limit["description"])
        self.assertIn("limit must be 1-500", help_tool["description"])

        rejected = adapter.handle_message(
            rpc(
                3,
                "tools/call",
                {
                    "name": "hia_node_help",
                    "arguments": {
                        "requests": [
                            {
                                "node_type": "Cop/streakblur",
                                "include_parameters": True,
                                "limit": 600,
                            }
                        ]
                    },
                },
            )
        )

        self.assertEqual(-32602, rejected["error"]["code"])
        self.assertEqual("INVALID_ARGUMENTS", rejected["error"]["data"]["code"])
        self.assertEqual(500, rejected["error"]["data"]["details"]["maximum"])
        self.assertEqual([], transport.calls)

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
        all_inspect_views = [
            "parameters",
            "connections",
            "flags",
            "errors",
            "geometry",
            "children",
            "evidence",
        ]
        for request_id, name, arguments in (
            (2, "hia_context", {"include_graph": True}),
            (
                3,
                "hia_inspect",
                {"paths": ["/obj/geo1"], "views": all_inspect_views},
            ),
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

    def test_inspect_accepts_singular_path_but_rejects_ambiguous_targets(self) -> None:
        transport = FakeTransport()
        adapter = HiaMcpAdapter(transport)
        initialize(adapter)

        response = adapter.handle_message(
            rpc(
                2,
                "tools/call",
                {"name": "hia_inspect", "arguments": {"path": "/obj"}},
            )
        )
        self.assertFalse(response["result"]["isError"])
        self.assertEqual(
            {"path": "/obj"},
            transport.calls[0][1],
        )

        ambiguous = adapter.handle_message(
            rpc(
                3,
                "tools/call",
                {
                    "name": "hia_inspect",
                    "arguments": {"path": "/obj", "paths": ["/obj"]},
                },
            )
        )
        self.assertEqual(-32602, ambiguous["error"]["code"])
        self.assertEqual("INVALID_ARGUMENTS", ambiguous["error"]["data"]["code"])
        self.assertEqual(1, len(transport.calls))

    def test_execute_accepts_more_than_sixty_four_expected_deletions(self) -> None:
        transport = FakeTransport()
        adapter = HiaMcpAdapter(transport)
        initialize(adapter)
        expected = [f"/mat/delete_{index}" for index in range(65)]

        response = adapter.handle_message(
            rpc(
                2,
                "tools/call",
                {
                    "name": "hia_execute_hom",
                    "arguments": {
                        "script": "pass",
                        "expected_deletions": expected,
                    },
                },
            )
        )

        self.assertFalse(response["result"]["isError"])
        self.assertEqual(expected, transport.calls[0][1]["expected_deletions"])

    def test_batch_query_forms_remain_one_transport_dispatch_each(self) -> None:
        transport = FakeTransport()
        adapter = HiaMcpAdapter(transport)
        initialize(adapter)
        for request_id, name, arguments in (
            (
                2,
                "hia_search_node_types",
                {"queries": ["vellum", "pyro", "materialx"]},
            ),
            (
                3,
                "hia_node_help",
                {
                    "include_parameters": False,
                    "requests": [
                        {
                            "category": "Sop",
                            "node_type": "vellumsolver",
                        },
                        {
                            "node_type": "Lop/karmarendersettings",
                        },
                    ]
                },
            ),
            (
                4,
                "hia_local_help_search",
                {"queries": ["vellum", "materialx"]},
            ),
        ):
            response = adapter.handle_message(
                rpc(
                    request_id,
                    "tools/call",
                    {"name": name, "arguments": arguments},
                )
            )
            self.assertFalse(response["result"]["isError"])
        self.assertEqual(
            [
                "hia_search_node_types",
                "hia_node_help",
                "hia_local_help_search",
            ],
            [call[0] for call in transport.calls],
        )

    def test_local_help_exact_ids_may_dispatch_without_query(self) -> None:
        transport = FakeTransport()
        adapter = HiaMcpAdapter(transport)
        initialize(adapter)
        for request_id, arguments in (
            (2, {"card_id": "karma-xpu"}),
            (3, {"canonical_id": "materialx-solaris"}),
            (4, {"card_id": "karma-xpu", "query": "render workflow"}),
        ):
            response = adapter.handle_message(
                rpc(
                    request_id,
                    "tools/call",
                    {
                        "name": "hia_local_help_search",
                        "arguments": arguments,
                    },
                )
            )
            self.assertFalse(response["result"]["isError"])
        self.assertEqual(
            [
                ("hia_local_help_search", {"card_id": "karma-xpu"}, 2),
                (
                    "hia_local_help_search",
                    {"canonical_id": "materialx-solaris"},
                    3,
                ),
                (
                    "hia_local_help_search",
                    {"card_id": "karma-xpu", "query": "render workflow"},
                    4,
                ),
            ],
            transport.calls,
        )

    def test_project_memory_actions_validate_and_dispatch_once_each(self) -> None:
        transport = FakeTransport()
        adapter = HiaMcpAdapter(transport)
        initialize(adapter)
        calls = (
            {
                "action": "record",
                "memory_type": "decision",
                "title": "Render backend",
                "body": "Use Karma XPU for approved previews.",
                "tags": ["render", "karma"],
                "scope": "project",
                "source_thread_id": "thread-1",
                "source_turn_id": "turn-2",
            },
            {
                "action": "search",
                "query": "render backend",
                "mode": "hybrid",
            },
            {"action": "list"},
            {"action": "delete", "memory_id": "memory-1"},
            {
                "action": "supersede",
                "memory_id": "memory-1",
                "memory_type": "decision",
                "title": "Updated render backend",
                "body": "Use Karma CPU for final output.",
            },
        )
        for request_id, arguments in enumerate(calls, start=20):
            response = adapter.handle_message(
                rpc(
                    request_id,
                    "tools/call",
                    {"name": "hia_project_memory", "arguments": arguments},
                )
            )
            self.assertFalse(response["result"]["isError"])

        self.assertEqual(5, len(transport.calls))
        self.assertTrue(
            all(call[0] == "hia_project_memory" for call in transport.calls)
        )
        self.assertEqual(list(calls), [call[1] for call in transport.calls])

    def test_project_memory_action_specific_fields_are_rejected_before_transport(
        self,
    ) -> None:
        transport = FakeTransport()
        adapter = HiaMcpAdapter(transport)
        initialize(adapter)
        cases = (
            (
                {"action": "record", "title": "Missing", "body": "Body."},
                "INVALID_ARGUMENTS",
            ),
            (
                {
                    "action": "search",
                    "query": "durable decision",
                    "body": "Search must not write.",
                },
                "INVALID_ARGUMENTS",
            ),
            ({"action": "list", "query": "not a search"}, "INVALID_ARGUMENTS"),
            ({"action": "delete"}, "INVALID_ARGUMENTS"),
            (
                {
                    "action": "supersede",
                    "memory_id": "memory-1",
                    "title": "Missing type",
                    "body": "Body.",
                },
                "INVALID_ARGUMENTS",
            ),
            (
                {
                    "action": "record",
                    "memory_type": "lesson",
                    "title": "No model selection",
                    "body": "The launcher selects the active embedding profile.",
                    "model_id": "forbidden-runtime-profile",
                },
                "INVALID_ARGUMENTS",
            ),
            (
                {
                    "action": "record",
                    "memory_type": "lesson",
                    "title": " ",
                    "body": "Blank required strings are invalid.",
                },
                "INVALID_ARGUMENTS",
            ),
            (
                {
                    "action": "record",
                    "memory_type": "lesson",
                    "title": "x" * 513,
                    "body": "Bounded body.",
                },
                "REQUEST_TOO_LARGE",
            ),
        )
        for request_id, (arguments, stable_code) in enumerate(
            cases,
            start=40,
        ):
            with self.subTest(arguments=arguments):
                response = adapter.handle_message(
                    rpc(
                        request_id,
                        "tools/call",
                        {"name": "hia_project_memory", "arguments": arguments},
                    )
                )
                self.assertEqual(-32602, response["error"]["code"])
                self.assertEqual(stable_code, response["error"]["data"]["code"])
        self.assertEqual([], transport.calls)

    def test_capability_search_is_local_and_does_not_dispatch(self) -> None:
        transport = FakeTransport()
        adapter = HiaMcpAdapter(transport)
        initialize(adapter)

        def search(
            query: str = "",
            *,
            domain: str = "",
            offset: int = 0,
        ) -> dict[str, Any]:
            arguments: dict[str, Any] = {"query": query, "offset": offset}
            if domain:
                arguments["domain"] = domain
            response = adapter.handle_message(
                rpc(
                    2,
                    "tools/call",
                    {
                        "name": "hia_search_capabilities",
                        "arguments": arguments,
                    },
                )
            )
            return response["result"]["structuredContent"]

        solaris = search("composed USD stage")["result"]
        self.assertEqual("solaris_usd_understanding", solaris["capabilities"][0]["domain"])
        self.assertEqual(
            {"registered": 18, "catalogued": 18, "missing": [], "orphaned": []},
            solaris["catalog_health"],
        )
        self.assertIsNone(solaris["empty_reason"])
        self.assertEqual(
            ["hia_scene_diff"],
            search("hia_scene_diff")["result"]["capabilities"][0]["tools"],
        )
        scene_tools = {
            name
            for item in search(domain="scene_perception")["result"]["capabilities"]
            for name in item["tools"]
        }
        self.assertEqual(
            {"hia_context", "hia_inspect", "hia_scene_graph"},
            scene_tools,
        )

        for query in ("modeling", "model", "建模", "几何建模"):
            with self.subTest(query=query):
                names = {
                    name
                    for item in search(query)["result"]["capabilities"]
                    for name in item["tools"]
                }
                self.assertEqual(
                    {"hia_geometry_summary", "hia_execute_hom"},
                    names,
                )

        for query in ("checkpoint", "检查点", "备份", "checkpoint_label"):
            with self.subTest(query=query):
                names = {
                    name
                    for item in search(query)["result"]["capabilities"]
                    for name in item["tools"]
                }
                self.assertIn("hia_execute_hom", names)
        for query in (
            "runtime",
            "recovery",
            "recover",
            "运行时",
            "恢复",
            "崩溃恢复",
        ):
            with self.subTest(query=query):
                names = {
                    name
                    for item in search(query)["result"]["capabilities"]
                    for name in item["tools"]
                }
                self.assertIn("hia_context", names)

        for query in (
            "runtime recovery checkpoint",
            "runtime/recovery/checkpoint",
            "运行时 恢复 检查点",
            "运行时/恢复/检查点",
            "运行时恢复检查点",
        ):
            with self.subTest(query=query):
                combined = search(query)["result"]
                names = [
                    name
                    for item in combined["capabilities"]
                    for name in item["tools"]
                ]
                self.assertIn("hia_context", names)
                self.assertIn("hia_execute_hom", names)
                self.assertLess(
                    names.index("hia_context"),
                    names.index("hia_execute_hom"),
                )
                self.assertIsNone(combined["empty_reason"])

        page_past_end = search("Solaris", offset=999)["result"]
        self.assertEqual(1, page_past_end["total"])
        self.assertEqual([], page_past_end["capabilities"])
        self.assertIsNone(page_past_end["empty_reason"])
        self.assertEqual([], transport.calls)

    def test_capability_search_distinguishes_no_match_from_incomplete_catalog(self) -> None:
        adapter = HiaMcpAdapter(FakeTransport())
        initialize(adapter)

        response = adapter.handle_message(
            rpc(
                2,
                "tools/call",
                {
                    "name": "hia_search_capabilities",
                    "arguments": {"query": "not-a-real-hia-capability"},
                },
            )
        )
        payload = response["result"]["structuredContent"]
        self.assertEqual("NO_MATCH", payload["result"]["empty_reason"])
        self.assertEqual([], payload["warnings"])

        adapter._descriptors = [  # type: ignore[attr-defined]
            item
            for item in adapter._descriptors  # type: ignore[attr-defined]
            if item["name"] != "hia_context"
        ]
        adapter._descriptors.append({"name": "hia_uncatalogued"})  # type: ignore[attr-defined]
        response = adapter.handle_message(
            rpc(
                3,
                "tools/call",
                {
                    "name": "hia_search_capabilities",
                    "arguments": {"query": "not-a-real-hia-capability"},
                },
            )
        )
        payload = response["result"]["structuredContent"]
        self.assertEqual("CATALOG_INCOMPLETE", payload["result"]["empty_reason"])
        self.assertEqual(
            {
                "registered": 18,
                "catalogued": 18,
                "missing": ["hia_uncatalogued"],
                "orphaned": ["hia_context"],
            },
            payload["result"]["catalog_health"],
        )
        self.assertEqual(1, len(payload["warnings"]))

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

    def test_semantic_check_shape_is_rejected_before_transport(self) -> None:
        transport = FakeTransport()
        adapter = HiaMcpAdapter(transport)
        initialize(adapter)
        cases = (
            {
                "semantic_checks": [
                    {"type": "sample", "data_kind": "attribute", "name": "v"}
                ]
            },
            {
                "semantic_checks": [
                    {
                        "type": "sample",
                        "path": "/obj/out",
                        "data_kind": "attribute",
                        "name": "v",
                        "min_magnitude": 2.0,
                        "max_magnitude": 1.0,
                    }
                ]
            },
            {"semantic_checks": [{"type": "mapping", "source": {
                "path": "/obj/source",
                "data_kind": "volume",
                "name": "density",
            }}]},
        )
        for request_id, arguments in enumerate(cases, start=20):
            response = adapter.handle_message(
                rpc(
                    request_id,
                    "tools/call",
                    {"name": "hia_validate", "arguments": arguments},
                )
            )
            self.assertEqual(-32602, response["error"]["code"])
            self.assertEqual(
                "INVALID_ARGUMENTS",
                response["error"]["data"]["code"],
            )
        self.assertEqual([], transport.calls)

    def test_ambiguous_or_missing_batch_query_forms_are_rejected_before_transport(
        self,
    ) -> None:
        transport = FakeTransport()
        adapter = HiaMcpAdapter(transport)
        initialize(adapter)
        cases = (
            (
                "hia_search_node_types",
                {"query": "pyro", "queries": ["pyro"]},
            ),
            ("hia_local_help_search", {}),
            (
                "hia_local_help_search",
                {"query": "vellum", "mode": "semantic"},
            ),
        )
        for request_id, (name, arguments) in enumerate(cases, start=10):
            with self.subTest(name=name):
                response = adapter.handle_message(
                    rpc(
                        request_id,
                        "tools/call",
                        {"name": name, "arguments": arguments},
                    )
                )
                self.assertEqual(-32602, response["error"]["code"])
                self.assertEqual(
                    "INVALID_ARGUMENTS",
                    response["error"]["data"]["code"],
                )
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

    def test_capture_frame_range_shape_is_rejected_before_transport(self) -> None:
        transport = FakeTransport()
        adapter = HiaMcpAdapter(transport)
        initialize(adapter)
        response = adapter.handle_message(
            rpc(
                2,
                "tools/call",
                {
                    "name": "hia_capture_viewport",
                    "arguments": {"mode": "flipbook", "frame_range": [12]},
                },
            )
        )
        self.assertEqual(-32602, response["error"]["code"])
        self.assertEqual("INVALID_ARGUMENTS", response["error"]["data"]["code"])
        self.assertEqual([], transport.calls)

    def test_runtime_parameter_contracts_are_rejected_before_transport(
        self,
    ) -> None:
        transport = FakeTransport()
        adapter = HiaMcpAdapter(transport)
        initialize(adapter)
        cases = (
            (
                "hia_capture_viewport",
                {
                    "validation_paths": [
                        f"/obj/target_{index}" for index in range(17)
                    ]
                },
            ),
            (
                "hia_capture_viewport",
                {"frame": 1, "frames": [1, 2]},
            ),
            (
                "hia_capture_viewport",
                {"frame_step": 2},
            ),
            (
                "hia_capture_viewport",
                {"frame": float("nan")},
            ),
            (
                "hia_run_effect_experiment",
                {
                    "target_network": "/obj/effect",
                    "baseline": {"parameters": {}},
                    "candidates": [
                        {
                            "name": "low",
                            "parameters": {
                                f"/obj/effect/low_{index}": index
                                for index in range(9)
                            },
                        },
                        {
                            "name": "high",
                            "parameters": {
                                f"/obj/effect/high_{index}": index
                                for index in range(9)
                            },
                        },
                    ],
                    "frame_range": [1, 48],
                    "sample_frames": [1, 24, 44],
                },
            ),
            (
                "hia_run_effect_experiment",
                {
                    "target_network": "/obj/effect",
                    "baseline": {"parameters": {}},
                    "candidates": [
                        {
                            "name": "low",
                            "parameters": {"/obj/effect/scale": 0.5},
                        },
                        {
                            "name": "high",
                            "parameters": {"/obj/effect/scale": 1.5},
                        },
                    ],
                    "frame_range": [1, 48],
                    "sample_frames": [1, 24, 44],
                    "timeout_seconds": 301,
                },
            ),
            (
                "hia_run_effect_experiment",
                {
                    "target_network": "/obj/effect",
                    "baseline": {"parameters": {}},
                    "candidates": [
                        {
                            "name": "low",
                            "parameters": {"/obj/effect/scale": [0.5]},
                        },
                        {
                            "name": "high",
                            "parameters": {"/obj/effect/scale": 1.5},
                        },
                    ],
                    "frame_range": [1, 48],
                    "sample_frames": [1, 24, 44],
                },
            ),
            (
                "hia_node_help",
                {},
            ),
            (
                "hia_scene_diff",
                {"action": "compare"},
            ),
        )
        for request_id, (name, arguments) in enumerate(cases, start=30):
            with self.subTest(name=name, arguments=arguments):
                response = adapter.handle_message(
                    rpc(
                        request_id,
                        "tools/call",
                        {"name": name, "arguments": arguments},
                    )
                )
                self.assertEqual(-32602, response["error"]["code"])
                self.assertEqual(
                    "INVALID_ARGUMENTS",
                    response["error"]["data"]["code"],
                )
        self.assertEqual([], transport.calls)

    def test_valid_effect_experiment_contract_dispatches_once(self) -> None:
        transport = FakeTransport()
        adapter = HiaMcpAdapter(transport)
        initialize(adapter)
        arguments = {
            "target_network": "/obj/effect",
            "baseline": {"parameters": {}},
            "candidates": [
                {
                    "name": "low",
                    "parameters": {"/obj/effect/scale": 0.5},
                },
                {
                    "name": "high",
                    "parameters": {"/obj/effect/scale": 1.5},
                },
            ],
            "frame_range": [1, 48],
            "sample_frames": [1, 24, 44],
            "timeout_seconds": 300,
        }

        response = adapter.handle_message(
            rpc(
                30,
                "tools/call",
                {
                    "name": "hia_run_effect_experiment",
                    "arguments": arguments,
                },
            )
        )

        self.assertNotIn("error", response)
        self.assertEqual(
            [("hia_run_effect_experiment", arguments, 30)],
            transport.calls,
        )

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
