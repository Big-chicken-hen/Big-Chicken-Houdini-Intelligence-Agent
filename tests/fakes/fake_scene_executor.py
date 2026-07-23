"""Pure-Python general graph executor for P2-V Gate B3 offline tests.

This module is test-only.  It models the frozen five-tool contract with an
independent observed scene, deterministic transaction boundaries, confined
rollback, mandatory postcondition verification, and a simulated Undo record.
It never imports Houdini and none of its evidence applies to live ``hou``.
Every accepted graph follows one object-agnostic path.
"""

from __future__ import annotations

import copy
import hashlib
import json
import threading
from collections import Counter, OrderedDict
from dataclasses import dataclass
from typing import Any, Callable, Mapping

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
MUTATION_BOUNDARIES = (
    "create_root",
    "create_nodes",
    "set_parameters",
    "connect_nodes",
    "set_flags_layout",
    "postcondition",
    "commit",
)
UNEXPECTED_FAILURE_POINTS = (
    "publish_root",
    "create_root",
    "create_nodes",
    "set_parameters",
    "connect_nodes",
    "set_flags_layout",
    "postcondition",
    "commit",
    "build_result",
    "build_audit",
)
ROLLBACK_TAMPER_POINTS = (
    "key",
    "path",
    "target_parent",
    "target_name",
    "identity",
    "object",
    "transaction_id",
    "ownership",
)
_READ_LOCK_TIMEOUT_SECONDS = 2.0
_SENTINEL_PATH = "/obj/HIA_Graph_UserSentinel"
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


class _InjectedMutationFailure(RuntimeError):
    def __init__(self, boundary: str) -> None:
        super().__init__("deterministic fake mutation failure")
        self.boundary = boundary


class _PostconditionFailure(RuntimeError):
    pass


class FakeExecutionGuardAbort(RuntimeError):
    """Fake-only control-plane abort; never serialized as a tool error."""

    def __init__(self, reason: str) -> None:
        super().__init__("fake execution guard stopped the transaction")
        self.reason = reason
        self.rollback_proven = False


@dataclass(frozen=True)
class _UndoRecord:
    transaction_id: str
    root_path: str
    root_identity: str
    revision_before: int
    fingerprint_before: str
    dirty_before: bool
    content_digest_before: str
    sentinel_digest_before: str


@dataclass
class _RollbackProof:
    root_key: str
    root_object: dict[str, Any]
    root_path: str
    root_identity: str
    transaction_id: str
    target_parent: str
    target_name: str
    revision_before: int
    fingerprint_before: str
    dirty_before: bool
    content_digest_before: str
    sentinel_digest_before: str
    undo_records_before: tuple[_UndoRecord, ...]
    audit_records_before: tuple[dict[str, Any], ...]
    undo_record: _UndoRecord | None = None
    success_audit_record: dict[str, Any] | None = None


class _InjectedBaseException(BaseException):
    """Deterministic non-``Exception`` used to prove best-effort rollback."""

    def __init__(self, point: str) -> None:
        super().__init__("deterministic fake base exception")
        self.point = point


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
    """Deterministic observed-scene transaction simulator used only by tests."""

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
        self._dirty = False
        self._roots: dict[str, dict[str, Any]] = {
            _SENTINEL_PATH: self._make_sentinel_root()
        }
        self._validated_graphs: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._undo_stack: list[_UndoRecord] = []
        self._consumed_claims: set[str] = set()
        self._claim_authorities: list[object] = []
        self._identity_sequence = 0
        self._transaction_sequence = 0
        # One re-entrant lock is the complete fake scene transaction/snapshot
        # boundary.  Reads never observe staging state, and transaction-internal
        # digest/postcondition reads cannot deadlock themselves.
        self._scene_lock = threading.RLock()
        self._write_lock = self._scene_lock
        self._claim_lock = threading.Lock()
        self._failure_boundary: str | None = None
        self._failure_rollback = False
        self._rollback_exception = False
        self._rollback_tamper: str | None = None
        self._unexpected_failure_point: str | None = None
        self._unexpected_base_exception_point: str | None = None
        self._unexpected_base_exception_type: type[BaseException] | None = None
        self._pause_point: str | None = None
        self._pause_entered: threading.Event | None = None
        self._pause_release: threading.Event | None = None
        self._undo_pause_entered: threading.Event | None = None
        self._undo_pause_release: threading.Event | None = None
        self._postcondition_tamper = False
        self._writes_indeterminate = False
        self._active_transaction_id: str | None = None
        self.last_transaction_trace: tuple[str, ...] = ()
        self.internal_postcondition_count = 0
        self.apply_execution_count = 0
        self.audit_records: list[dict[str, Any]] = []
        self.execution_count = 0

    @staticmethod
    def _make_sentinel_root() -> dict[str, Any]:
        return {
            "object_identity": "sentinel-root-00000000",
            "ownership": "user_owned",
            "transaction_id": None,
            "path": _SENTINEL_PATH,
            "context": {"root": "Object", "children": "Sop"},
            "target": {
                "parent_path": "/obj",
                "name_hint": "HIA_Graph_UserSentinel",
                "root_local_id": "root",
                "root_type": {"context": "Object", "name": "geo"},
                "declaration_ownership": "user_owned",
            },
            "nodes": {
                "sentinel_node": {
                    "object_identity": "sentinel-node-00000000",
                    "ownership": "user_owned",
                    "transaction_id": None,
                    "id": "sentinel_node",
                    "type": {"context": "Sop", "name": "null"},
                    "name_hint": "sentinel_node",
                    "parent": "root",
                    "parameters": {},
                    "flags": {"display": True, "render": True},
                }
            },
            "connections": [],
            "layout": {"mode": "none"},
            "committed": True,
            "approval_binding_digest": None,
        }

    @property
    def graphs(self) -> dict[str, dict[str, Any]]:
        """Return a detached HIA-owned snapshot; never expose mutable state."""

        with self._scene_lock:
            return {
                path: {
                    "observed": copy.deepcopy(root),
                    "graph_digest": self._observed_digest(root),
                    "approval_binding_digest": root["approval_binding_digest"],
                }
                for path, root in sorted(self._roots.items())
                if root["ownership"] == "hia_owned" and root["committed"]
            }

    @property
    def observed_scene(self) -> dict[str, Any]:
        with self._scene_lock:
            return {
                "dirty": self._dirty,
                "roots": copy.deepcopy(self._roots),
            }

    @property
    def writes_indeterminate(self) -> bool:
        with self._scene_lock:
            return self._writes_indeterminate

    @property
    def undo_depth(self) -> int:
        with self._scene_lock:
            return len(self._undo_stack)

    @property
    def sentinel_digest(self) -> str:
        with self._scene_lock:
            return self._sentinel_digest_unlocked()

    @property
    def scene_content_digest(self) -> str:
        with self._scene_lock:
            return self._scene_content_digest_unlocked()

    def _sentinel_digest_unlocked(self) -> str:
        return canonical_json_sha256(self._roots[_SENTINEL_PATH])

    def _scene_content_digest_unlocked(self) -> str:
        return canonical_json_sha256({"dirty": self._dirty, "roots": self._roots})

    def capability_snapshot(self) -> dict[str, Any]:
        with self._scene_lock:
            return self._capability_snapshot_unlocked()

    def with_stable_capability_snapshot(
        self, callback: Callable[[dict[str, Any]], Any]
    ) -> Any:
        """Run a fake-only callback while one stable scene snapshot is held."""

        with self._scene_lock:
            return callback(self._capability_snapshot_unlocked())

    def _capability_snapshot_unlocked(self) -> dict[str, Any]:
        return {
            "hip_session_id": self.hip_session_id,
            "hip_fingerprint": self.hip_fingerprint,
            "scene_revision": self.scene_revision,
        }

    def inject_failure_once(
        self, boundary: str, *, rollback_failure: bool = False
    ) -> None:
        """Configure one deterministic test-only failure at a reviewed boundary."""

        if boundary not in MUTATION_BOUNDARIES:
            raise ValueError("unknown fake mutation boundary")
        if self._failure_boundary is not None:
            raise RuntimeError("a fake mutation failure is already configured")
        self._failure_boundary = boundary
        self._failure_rollback = bool(rollback_failure)

    def inject_unexpected_failure_once(
        self, point: str
    ) -> None:
        """Raise a real RuntimeError after mutation at ``point``."""

        if point not in UNEXPECTED_FAILURE_POINTS:
            raise ValueError("unknown fake unexpected failure point")
        if self._unexpected_failure_point or self._unexpected_base_exception_point:
            raise RuntimeError("an unexpected fake failure is already configured")
        self._unexpected_failure_point = point

    def inject_base_exception_once(
        self,
        point: str,
        exception_type: type[BaseException],
    ) -> None:
        if point not in UNEXPECTED_FAILURE_POINTS:
            raise ValueError("unknown fake unexpected failure point")
        if exception_type not in {KeyboardInterrupt, SystemExit}:
            raise ValueError("only KeyboardInterrupt/SystemExit are admitted")
        if self._unexpected_failure_point or self._unexpected_base_exception_point:
            raise RuntimeError("an unexpected fake failure is already configured")
        self._unexpected_base_exception_point = point
        self._unexpected_base_exception_type = exception_type

    def inject_rollback_exception_once(self) -> None:
        self._rollback_exception = True

    def inject_rollback_tamper_once(self, point: str) -> None:
        if point not in ROLLBACK_TAMPER_POINTS:
            raise ValueError("unknown fake rollback tamper point")
        if self._rollback_tamper is not None:
            raise RuntimeError("a fake rollback tamper is already configured")
        self._rollback_tamper = point

    def pause_at_boundary_once(
        self,
        point: str,
        entered: threading.Event,
        release: threading.Event,
    ) -> None:
        before_mutation_points = tuple(
            f"before_{boundary}" for boundary in MUTATION_BOUNDARIES[:-1]
        )
        if point not in (
            *MUTATION_BOUNDARIES,
            *before_mutation_points,
            "before_commit",
        ):
            raise ValueError("unknown fake pause point")
        if self._pause_point is not None:
            raise RuntimeError("a fake transaction pause is already configured")
        self._pause_point = point
        self._pause_entered = entered
        self._pause_release = release

    def pause_simulated_undo_once(
        self, entered: threading.Event, release: threading.Event
    ) -> None:
        if self._undo_pause_entered is not None:
            raise RuntimeError("a fake Undo pause is already configured")
        self._undo_pause_entered = entered
        self._undo_pause_release = release

    def inject_postcondition_tamper_once(self) -> None:
        """Deterministically corrupt observed state before internal verification."""

        self._postcondition_tamper = True

    def tamper_observed_parameter(
        self,
        root_path: str,
        node_id: str,
        parameter_name: str,
        typed_value: Mapping[str, Any],
    ) -> None:
        """Test-only observed-state tamper; no declaration object is retained."""

        with self._scene_lock:
            root = self._roots[root_path]
            node = root["nodes"][node_id]
            if parameter_name not in node["parameters"]:
                raise KeyError("observed parameter does not exist")
            node["parameters"][parameter_name] = copy.deepcopy(dict(typed_value))

    def tamper_observed_ownership(
        self, root_path: str, ownership: str, *, node_id: str | None = None
    ) -> None:
        with self._scene_lock:
            target = (
                self._roots[root_path]
                if node_id is None
                else self._roots[root_path]["nodes"][node_id]
            )
            target["ownership"] = ownership

    def tamper_observed_connections(
        self, root_path: str, connections: list[Mapping[str, Any]]
    ) -> None:
        with self._scene_lock:
            self._roots[root_path]["connections"] = copy.deepcopy(connections)

    def tamper_observed_flags(
        self, root_path: str, node_id: str, flags: Mapping[str, Any]
    ) -> None:
        with self._scene_lock:
            self._roots[root_path]["nodes"][node_id]["flags"] = copy.deepcopy(
                dict(flags)
            )

    def tamper_observed_cook_state(
        self, root_path: str, node_id: str, state: str
    ) -> None:
        with self._scene_lock:
            self._roots[root_path]["nodes"][node_id]["cook_state"] = state

    def remove_observed_cook_state(self, root_path: str, node_id: str) -> None:
        with self._scene_lock:
            self._roots[root_path]["nodes"][node_id].pop("cook_state", None)

    def tamper_observed_transaction(
        self, root_path: str, transaction_id: str | None
    ) -> None:
        with self._scene_lock:
            root = self._roots[root_path]
            root["transaction_id"] = transaction_id
            for node in root["nodes"].values():
                node["transaction_id"] = transaction_id

    def tamper_observed_object_identity(
        self, root_path: str, node_id: str | None, identity: str
    ) -> None:
        with self._scene_lock:
            target = (
                self._roots[root_path]
                if node_id is None
                else self._roots[root_path]["nodes"][node_id]
            )
            target["object_identity"] = identity

    def tamper_observed_node_key(
        self, root_path: str, node_id: str, replacement_key: str
    ) -> None:
        with self._scene_lock:
            nodes = self._roots[root_path]["nodes"]
            node = nodes.pop(node_id)
            nodes[replacement_key] = node

    def execute(self, tool_name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        """Execute a read or validation; direct apply has no approval authority."""

        return self._execute(tool_name, arguments, approved_claim_digest=None)

    def _bind_claim_authority(self, authority: object) -> None:
        """Bind one explicit test harness by object identity, never by input data."""

        if any(authority is candidate for candidate in self._claim_authorities):
            raise ValueError("fake claim authority is already bound")
        self._claim_authorities.append(authority)

    def _execute_authorized_claim(
        self,
        work: Any,
        *,
        authority: object,
        execution_guard: Any | None = None,
    ) -> dict[str, Any]:
        """Execute one SceneQueue claim owned by an explicitly bound harness."""

        if not any(authority is candidate for candidate in self._claim_authorities):
            raise ValueError("fake execution requires a bound offline harness")

        tool_name = getattr(work, "tool_name", None)
        arguments = getattr(work, "arguments", None)
        request_digest = getattr(work, "request_digest", None)
        executor_token = getattr(work, "executor_token", None)
        if (
            not isinstance(tool_name, str)
            or not isinstance(arguments, Mapping)
            or not isinstance(request_digest, str)
            or not isinstance(executor_token, str)
            or not executor_token
        ):
            raise ValueError("fake execution requires one complete SceneQueue claim")
        claim_digest = hashlib.sha256(executor_token.encode("utf-8")).hexdigest()
        with self._claim_lock:
            if claim_digest in self._consumed_claims:
                return make_error_result(
                    tool_name,
                    arguments,
                    "APPROVAL_MISMATCH",
                    "The fake scene claim was already consumed.",
                    scene_revision=self.scene_revision,
                )
            self._consumed_claims.add(claim_digest)
        return self._execute(
            tool_name,
            arguments,
            approved_claim_digest=request_digest,
            execution_guard=execution_guard,
        )

    def _execute(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
        *,
        approved_claim_digest: str | None,
        execution_guard: Any | None = None,
    ) -> dict[str, Any]:
        """Execute one already schema-validated request through the fake path."""

        dispatch = {
            "houdini_scene_info": self._scene_info,
            "houdini_node_type_info": self._node_type_info,
            "houdini_graph_validate": self._graph_validate,
            "houdini_graph_apply": self._graph_apply,
            "houdini_graph_verify": self._graph_verify,
        }
        if tool_name not in dispatch:
            raise ValueError("fake executor received an unregistered tool")
        is_write = tool_name == "houdini_graph_apply"
        acquired = (
            self._scene_lock.acquire(blocking=False)
            if is_write
            else self._scene_lock.acquire(timeout=_READ_LOCK_TIMEOUT_SECONDS)
        )
        if not acquired:
            return make_error_result(
                tool_name,
                arguments,
                "WRITE_IN_PROGRESS",
                "The stable fake scene snapshot is temporarily unavailable.",
                scene_revision=arguments["base_scene_revision"],
            )
        try:
            self.execution_count += 1
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
            if is_write:
                if approved_claim_digest is None:
                    return make_error_result(
                        tool_name,
                        arguments,
                        "APPROVAL_REQUIRED",
                        "Graph apply requires an explicit one-use scene approval.",
                        scene_revision=self.scene_revision,
                    )
                if self._writes_indeterminate:
                    return make_error_result(
                        tool_name,
                        arguments,
                        "SCENE_STATE_INDETERMINATE",
                        "Fake scene writes are frozen pending trusted inspection.",
                        scene_revision=self.scene_revision,
                    )
                self.apply_execution_count += 1
                return self._graph_apply(
                    arguments,
                    approved_claim_digest=approved_claim_digest,
                    execution_guard=execution_guard,
                )
            return dispatch[tool_name](arguments)
        finally:
            self._scene_lock.release()

    def _scene_info(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        output = _common_output(arguments, ok=True, scene_revision=self.scene_revision)
        summaries = []
        graph_paths = sorted(
            path
            for path, root in self._roots.items()
            if root["ownership"] == "hia_owned" and root["committed"]
        )
        if arguments["include_graph_summaries"]:
            for root_path in graph_paths[:128]:
                root = self._roots[root_path]
                observed_graph = self._rebuild_graph_from_observed(root)
                summaries.append(
                    {
                        "root_path": root_path,
                        "context": "Object",
                        "ownership": "hia_owned",
                        "graph_digest": graph_digest(observed_graph),
                        "node_count": len(observed_graph["nodes"]),
                        "connection_count": len(observed_graph["connections"]),
                        "cook_state": "clean",
                    }
                )
        output["result"] = {
            "hip_fingerprint": self.hip_fingerprint,
            "current_frame": 1.0,
            "fps": 24.0,
            "dirty": self._dirty,
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

    def _graph_apply(
        self,
        arguments: Mapping[str, Any],
        *,
        approved_claim_digest: str,
        execution_guard: Any | None = None,
    ) -> dict[str, Any]:
        revision_before = self.scene_revision
        fingerprint_before = self.hip_fingerprint
        dirty_before = self._dirty
        content_before = self._scene_content_digest_unlocked()
        sentinel_before = self._sentinel_digest_unlocked()
        transaction_trace: list[str] = []
        proof: _RollbackProof | None = None
        normalized: dict[str, Any] | None = None
        digest: str | None = None
        binding_digest: str | None = None
        mutation_started = False
        success_finalized = False
        rollback_attempted = False
        rollback_resolved = False
        try:
            normalized = normalize_graph(arguments["graph"])
            validate_graph_relations(normalized)
            _validate_fake_catalog(normalized)
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
            if root_path in self._roots:
                return make_error_result(
                    "houdini_graph_apply",
                    arguments,
                    "NAME_CONFLICT",
                    "The requested HIA-owned graph root already exists.",
                    scene_revision=self.scene_revision,
                )

            binding_digest = _binding_digest(arguments, normalized)
            transaction_id: str | None = None
            root_identity: str | None = None
            root: dict[str, Any] | None = None

            def create_root() -> None:
                nonlocal mutation_started, proof, root, root_identity, transaction_id
                transaction_id = self._next_transaction_id()
                root_identity = self._next_object_identity("root")
                root = {
                    "object_identity": root_identity,
                    "ownership": "hia_owned",
                    "transaction_id": transaction_id,
                    "path": root_path,
                    "context": copy.deepcopy(normalized["context"]),
                    "target": {
                        "parent_path": normalized["target"]["parent_path"],
                        "name_hint": normalized["target"]["name_hint"],
                        "root_local_id": normalized["target"]["root_local_id"],
                        "root_type": copy.deepcopy(normalized["target"]["root_type"]),
                        "declaration_ownership": normalized["target"]["ownership"],
                    },
                    "nodes": {},
                    "connections": [],
                    "layout": None,
                    "committed": False,
                    "approval_binding_digest": binding_digest,
                    "approved_claim_digest": approved_claim_digest,
                }
                proof = _RollbackProof(
                    root_key=root_path,
                    root_object=root,
                    root_path=root_path,
                    root_identity=root_identity,
                    transaction_id=transaction_id,
                    target_parent=normalized["target"]["parent_path"],
                    target_name=normalized["target"]["name_hint"],
                    revision_before=revision_before,
                    fingerprint_before=fingerprint_before,
                    dirty_before=dirty_before,
                    content_digest_before=content_before,
                    sentinel_digest_before=sentinel_before,
                    undo_records_before=tuple(self._undo_stack),
                    audit_records_before=tuple(self.audit_records),
                )
                mutation_started = True
                self._active_transaction_id = transaction_id
                self._roots[root_path] = root
                self._raise_unexpected_if_configured("publish_root")

            self._perform_guarded_mutation(
                "create_root",
                transaction_trace,
                create_root,
                execution_guard=execution_guard,
            )
            if root is None or proof is None or transaction_id is None or root_identity is None:
                raise RuntimeError("fake root mutation did not establish its proof")

            def create_nodes() -> None:
                for node in normalized["nodes"]:
                    root["nodes"][node["id"]] = {
                        "object_identity": self._next_object_identity("node"),
                        "ownership": "hia_owned",
                        "transaction_id": transaction_id,
                        "id": node["id"],
                        "type": copy.deepcopy(node["type"]),
                        "name_hint": node["name_hint"],
                        "parent": node["parent"],
                        "parameters": {},
                        "flags": {"display": False, "render": False},
                        "cook_state": "clean",
                    }

            self._perform_guarded_mutation(
                "create_nodes",
                transaction_trace,
                create_nodes,
                execution_guard=execution_guard,
            )

            def set_parameters() -> None:
                for node in normalized["nodes"]:
                    observed_node = root["nodes"][node["id"]]
                    observed_node["parameters"] = {
                        parameter["name"]: copy.deepcopy(parameter["value"])
                        for parameter in node["parameters"]
                    }

            self._perform_guarded_mutation(
                "set_parameters",
                transaction_trace,
                set_parameters,
                execution_guard=execution_guard,
            )

            def connect_nodes() -> None:
                root["connections"] = copy.deepcopy(normalized["connections"])

            self._perform_guarded_mutation(
                "connect_nodes",
                transaction_trace,
                connect_nodes,
                execution_guard=execution_guard,
            )

            def set_flags_layout() -> None:
                for node in normalized["nodes"]:
                    root["nodes"][node["id"]]["flags"] = copy.deepcopy(
                        node["flags"]
                    )
                root["layout"] = copy.deepcopy(normalized["layout"])

            self._perform_guarded_mutation(
                "set_flags_layout",
                transaction_trace,
                set_flags_layout,
                execution_guard=execution_guard,
            )

            def verify_postcondition() -> None:
                if self._postcondition_tamper:
                    self._postcondition_tamper = False
                    self._tamper_first_observed_value(root)
                self.internal_postcondition_count += 1
                self._assert_postcondition(root, normalized, digest)

            self._perform_guarded_mutation(
                "postcondition",
                transaction_trace,
                verify_postcondition,
                execution_guard=execution_guard,
            )

            created_nodes = self._created_node_records(root)
            self._raise_unexpected_if_configured("build_result")
            revision_after = revision_before + 1
            output = _common_output(
                arguments, ok=True, scene_revision=revision_after
            )
            output["result"] = {
                "root_path": root_path,
                "canonical_graph_digest": digest,
                "approval_binding_digest": binding_digest,
                "replay": False,
                "revision_before": revision_before,
                "revision_after": revision_after,
                "created_nodes": created_nodes,
                "changed_nodes": [item["path"] for item in created_nodes],
                "undo_transaction": {
                    "label": "HIA: Apply Graph",
                    "opened": True,
                    "committed": True,
                },
                "rollback": {
                    "attempted": False,
                    "complete": True,
                    "retained_paths": [],
                },
                "artifacts": [],
                "job_id": None,
            }
            json.dumps(output, allow_nan=False, sort_keys=True)
            undo_record = _UndoRecord(
                transaction_id=transaction_id,
                root_path=root_path,
                root_identity=root_identity,
                revision_before=revision_before,
                fingerprint_before=fingerprint_before,
                dirty_before=dirty_before,
                content_digest_before=content_before,
                sentinel_digest_before=sentinel_before,
            )
            proof.undo_record = undo_record
            success_audit = self._build_audit_record(
                arguments,
                graph_digest_value=digest,
                result_code="OK",
                rollback_status="not_attempted",
                trace=transaction_trace,
                revision_before=revision_before,
                revision_after=revision_after,
            )
            self._raise_unexpected_if_configured("build_audit")
            proof.success_audit_record = success_audit

            def commit_scene() -> dict[str, Any]:
                return self._commit_transaction(
                    root,
                    proof,
                    output,
                    success_audit,
                    transaction_trace,
                    execution_guard=execution_guard,
                )

            self._pause_if_configured("before_commit")
            if execution_guard is not None:
                execution_guard.checkpoint("before_commit")
                finalized = execution_guard.finalize(output, commit_scene)
            else:
                finalized = commit_scene()
            success_finalized = True
            return finalized
        except BaseException as exc:
            if mutation_started and proof is not None:
                rollback_attempted = True
                rollback_resolved = self._attempt_confined_rollback(proof)
                if not rollback_resolved:
                    self._writes_indeterminate = True

            if isinstance(exc, FakeExecutionGuardAbort):
                exc.rollback_proven = (not mutation_started) or rollback_resolved
                self.last_transaction_trace = tuple(transaction_trace)
                self._safe_append_failure_audit(
                    arguments,
                    graph_digest_value=digest,
                    result_code="CONTROL_ABORT",
                    rollback_status=(
                        "not_required"
                        if not mutation_started
                        else "complete" if rollback_resolved else "indeterminate"
                    ),
                    trace=transaction_trace,
                    revision_before=revision_before,
                )
                raise

            if not isinstance(exc, Exception):
                self.last_transaction_trace = tuple(transaction_trace)
                raise

            error_code = "INTERNAL_ERROR"
            error_message = "The fake transaction failed and was safely rolled back."
            if isinstance(exc, ContractError):
                error_code = exc.code
                error_message = exc.message
            elif isinstance(exc, _PostconditionFailure):
                error_code = "POSTCONDITION_FAILED"
                error_message = "Mandatory fake postcondition verification failed."
            elif isinstance(exc, _InjectedMutationFailure):
                error_message = "A deterministic fake transaction boundary failed."

            if mutation_started and not rollback_resolved:
                error_code = "SCENE_STATE_INDETERMINATE"
                error_message = (
                    "Fake rollback could not prove the scene state; later writes are frozen."
                )
                rollback_status = "indeterminate"
            else:
                rollback_status = "complete" if mutation_started else "not_required"
            self.last_transaction_trace = tuple(transaction_trace)
            self._safe_append_failure_audit(
                arguments,
                graph_digest_value=digest,
                result_code=error_code,
                rollback_status=rollback_status,
                trace=transaction_trace,
                revision_before=revision_before,
            )
            return make_error_result(
                "houdini_graph_apply",
                arguments,
                error_code,
                error_message,
                scene_revision=self.scene_revision,
            )
        finally:
            if (
                mutation_started
                and not success_finalized
                and not rollback_attempted
                and proof is not None
            ):
                rollback_resolved = self._attempt_confined_rollback(proof)
                if not rollback_resolved:
                    self._writes_indeterminate = True
            self._active_transaction_id = None

    def _graph_verify(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        root_path = arguments["root_path"]
        root = self._roots.get(root_path)
        if root is None or not root.get("committed"):
            return make_error_result(
                "houdini_graph_verify",
                arguments,
                "GRAPH_NOT_FOUND",
                "The requested HIA-owned graph does not exist.",
                scene_revision=self.scene_revision,
            )
        expected = arguments["expected_graph_digest"]
        try:
            graph = self._rebuild_graph_from_observed(root, require_ownership=False)
            validate_graph_relations(graph)
            _validate_fake_catalog(graph)
            observed = graph_digest(graph)
            nodes = self._verification_node_records(root, graph)
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
        except (ContractError, KeyError, TypeError, ValueError):
            return make_error_result(
                "houdini_graph_verify",
                arguments,
                "VERIFY_FAILED",
                "Observed fake scene state could not be reconstructed safely.",
                scene_revision=self.scene_revision,
            )
        digest_matches = expected.casefold() == observed.casefold()
        validation = self._validated_graphs.get(expected.casefold())
        expected_graph = (
            copy.deepcopy(validation["graph"])
            if validation is not None
            and validation.get("hip_session_id") == self.hip_session_id
            else None
        )
        target = root.get("target")
        target_structural = (
            isinstance(target, Mapping)
            and root.get("path") == root_path
            and target.get("parent_path") == "/obj"
            and isinstance(target.get("name_hint"), str)
            and root_path == f"/obj/{target.get('name_hint')}"
            and target.get("root_local_id") == "root"
            and target.get("root_type") == {"context": "Object", "name": "geo"}
            and root.get("context") == {"root": "Object", "children": "Sop"}
        )
        raw_nodes = root.get("nodes", {})
        anchors = [
            record for record in self._undo_stack if record.root_path == root_path
        ]
        anchor = anchors[0] if len(anchors) == 1 else None
        anchor_valid = (
            anchor is not None
            and isinstance(root.get("object_identity"), str)
            and bool(root.get("object_identity"))
            and isinstance(root.get("transaction_id"), str)
            and bool(root.get("transaction_id"))
            and root.get("object_identity") == anchor.root_identity
            and root.get("transaction_id") == anchor.transaction_id
        )
        ownership_valid = bool(
            anchor_valid
            and root.get("ownership") == "hia_owned"
            and all(
                node.get("ownership") == "hia_owned"
                and node.get("transaction_id") == anchor.transaction_id
                for node in raw_nodes.values()
            )
        )
        node_shape = [
            {
                "id": node["id"],
                "type": node["type"],
                "name_hint": node["name_hint"],
                "parent": node["parent"],
            }
            for node in graph["nodes"]
        ]
        parameter_shape = [
            {"id": node["id"], "parameters": node["parameters"]}
            for node in graph["nodes"]
        ]
        flag_shape = [
            {"id": node["id"], "flags": node["flags"]}
            for node in graph["nodes"]
        ]
        if expected_graph is None:
            expected_target_matches = True
            expected_nodes_match = True
            expected_parameters_match = True
            expected_connections_match = True
            expected_flags_match = True
            comparison_message = (
                " Structural validity only; canonical digest is authoritative."
            )
        else:
            expected_target_matches = (
                graph["context"] == expected_graph["context"]
                and graph["target"] == expected_graph["target"]
            )
            expected_nodes_match = node_shape == [
                {
                    "id": node["id"],
                    "type": node["type"],
                    "name_hint": node["name_hint"],
                    "parent": node["parent"],
                }
                for node in expected_graph["nodes"]
            ]
            expected_parameters_match = parameter_shape == [
                {"id": node["id"], "parameters": node["parameters"]}
                for node in expected_graph["nodes"]
            ]
            expected_connections_match = (
                graph["connections"] == expected_graph["connections"]
            )
            expected_flags_match = flag_shape == [
                {"id": node["id"], "flags": node["flags"]}
                for node in expected_graph["nodes"]
            ]
            comparison_message = " Compared with the validated declaration."

        node_ids = [node["id"] for node in graph["nodes"]]
        node_object_identities = [
            node.get("object_identity") for node in raw_nodes.values()
        ]
        all_object_identities = [root.get("object_identity"), *node_object_identities]
        node_structure_valid = (
            len(node_ids) == len(set(node_ids))
            and all(node["parent"] == "root" for node in graph["nodes"])
            and all(key == node.get("id") for key, node in raw_nodes.items())
            and all(
                isinstance(identity, str) and bool(identity)
                for identity in all_object_identities
            )
            and len(all_object_identities) == len(set(all_object_identities))
            and anchor is not None
            and root.get("object_identity") == anchor.root_identity
        )
        parameter_structure_valid = True  # catalog validation above is authoritative
        connection_structure_valid = True  # normalize/catalog validation above
        display_nodes = [node for node in graph["nodes"] if node["flags"]["display"]]
        render_nodes = [node for node in graph["nodes"] if node["flags"]["render"]]
        flags_structure_valid = (
            len(display_nodes) == 1
            and len(render_nodes) == 1
            and display_nodes[0]["id"] == render_nodes[0]["id"]
        )
        cook_valid = all(node["cook_state"] == "clean" for node in nodes)
        def checked_message(passed: bool, success: str, failure: str) -> str:
            return success if passed else failure

        target_valid = bool(target_structural and expected_target_matches)
        nodes_valid = bool(node_structure_valid and expected_nodes_match)
        parameters_valid = bool(
            parameter_structure_valid and expected_parameters_match
        )
        connections_valid = bool(
            connection_structure_valid and expected_connections_match
        )
        flags_valid = bool(flags_structure_valid and expected_flags_match)
        checks_by_name = {
            "session": (
                arguments["hip_session_id"] == self.hip_session_id,
                "Request and observed fake HIP session match.",
            ),
            "revision": (
                arguments["base_scene_revision"] == self.scene_revision,
                "Request and observed fake scene revision match.",
            ),
            "target": (
                target_valid,
                checked_message(
                    target_valid,
                    "Observed target path and declaration are consistent."
                    + comparison_message,
                    "Observed target path or declaration differs from its proof.",
                ),
            ),
            "ownership": (
                ownership_valid,
                checked_message(
                    ownership_valid,
                    "Observed root and nodes retain one HIA transaction ownership.",
                    "Observed root or node ownership differs from the transaction.",
                ),
            ),
            "nodes": (
                nodes_valid,
                checked_message(
                    nodes_valid,
                    "Observed node identities, types, names, and parents are valid."
                    + comparison_message,
                    "Observed node identities, types, names, or parents differ.",
                ),
            ),
            "parameters": (
                parameters_valid,
                checked_message(
                    parameters_valid,
                    "Observed typed parameters satisfy the fake catalog."
                    + comparison_message,
                    "Observed typed parameters differ from the validated declaration.",
                ),
            ),
            "connections": (
                connections_valid,
                checked_message(
                    connections_valid,
                    "Observed connections are bounded and structurally valid."
                    + comparison_message,
                    "Observed connections differ from the validated declaration.",
                ),
            ),
            "flags": (
                flags_valid,
                checked_message(
                    flags_valid,
                    "Observed display/render flags select one shared node."
                    + comparison_message,
                    "Observed display/render flags differ from the validated declaration.",
                ),
            ),
            "cook": (
                cook_valid,
                "Observed fake-only cook markers are clean."
                if cook_valid
                else "At least one observed fake-only cook marker is not clean.",
            ),
            "graph_digest": (
                digest_matches,
                "Canonical graph digest matches."
                if digest_matches
                else "Canonical graph digest differs.",
            ),
        }
        checks = [
            {"name": name, "passed": checks_by_name[name][0], "message": checks_by_name[name][1]}
            for name in _CHECK_NAMES
        ]
        valid = all(check["passed"] for check in checks)
        output = _common_output(arguments, ok=True, scene_revision=self.scene_revision)
        output["result"] = {
            "valid": valid,
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

    def _next_object_identity(self, kind: str) -> str:
        self._identity_sequence += 1
        return f"fake-{kind}-{self._identity_sequence:08d}"

    def _next_transaction_id(self) -> str:
        self._transaction_sequence += 1
        return f"fake-transaction-{self._transaction_sequence:08d}"

    def _reach_boundary(
        self,
        boundary: str,
        trace: list[str],
        *,
        execution_guard: Any | None = None,
    ) -> None:
        trace.append(boundary)
        if self._failure_boundary == boundary:
            self._failure_boundary = None
            raise _InjectedMutationFailure(boundary)
        self._raise_unexpected_if_configured(boundary)
        self._pause_if_configured(boundary)
        if execution_guard is not None:
            execution_guard.checkpoint(boundary)

    def _perform_guarded_mutation(
        self,
        boundary: str,
        trace: list[str],
        mutation: Callable[[], None],
        *,
        execution_guard: Any | None,
    ) -> None:
        """Atomically arbitrate control state before each fake scene mutation."""

        self._pause_if_configured(f"before_{boundary}")
        if execution_guard is None:
            mutation()
        else:
            execution_guard.mutate(boundary, mutation)
        self._reach_boundary(boundary, trace, execution_guard=execution_guard)

    def _raise_unexpected_if_configured(self, point: str) -> None:
        if self._unexpected_failure_point == point:
            self._unexpected_failure_point = None
            raise RuntimeError("deterministic fake unexpected transaction failure")
        if self._unexpected_base_exception_point == point:
            exception_type = self._unexpected_base_exception_type
            self._unexpected_base_exception_point = None
            self._unexpected_base_exception_type = None
            if exception_type is None:  # pragma: no cover - defensive invariant
                raise _InjectedBaseException(point)
            raise exception_type("deterministic fake transaction interruption")

    def _pause_if_configured(self, point: str) -> None:
        if self._pause_point != point:
            return
        entered = self._pause_entered
        release = self._pause_release
        self._pause_point = None
        self._pause_entered = None
        self._pause_release = None
        if entered is None or release is None:  # pragma: no cover - invariant
            raise RuntimeError("invalid fake pause configuration")
        entered.set()
        if not release.wait(5.0):
            raise RuntimeError("deterministic fake transaction pause timed out")

    def _commit_transaction(
        self,
        root: dict[str, Any],
        proof: _RollbackProof,
        output: dict[str, Any],
        success_audit: dict[str, Any],
        trace: list[str],
        *,
        execution_guard: Any | None,
    ) -> dict[str, Any]:
        """Publish every fake scene commit field inside one snapshot lock."""

        root["committed"] = True
        self._raise_unexpected_if_configured("commit")
        self.scene_revision = proof.revision_before + 1
        self._dirty = True
        self.hip_fingerprint = canonical_json_sha256(
            {
                "previous": proof.fingerprint_before,
                "content": self._scene_content_digest_unlocked(),
                "scene_revision": self.scene_revision,
            }
        )
        if proof.undo_record is None or proof.success_audit_record is not success_audit:
            raise RuntimeError("fake commit proof was not fully prepared")
        self._undo_stack.append(proof.undo_record)
        self.audit_records.append(success_audit)
        self._reach_boundary("commit", trace, execution_guard=execution_guard)
        if self._sentinel_digest_unlocked() != proof.sentinel_digest_before:
            raise _PostconditionFailure("sentinel changed during fake apply")
        self.last_transaction_trace = tuple(trace)
        return output

    def _attempt_confined_rollback(self, proof: _RollbackProof) -> bool:
        try:
            return self._rollback_owned_root(proof)
        except BaseException:
            return False

    def _rollback_owned_root(
        self,
        proof: _RollbackProof,
    ) -> bool:
        if self._rollback_exception:
            self._rollback_exception = False
            raise RuntimeError("deterministic fake rollback exception")
        if self._failure_rollback:
            self._failure_rollback = False
            return False
        self._apply_rollback_tamper(proof)
        root = self._roots.get(proof.root_key)
        if root is None:
            return (
                self._scene_content_digest_unlocked() == proof.content_digest_before
                and self._sentinel_digest_unlocked() == proof.sentinel_digest_before
                and self.scene_revision == proof.revision_before
                and self.hip_fingerprint == proof.fingerprint_before
                and self._dirty == proof.dirty_before
                and self._sequence_matches_exact(
                    self._undo_stack, proof.undo_records_before
                )
                and self._sequence_matches_exact(
                    self.audit_records, proof.audit_records_before
                )
            )
        expected_path = f"{proof.target_parent.rstrip('/')}/{proof.target_name}"
        if (
            root is None
            or root is not proof.root_object
            or proof.root_key != proof.root_path
            or proof.root_path != expected_path
            or root.get("path") != proof.root_path
            or root.get("object_identity") != proof.root_identity
            or root.get("transaction_id") != proof.transaction_id
            or root.get("ownership") != "hia_owned"
            or not isinstance(root.get("target"), Mapping)
            or root["target"].get("parent_path") != proof.target_parent
            or root["target"].get("name_hint") != proof.target_name
        ):
            return False
        if not self._sequence_matches_optional_exact(
            self._undo_stack, proof.undo_records_before, proof.undo_record
        ):
            return False
        if not self._sequence_matches_optional_exact(
            self.audit_records,
            proof.audit_records_before,
            proof.success_audit_record,
        ):
            return False

        del self._roots[proof.root_key]
        if proof.undo_record is not None:
            self._undo_stack = [
                record for record in self._undo_stack if record is not proof.undo_record
            ]
        if proof.success_audit_record is not None:
            self.audit_records = [
                record
                for record in self.audit_records
                if record is not proof.success_audit_record
            ]
        self.scene_revision = proof.revision_before
        self.hip_fingerprint = proof.fingerprint_before
        self._dirty = proof.dirty_before
        return (
            self._scene_content_digest_unlocked() == proof.content_digest_before
            and self._sentinel_digest_unlocked() == proof.sentinel_digest_before
            and self.scene_revision == proof.revision_before
            and self.hip_fingerprint == proof.fingerprint_before
            and self._dirty == proof.dirty_before
            and self._sequence_matches_exact(
                self._undo_stack, proof.undo_records_before
            )
            and self._sequence_matches_exact(
                self.audit_records, proof.audit_records_before
            )
        )

    @staticmethod
    def _sequence_matches_exact(
        current: list[Any], expected: tuple[Any, ...]
    ) -> bool:
        return len(current) == len(expected) and all(
            actual is prior for actual, prior in zip(current, expected)
        )

    @classmethod
    def _sequence_matches_optional_exact(
        cls,
        current: list[Any],
        baseline: tuple[Any, ...],
        optional: Any | None,
    ) -> bool:
        if cls._sequence_matches_exact(current, baseline):
            return True
        return (
            optional is not None
            and len(current) == len(baseline) + 1
            and all(actual is prior for actual, prior in zip(current, baseline))
            and current[-1] is optional
        )

    def _apply_rollback_tamper(self, proof: _RollbackProof) -> None:
        point = self._rollback_tamper
        self._rollback_tamper = None
        if point is None:
            return
        candidate = self._roots.get(proof.root_key)
        if candidate is None:
            return
        if point == "key":
            del self._roots[proof.root_key]
            self._roots["/obj/HIA_Graph_TamperedKey"] = candidate
        elif point == "path":
            candidate["path"] = "/obj/HIA_Graph_TamperedPath"
        elif point == "target_parent":
            candidate["target"]["parent_path"] = "/stage"
        elif point == "target_name":
            candidate["target"]["name_hint"] = "HIA_Graph_TamperedTarget"
        elif point == "identity":
            candidate["object_identity"] = "fake-root-tampered"
        elif point == "object":
            self._roots[proof.root_key] = copy.deepcopy(candidate)
        elif point == "transaction_id":
            candidate["transaction_id"] = "fake-transaction-tampered"
        elif point == "ownership":
            candidate["ownership"] = "user_owned"

    def _assert_postcondition(
        self,
        root: Mapping[str, Any],
        normalized: Mapping[str, Any],
        expected_digest: str,
    ) -> None:
        if root.get("ownership") != "hia_owned":
            raise _PostconditionFailure("ownership mismatch")
        observed = self._rebuild_graph_from_observed(root)
        _validate_fake_catalog(observed)
        if normalize_graph(observed) != normalize_graph(normalized):
            raise _PostconditionFailure("observed graph mismatch")
        if graph_digest(observed).casefold() != expected_digest.casefold():
            raise _PostconditionFailure("observed digest mismatch")

    @staticmethod
    def _tamper_first_observed_value(root: dict[str, Any]) -> None:
        for node_id in sorted(root["nodes"]):
            parameters = root["nodes"][node_id]["parameters"]
            for parameter_name in sorted(parameters):
                typed = parameters[parameter_name]
                values = typed.get("value") if isinstance(typed, dict) else None
                if isinstance(values, list) and values:
                    values[0] = float(values[0]) + 1.0
                    return
        first_node = root["nodes"][sorted(root["nodes"])[0]]
        first_node["flags"]["display"] = not first_node["flags"]["display"]

    def _rebuild_graph_from_observed(
        self,
        root: Mapping[str, Any],
        *,
        require_ownership: bool = True,
    ) -> dict[str, Any]:
        nodes = []
        for node_id in sorted(root["nodes"]):
            node = root["nodes"][node_id]
            if require_ownership and (
                node.get("ownership") != "hia_owned"
                or node.get("transaction_id") != root.get("transaction_id")
            ):
                raise ContractError(
                    "OWNERSHIP_MISMATCH",
                    "Observed fake node ownership differs from its root",
                )
            nodes.append(
                {
                    "id": node["id"],
                    "type": copy.deepcopy(node["type"]),
                    "name_hint": node["name_hint"],
                    "parent": node["parent"],
                    "parameters": [
                        {"name": name, "value": copy.deepcopy(value)}
                        for name, value in sorted(node["parameters"].items())
                    ],
                    "flags": copy.deepcopy(node["flags"]),
                }
            )
        target = root["target"]
        return normalize_graph(
            {
                "schema_version": "0.1.0",
                "context": copy.deepcopy(root["context"]),
                "target": {
                    "parent_path": target["parent_path"],
                    "name_hint": target["name_hint"],
                    "root_local_id": target["root_local_id"],
                    "root_type": copy.deepcopy(target["root_type"]),
                    "ownership": target["declaration_ownership"],
                },
                "nodes": nodes,
                "connections": copy.deepcopy(root["connections"]),
                "layout": copy.deepcopy(root["layout"]),
            }
        )

    def _observed_digest(self, root: Mapping[str, Any]) -> str:
        try:
            return graph_digest(self._rebuild_graph_from_observed(root))
        except (ContractError, KeyError, TypeError, ValueError):
            return "0" * 64

    @staticmethod
    def _created_node_records(root: Mapping[str, Any]) -> list[dict[str, Any]]:
        root_path = root["path"]
        created = [
            {
                "request_local_id": "root",
                "path": root_path,
                "context": "Object",
                "resolved_type": "geo",
            }
        ]
        created.extend(
            {
                "request_local_id": node["id"],
                "path": f"{root_path}/{node['name_hint']}",
                "context": node["type"]["context"],
                "resolved_type": node["type"]["name"],
            }
            for node in sorted(root["nodes"].values(), key=lambda item: item["id"])
        )
        return created

    @staticmethod
    def _verification_node_records(
        root: Mapping[str, Any], graph: Mapping[str, Any]
    ) -> list[dict[str, Any]]:
        root_path = root["path"]
        observed_by_id = {
            node["id"]: node for node in root["nodes"].values()
        }
        if len(observed_by_id) != len(root["nodes"]):
            raise ValueError("observed fake node ids are not unique")
        allowed_cook_states = {"not_cooked", "clean", "warning", "error"}
        for observed in observed_by_id.values():
            if observed.get("cook_state") not in allowed_cook_states:
                raise ValueError("observed fake cook state is outside the frozen schema")
        return [
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
                "cook_state": observed_by_id[node["id"]]["cook_state"],
            }
            for node in graph["nodes"]
        ]

    def _build_audit_record(
        self,
        arguments: Mapping[str, Any],
        *,
        graph_digest_value: str | None,
        result_code: str,
        rollback_status: str,
        trace: list[str],
        revision_before: int,
        revision_after: int,
    ) -> dict[str, Any]:
        correlation_digest = canonical_json_sha256(
            {
                "request_id": arguments.get("request_id"),
                "thread_id": arguments.get("thread_id"),
                "turn_id": arguments.get("turn_id"),
                "idempotency_key": arguments.get("idempotency_key"),
            }
        )
        record = {
            "profile": "gate_b3_fake_only",
            "tool_name": "houdini_graph_apply",
            "correlation_digest": correlation_digest,
            "graph_digest": graph_digest_value,
            "result_code": result_code,
            "rollback_status": rollback_status,
            "mutation_boundaries": list(trace),
            "revision_before": revision_before,
            "revision_after": revision_after,
            "simulated": True,
        }
        json.dumps(record, allow_nan=False, sort_keys=True)
        return record

    def _safe_append_failure_audit(
        self,
        arguments: Mapping[str, Any],
        *,
        graph_digest_value: str | None,
        result_code: str,
        rollback_status: str,
        trace: list[str],
        revision_before: int,
    ) -> None:
        try:
            record = self._build_audit_record(
                arguments,
                graph_digest_value=graph_digest_value,
                result_code=result_code,
                rollback_status=rollback_status,
                trace=trace,
                revision_before=revision_before,
                revision_after=self.scene_revision,
            )
            self.audit_records.append(record)
        except BaseException:
            # Audit is best-effort on an already-sanitized failure path.  It
            # must never leak or replace the authoritative transaction result.
            return

    def simulate_undo(self) -> dict[str, Any]:
        """Test-only one-step Undo; this is not evidence about ``hou.undos``."""

        if not self._write_lock.acquire(blocking=False):
            raise ContractError(
                "WRITE_IN_PROGRESS",
                "Another fake scene mutation currently owns the writer lock",
            )
        try:
            if self._writes_indeterminate:
                raise ContractError(
                    "SCENE_STATE_INDETERMINATE",
                    "Fake scene state is indeterminate and cannot be undone safely",
                )
            if not self._undo_stack:
                raise ContractError("GRAPH_NOT_FOUND", "No simulated Undo is available")
            record = self._undo_stack.pop()
            root = self._roots.get(record.root_path)
            if (
                root is None
                or root.get("object_identity") != record.root_identity
                or root.get("transaction_id") != record.transaction_id
                or root.get("ownership") != "hia_owned"
            ):
                self._writes_indeterminate = True
                raise ContractError(
                    "SCENE_STATE_INDETERMINATE",
                    "Simulated Undo could not prove the owned root identity",
                )
            self._roots.pop(record.root_path)
            undo_entered = self._undo_pause_entered
            undo_release = self._undo_pause_release
            self._undo_pause_entered = None
            self._undo_pause_release = None
            if undo_entered is not None and undo_release is not None:
                undo_entered.set()
                if not undo_release.wait(5.0):
                    self._writes_indeterminate = True
                    raise ContractError(
                        "SCENE_STATE_INDETERMINATE",
                        "Simulated Undo pause timed out after mutation",
                    )
            self._dirty = record.dirty_before
            self.hip_fingerprint = record.fingerprint_before
            self.scene_revision += 1
            if (
                self.scene_content_digest != record.content_digest_before
                or self.sentinel_digest != record.sentinel_digest_before
            ):
                self._writes_indeterminate = True
                raise ContractError(
                    "SCENE_STATE_INDETERMINATE",
                    "Simulated Undo did not restore the prior content state",
                )
            audit = {
                "profile": "gate_b3_fake_only",
                "event": "simulated_undo",
                "result_code": "OK",
                "revision_after": self.scene_revision,
                "simulated": True,
            }
            json.dumps(audit, allow_nan=False, sort_keys=True)
            self.audit_records.append(audit)
            return copy.deepcopy(audit)
        finally:
            self._write_lock.release()
