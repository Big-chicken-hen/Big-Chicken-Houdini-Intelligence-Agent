"""Pure-Python general graph executor for P2-V Gate B1 offline tests.

This deterministic fixture models the frozen five-tool contract without
importing Houdini or claiming evidence about live cooking, Undo, rollback, or
main-thread execution.  Every accepted graph follows one object-agnostic path.
"""

from __future__ import annotations

import copy
import hashlib
from collections import Counter, OrderedDict
from typing import Any, Mapping

from hia_core.houdini_contract import (
    ContractError,
    approval_binding_digest,
    canonical_json_sha256,
    graph_digest,
    graph_side_effect_summary,
    normalize_graph,
    validate_graph_relations,
)


FAKE_CATALOG_DIGEST = hashlib.sha256(b"hia-p2-v-fake-catalog-0.1.0").hexdigest()
_FINGERPRINTED_TOOLS = frozenset(
    {"houdini_graph_validate", "houdini_graph_apply", "houdini_graph_verify"}
)
_CHECK_NAMES = (
    "session",
    "revision",
    "target",
    "ownership",
    "nodes",
    "parameters",
    "connections",
    "flags",
    "cook",
    "graph_digest",
)
_MAX_VALIDATION_RECORDS = 256
_FAKE_NODE_TYPE_CATALOG: dict[tuple[str, str], dict[str, Any]] = {
    ("Object", "geo"): {"input_count": 0, "output_count": 1, "parameters": {}},
    ("Sop", "box"): {
        "input_count": 0,
        "output_count": 1,
        "parameters": {"size": ("float", 3), "t": ("float", 3)},
    },
    ("Sop", "transform"): {
        "input_count": 1,
        "output_count": 1,
        "parameters": {"t": ("float", 3)},
    },
    ("Sop", "merge"): {"input_count": 64, "output_count": 1, "parameters": {}},
    ("Sop", "null"): {"input_count": 1, "output_count": 1, "parameters": {}},
}


def _common_output(
    arguments: Mapping[str, Any], *, ok: bool, scene_revision: int
) -> dict[str, Any]:
    return {
        "ok": ok,
        "request_id": arguments["request_id"],
        "thread_id": arguments["thread_id"],
        "turn_id": arguments["turn_id"],
        "hip_session_id": arguments["hip_session_id"],
        "base_scene_revision": arguments["base_scene_revision"],
        "idempotency_key": arguments["idempotency_key"],
        "scene_revision": scene_revision,
        "result": None,
        "warnings": [],
        "structured_error": None,
    }


def make_error_result(
    tool_name: str,
    arguments: Mapping[str, Any],
    code: str,
    message: str,
    *,
    retryable: bool = False,
    scene_revision: int | None = None,
) -> dict[str, Any]:
    """Build one schema-shaped, secret-free failure for an admitted tool."""

    del retryable  # The frozen error schema intentionally has no retry hint.
    if tool_name not in {
        "houdini_scene_info",
        "houdini_node_type_info",
        "houdini_graph_validate",
        "houdini_graph_apply",
        "houdini_graph_verify",
    }:
        raise ValueError("fake error result requires an admitted tool")
    revision = arguments["base_scene_revision"] if scene_revision is None else scene_revision
    output = _common_output(arguments, ok=False, scene_revision=revision)
    output["structured_error"] = {
        "code": code,
        "message": message[:1024],
        "details": [],
    }
    return output


def _binding_digest(
    arguments: Mapping[str, Any], normalized_graph: Mapping[str, Any]
) -> str:
    digest = graph_digest(normalized_graph)
    return approval_binding_digest(
        arguments,
        normalized_graph,
        digest,
        graph_side_effect_summary(normalized_graph),
    )


def _graph_summary(graph: Mapping[str, Any]) -> dict[str, Any]:
    counts = Counter(node["type"]["name"] for node in graph["nodes"])
    display = [node["id"] for node in graph["nodes"] if node["flags"]["display"]]
    render = [node["id"] for node in graph["nodes"] if node["flags"]["render"]]
    return {
        "node_count": len(graph["nodes"]),
        "connection_count": len(graph["connections"]),
        "type_counts": [
            {"context": "Sop", "name": name, "count": counts[name]}
            for name in sorted(counts)
        ],
        "display_node_id": display[0] if len(display) == 1 else None,
        "render_node_id": render[0] if len(render) == 1 else None,
    }


def _validate_fake_catalog(graph: Mapping[str, Any]) -> None:
    """Intersect the graph with one bounded fake live-schema snapshot."""

    catalog_by_node_id: dict[str, dict[str, Any]] = {}
    for node_index, node in enumerate(graph["nodes"]):
        key = (node["type"]["context"], node["type"]["name"])
        catalog = _FAKE_NODE_TYPE_CATALOG.get(key)
        if catalog is None:
            raise ContractError(
                "NODE_TYPE_UNAVAILABLE",
                "Node type is absent from the bounded fake node-type catalog",
                {"path": f"$.graph.nodes[{node_index}].type"},
            )
        catalog_by_node_id[node["id"]] = catalog
        admitted = catalog["parameters"]
        for parameter_index, parameter in enumerate(node["parameters"]):
            name = parameter["name"]
            path = f"$.graph.nodes[{node_index}].parameters[{parameter_index}]"
            specification = admitted.get(name)
            if specification is None:
                raise ContractError(
                    "PARAMETER_NOT_ALLOWED",
                    "Parameter is absent from the bounded fake node-type catalog",
                    {"path": f"{path}.name"},
                )
            items_type, tuple_size = specification
            typed_value = parameter["value"]
            if (
                typed_value.get("type") != "tuple"
                or typed_value.get("items_type") != items_type
                or not isinstance(typed_value.get("value"), list)
                or len(typed_value["value"]) != tuple_size
            ):
                raise ContractError(
                    "PARAMETER_TYPE_MISMATCH",
                    "Typed parameter does not match the bounded fake node-type catalog",
                    {"path": f"{path}.value"},
                )
    for connection_index, connection in enumerate(graph["connections"]):
        source = connection["source"]
        destination = connection["destination"]
        source_catalog = catalog_by_node_id[source["node"]]
        destination_catalog = catalog_by_node_id[destination["node"]]
        if source["output"] >= source_catalog["output_count"]:
            raise ContractError(
                "TOPOLOGY_NOT_ALLOWED",
                "Source output is outside the bounded fake node-type catalog",
                {
                    "path": (
                        f"$.graph.connections[{connection_index}].source.output"
                    )
                },
            )
        if destination["input"] >= destination_catalog["input_count"]:
            raise ContractError(
                "TOPOLOGY_NOT_ALLOWED",
                "Destination input is outside the bounded fake node-type catalog",
                {
                    "path": (
                        f"$.graph.connections[{connection_index}].destination.input"
                    )
                },
            )


class FakeSceneExecutor:
    """Small deterministic general-graph state machine used only by tests."""

    def __init__(
        self,
        *,
        hip_session_id: str = "fake-hip-session-001",
        hip_fingerprint: str | None = None,
        scene_revision: int = 0,
    ) -> None:
        self.hip_session_id = hip_session_id
        self.hip_fingerprint = hip_fingerprint or hashlib.sha256(
            b"hia-p2-v-fake-hip-empty"
        ).hexdigest()
        self.scene_revision = scene_revision
        self.graphs: dict[str, dict[str, Any]] = {}
        self._validated_graphs: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self.execution_count = 0

    def execute(self, tool_name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        """Execute one already schema-validated request through the fake path."""

        self.execution_count += 1
        dispatch = {
            "houdini_scene_info": self._scene_info,
            "houdini_node_type_info": self._node_type_info,
            "houdini_graph_validate": self._graph_validate,
            "houdini_graph_apply": self._graph_apply,
            "houdini_graph_verify": self._graph_verify,
        }
        if tool_name not in dispatch:
            raise ValueError("fake executor received an unregistered tool")
        if arguments["hip_session_id"] != self.hip_session_id:
            return make_error_result(
                tool_name,
                arguments,
                "HIP_SESSION_MISMATCH",
                "The fake HIP session changed.",
                scene_revision=self.scene_revision,
            )
        if arguments["base_scene_revision"] != self.scene_revision:
            return make_error_result(
                tool_name,
                arguments,
                "SCENE_CONFLICT",
                "The fake scene revision changed.",
                retryable=True,
                scene_revision=self.scene_revision,
            )
        if (
            tool_name in _FINGERPRINTED_TOOLS
            and arguments["expected_hip_fingerprint"].casefold()
            != self.hip_fingerprint.casefold()
        ):
            return make_error_result(
                tool_name,
                arguments,
                "CAPABILITY_MISMATCH",
                "The fake HIP fingerprint does not match.",
                scene_revision=self.scene_revision,
            )
        return dispatch[tool_name](arguments)

    def _scene_info(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        output = _common_output(arguments, ok=True, scene_revision=self.scene_revision)
        summaries = []
        graph_paths = sorted(self.graphs)
        if arguments["include_graph_summaries"]:
            for root_path in graph_paths[:128]:
                record = self.graphs[root_path]
                summaries.append(
                    {
                        "root_path": root_path,
                        "context": "Object",
                        "ownership": "hia_owned",
                        "graph_digest": record["graph_digest"],
                        "node_count": len(record["graph"]["nodes"]),
                        "connection_count": len(record["graph"]["connections"]),
                        "cook_state": "clean",
                    }
                )
        output["result"] = {
            "hip_fingerprint": self.hip_fingerprint,
            "current_frame": 1.0,
            "fps": 24.0,
            "dirty": bool(self.graphs),
            "enabled_contexts": ["Object", "Sop"],
            "hia_graphs": summaries,
            "graph_summaries_truncated": len(graph_paths) > 128,
        }
        return output

    @staticmethod
    def _parameter_info(name: str, items_type: str, tuple_size: int) -> dict[str, Any]:
        return {
            "name": name,
            "label": name,
            "value_type": "tuple",
            "tuple_size": tuple_size,
            "writable": True,
            "allows_expression": False,
            "default_value": {
                "type": "tuple",
                "items_type": items_type,
                "value": [1.0, 1.0, 1.0] if name == "size" else [0.0, 0.0, 0.0],
            },
        }

    def _node_type_info(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        output = _common_output(arguments, ok=True, scene_revision=self.scene_revision)
        node_types = []
        for requested in arguments["node_types"]:
            context = requested["context"]
            name = requested["name"]
            catalog = _FAKE_NODE_TYPE_CATALOG[(context, name)]
            parameters = [
                self._parameter_info(parameter_name, items_type, tuple_size)
                for parameter_name, (items_type, tuple_size) in catalog["parameters"].items()
            ]
            node_types.append(
                {
                    "context": context,
                    "requested_name": name,
                    "resolved_name": name,
                    "available": True,
                    "creatable": True,
                    "schema_source": "live_houdini_instance",
                    "parameters": parameters,
                    "input_count": catalog["input_count"],
                    "output_count": catalog["output_count"],
                }
            )
        output["result"] = {"node_types": node_types}
        return output

    def _graph_validate(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        before = (self.scene_revision, self.hip_fingerprint, copy.deepcopy(self.graphs))
        try:
            normalized = normalize_graph(arguments["graph"])
            validate_graph_relations(normalized)
            _validate_fake_catalog(normalized)
        except ContractError as exc:
            return make_error_result(
                "houdini_graph_validate",
                arguments,
                exc.code,
                exc.message,
                scene_revision=self.scene_revision,
            )
        digest = graph_digest(normalized)
        binding_digest = _binding_digest(arguments, normalized)
        self._validated_graphs[digest] = {
            "graph": copy.deepcopy(normalized),
            "hip_session_id": self.hip_session_id,
            "hip_fingerprint": self.hip_fingerprint,
            "scene_revision": self.scene_revision,
            "approval_binding_digest": binding_digest,
        }
        self._validated_graphs.move_to_end(digest)
        while len(self._validated_graphs) > _MAX_VALIDATION_RECORDS:
            self._validated_graphs.popitem(last=False)
        if before != (self.scene_revision, self.hip_fingerprint, self.graphs):
            raise AssertionError("read-only fake validation mutated scene state")
        output = _common_output(arguments, ok=True, scene_revision=self.scene_revision)
        output["result"] = {
            "valid": True,
            "scene_mutated": False,
            "normalized_graph": normalized,
            "canonical_graph_digest": digest,
            "approval_binding_digest": binding_digest,
            "summary": _graph_summary(normalized),
            "issues": [],
        }
        return output

    def _graph_apply(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        try:
            normalized = normalize_graph(arguments["graph"])
            validate_graph_relations(normalized)
            _validate_fake_catalog(normalized)
        except ContractError as exc:
            return make_error_result(
                "houdini_graph_apply",
                arguments,
                exc.code,
                exc.message,
                scene_revision=self.scene_revision,
            )
        digest = graph_digest(normalized)
        if digest.casefold() != arguments["canonical_graph_digest"].casefold():
            return make_error_result(
                "houdini_graph_apply",
                arguments,
                "DIGEST_MISMATCH",
                "The supplied canonical graph digest does not match the normalized graph.",
                scene_revision=self.scene_revision,
            )
        validated = self._validated_graphs.get(digest)
        if validated is None or (
            validated["hip_session_id"] != self.hip_session_id
            or validated["hip_fingerprint"] != self.hip_fingerprint
            or validated["scene_revision"] != self.scene_revision
            or validated["graph"] != normalized
        ):
            return make_error_result(
                "houdini_graph_apply",
                arguments,
                "APPROVAL_MISMATCH",
                "The graph is not bound to a current successful validation.",
                scene_revision=self.scene_revision,
            )

        root_path = f"/obj/{normalized['target']['name_hint']}"
        if root_path in self.graphs:
            return make_error_result(
                "houdini_graph_apply",
                arguments,
                "NAME_CONFLICT",
                "The requested HIA-owned graph root already exists.",
                scene_revision=self.scene_revision,
            )
        revision_before = self.scene_revision
        binding_digest = _binding_digest(arguments, normalized)
        self.graphs[root_path] = {
            "graph": copy.deepcopy(normalized),
            "graph_digest": digest,
            "approval_binding_digest": binding_digest,
        }
        self.scene_revision += 1
        self.hip_fingerprint = canonical_json_sha256(
            {
                "previous": self.hip_fingerprint,
                "root_path": root_path,
                "graph_digest": digest,
                "scene_revision": self.scene_revision,
            }
        )
        created_nodes = [
            {
                "request_local_id": "root",
                "path": root_path,
                "context": "Object",
                "resolved_type": "geo",
            }
        ]
        created_nodes.extend(
            {
                "request_local_id": node["id"],
                "path": f"{root_path}/{node['name_hint']}",
                "context": node["type"]["context"],
                "resolved_type": node["type"]["name"],
            }
            for node in normalized["nodes"]
        )
        output = _common_output(arguments, ok=True, scene_revision=self.scene_revision)
        output["result"] = {
            "root_path": root_path,
            "canonical_graph_digest": digest,
            "approval_binding_digest": binding_digest,
            "replay": False,
            "revision_before": revision_before,
            "revision_after": self.scene_revision,
            "created_nodes": created_nodes,
            "changed_nodes": [item["path"] for item in created_nodes],
            "undo_transaction": {
                "label": "HIA: Apply Graph",
                "opened": True,
                "committed": True,
            },
            "rollback": {"attempted": False, "complete": True, "retained_paths": []},
            "artifacts": [],
            "job_id": None,
        }
        return output

    def _graph_verify(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        root_path = arguments["root_path"]
        record = self.graphs.get(root_path)
        if record is None:
            return make_error_result(
                "houdini_graph_verify",
                arguments,
                "GRAPH_NOT_FOUND",
                "The requested HIA-owned graph does not exist.",
                scene_revision=self.scene_revision,
            )
        expected = arguments["expected_graph_digest"]
        graph = record["graph"]
        try:
            observed = graph_digest(graph)
        except ContractError as exc:
            return make_error_result(
                "houdini_graph_verify",
                arguments,
                "VERIFY_FAILED",
                f"Stored fake graph failed deterministic verification: {exc.code}",
                scene_revision=self.scene_revision,
            )
        digest_matches = expected.casefold() == observed.casefold()
        nodes = [
            {
                "request_local_id": node["id"],
                "path": f"{root_path}/{node['name_hint']}",
                "context": node["type"]["context"],
                "resolved_type": node["type"]["name"],
                "parameters": [
                    {
                        "name": parameter["name"],
                        "value": copy.deepcopy(parameter["value"]),
                        "expression_present": False,
                    }
                    for parameter in node["parameters"]
                ],
                "flags": copy.deepcopy(node["flags"]),
                "cook_state": "clean",
            }
            for node in graph["nodes"]
        ]
        connections = [
            {
                "source": {
                    "node": connection["source"]["node"],
                    "index": connection["source"]["output"],
                },
                "destination": {
                    "node": connection["destination"]["node"],
                    "index": connection["destination"]["input"],
                },
            }
            for connection in graph["connections"]
        ]
        checks = [
            {
                "name": name,
                "passed": digest_matches if name == "graph_digest" else True,
                "message": (
                    "Canonical graph digest matches."
                    if name == "graph_digest" and digest_matches
                    else "Canonical graph digest differs."
                    if name == "graph_digest"
                    else f"{name} check passed in the pure-Python fixture."
                ),
            }
            for name in _CHECK_NAMES
        ]
        output = _common_output(arguments, ok=True, scene_revision=self.scene_revision)
        output["result"] = {
            "valid": digest_matches,
            "root_path": root_path,
            "ownership": "hia_owned",
            "context": copy.deepcopy(graph["context"]),
            "expected_graph_digest": expected,
            "observed_graph_digest": observed,
            "digest_matches": digest_matches,
            "nodes": nodes,
            "connections": connections,
            "checks": checks,
        }
        return output
