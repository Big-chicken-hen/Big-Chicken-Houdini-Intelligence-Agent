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
                "revision": 4,
                "dirty": True,
                "elapsed_seconds": 0.01,
                "script_sha256": "a" * 64,
                "scene_change_status": "changed",
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
    def test_capture_tool_requires_project_local_flipbook(self) -> None:
        capture = next(
            spec for spec in TOOL_SPECS if spec.name == "hia_capture_viewport"
        )
        description = capture.description.casefold()
        self.assertIn("sceneviewer.flipbook", description)
        self.assertIn(".runtime/cache/screenshots", description)
        self.assertIn("viewport_capture_unavailable", description)

    def test_effect_experiment_is_not_exposed(self) -> None:
        self.assertNotIn("hia_run_effect_experiment", TOOL_NAMES)

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
        self.assertEqual(17, len(names))
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
        self.assertEqual("hybrid", local_help_properties["mode"]["default"])
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
        self.assertIn("return an explicit error when it is unavailable", local_help_description)
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
        self.assertEqual("hybrid", memory_properties["mode"]["default"])
        self.assertNotIn("model", memory_properties)
        self.assertNotIn("model_id", memory_properties)
        self.assertNotIn("profile", memory_properties)
        self.assertFalse(memory_tool["annotations"]["readOnlyHint"])
        self.assertTrue(memory_tool["annotations"]["destructiveHint"])
        memory_description = memory_tool["description"].casefold()
        self.assertIn("nothing is saved automatically", memory_description)
        self.assertIn("qwen only encodes text", memory_description)
        self.assertIn("lexical search must be requested explicitly", memory_description)
        self.assertIn("requires the selected qwen encoder profile", memory_description)

        execute_tool = next(
            item for item in response["result"]["tools"] if item["name"] == "hia_execute_hom"
        )
        execute_description = execute_tool["description"].casefold()
        execute_properties = execute_tool["inputSchema"]["properties"]
        self.assertEqual({"script", "timeout_seconds"}, set(execute_properties))
        self.assertIn("undo group", execute_description)
        self.assertIn("never calls performundo", execute_description)
        self.assertIn("does not validate", execute_description)
        self.assertIn("hia_scene_diff", execute_description)
        self.assertIn("hia_validate", execute_description)

        inspect_tool = next(
            item for item in response["result"]["tools"] if item["name"] == "hia_inspect"
        )
        self.assertIn(
            "evidence",
            inspect_tool["inputSchema"]["properties"]["views"]["items"]["enum"],
        )
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
        self.assertNotIn("protected_paths", validate_properties)
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
        self.assertIn("objective capture and frame evidence", capture_description)
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
                    },
                },
            )
        )
        self.assertEqual(1, len(transport.calls))
        self.assertEqual("hia_execute_hom", transport.calls[0][0])
        structured = response["result"]["structuredContent"]
        self.assertTrue(structured["ok"])
        self.assertEqual(
            {
                "ok",
                "result",
                "stdout",
                "warnings",
                "errors",
                "revision",
                "dirty",
                "elapsed_seconds",
                "script_sha256",
                "scene_change_status",
            },
            set(structured),
        )
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
                    "requests": [
                        {
                            "category": "Sop",
                            "node_type": "vellumsolver",
                            "include_parameters": False,
                        },
                        {
                            "node_type": "Lop/karmarendersettings",
                            "include_parameters": False,
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
            {"registered": 17, "catalogued": 17, "missing": [], "orphaned": []},
            solaris["catalog_health"],
        )
        self.assertIsNone(solaris["empty_reason"])
        self.assertIn(
            ["hia_scene_diff"],
            [
                item["tools"]
                for item in search("hia_scene_diff")["result"]["capabilities"]
            ],
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

        for query in ("execute HOM", "manual Undo", "Python/HOM batch"):
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
        ):
            with self.subTest(query=query):
                names = {
                    name
                    for item in search(query)["result"]["capabilities"]
                    for name in item["tools"]
                }
                self.assertIn("hia_context", names)

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
                "registered": 17,
                "catalogued": 17,
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
            (
                "hia_node_help",
                {
                    "node_type": "Sop/box",
                    "requests": [{"node_type": "Sop/box"}],
                },
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
