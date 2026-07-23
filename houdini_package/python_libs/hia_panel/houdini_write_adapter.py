"""Dormant, dependency-injected HOM graph writer for P2-V Gate B4A.

This module has no production wiring.  It never imports ``hou`` or Qt and it
does not expose an entry point.  The writer consumes an immutable binding made
from one canonical :class:`SceneRequest` and its executor-only
:class:`Claim`, then intersects the frozen graph contract with an injected,
attested capability catalog.  Node semantics live in that catalog; this
module contains no per-node handlers or permanent node-type allowlist.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from hia_bridge.scene_queue import Claim, SceneRequest
from hia_core.houdini_contract import (
    ContractError,
    SchemaRegistry,
    approval_binding_digest,
    canonical_json_bytes,
    canonical_json_sha256,
    graph_digest,
    graph_side_effect_summary,
    normalize_graph,
    validate_graph_relations,
)

from .houdini_read_adapter import _same_houdini_node


_WRITE_TOOL = "houdini_graph_apply"
_UNDO_LABEL = "HIA: Apply Graph"
_OWNERSHIP_KEY = "hia_ownership"
_TRANSACTION_KEY = "hia_transaction_id"
_GRAPH_DIGEST_KEY = "hia_graph_digest"
_OWNERSHIP_VALUE = "hia_owned"
_SILENT_READBACK_OPERATIONS = frozenset(
    f"set_user_data:{key}"
    for key in (_OWNERSHIP_KEY, _TRANSACTION_KEY, _GRAPH_DIGEST_KEY)
)
_SAFE_RISK_LEVEL = "ordinary_graph_write"
_MAX_OBJ_CHILDREN = 4096
_PHASES = (
    "create_root",
    "create_nodes",
    "set_parameters",
    "connect_nodes",
    "set_flags_layout",
    "postcondition",
    "commit",
)


class HoudiniWriteAdapterError(RuntimeError):
    """Structured adapter failure used when no valid tool output can exist."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": []}


class WriteControlAbort(RuntimeError):
    """Internal control-plane abort; never serialized as a tool result."""

    def __init__(self, reason: str) -> None:
        if reason not in {"cancel", "shutdown", "deadline"}:
            raise ValueError("unsupported write control reason")
        super().__init__("The scene transaction was stopped by its control guard")
        self.reason = reason
        self.rollback_proven = False


@dataclass(frozen=True, slots=True)
class _ApprovedWriteBinding:
    """Content-immutable pairing of one approved request and queue claim.

    The factory is intentionally private.  It does not decide approval; it only
    seals an already canonical SceneQueue request/claim pair supplied by an
    internal controller.  Mutable request dictionaries and the raw executor
    token are not retained.
    """

    arguments_json: bytes
    approval_payload_json: bytes
    request_digest: str
    approval_binding_digest: str
    launch_id: str
    generation: int
    attestation_digest: str
    absolute_deadline: float
    catalog_digest: str
    obj_fingerprint: str
    # Python cannot turn an in-process object into a security boundary, but an
    # identity-only capability prevents request data (or a freshly constructed
    # Claim with copied fields) from authorizing itself.  The dormant internal
    # controller that owns the exact SceneQueue claim must also own this object,
    # and the adapter accepts bindings only from that same controller instance.
    claim_authority: object = field(repr=False, compare=False)
    claim_execution_token: object = field(repr=False, compare=False)

    @classmethod
    def from_scene_queue(
        cls,
        request: SceneRequest,
        claim: Claim,
        *,
        attestation: Any,
        catalog: Any,
        obj_fingerprint: str,
        claim_authority: object,
    ) -> "_ApprovedWriteBinding":
        if not isinstance(request, SceneRequest) or not isinstance(claim, Claim):
            raise HoudiniWriteAdapterError(
                "APPROVAL_REQUIRED", "A canonical internal SceneQueue claim is required"
            )
        issue_exact_claim = getattr(claim_authority, "issue_exact_claim", None)
        consume_binding = getattr(claim_authority, "consume_binding", None)
        if not callable(issue_exact_claim) or not callable(consume_binding):
            raise HoudiniWriteAdapterError(
                "APPROVAL_REQUIRED", "An internal claim authority is required"
            )
        if request.tool_name != _WRITE_TOOL or claim.tool_name != _WRITE_TOOL:
            raise HoudiniWriteAdapterError(
                "APPROVAL_MISMATCH", "The internal claim is not a graph apply claim"
            )
        if request.approval_payload is None or request.approval_binding_digest is None:
            raise HoudiniWriteAdapterError(
                "APPROVAL_REQUIRED", "The graph apply request has no approval binding"
            )
        request_id = request.arguments.get("request_id")
        if (
            claim.request_id != request_id
            or claim.arguments != request.arguments
            or claim.request_digest != request.request_digest
            or claim.attestation_digest != request.attestation_digest
            or claim.absolute_deadline != request.absolute_deadline
            or not isinstance(claim.claim_token, str)
            or not claim.claim_token
            or claim.cancel_requested is not False
        ):
            raise HoudiniWriteAdapterError(
                "APPROVAL_MISMATCH", "The executor claim does not match its approved request"
            )

        rebuilt = SceneRequest.build(
            request.tool_name,
            request.arguments,
            request.absolute_deadline,
            request.launch_id,
            request.generation,
            request.attestation_digest,
        )
        if rebuilt != request:
            raise HoudiniWriteAdapterError(
                "APPROVAL_MISMATCH", "The approved request is not canonical"
            )
        payload_digest = canonical_json_sha256(request.approval_payload)
        if payload_digest.casefold() != request.approval_binding_digest.casefold():
            raise HoudiniWriteAdapterError(
                "APPROVAL_MISMATCH", "The sealed approval payload digest is invalid"
            )

        try:
            attestation_digest = str(attestation.digest)
            attested_catalog_digest = str(attestation.catalog_digest)
        except Exception as exc:
            raise HoudiniWriteAdapterError(
                "CAPABILITY_MISMATCH", "The injected capability attestation is invalid"
            ) from exc
        catalog_digest = canonical_json_sha256(catalog)
        if (
            attestation_digest.casefold() != request.attestation_digest.casefold()
            or attested_catalog_digest.casefold() != catalog_digest.casefold()
        ):
            raise HoudiniWriteAdapterError(
                "CAPABILITY_MISMATCH", "The approved claim is not bound to this catalog"
            )
        try:
            execution_token = issue_exact_claim(request, claim)
        except Exception as exc:
            raise HoudiniWriteAdapterError(
                "APPROVAL_REQUIRED",
                "The exact internal SceneQueue claim could not be sealed",
            ) from exc
        if execution_token is None or isinstance(execution_token, bool):
            raise HoudiniWriteAdapterError(
                "APPROVAL_REQUIRED",
                "The exact internal SceneQueue claim is absent or already sealed",
            )
        _require_sha256(obj_fingerprint, "obj_fingerprint")
        return cls(
            arguments_json=canonical_json_bytes(request.arguments),
            approval_payload_json=canonical_json_bytes(request.approval_payload),
            request_digest=request.request_digest,
            approval_binding_digest=request.approval_binding_digest,
            launch_id=request.launch_id,
            generation=request.generation,
            attestation_digest=request.attestation_digest,
            absolute_deadline=request.absolute_deadline,
            catalog_digest=catalog_digest,
            obj_fingerprint=obj_fingerprint,
            claim_authority=claim_authority,
            claim_execution_token=execution_token,
        )


@dataclass(frozen=True, slots=True)
class _CatalogParameter:
    name: str
    value_type: str
    tuple_size: int
    items_type: str | None
    writable: bool
    allows_expression: bool


@dataclass(frozen=True, slots=True)
class _CatalogNodeType:
    context: str
    canonical_name: str
    resolved_name: str
    category: str
    input_count: int
    output_count: int
    available: bool
    creatable: bool
    risk_level: str
    parameters: tuple[_CatalogParameter, ...]


@dataclass(slots=True)
class _CreatedChildProof:
    node: Any
    session_id: Any
    path: str
    name: str
    resolved_type: str


@dataclass(slots=True)
class _RollbackProof:
    root: Any
    root_session_id: Any
    parent: Any
    root_path: str
    root_name: str
    transaction_id: str
    graph_digest: str
    obj_fingerprint_before: str
    parent_children_before: tuple[Any, ...]
    created_children: list[_CreatedChildProof] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class _RollbackOutcome:
    proven: bool
    critical: BaseException | None = None


class HoudiniWriteAdapter:
    """Apply one prevalidated graph through a catalog-driven HOM path.

    Construction is dependency-only and performs no HOM access.  No production
    module imports or constructs this class in Gate B4A.
    """

    def __init__(
        self,
        hou_module: Any,
        read_adapter: Any,
        *,
        capability_attestation: Any,
        capability_catalog: Any,
        main_thread_id: int,
        clock: Callable[[], float],
        schema_registry: SchemaRegistry | None = None,
        control_guard: Any | None = None,
        claim_authority: object,
        strict_event_evidence: bool = False,
    ) -> None:
        self._hou = hou_module
        self._read_adapter = read_adapter
        self._main_thread_id = int(main_thread_id)
        self._clock = clock
        self._registry = schema_registry or SchemaRegistry()
        if (
            control_guard is None
            or not callable(getattr(control_guard, "checkpoint", None))
            or not callable(getattr(control_guard, "mutate", None))
            or not callable(getattr(control_guard, "finalize", None))
            or not callable(getattr(control_guard, "contain", None))
        ):
            raise HoudiniWriteAdapterError(
                "HOUDINI_UNAVAILABLE",
                "An atomic internal write-control guard is required",
            )
        self._control_guard = control_guard
        if (
            not callable(getattr(claim_authority, "issue_exact_claim", None))
            or not callable(getattr(claim_authority, "consume_binding", None))
        ):
            raise HoudiniWriteAdapterError(
                "APPROVAL_REQUIRED", "An internal claim authority is required"
            )
        self._claim_authority = claim_authority
        self._strict_event_evidence = bool(strict_event_evidence)
        if self._strict_event_evidence and (
            getattr(read_adapter, "strict_event_evidence", False) is not True
            or not callable(
                getattr(read_adapter, "install_owned_node_observer", None)
            )
            or not callable(getattr(read_adapter, "record_owned_noop", None))
        ):
            raise HoudiniWriteAdapterError(
                "HOUDINI_UNAVAILABLE",
                "Strict event evidence requires a strict live read adapter",
            )
        self._writer_lock = threading.Lock()
        self._frozen = False
        self._phase_hook: Callable[[str], None] | None = None
        self._active_deadline: float | None = None

        catalog_json = canonical_json_bytes(capability_catalog)
        self._catalog_json = bytes(catalog_json)
        self._catalog_digest = hashlib.sha256(catalog_json).hexdigest()
        self._catalog = self._parse_catalog(json.loads(catalog_json.decode("utf-8")))
        self._attestation = self._freeze_attestation(capability_attestation)
        if self._attestation["catalog_digest"].casefold() != self._catalog_digest:
            raise HoudiniWriteAdapterError(
                "CAPABILITY_MISMATCH", "Capability catalog digest does not match attestation"
            )
        if self._attestation["schema_digest"].casefold() != self._registry.manifest_digest:
            raise HoudiniWriteAdapterError(
                "CAPABILITY_MISMATCH", "Capability schema digest does not match frozen contract"
            )

    @property
    def frozen(self) -> bool:
        return self._frozen

    def apply_prevalidated(
        self, binding: _ApprovedWriteBinding
    ) -> dict[str, Any]:
        """Apply exactly one internally claimed graph or fail closed."""

        if not isinstance(binding, _ApprovedWriteBinding):
            raise HoudiniWriteAdapterError(
                "APPROVAL_REQUIRED", "An immutable internal approval binding is required"
            )
        if binding.claim_authority is not self._claim_authority:
            raise HoudiniWriteAdapterError(
                "APPROVAL_REQUIRED",
                "The approval binding was not sealed by this internal controller",
            )
        arguments = self._decode_binding_arguments(binding)
        if threading.get_ident() != self._main_thread_id:
            return self._error(arguments, "MAIN_THREAD_REQUIRED", "Scene writes require the Houdini UI main thread")
        if self._frozen:
            return self._error(arguments, "SCENE_STATE_INDETERMINATE", "Scene writes are frozen pending reconciliation")
        try:
            binding_consumed = self._claim_authority.consume_binding(
                binding.claim_execution_token
            )
        except Exception:
            binding_consumed = False
        if binding_consumed is not True:
            return self._error(
                arguments,
                "APPROVAL_REQUIRED",
                "The internal approval binding is absent or already consumed",
            )
        try:
            now = self._read_clock()
        except HoudiniWriteAdapterError as exc:
            return self._error(arguments, exc.code, exc.message)
        if now >= binding.absolute_deadline:
            return self._error(arguments, "DEADLINE_EXCEEDED", "The graph apply deadline has expired")

        try:
            normalized, digest, approval_digest = self._validate_binding(binding, arguments)
        except ContractError as exc:
            return self._error(arguments, _admitted_contract_code(exc.code), exc.message)
        except HoudiniWriteAdapterError as exc:
            return self._error(arguments, exc.code, exc.message)

        try:
            report = self._read_adapter.refresh()
        except Exception:
            return self._error(arguments, "HOUDINI_UNAVAILABLE", "Live Houdini capability refresh failed")
        mismatch = self._capability_mismatch(arguments, binding, report)
        if mismatch is not None:
            return mismatch
        try:
            live_catalog = self._index_live_catalog(report.get("catalog"))
            execution_catalog = self._resolve_graph_catalog(normalized, live_catalog)
            obj = self._hou.node("/obj")
            if obj is None:
                return self._error(arguments, "HOUDINI_UNAVAILABLE", "The /obj context is unavailable")
            current_obj_fingerprint = self._obj_fingerprint(obj)
            if current_obj_fingerprint.casefold() != binding.obj_fingerprint.casefold():
                return self._error(arguments, "CAPABILITY_MISMATCH", "The /obj fingerprint changed before apply")
            root_name = normalized["target"]["name_hint"]
            if self._child_named(obj, root_name) is not None:
                return self._error(arguments, "NAME_CONFLICT", "The requested HIA graph root already exists")
        except HoudiniWriteAdapterError as exc:
            return self._error(arguments, exc.code, exc.message)
        except Exception:
            return self._error(arguments, "HOUDINI_UNAVAILABLE", "The live Houdini capability could not be verified")

        if not self._writer_lock.acquire(blocking=False):
            return self._error(arguments, "WRITE_IN_PROGRESS", "Another graph write is already active")
        try:
            # Recheck all state that can drift between catalog validation and
            # acquiring the local single-writer latch.
            try:
                report = self._read_adapter.refresh()
            except Exception:
                return self._error(arguments, "HOUDINI_UNAVAILABLE", "Live Houdini capability refresh failed")
            mismatch = self._capability_mismatch(arguments, binding, report)
            if mismatch is not None:
                return mismatch
            obj = self._hou.node("/obj")
            if obj is None or self._obj_fingerprint(obj).casefold() != binding.obj_fingerprint.casefold():
                return self._error(arguments, "CAPABILITY_MISMATCH", "The /obj state changed before mutation")
            if self._child_named(obj, normalized["target"]["name_hint"]) is not None:
                return self._error(arguments, "NAME_CONFLICT", "The requested HIA graph root already exists")
            self._active_deadline = binding.absolute_deadline
            try:
                self._checkpoint("preflight")
            except WriteControlAbort:
                raise
            except HoudiniWriteAdapterError as exc:
                return self._error(arguments, exc.code, exc.message)
            except Exception:
                return self._error(
                    arguments,
                    "HOUDINI_UNAVAILABLE",
                    "The transaction preflight clock is unavailable",
                )
            transaction_id = canonical_json_sha256(
                {
                    "request_id": arguments["request_id"],
                    "request_digest": binding.request_digest,
                    "graph_digest": digest,
                }
            )
            try:
                read_token = self._read_adapter.begin_owned_write(
                    transaction_id,
                    expected_hip_session_id=arguments["hip_session_id"],
                    expected_scene_revision=arguments["base_scene_revision"],
                    expected_hip_fingerprint=arguments["expected_hip_fingerprint"],
                )
            except Exception as exc:
                code = getattr(exc, "code", "HOUDINI_UNAVAILABLE")
                if code not in {
                    "HIP_SESSION_MISMATCH",
                    "SCENE_CONFLICT",
                    "CAPABILITY_MISMATCH",
                    "HOUDINI_UNAVAILABLE",
                    "WRITE_IN_PROGRESS",
                }:
                    code = "HOUDINI_UNAVAILABLE"
                return self._error(arguments, code, "Owned transaction observation could not start")
            return self._apply_transaction(
                arguments,
                normalized,
                digest,
                approval_digest,
                binding,
                execution_catalog,
                obj,
                transaction_id,
                read_token,
            )
        finally:
            self._active_deadline = None
            self._writer_lock.release()

    def _apply_transaction(
        self,
        arguments: Mapping[str, Any],
        graph: Mapping[str, Any],
        digest: str,
        approval_digest: str,
        binding: _ApprovedWriteBinding,
        catalog: Mapping[tuple[str, str], _CatalogNodeType],
        obj: Any,
        transaction_id: str,
        read_token: Any,
    ) -> dict[str, Any]:
        root: Any | None = None
        proof: _RollbackProof | None = None
        nodes: dict[str, Any] = {}
        node_session_ids: dict[str, Any] = {}
        mutation_started = False
        rollback_attempted = False
        rollback_proven = False
        pending_exception: BaseException | None = None
        failure_code = "INTERNAL_ERROR"
        failure_message = "The graph transaction failed safely"
        candidate_result: dict[str, Any] | None = None
        parent_children_before = tuple(obj.children())
        rollback_critical: BaseException | None = None
        untrusted_creation_result = False

        def attempt_confined_rollback() -> bool:
            nonlocal rollback_critical
            if proof is None:
                return False
            outcome = self._attempt_rollback(proof, read_token)
            if rollback_critical is None and outcome.critical is not None:
                rollback_critical = outcome.critical
            return outcome.proven

        # Manage the context explicitly so failures from group(), __enter__(),
        # and __exit__() are all inside the same containment decision.  No
        # second Undo group is opened for rollback.
        undo_context: Any | None = None
        undo_enter_attempted = False

        def open_undo_boundary() -> None:
            nonlocal undo_context, undo_enter_attempted
            self._check_deadline()
            undo_context = self._hou.undos.group(_UNDO_LABEL)
            undo_enter_attempted = True
            undo_context.__enter__()

        try:
            self._mutate_once("create_root", open_undo_boundary)
        except BaseException as enter_exception:
            exit_exception: BaseException | None = None
            if undo_enter_attempted and undo_context is not None:
                try:
                    self._contain_once(
                        lambda: undo_context.__exit__(
                            type(enter_exception),
                            enter_exception,
                            enter_exception.__traceback__,
                        )
                    )
                except BaseException as exc:
                    exit_exception = exc
            finish_exception = self._finish_owned_write_safely(
                read_token,
                arguments,
                outcome=(
                    "indeterminate"
                    if undo_enter_attempted or exit_exception is not None
                    else "rolled_back"
                ),
            )
            if (
                undo_enter_attempted
                or exit_exception is not None
                or finish_exception is not None
            ):
                self._frozen = True
            critical = next(
                (
                    item
                    for item in (enter_exception, exit_exception, finish_exception)
                    if item is not None and not isinstance(item, Exception)
                ),
                None,
            )
            if critical is not None:
                raise critical
            if (
                undo_enter_attempted
                or exit_exception is not None
                or finish_exception is not None
            ):
                return self._indeterminate_error(arguments)
            if isinstance(enter_exception, WriteControlAbort):
                enter_exception.rollback_proven = True
                raise enter_exception
            return self._error(
                arguments,
                "INTERNAL_ERROR",
                "The Houdini Undo transaction could not start",
                scene_revision=arguments["base_scene_revision"],
            )

        try:
            mutation_started = True
            root_spec = catalog[(
                graph["target"]["root_type"]["context"],
                graph["target"]["root_type"]["name"],
            )]
            root = self._guarded_mutation(
                "create_root",
                read_token,
                obj,
                lambda: obj.createNode(
                    root_spec.resolved_name,
                    node_name=graph["target"]["name_hint"],
                    run_init_scripts=False,
                    exact_type_name=True,
                ),
                operation="create_root:root",
                event_source_rules={
                    "ChildCreated": (obj,),
                    "ChildSwitched": (obj,),
                },
                required_event_types=("ChildCreated",),
                created_subject=True,
            )
            approved_root_name = graph["target"]["name_hint"]
            approved_root_path = f"/obj/{approved_root_name}"
            try:
                root_session_id = self._prove_exact_new_child(
                    obj,
                    parent_children_before,
                    root,
                    expected_name=approved_root_name,
                    expected_path=approved_root_path,
                    expected_resolved_type=root_spec.resolved_name,
                )
            except BaseException:
                untrusted_creation_result = True
                raise
            proof = _RollbackProof(
                root=root,
                root_session_id=root_session_id,
                parent=obj,
                root_path=root.path(),
                root_name=root.name(),
                transaction_id=transaction_id,
                graph_digest=digest,
                obj_fingerprint_before=binding.obj_fingerprint,
                parent_children_before=parent_children_before,
            )
            self._install_strict_observer(read_token, root)
            self._set_user_data_with_evidence(
                "create_root",
                read_token,
                root,
                key=_OWNERSHIP_KEY,
                value=_OWNERSHIP_VALUE,
            )
            self._set_user_data_with_evidence(
                "create_root",
                read_token,
                root,
                key=_TRANSACTION_KEY,
                value=transaction_id,
            )
            self._set_user_data_with_evidence(
                "create_root",
                read_token,
                root,
                key=_GRAPH_DIGEST_KEY,
                value=digest,
            )
            self._after_phase("create_root")

            for node in graph["nodes"]:
                specification = catalog[(node["type"]["context"], node["type"]["name"])]
                children_before = tuple(root.children())
                live_node = self._guarded_mutation(
                    "create_nodes",
                    read_token,
                    root,
                    lambda specification=specification, node=node: root.createNode(
                        specification.resolved_name,
                        node_name=node["name_hint"],
                        run_init_scripts=False,
                        exact_type_name=True,
                    ),
                    operation=f"create_node:{node['id']}",
                    event_source_rules={
                        "ChildCreated": (root,),
                        "ChildSwitched": (root,),
                    },
                    required_event_types=("ChildCreated",),
                    created_subject=True,
                )
                try:
                    node_session_id = self._prove_exact_new_child(
                        root,
                        children_before,
                        live_node,
                        expected_name=node["name_hint"],
                        expected_path=f"{approved_root_path}/{node['name_hint']}",
                        expected_resolved_type=specification.resolved_name,
                    )
                except BaseException:
                    untrusted_creation_result = True
                    raise
                nodes[node["id"]] = live_node
                node_session_ids[node["id"]] = node_session_id
                proof.created_children.append(
                    _CreatedChildProof(
                        node=live_node,
                        session_id=node_session_id,
                        path=f"{approved_root_path}/{node['name_hint']}",
                        name=node["name_hint"],
                        resolved_type=specification.resolved_name,
                    )
                )
                self._install_strict_observer(read_token, live_node)
            self._after_phase("create_nodes")

            for node in graph["nodes"]:
                specification = catalog[(node["type"]["context"], node["type"]["name"])]
                parameter_catalog = {item.name: item for item in specification.parameters}
                for assignment in node["parameters"]:
                    self._guarded_mutation(
                        "set_parameters",
                        read_token,
                        nodes[node["id"]],
                        lambda node=node, assignment=assignment: self._set_typed_parameter(
                            nodes[node["id"]],
                            assignment,
                            parameter_catalog[assignment["name"]],
                        ),
                        operation=(
                            f"set_parameter:{node['id']}:{assignment['name']}"
                        ),
                        event_source_rules={
                            "ParmTupleChanged": (nodes[node["id"]],),
                            "ParmTupleAnimated": (nodes[node["id"]],),
                            "ParmTupleChannelChanged": (nodes[node["id"]],),
                        },
                    )
            self._after_phase("set_parameters")

            # Canonical graph order is source-first for stable hashing.  HOM
            # mutation order is different: unordered inputs such as Merge
            # cannot contain gaps, so populate each destination from its
            # lowest input index upward without changing the approved graph.
            connections_to_apply = sorted(
                graph["connections"],
                key=lambda item: (
                    item["destination"]["node"],
                    item["destination"]["input"],
                    item["source"]["node"],
                    item["source"]["output"],
                ),
            )
            for connection in connections_to_apply:
                source = connection["source"]
                destination = connection["destination"]
                self._guarded_mutation(
                    "connect_nodes",
                    read_token,
                    nodes[destination["node"]],
                    lambda source=source, destination=destination: nodes[
                        destination["node"]
                    ].setInput(
                        destination["input"],
                        nodes[source["node"]],
                        source["output"],
                    ),
                    operation=(
                        f"connect:{destination['node']}:{destination['input']}"
                    ),
                    event_source_rules={
                        "InputRewired": (nodes[destination["node"]],),
                    },
                )
            self._after_phase("connect_nodes")

            owned_flag_nodes = tuple(nodes.values())
            for node in graph["nodes"]:
                live_node = nodes[node["id"]]
                self._set_flag_with_evidence(
                    read_token,
                    root,
                    live_node,
                    owned_nodes=owned_flag_nodes,
                    node_id=node["id"],
                    flag_name="display",
                    desired=node["flags"]["display"],
                )
                self._set_flag_with_evidence(
                    read_token,
                    root,
                    live_node,
                    owned_nodes=owned_flag_nodes,
                    node_id=node["id"],
                    flag_name="render",
                    desired=node["flags"]["render"],
                )
            # The frozen layout declaration is validation-only in B4A.  No HOM
            # layout mutation or request-derived observed layout is fabricated.
            self._after_phase("set_flags_layout")

            self._checkpoint("postcondition")
            self._verify_observed(
                graph,
                digest,
                root,
                proof,
                nodes,
                node_session_ids,
                catalog,
            )
            self._after_phase("postcondition")

            expected_revision = arguments["base_scene_revision"] + 1
            candidate_result = self._success_output(
                arguments,
                graph,
                digest,
                approval_digest,
                root,
                nodes,
                expected_revision,
            )
            candidate_result = self._registry.validate_output(
                _WRITE_TOOL, arguments, candidate_result
            )
            # Failure injection happens before the single commit authority
            # point.  Once the guard below returns, later cancellation belongs
            # to a later transaction and cannot race this commit.
            self._after_phase("commit")
        except BaseException as exc:
            pending_exception = exc
            if isinstance(exc, _ObservedStateMismatch):
                failure_code = "POSTCONDITION_FAILED"
                failure_message = "Observed Houdini state does not match the approved graph"
            if mutation_started:
                rollback_attempted = True
                rollback_proven = attempt_confined_rollback()
                if untrusted_creation_result:
                    rollback_proven = False
            else:
                rollback_proven = True

        if pending_exception is None and candidate_result is None:
            # A broken dependency must not bypass rollback or leave the
            # explicitly-entered Undo context open.
            self._frozen = True
            pending_exception = RuntimeError(
                "The frozen output validator returned no result"
            )
            failure_code = "HOUDINI_UNAVAILABLE"
            failure_message = "The frozen output validator returned no result"
            rollback_attempted = True
            rollback_proven = attempt_confined_rollback()

        if pending_exception is None:

            undo_exit_attempted = False
            read_finish_attempted = False
            read_refresh_attempted = False
            commit_boundary_calls = 0
            committed_publication: dict[str, Any] | None = None
            committed_report: dict[str, Any] | None = None

            def commit_boundary() -> dict[str, Any]:
                nonlocal commit_boundary_calls, committed_publication
                nonlocal committed_report
                nonlocal undo_exit_attempted, read_finish_attempted
                nonlocal read_refresh_attempted
                self._require_main_thread()
                commit_boundary_calls += 1
                if commit_boundary_calls != 1:
                    raise RuntimeError("commit boundary invoked more than once")
                self._check_deadline()
                undo_exit_attempted = True
                undo_context.__exit__(None, None, None)
                # Undo exit is executable dependency code.  Re-prove the
                # exact approved graph after it returns and before publishing
                # any committed revision.
                self._verify_observed(
                    graph,
                    digest,
                    root,
                    proof,
                    nodes,
                    node_session_ids,
                    catalog,
                )
                read_finish_attempted = True
                committed_publication = self._read_adapter.finish_owned_write(
                    read_token, outcome="committed"
                )
                # The committed nodes did not exist when the B2 observer set
                # was last installed.  Refresh before releasing final authority
                # so the new owned root/children cannot have an unobserved gap.
                read_refresh_attempted = True
                committed_report = self._read_adapter.refresh()
                return committed_report

            try:
                final_report = self._control_guard.finalize(commit_boundary)
            except BaseException as exc:
                if undo_exit_attempted:
                    # Once Undo exit was attempted, the scene/Undo publication
                    # boundary is unknowable.  Do not mutate after that point.
                    self._frozen = True
                    finish_exception = None
                    refresh_exception = None
                    if not read_finish_attempted:
                        finish_exception = self._finish_owned_write_safely(
                            read_token, arguments, outcome="indeterminate"
                        )
                    elif not read_refresh_attempted:
                        refresh_exception = self._refresh_indeterminate_scope_safely()
                    critical = next(
                        (
                            item
                            for item in (exc, finish_exception, refresh_exception)
                            if item is not None and not isinstance(item, Exception)
                        ),
                        None,
                    )
                    if critical is not None:
                        raise critical
                    return self._indeterminate_error(arguments)

                # The authority guard rejected before the commit boundary.
                # The group is still open, so exact rollback remains possible.
                pending_exception = exc
                if mutation_started:
                    rollback_attempted = True
                    rollback_proven = attempt_confined_rollback()
                    if untrusted_creation_result:
                        rollback_proven = False
                else:
                    rollback_proven = True
            else:
                if not undo_exit_attempted:
                    # A finalizer that never invokes its callback has no commit
                    # authority.  The Undo group is still open, so contain the
                    # exact root, close the group, freeze this untrusted guard,
                    # and publish a rolled-back outcome below.
                    self._frozen = True
                    failure_code = "HOUDINI_UNAVAILABLE"
                    failure_message = "The write-control finalizer did not execute its commit boundary"
                    pending_exception = RuntimeError(failure_message)
                    rollback_attempted = True
                    rollback_proven = attempt_confined_rollback()
                    if untrusted_creation_result:
                        rollback_proven = False
                elif (
                    commit_boundary_calls != 1
                    or not read_finish_attempted
                    or committed_publication is None
                    or committed_report is None
                    or final_report is not committed_report
                ):
                    # Undo exit was attempted, so a forged/replaced finalizer
                    # return cannot be repaired by another scene mutation.
                    self._frozen = True
                    return self._indeterminate_error(arguments)
                else:
                    try:
                        observed_final_report = self._contain_once(
                            self._read_adapter.capability_report
                        )
                    except BaseException as exc:
                        self._frozen = True
                        if not isinstance(exc, Exception):
                            raise
                        return self._indeterminate_error(arguments)
                    try:
                        self._validate_owned_write_reports(
                            (
                                committed_publication,
                                final_report,
                                observed_final_report,
                            ),
                            arguments,
                            outcome="committed",
                        )
                    except BaseException as exc:
                        self._frozen = True
                        if not isinstance(exc, Exception):
                            raise
                        return self._indeterminate_error(arguments)
                    return candidate_result

        # Failure and pre-commit control paths leave the Undo context here,
        # after any confined rollback, and publish exactly one read outcome.
        exit_exception: BaseException | None = None
        try:
            self._contain_once(
                lambda: undo_context.__exit__(
                    type(pending_exception),
                    pending_exception,
                    pending_exception.__traceback__
                    if pending_exception is not None
                    else None,
                )
            )
        except BaseException as exc:
            exit_exception = exc

        if exit_exception is not None:
            # A failed Undo boundary makes the Undo stack and physical commit
            # status unknowable.  Never perform a blind mutation after exit.
            self._frozen = True
            finish_exception = self._finish_owned_write_safely(
                read_token, arguments, outcome="indeterminate"
            )
            critical = next(
                (
                    item
                    for item in (
                        pending_exception,
                        rollback_critical,
                        exit_exception,
                        finish_exception,
                    )
                    if item is not None and not isinstance(item, Exception)
                ),
                None,
            )
            if critical is not None:
                raise critical
            return self._indeterminate_error(arguments)

        if rollback_proven:
            try:
                post_exit_proven = self._contain_once(
                    lambda: self._rollback_poststate_matches(proof)
                )
            except BaseException as exc:
                rollback_proven = False
                if rollback_critical is None and not isinstance(exc, Exception):
                    rollback_critical = exc
            else:
                if post_exit_proven is not True:
                    rollback_proven = False

        outcome = "rolled_back" if rollback_proven else "indeterminate"
        finish_exception = self._finish_owned_write_safely(
            read_token, arguments, outcome=outcome
        )
        if finish_exception is not None:
            rollback_proven = False
        if rollback_critical is not None:
            rollback_proven = False
        if not rollback_proven:
            self._frozen = True
        critical = next(
            (
                item
                for item in (
                    pending_exception,
                    rollback_critical,
                    finish_exception,
                )
                if item is not None and not isinstance(item, Exception)
            ),
            None,
        )
        if critical is not None:
            raise critical
        if isinstance(pending_exception, WriteControlAbort):
            pending_exception.rollback_proven = rollback_proven
            raise pending_exception
        if rollback_attempted and not rollback_proven:
            return self._indeterminate_error(arguments)
        return self._error(
            arguments,
            failure_code,
            failure_message,
            scene_revision=arguments["base_scene_revision"],
        )

    def _validate_binding(
        self,
        binding: _ApprovedWriteBinding,
        arguments: Mapping[str, Any],
    ) -> tuple[dict[str, Any], str, str]:
        self._registry.validate_input(_WRITE_TOOL, arguments)
        graph = normalize_graph(arguments["graph"])
        validate_graph_relations(graph)
        digest = graph_digest(graph)
        if digest.casefold() != arguments["canonical_graph_digest"].casefold():
            raise HoudiniWriteAdapterError("DIGEST_MISMATCH", "Graph digest changed after approval")
        approval_digest = approval_binding_digest(
            arguments,
            graph,
            digest,
            graph_side_effect_summary(graph),
        )
        payload = {
            **{key: arguments[key] for key in (
                "request_id", "thread_id", "turn_id", "hip_session_id",
                "expected_hip_fingerprint", "base_scene_revision", "idempotency_key",
                "deadline_ms", "permission_level",
            )},
            "canonical_graph_digest": digest,
            "schema_version": graph["schema_version"],
            "context": copy.deepcopy(graph["context"]),
            "target": copy.deepcopy(graph["target"]),
            "nodes": copy.deepcopy(graph["nodes"]),
            "connections": copy.deepcopy(graph["connections"]),
            "layout": copy.deepcopy(graph["layout"]),
            "side_effect_summary": graph_side_effect_summary(graph),
        }
        if (
            canonical_json_bytes(payload) != binding.approval_payload_json
            or approval_digest.casefold() != binding.approval_binding_digest.casefold()
        ):
            raise HoudiniWriteAdapterError("APPROVAL_MISMATCH", "Approval binding changed before execution")
        rebuilt = SceneRequest.build(
            _WRITE_TOOL,
            arguments,
            binding.absolute_deadline,
            binding.launch_id,
            binding.generation,
            binding.attestation_digest,
        )
        if (
            rebuilt.request_digest.casefold() != binding.request_digest.casefold()
            or rebuilt.approval_binding_digest is None
            or rebuilt.approval_binding_digest.casefold() != approval_digest.casefold()
        ):
            raise HoudiniWriteAdapterError("APPROVAL_MISMATCH", "Request identity changed after claim")
        if binding.catalog_digest.casefold() != self._catalog_digest.casefold():
            raise HoudiniWriteAdapterError("CAPABILITY_MISMATCH", "Claim catalog changed before execution")
        return graph, digest, approval_digest

    def _capability_mismatch(
        self,
        arguments: Mapping[str, Any],
        binding: _ApprovedWriteBinding,
        report: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        if not isinstance(report, Mapping) or report.get("available") is not True:
            return self._error(arguments, "HOUDINI_UNAVAILABLE", "Live Houdini capability is unavailable")
        if binding.attestation_digest.casefold() != self._attestation["digest"]:
            return self._error(arguments, "CAPABILITY_MISMATCH", "Capability attestation changed")
        try:
            live_catalog_digest = canonical_json_sha256(report.get("catalog"))
        except (TypeError, ValueError):
            return self._error(
                arguments,
                "CAPABILITY_MISMATCH",
                "Live capability catalog is not canonical",
            )
        if (
            live_catalog_digest.casefold() != binding.catalog_digest.casefold()
            or live_catalog_digest.casefold() != self._catalog_digest.casefold()
            or live_catalog_digest.casefold()
            != self._attestation["catalog_digest"].casefold()
        ):
            return self._error(
                arguments,
                "CAPABILITY_MISMATCH",
                "Live capability catalog changed before apply",
            )
        if report.get("hip_session_id") != arguments["hip_session_id"]:
            return self._error(arguments, "HIP_SESSION_MISMATCH", "HIP session changed before apply")
        if report.get("scene_revision") != arguments["base_scene_revision"]:
            return self._error(arguments, "SCENE_CONFLICT", "Scene revision changed before apply")
        fingerprint = report.get("hip_fingerprint")
        if (
            not isinstance(fingerprint, str)
            or fingerprint.casefold() != arguments["expected_hip_fingerprint"].casefold()
            or fingerprint.casefold() != self._attestation["hip_fingerprint"]
        ):
            return self._error(arguments, "CAPABILITY_MISMATCH", "HIP fingerprint changed before apply")
        if (
            self._attestation["hip_session_id"] != arguments["hip_session_id"]
            or self._attestation["scene_revision"] != arguments["base_scene_revision"]
        ):
            return self._error(arguments, "CAPABILITY_MISMATCH", "Attestation is stale")
        return None

    def _resolve_graph_catalog(
        self,
        graph: Mapping[str, Any],
        live_catalog: Mapping[tuple[str, str], Mapping[str, Any]],
    ) -> dict[tuple[str, str], _CatalogNodeType]:
        requested = [graph["target"]["root_type"]] + [node["type"] for node in graph["nodes"]]
        resolved: dict[tuple[str, str], _CatalogNodeType] = {}
        for reference in requested:
            key = (reference["context"], reference["name"])
            certified = self._catalog.get(key)
            live = live_catalog.get(key)
            if certified is None:
                raise HoudiniWriteAdapterError("NODE_TYPE_NOT_ALLOWED", "Node type is absent from the certified catalog")
            if (
                not certified.available
                or not certified.creatable
                or certified.risk_level != _SAFE_RISK_LEVEL
            ):
                raise HoudiniWriteAdapterError("NODE_TYPE_NOT_ALLOWED", "Node type risk or creation policy is not admitted")
            if live is None or live.get("available") is not True:
                raise HoudiniWriteAdapterError("NODE_TYPE_UNAVAILABLE", "Certified node type is unavailable in Houdini")
            if (
                live.get("requested_name") != certified.canonical_name
                or
                live.get("resolved_name") != certified.resolved_name
                or live.get("context") != certified.context
                or live.get("category") != certified.category
                or live.get("input_count") != certified.input_count
                or live.get("output_count") != certified.output_count
                or live.get("available") is not certified.available
                or live.get("creatable") is not certified.creatable
                or live.get("risk_level") != certified.risk_level
            ):
                raise HoudiniWriteAdapterError("CAPABILITY_MISMATCH", "Live node type no longer matches its catalog")
            raw_live_parameters = live.get("parameters")
            if not isinstance(raw_live_parameters, list):
                raise HoudiniWriteAdapterError(
                    "CAPABILITY_MISMATCH", "Live parameter catalog is invalid"
                )
            live_parameters: dict[str, Mapping[str, Any]] = {}
            for item in raw_live_parameters:
                if not isinstance(item, Mapping) or not isinstance(item.get("name"), str):
                    raise HoudiniWriteAdapterError(
                        "CAPABILITY_MISMATCH", "Live parameter catalog is invalid"
                    )
                if item["name"] in live_parameters:
                    raise HoudiniWriteAdapterError(
                        "CAPABILITY_MISMATCH", "Live parameter catalog contains duplicates"
                    )
                live_parameters[item["name"]] = item
            if len(live_parameters) != len(certified.parameters):
                raise HoudiniWriteAdapterError(
                    "CAPABILITY_MISMATCH", "Live parameter catalog changed"
                )
            for parameter in certified.parameters:
                live_parameter = live_parameters.get(parameter.name)
                if live_parameter is None or (
                    live_parameter.get("value_type") != parameter.value_type
                    or live_parameter.get("tuple_size") != parameter.tuple_size
                    or live_parameter.get("items_type") != parameter.items_type
                    or live_parameter.get("writable") is not parameter.writable
                    or live_parameter.get("allows_expression")
                    is not parameter.allows_expression
                ):
                    raise HoudiniWriteAdapterError("CAPABILITY_MISMATCH", "Live parameter schema changed")
            resolved[key] = certified

        by_node = {
            node["id"]: resolved[(node["type"]["context"], node["type"]["name"])]
            for node in graph["nodes"]
        }
        for node in graph["nodes"]:
            specification = by_node[node["id"]]
            admitted = {item.name: item for item in specification.parameters}
            for assignment in node["parameters"]:
                parameter = admitted.get(assignment["name"])
                if (
                    parameter is None
                    or not parameter.writable
                    or parameter.allows_expression
                ):
                    raise HoudiniWriteAdapterError("PARAMETER_NOT_ALLOWED", "Parameter is absent from the certified catalog")
                self._validate_typed_value(assignment["value"], parameter)
        for connection in graph["connections"]:
            source = connection["source"]
            destination = connection["destination"]
            if source["output"] >= by_node[source["node"]].output_count:
                raise HoudiniWriteAdapterError("TOPOLOGY_NOT_ALLOWED", "Connection output exceeds catalog bounds")
            if destination["input"] >= by_node[destination["node"]].input_count:
                raise HoudiniWriteAdapterError("TOPOLOGY_NOT_ALLOWED", "Connection input exceeds catalog bounds")
        return resolved

    @staticmethod
    def _validate_typed_value(value: Mapping[str, Any], parameter: _CatalogParameter) -> None:
        if value["type"] != parameter.value_type:
            raise HoudiniWriteAdapterError("PARAMETER_TYPE_MISMATCH", "Parameter value type differs from catalog")
        if value["type"] == "tuple":
            if value.get("items_type") != parameter.items_type or len(value["value"]) != parameter.tuple_size:
                raise HoudiniWriteAdapterError("PARAMETER_TYPE_MISMATCH", "Parameter tuple shape differs from catalog")
        elif parameter.tuple_size != 1:
            raise HoudiniWriteAdapterError("PARAMETER_TYPE_MISMATCH", "Scalar parameter has a non-scalar catalog shape")

    @staticmethod
    def _set_typed_parameter(node: Any, assignment: Mapping[str, Any], parameter: _CatalogParameter) -> None:
        typed = assignment["value"]
        if typed["type"] == "tuple":
            target = node.parmTuple(parameter.name)
            if target is None:
                raise _ObservedStateMismatch("parameter tuple is unavailable")
            target.set(tuple(typed["value"]))
        else:
            target = node.parm(parameter.name)
            if target is None:
                raise _ObservedStateMismatch("parameter is unavailable")
            target.set(typed["value"])

    def _verify_observed(
        self,
        graph: Mapping[str, Any],
        digest: str,
        root: Any,
        proof: _RollbackProof,
        nodes: Mapping[str, Any],
        node_session_ids: Mapping[str, Any],
        catalog: Mapping[tuple[str, str], _CatalogNodeType],
    ) -> None:
        approved_root_name = graph["target"]["name_hint"]
        approved_root_path = f"/obj/{approved_root_name}"
        if (
            not _same_houdini_node(root, proof.root)
            or root.sessionId() != proof.root_session_id
            or not _same_houdini_node(root.parent(), proof.parent)
            or not _same_houdini_node(self._hou.node("/obj"), proof.parent)
            or root.path() != proof.root_path
            or root.path() != approved_root_path
            or not _same_houdini_node(self._hou.node(proof.root_path), root)
            or root.name() != proof.root_name
            or root.name() != approved_root_name
            or root.type().name()
            != catalog[(graph["target"]["root_type"]["context"], graph["target"]["root_type"]["name"])].resolved_name
            or root.userData(_OWNERSHIP_KEY) != _OWNERSHIP_VALUE
            or root.userData(_TRANSACTION_KEY) != proof.transaction_id
            or root.userData(_GRAPH_DIGEST_KEY) != digest
            or not _same_identity_members(
                tuple(proof.parent.children()),
                proof.parent_children_before + (root,),
            )
            or self._children_fingerprint(proof.parent_children_before).casefold()
            != proof.obj_fingerprint_before.casefold()
        ):
            raise _ObservedStateMismatch("root identity or ownership changed")
        observed_children = tuple(root.children())
        if not _same_identity_members(observed_children, tuple(nodes.values())):
            raise _ObservedStateMismatch("created node scope changed")

        if tuple(root.errors()):
            raise _ObservedStateMismatch("created root reports an error")
        if tuple(root.inputConnections()) or tuple(root.outputConnections()):
            raise _ObservedStateMismatch(
                "created root has an out-of-contract connection"
            )

        declared_connections: set[tuple[str, int, str, int]] = set()
        for connection in graph["connections"]:
            declared_connections.add((
                connection["source"]["node"], connection["source"]["output"],
                connection["destination"]["node"], connection["destination"]["input"],
            ))
        reverse_ids = tuple((live, local_id) for local_id, live in nodes.items())
        observed_connections: set[tuple[str, int, str, int]] = set()
        observed_connection_count = 0
        observed_outputs: set[tuple[str, int, str, int]] = set()
        observed_output_count = 0

        for declaration in graph["nodes"]:
            local_id = declaration["id"]
            live = nodes[local_id]
            expected_path = f"{approved_root_path}/{declaration['name_hint']}"
            specification = catalog[(declaration["type"]["context"], declaration["type"]["name"])]
            if (
                live.sessionId() != node_session_ids.get(local_id)
                or not _same_houdini_node(live.parent(), root)
                or live.path() != expected_path
                or not _same_houdini_node(self._hou.node(expected_path), live)
                or live.name() != declaration["name_hint"]
                or live.type().name() != specification.resolved_name
                or live.isDisplayFlagSet() is not declaration["flags"]["display"]
                or live.isRenderFlagSet() is not declaration["flags"]["render"]
            ):
                raise _ObservedStateMismatch("observed node type, parent, name, or flags changed")
            parameter_catalog = {
                item.name: item for item in specification.parameters
            }
            for assignment in declaration["parameters"]:
                typed = assignment["value"]
                parameter = parameter_catalog.get(assignment["name"])
                if parameter is None:
                    raise _ObservedStateMismatch("observed parameter schema is unavailable")
                self._validate_typed_value(typed, parameter)
                handle = (
                    live.parmTuple(parameter.name)
                    if parameter.value_type == "tuple"
                    else live.parm(parameter.name)
                )
                if handle is None:
                    raise _ObservedStateMismatch("observed parameter is unavailable")
                observed = handle.eval()
                expected = (
                    tuple(typed["value"])
                    if parameter.value_type == "tuple"
                    else typed["value"]
                )
                if (
                    not _typed_observed_value_is_valid(observed, parameter)
                    or observed != expected
                ):
                    raise _ObservedStateMismatch("observed parameter value changed")
            errors = tuple(live.errors())
            if errors:
                raise _ObservedStateMismatch("created node reports an error")
            for connection in live.inputConnections():
                observed_connection_count += 1
                source = connection.inputNode()
                destination = connection.outputNode()
                source_id = _identity_lookup(reverse_ids, source)
                destination_id = _identity_lookup(reverse_ids, destination)
                if (
                    source_id is None
                    or destination_id is None
                    or not _same_houdini_node(destination, live)
                ):
                    raise _ObservedStateMismatch("connection endpoint escaped transaction scope")
                observed_connections.add((
                    source_id,
                    connection.outputIndex(),
                    destination_id,
                    connection.inputIndex(),
                ))
            for connection in live.outputConnections():
                observed_output_count += 1
                source = connection.inputNode()
                destination = connection.outputNode()
                source_id = _identity_lookup(reverse_ids, source)
                destination_id = _identity_lookup(reverse_ids, destination)
                if (
                    source_id is None
                    or destination_id is None
                    or not _same_houdini_node(source, live)
                ):
                    raise _ObservedStateMismatch(
                        "outgoing connection escaped transaction scope"
                    )
                observed_outputs.add((
                    source_id,
                    connection.outputIndex(),
                    destination_id,
                    connection.inputIndex(),
                ))
        if (
            observed_connection_count != len(declared_connections)
            or observed_connections != declared_connections
            or observed_output_count != len(declared_connections)
            or observed_outputs != declared_connections
        ):
            raise _ObservedStateMismatch("observed connections changed")

    def _prove_exact_new_child(
        self,
        parent: Any,
        children_before: tuple[Any, ...],
        child: Any,
        *,
        expected_name: str,
        expected_path: str,
        expected_resolved_type: str,
    ) -> Any:
        """Prove a createNode return before it can receive another mutation."""

        if child is None or any(
            _same_houdini_node(child, item) for item in children_before
        ):
            raise _ObservedStateMismatch("createNode returned a pre-existing identity")
        session_id = child.sessionId()
        if (
            isinstance(session_id, bool)
            or not isinstance(session_id, int)
            or session_id < 0
            or not _same_houdini_node(child.parent(), parent)
            or child.name() != expected_name
            or child.path() != expected_path
            or child.type().name() != expected_resolved_type
            or not _same_houdini_node(self._hou.node(expected_path), child)
            or not _same_houdini_node(self._hou.node(parent.path()), parent)
            or not _same_identity_members(
                tuple(parent.children()), children_before + (child,)
            )
        ):
            raise _ObservedStateMismatch(
                "createNode did not return the exact approved new child"
            )
        return session_id

    def _attempt_rollback(
        self, proof: _RollbackProof, read_token: Any
    ) -> _RollbackOutcome:
        def contained_rollback() -> _RollbackOutcome:
            current_children = tuple(proof.parent.children())
            root_children = tuple(proof.root.children())
            retained_children = tuple(
                child.node for child in proof.created_children
            )
            owned_identities = (proof.root,) + retained_children
            if (
                not _same_houdini_node(self._hou.node("/obj"), proof.parent)
                or proof.root.sessionId() != proof.root_session_id
                or not _same_houdini_node(proof.root.parent(), proof.parent)
                or proof.root.path() != proof.root_path
                or not _same_houdini_node(
                    self._hou.node(proof.root_path), proof.root
                )
                or proof.root.name() != proof.root_name
                or proof.root.userData(_OWNERSHIP_KEY) != _OWNERSHIP_VALUE
                or proof.root.userData(_TRANSACTION_KEY) != proof.transaction_id
                or proof.root.userData(_GRAPH_DIGEST_KEY) != proof.graph_digest
                or not _same_identity_members(
                    current_children,
                    proof.parent_children_before + (proof.root,),
                )
                or self._children_fingerprint(
                    proof.parent_children_before
                ).casefold()
                != proof.obj_fingerprint_before.casefold()
                or not _same_identity_members(root_children, retained_children)
                or any(
                    child.node.sessionId() != child.session_id
                    or not _same_houdini_node(child.node.parent(), proof.root)
                    or child.node.path() != child.path
                    or child.node.name() != child.name
                    or child.node.type().name() != child.resolved_type
                    or not _same_houdini_node(
                        self._hou.node(child.path), child.node
                    )
                    for child in proof.created_children
                )
                or any(
                    not _same_houdini_node(connection.outputNode(), node)
                    or not _contains_identity(
                        owned_identities, connection.inputNode()
                    )
                    or not _contains_identity(
                        owned_identities, connection.outputNode()
                    )
                    for node in owned_identities
                    for connection in node.inputConnections()
                )
                or any(
                    not _same_houdini_node(connection.inputNode(), node)
                    or not _contains_identity(
                        owned_identities, connection.inputNode()
                    )
                    or not _contains_identity(
                        owned_identities, connection.outputNode()
                    )
                    for node in owned_identities
                    for connection in node.outputConnections()
                )
            ):
                return _RollbackOutcome(False)
            expectation: Any | None = None
            try:
                if self._strict_event_evidence:
                    owned_subjects = (
                        proof.root,
                        *(child.node for child in proof.created_children),
                    )
                    expectation = self._read_adapter.begin_owned_mutation(
                        read_token,
                        operation="rollback_destroy:root",
                        event_source_rules={
                            "BeingDeleted": owned_subjects,
                            "ChildDeleted": (proof.parent, proof.root),
                            "ChildSwitched": (proof.root,),
                        },
                        allowed_child_subjects=owned_subjects,
                        required_event_types=("ChildDeleted",),
                    )
                else:
                    expectation = self._read_adapter.begin_owned_mutation(
                        read_token,
                        # Houdini ChildDeleted is registered on and reports the
                        # subnet/parent node; the deleted child is separate event
                        # detail.  /obj was already observed before this root was
                        # created, so the exact rollback source is the retained
                        # parent identity, not the new root.
                        expected_callback_source=proof.parent,
                    )
            except BaseException as exc:
                return _RollbackOutcome(
                    False, exc if not isinstance(exc, Exception) else None
                )
            observation_ok = True
            critical: BaseException | None = None
            try:
                proof.root.destroy()
            except BaseException as exc:
                observation_ok = False
                if not isinstance(exc, Exception):
                    critical = exc
            finally:
                if expectation is not None:
                    try:
                        finish_arguments: dict[str, Any] = {}
                        if self._strict_event_evidence:
                            finish_arguments = {
                                "expected_child_subjects": owned_subjects,
                                "require_all_child_subjects": True,
                            }
                        self._read_adapter.finish_owned_mutation(
                            read_token,
                            expectation,
                            **finish_arguments,
                        )
                    except BaseException as exc:
                        observation_ok = False
                        if critical is None and not isinstance(exc, Exception):
                            critical = exc
            proven = observation_ok and self._rollback_poststate_matches(proof)
            return _RollbackOutcome(proven, critical)

        try:
            outcome = self._contain_once(contained_rollback)
        except BaseException as exc:
            return _RollbackOutcome(
                False, exc if not isinstance(exc, Exception) else None
            )
        if not isinstance(outcome, _RollbackOutcome):
            return _RollbackOutcome(False)
        return outcome

    def _rollback_poststate_matches(self, proof: _RollbackProof | None) -> bool:
        if proof is None:
            return False
        return bool(
            _same_houdini_node(self._hou.node("/obj"), proof.parent)
            and _same_identity_members(
                tuple(proof.parent.children()),
                proof.parent_children_before,
            )
            and self._hou.node(proof.root_path) is None
            and all(
                self._hou.node(child.path) is None
                for child in proof.created_children
            )
            and self._obj_fingerprint(proof.parent).casefold()
            == proof.obj_fingerprint_before.casefold()
        )

    def _success_output(
        self,
        arguments: Mapping[str, Any],
        graph: Mapping[str, Any],
        digest: str,
        approval_digest: str,
        root: Any,
        nodes: Mapping[str, Any],
        revision_after: int,
    ) -> dict[str, Any]:
        root_path = root.path()
        created = [{
            "request_local_id": "root",
            "path": root_path,
            "context": graph["target"]["root_type"]["context"],
            "resolved_type": graph["target"]["root_type"]["name"],
        }]
        created.extend({
            "request_local_id": node["id"],
            "path": nodes[node["id"]].path(),
            "context": node["type"]["context"],
            # Frozen 0.1.0 validation historically requires the canonical name
            # here.  The actual resolved HOM type was independently verified.
            "resolved_type": node["type"]["name"],
        } for node in graph["nodes"])
        changed = [item["path"] for item in created]
        return {
            "ok": True,
            "request_id": arguments["request_id"],
            "thread_id": arguments["thread_id"],
            "turn_id": arguments["turn_id"],
            "hip_session_id": arguments["hip_session_id"],
            "base_scene_revision": arguments["base_scene_revision"],
            "idempotency_key": arguments["idempotency_key"],
            "scene_revision": revision_after,
            "result": {
                "root_path": root_path,
                "canonical_graph_digest": digest,
                "approval_binding_digest": approval_digest,
                "replay": False,
                "revision_before": arguments["base_scene_revision"],
                "revision_after": revision_after,
                "created_nodes": created,
                "changed_nodes": changed,
                "undo_transaction": {"label": _UNDO_LABEL, "opened": True, "committed": True},
                "rollback": {"attempted": False, "complete": True, "retained_paths": []},
                "artifacts": [],
                "job_id": None,
            },
            "warnings": [],
            "structured_error": None,
        }

    def _decode_binding_arguments(self, binding: _ApprovedWriteBinding) -> dict[str, Any]:
        try:
            value = json.loads(binding.arguments_json.decode("utf-8"))
        except Exception as exc:
            raise HoudiniWriteAdapterError("APPROVAL_MISMATCH", "Approval arguments are invalid") from exc
        if not isinstance(value, dict):
            raise HoudiniWriteAdapterError("APPROVAL_MISMATCH", "Approval arguments are not an object")
        return value

    def _error(
        self,
        arguments: Mapping[str, Any],
        code: str,
        message: str,
        *,
        scene_revision: int | None = None,
    ) -> dict[str, Any]:
        result = self._registry.make_error_output(
            _WRITE_TOOL,
            arguments,
            code,
            message[:1024],
            scene_revision=scene_revision,
        )
        return self._registry.validate_output(_WRITE_TOOL, arguments, result)

    def _indeterminate_error(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        return self._error(
            arguments,
            "SCENE_STATE_INDETERMINATE",
            "The transaction boundary could not prove a determinate scene state",
            scene_revision=self._safe_scene_revision(
                arguments["base_scene_revision"] + 1
            ),
        )

    def _finish_owned_write_safely(
        self,
        read_token: Any,
        arguments: Mapping[str, Any],
        *,
        outcome: str,
    ) -> BaseException | None:
        published: Any | None = None
        refreshed: Any | None = None
        observed: Any | None = None
        publication_exception: BaseException | None = None
        refresh_exception: BaseException | None = None
        observation_exception: BaseException | None = None
        try:
            published = self._contain_once(
                lambda: self._read_adapter.finish_owned_write(
                    read_token, outcome=outcome
                )
            )
        except BaseException as exc:
            publication_exception = exc
        if outcome == "indeterminate":
            # A failed transaction may have retained an exact or partial new
            # graph.  Refresh even when publication cleared internal state and
            # then raised; a stale pre-write observer snapshot is never enough.
            try:
                refreshed = self._contain_once(self._read_adapter.refresh)
            except BaseException as exc:
                refresh_exception = exc
        try:
            observed = self._contain_once(
                self._read_adapter.capability_report
            )
        except BaseException as exc:
            observation_exception = exc

        critical = next(
            (
                item
                for item in (
                    publication_exception,
                    refresh_exception,
                    observation_exception,
                )
                if item is not None and not isinstance(item, Exception)
            ),
            None,
        )
        if critical is not None:
            return critical
        ordinary = next(
            (
                item
                for item in (
                    publication_exception,
                    refresh_exception,
                    observation_exception,
                )
                if item is not None
            ),
            None,
        )
        if ordinary is not None:
            return ordinary
        try:
            reports = (
                (published, refreshed, observed)
                if outcome == "indeterminate"
                else (published, observed)
            )
            self._validate_owned_write_reports(
                reports, arguments, outcome=outcome
            )
        except BaseException as exc:
            return exc
        return None

    def _validate_owned_write_reports(
        self,
        reports: tuple[Any, ...],
        arguments: Mapping[str, Any],
        *,
        outcome: str,
    ) -> None:
        if outcome not in {"committed", "rolled_back", "indeterminate"}:
            raise RuntimeError("owned-write publication outcome is invalid")
        expected_revision = arguments["base_scene_revision"]
        if outcome != "rolled_back":
            expected_revision += 1
        expected_session = arguments["hip_session_id"]
        base_fingerprint = arguments["expected_hip_fingerprint"].casefold()
        fingerprints: list[str] = []
        catalog_digests: list[str] = []
        for report in reports:
            if not isinstance(report, Mapping):
                raise RuntimeError(
                    "owned-write publication is not a capability report"
                )
            fingerprint = report.get("hip_fingerprint")
            try:
                catalog_digest = canonical_json_sha256(report.get("catalog"))
            except (AttributeError, TypeError, ValueError) as exc:
                raise RuntimeError(
                    "owned-write publication has no valid catalog"
                ) from exc
            if (
                report.get("available") is not True
                or report.get("hip_session_id") != expected_session
                or report.get("scene_revision") != expected_revision
                or not _is_sha256(fingerprint)
                or catalog_digest.casefold() != self._catalog_digest.casefold()
            ):
                raise RuntimeError(
                    "owned-write publication does not match the transaction"
                )
            fingerprints.append(fingerprint.casefold())
            catalog_digests.append(catalog_digest.casefold())
        if (
            not fingerprints
            or any(item != fingerprints[0] for item in fingerprints[1:])
            or any(item != catalog_digests[0] for item in catalog_digests[1:])
            or (outcome == "rolled_back" and fingerprints[0] != base_fingerprint)
            or (outcome != "rolled_back" and fingerprints[0] == base_fingerprint)
        ):
            raise RuntimeError("owned-write publication reports disagree")

    def _refresh_indeterminate_scope_safely(self) -> BaseException | None:
        try:
            self._contain_once(self._read_adapter.refresh)
        except BaseException as exc:
            return exc
        return None

    def _contain_once(self, operation: Callable[[], Any]) -> Any:
        """Require one exact non-cancellable containment callback invocation."""

        call_count = 0
        captured: Any = None

        def boundary() -> Any:
            nonlocal call_count, captured
            self._require_main_thread()
            call_count += 1
            if call_count != 1:
                raise RuntimeError("containment boundary invoked more than once")
            captured = operation()
            return captured

        returned = self._control_guard.contain(boundary)
        if call_count != 1 or returned is not captured:
            raise RuntimeError(
                "containment guard did not return its exact boundary result"
            )
        return captured

    def _checkpoint(self, phase: str) -> None:
        if phase != "preflight" and phase not in _PHASES:
            raise RuntimeError("unknown graph transaction phase")
        guard = self._control_guard
        guard.checkpoint(phase)
        self._check_deadline()

    def _check_deadline(self) -> None:
        deadline = self._active_deadline
        if deadline is not None and self._read_clock() >= deadline:
            raise WriteControlAbort("deadline")

    def _read_clock(self) -> float:
        try:
            value = float(self._clock())
        except Exception as exc:
            raise HoudiniWriteAdapterError(
                "HOUDINI_UNAVAILABLE", "The transaction clock is unavailable"
            ) from exc
        if not math.isfinite(value) or value < 0:
            raise HoudiniWriteAdapterError(
                "HOUDINI_UNAVAILABLE", "The transaction clock is invalid"
            )
        return value

    def _guarded_mutation(
        self,
        phase: str,
        read_token: Any,
        callback_source: Any,
        mutation: Callable[[], Any],
        *,
        operation: str | None = None,
        event_source_rules: Mapping[str, tuple[Any, ...]] | None = None,
        required_event_types: tuple[str, ...] | None = None,
        allowed_child_subjects: tuple[Any, ...] | None = None,
        created_subject: bool = False,
    ) -> Any:
        if phase not in _PHASES:
            raise RuntimeError("unknown graph transaction phase")
        allow_zero_events = operation in _SILENT_READBACK_OPERATIONS

        def checked_operation() -> Any:
            self._check_deadline()
            if self._strict_event_evidence:
                expectation = self._read_adapter.begin_owned_mutation(
                    read_token,
                    operation=operation,
                    event_source_rules=event_source_rules,
                    allowed_child_subjects=allowed_child_subjects,
                    required_event_types=required_event_types,
                    allow_zero_events=allow_zero_events,
                )
            else:
                expectation = self._read_adapter.begin_owned_mutation(
                    read_token,
                    expected_callback_source=callback_source,
                )
            result: Any = None
            returned = False
            try:
                result = mutation()
                returned = True
                return result
            finally:
                finish_arguments: dict[str, Any] = {}
                if self._strict_event_evidence and created_subject and returned:
                    finish_arguments = {
                        "expected_child_subjects": (result,),
                        "require_all_child_subjects": True,
                    }
                if self._strict_event_evidence and allow_zero_events:
                    finish_arguments["exact_readback_proven"] = bool(
                        returned and result is True
                    )
                self._read_adapter.finish_owned_mutation(
                    read_token,
                    expectation,
                    **finish_arguments,
                )

        return self._mutate_once(phase, checked_operation)

    def _set_user_data_with_evidence(
        self,
        phase: str,
        read_token: Any,
        root: Any,
        *,
        key: str,
        value: str,
    ) -> None:
        admitted_keys = (_OWNERSHIP_KEY, _TRANSACTION_KEY, _GRAPH_DIGEST_KEY)
        if key not in admitted_keys or not isinstance(value, str) or not value:
            raise RuntimeError("invalid owned metadata mutation")
        if root.userData(key) is not None:
            raise _ObservedStateMismatch("owned metadata already exists")

        def set_and_read_back() -> bool:
            root.setUserData(key, value)
            return root.userData(key) == value

        result = self._guarded_mutation(
            phase,
            read_token,
            root,
            set_and_read_back,
            operation=f"set_user_data:{key}",
            event_source_rules={
                "CustomDataChanged": (root,),
                "AppearanceChanged": (root,),
            },
            required_event_types=(),
        )
        if result is not True:
            raise _ObservedStateMismatch("owned metadata readback failed")

    def _install_strict_observer(self, read_token: Any, node: Any) -> None:
        if not self._strict_event_evidence:
            return
        self._contain_once(
            lambda: self._read_adapter.install_owned_node_observer(
                read_token, node
            )
        )

    def _set_flag_with_evidence(
        self,
        read_token: Any,
        parent: Any,
        live_node: Any,
        *,
        owned_nodes: tuple[Any, ...],
        node_id: str,
        flag_name: str,
        desired: bool,
    ) -> None:
        if flag_name == "display":
            getter = live_node.isDisplayFlagSet
            setter = live_node.setDisplayFlag
        elif flag_name == "render":
            getter = live_node.isRenderFlagSet
            setter = live_node.setRenderFlag
        else:
            raise RuntimeError("unknown graph flag")
        operation = f"set_flag:{node_id}:{flag_name}"
        if self._strict_event_evidence:
            observed = self._contain_once(getter)
            if type(observed) is not bool:
                raise HoudiniWriteAdapterError(
                    "CAPABILITY_MISMATCH",
                    "The live Houdini flag state is invalid",
                )
            if observed is desired:
                self._contain_once(
                    lambda: self._read_adapter.record_owned_noop(
                        read_token, operation=operation
                    )
                )
                return
        def set_and_read_back() -> bool:
            setter(desired)
            observed = getter()
            if type(observed) is not bool:
                raise _ObservedStateMismatch("flag readback is invalid")
            return observed is desired

        changed = self._guarded_mutation(
            "set_flags_layout",
            read_token,
            live_node,
            set_and_read_back,
            operation=operation,
            event_source_rules={
                "FlagChanged": owned_nodes,
                "ChildSwitched": (parent,),
            },
            required_event_types=("FlagChanged",),
            allowed_child_subjects=owned_nodes,
        )
        if changed is not True:
            raise _ObservedStateMismatch("flag readback did not match")

    def _after_phase(self, phase: str) -> None:
        hook = self._phase_hook
        if hook is not None:
            hook(phase)

    def _mutate_once(
        self, phase: str, operation: Callable[[], Any]
    ) -> Any:
        """Require one exact atomic mutator callback invocation."""

        call_count = 0
        captured: Any = None

        def boundary() -> Any:
            nonlocal call_count, captured
            self._require_main_thread()
            call_count += 1
            if call_count != 1:
                raise RuntimeError("mutation boundary invoked more than once")
            captured = operation()
            return captured

        returned = self._control_guard.mutate(phase, boundary)
        if call_count != 1 or returned is not captured:
            raise RuntimeError(
                "control guard did not return its exact mutation result"
            )
        return captured

    def _require_main_thread(self) -> None:
        if threading.get_ident() != self._main_thread_id:
            raise HoudiniWriteAdapterError(
                "MAIN_THREAD_REQUIRED",
                "Scene transaction callbacks require the Houdini UI main thread",
            )

    def _safe_scene_revision(self, fallback: int) -> int:
        try:
            report = self._read_adapter.capability_report()
            value = report.get("scene_revision")
            if (
                isinstance(value, int)
                and not isinstance(value, bool)
                and value >= fallback
            ):
                return value
        except Exception:
            pass
        return fallback

    @staticmethod
    def _child_named(parent: Any, name: str) -> Any | None:
        matches = [child for child in tuple(parent.children()) if child.name() == name]
        if len(matches) > 1:
            raise HoudiniWriteAdapterError("CAPABILITY_MISMATCH", "Duplicate target names are observable")
        return matches[0] if matches else None

    @staticmethod
    def _obj_fingerprint(obj: Any) -> str:
        return HoudiniWriteAdapter._children_fingerprint(tuple(obj.children()))

    @staticmethod
    def _children_fingerprint(children: tuple[Any, ...]) -> str:
        if len(children) > _MAX_OBJ_CHILDREN:
            raise HoudiniWriteAdapterError(
                "HOUDINI_UNAVAILABLE", "The /obj context exceeds bounded capacity"
            )
        records = []
        for child in children:
            records.append({
                "name": child.name(),
                "path": child.path(),
                "session_id": child.sessionId(),
                "type": child.type().name(),
            })
        return canonical_json_sha256(sorted(records, key=lambda item: (item["name"], item["session_id"])))

    @staticmethod
    def _freeze_attestation(attestation: Any) -> dict[str, Any]:
        try:
            payload = {
                "digest": str(attestation.digest),
                "catalog_digest": str(attestation.catalog_digest),
                "schema_digest": str(attestation.schema_digest),
                "hip_session_id": str(attestation.hip_session_id),
                "hip_fingerprint": str(attestation.hip_fingerprint),
                "scene_revision": int(attestation.scene_revision),
            }
        except Exception as exc:
            raise HoudiniWriteAdapterError("CAPABILITY_MISMATCH", "Capability attestation is incomplete") from exc
        for key in ("digest", "catalog_digest", "schema_digest", "hip_fingerprint"):
            _require_sha256(payload[key], key)
        return payload

    @staticmethod
    def _parse_catalog(value: Any) -> dict[tuple[str, str], _CatalogNodeType]:
        if not isinstance(value, list) or not value:
            raise HoudiniWriteAdapterError("CAPABILITY_MISMATCH", "Certified catalog must be a non-empty array")
        records: dict[tuple[str, str], _CatalogNodeType] = {}
        for raw in value:
            if not isinstance(raw, Mapping):
                raise HoudiniWriteAdapterError("CAPABILITY_MISMATCH", "Certified catalog record is invalid")
            context = raw.get("context")
            canonical = raw.get("requested_name")
            resolved = raw.get("resolved_name")
            category = raw.get("category")
            risk = raw.get("risk_level")
            if not all(isinstance(item, str) and item for item in (context, canonical, resolved, category, risk)):
                raise HoudiniWriteAdapterError("CAPABILITY_MISMATCH", "Certified catalog identity or risk is invalid")
            available = raw.get("available")
            creatable = raw.get("creatable")
            if not isinstance(available, bool) or not isinstance(creatable, bool):
                raise HoudiniWriteAdapterError("CAPABILITY_MISMATCH", "Certified catalog policy flags are invalid")
            input_count = _bounded_count(raw.get("input_count"), "input_count")
            output_count = _bounded_count(raw.get("output_count"), "output_count")
            parameters = []
            parameter_names: set[str] = set()
            for parameter in raw.get("parameters", []):
                if not isinstance(parameter, Mapping):
                    raise HoudiniWriteAdapterError("CAPABILITY_MISMATCH", "Certified parameter record is invalid")
                name = parameter.get("name")
                value_type = parameter.get("value_type")
                tuple_size = parameter.get("tuple_size")
                items_type = parameter.get("items_type")
                if (
                    not isinstance(name, str)
                    or value_type not in {"float", "int", "bool", "string", "tuple"}
                    or not isinstance(tuple_size, int)
                    or isinstance(tuple_size, bool)
                    or tuple_size < 1
                    or tuple_size > 16
                    or not isinstance(parameter.get("writable"), bool)
                    or not isinstance(parameter.get("allows_expression"), bool)
                ):
                    raise HoudiniWriteAdapterError("PARAMETER_NOT_ALLOWED", "Certified parameter shape is invalid")
                if value_type == "tuple":
                    if items_type not in {"float", "int", "bool", "string"}:
                        raise HoudiniWriteAdapterError("PARAMETER_TYPE_MISMATCH", "Tuple item type is invalid")
                elif items_type is not None or tuple_size != 1:
                    raise HoudiniWriteAdapterError("PARAMETER_TYPE_MISMATCH", "Scalar catalog shape is invalid")
                if name in parameter_names:
                    raise HoudiniWriteAdapterError(
                        "CAPABILITY_MISMATCH",
                        "Certified parameter catalog contains duplicate names",
                    )
                parameter_names.add(name)
                parameters.append(_CatalogParameter(
                    name,
                    value_type,
                    tuple_size,
                    items_type,
                    bool(parameter["writable"]),
                    bool(parameter["allows_expression"]),
                ))
            key = (context, canonical)
            if key in records:
                raise HoudiniWriteAdapterError("CAPABILITY_MISMATCH", "Certified catalog contains duplicate types")
            records[key] = _CatalogNodeType(
                context=context,
                canonical_name=canonical,
                resolved_name=resolved,
                category=category,
                input_count=input_count,
                output_count=output_count,
                available=available,
                creatable=creatable,
                risk_level=risk,
                parameters=tuple(parameters),
            )
        return records

    @staticmethod
    def _index_live_catalog(value: Any) -> dict[tuple[str, str], Mapping[str, Any]]:
        if not isinstance(value, list):
            raise HoudiniWriteAdapterError("HOUDINI_UNAVAILABLE", "Live Houdini catalog is unavailable")
        records: dict[tuple[str, str], Mapping[str, Any]] = {}
        for record in value:
            if not isinstance(record, Mapping):
                raise HoudiniWriteAdapterError("CAPABILITY_MISMATCH", "Live catalog record is invalid")
            context = record.get("context")
            canonical = record.get("requested_name")
            if not isinstance(context, str) or not isinstance(canonical, str):
                raise HoudiniWriteAdapterError("CAPABILITY_MISMATCH", "Live catalog identity is invalid")
            key = (context, canonical)
            if key in records:
                raise HoudiniWriteAdapterError("CAPABILITY_MISMATCH", "Live catalog contains duplicate types")
            records[key] = record
        return records


class _ObservedStateMismatch(RuntimeError):
    pass


def _same_identity_members(
    observed: tuple[Any, ...], expected: tuple[Any, ...]
) -> bool:
    return len(observed) == len(expected) and all(
        sum(
            _same_houdini_node(candidate, item) for candidate in observed
        )
        == 1
        for item in expected
    )


def _identity_lookup(
    pairs: tuple[tuple[Any, str], ...], observed: Any
) -> str | None:
    matches = [
        local_id
        for candidate, local_id in pairs
        if _same_houdini_node(candidate, observed)
    ]
    return matches[0] if len(matches) == 1 else None


def _contains_identity(items: tuple[Any, ...], observed: Any) -> bool:
    return any(_same_houdini_node(candidate, observed) for candidate in items)


def _typed_observed_value_is_valid(
    value: Any, parameter: _CatalogParameter
) -> bool:
    if parameter.value_type == "tuple":
        return bool(
            isinstance(value, tuple)
            and len(value) == parameter.tuple_size
            and parameter.items_type is not None
            and all(
                _typed_scalar_is_valid(item, parameter.items_type)
                for item in value
            )
        )
    return _typed_scalar_is_valid(value, parameter.value_type)


def _typed_scalar_is_valid(value: Any, value_type: str) -> bool:
    if value_type == "float":
        return bool(
            not isinstance(value, bool)
            and isinstance(value, (int, float))
            and math.isfinite(float(value))
        )
    if value_type == "int":
        return type(value) is int
    if value_type == "bool":
        return type(value) is bool
    if value_type == "string":
        return isinstance(value, str)
    return False


def _require_sha256(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdefABCDEF" for character in value)
    ):
        raise HoudiniWriteAdapterError("CAPABILITY_MISMATCH", f"{field} must be a SHA-256 digest")
    return value


def _is_sha256(value: Any) -> bool:
    return bool(
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdefABCDEF" for character in value)
    )


def _bounded_count(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > 9999:
        raise HoudiniWriteAdapterError("CAPABILITY_MISMATCH", f"{field} is outside safe bounds")
    return value


def _admitted_contract_code(code: str) -> str:
    admitted = {
        "INVALID_ARGUMENT",
        "SCHEMA_INVALID",
        "NODE_TYPE_NOT_ALLOWED",
        "NODE_TYPE_UNAVAILABLE",
        "PARAMETER_NOT_ALLOWED",
        "PARAMETER_TYPE_MISMATCH",
        "PATH_SCOPE_VIOLATION",
        "GRAPH_INVALID",
        "TOPOLOGY_NOT_ALLOWED",
        "DIGEST_MISMATCH",
        "APPROVAL_REQUIRED",
        "APPROVAL_MISMATCH",
    }
    return code if code in admitted else "INVALID_ARGUMENT"


__all__ = ["HoudiniWriteAdapter", "HoudiniWriteAdapterError", "WriteControlAbort"]
