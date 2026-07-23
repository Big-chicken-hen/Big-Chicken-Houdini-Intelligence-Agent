"""One-shot, local Gate B4B acceptance controller.

This module is deliberately outside the production Panel, Bridge, and MCP
dispatch paths.  It imports neither ``hou`` nor Qt and accepts the live HOM
module only by explicit construction from the dedicated acceptance Panel.

The controller performs exactly three user-driven actions:

* read and present the frozen stairs fixture and its exact approval binding;
* consume one process-local Apply opportunity through the existing B1 ledger,
  strict read facade, claim authority, and dormant graph writer; and
* after the user invokes Houdini Undo manually, prove the bounded scene returned
  to its original blank state using only reads and observer evidence.

It never invokes Undo/Redo, saves a HIP, cooks, renders, opens a network
connection, or reads configuration/environment state.
"""

from __future__ import annotations

import copy
import math
import sys
import threading
import time
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Callable

# The normal Houdini package intentionally exposes only ``python_libs`` and
# ``src``.  This dedicated, local-only acceptance module needs the existing B1
# ledger, so add exactly the repository's ``services/bridge`` directory after
# proving the resolved path remains inside the resolved project root.  No
# launcher or persistent interpreter configuration is changed.
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_BRIDGE_IMPORT_ROOT = (_PROJECT_ROOT / "services" / "bridge").resolve()
try:
    _BRIDGE_IMPORT_ROOT.relative_to(_PROJECT_ROOT)
except ValueError as exc:
    raise RuntimeError("B4B bridge import root escaped the project") from exc
if _BRIDGE_IMPORT_ROOT.is_dir() and str(_BRIDGE_IMPORT_ROOT) not in sys.path:
    sys.path.insert(0, str(_BRIDGE_IMPORT_ROOT))

from hia_bridge.scene_queue import FakeCapabilityAttestation, SceneQueue
from hia_core.houdini_contract import (
    SchemaRegistry,
    canonical_json_sha256,
    graph_digest,
    graph_side_effect_summary,
    normalize_graph,
    strict_json_loads,
    validate_graph_relations,
)

from .houdini_read_adapter import HoudiniReadAdapter, _same_houdini_node
from .houdini_write_adapter import (
    HoudiniWriteAdapter,
    WriteControlAbort,
    _ApprovedWriteBinding,
)


NEW = "NEW"
APPROVAL_PRESENTED = "APPROVAL_PRESENTED"
APPLYING = "APPLYING"
WAIT_MANUAL_UNDO = "WAIT_MANUAL_UNDO"
VERIFIED = "VERIFIED"
FAILED = "FAILED"

_WRITE_TOOL = "houdini_graph_apply"
_FIXTURE_DIGEST = "0a9cf0fd98882d8916dcdd9edda77655e93d9bde2857409581b0eb54f65290c4"
_TARGET_PATH = "/obj/HIA_Graph_stairs_demo"
_APPROVAL_SECONDS = 60.0
_MAX_EVENT_JOURNAL = 512
_OWNERSHIP_KEY = "hia_ownership"
_TRANSACTION_KEY = "hia_transaction_id"
_GRAPH_DIGEST_KEY = "hia_graph_digest"

# These are the exact types and parameters already certified for the frozen
# stairs fixture.  This is an acceptance-profile intersection, not a permanent
# graph-writer allowlist; the generic writer remains catalog driven.
_CERTIFIED_TYPES: dict[tuple[str, str], dict[str, Any]] = {
    ("Object", "geo"): {"resolved": "geo", "parameters": {}},
    ("Sop", "box"): {
        "resolved": "box",
        "parameters": {"size": ("float", 3), "t": ("float", 3)},
    },
    ("Sop", "transform"): {
        "resolved": "xform",
        "parameters": {"t": ("float", 3)},
    },
    ("Sop", "merge"): {"resolved": "merge", "parameters": {}},
    ("Sop", "null"): {"resolved": "null", "parameters": {}},
}


class B4BAcceptanceError(RuntimeError):
    """Content-safe local acceptance failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class _ProcessApplyLatch:
    """One irreversible Apply opportunity for a Houdini Python process."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._consumed = False

    def consume(self) -> bool:
        with self._lock:
            if self._consumed:
                return False
            self._consumed = True
            return True

    @property
    def consumed(self) -> bool:
        with self._lock:
            return self._consumed


_PROCESS_APPLY_LATCH = _ProcessApplyLatch()


class _ExactClaimAuthority:
    """Identity-only, one-shot bridge from one queue claim to one binding."""

    def __init__(self, request: Any, claim: Any) -> None:
        self._request = request
        self._claim = claim
        self._execution_token: object | None = None
        self._issued = False
        self._redeemed = False

    def issue_exact_claim(self, request: Any, claim: Any) -> object | None:
        if self._issued or request is not self._request or claim is not self._claim:
            return None
        self._issued = True
        self._execution_token = object()
        return self._execution_token

    def consume_binding(self, token: object) -> bool:
        if (
            self._redeemed
            or self._execution_token is None
            or token is not self._execution_token
        ):
            return False
        self._redeemed = True
        return True


class _LocalControlGuard:
    """Atomic local guard for the single non-cancellable writer boundary."""

    def __init__(
        self,
        *,
        clock: Callable[[], float],
        deadline: float,
    ) -> None:
        self._clock = clock
        self._deadline = _finite_clock_value(deadline)
        self._lock = threading.RLock()

    def checkpoint(self, _phase: str) -> None:
        if _finite_clock_value(self._clock()) >= self._deadline:
            raise WriteControlAbort("deadline")

    def mutate(self, phase: str, operation: Callable[[], Any]) -> Any:
        with self._lock:
            self.checkpoint(phase)
            return operation()

    def finalize(self, operation: Callable[[], Any]) -> Any:
        with self._lock:
            self.checkpoint("commit")
            return operation()

    def contain(self, operation: Callable[[], Any]) -> Any:
        with self._lock:
            return operation()


class _AcceptanceReadFacade:
    """Promote one exact live read catalog into the reviewed B4B write slice.

    The underlying adapter owns session, revision, fingerprint, observer, and
    event authority.  This facade cannot invent any of those values.  It only
    intersects the five already reviewed live schemas with the acceptance
    policy needed by the frozen fixture.
    """

    def __init__(self, adapter: Any) -> None:
        if getattr(adapter, "strict_event_evidence", False) is not True:
            raise B4BAcceptanceError(
                "HOUDINI_UNAVAILABLE",
                "Strict Houdini observer evidence is required for Gate B4B",
            )
        self._adapter = adapter

    @property
    def strict_event_evidence(self) -> bool:
        return True

    @property
    def main_thread_id(self) -> Any:
        return getattr(self._adapter, "main_thread_id", None)

    def start(self) -> dict[str, Any]:
        return self._promote(self._adapter.start())

    def refresh(self) -> dict[str, Any]:
        return self._promote(self._adapter.refresh())

    def capability_report(self) -> dict[str, Any]:
        return self._promote(self._adapter.capability_report())

    def begin_owned_write(self, *args: Any, **kwargs: Any) -> Any:
        return self._adapter.begin_owned_write(*args, **kwargs)

    def finish_owned_write(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._promote(self._adapter.finish_owned_write(*args, **kwargs))

    def begin_owned_mutation(self, *args: Any, **kwargs: Any) -> Any:
        return self._adapter.begin_owned_mutation(*args, **kwargs)

    def finish_owned_mutation(self, *args: Any, **kwargs: Any) -> Any:
        return self._adapter.finish_owned_mutation(*args, **kwargs)

    def install_owned_node_observer(self, *args: Any, **kwargs: Any) -> Any:
        return self._adapter.install_owned_node_observer(*args, **kwargs)

    def record_owned_noop(self, *args: Any, **kwargs: Any) -> Any:
        return self._adapter.record_owned_noop(*args, **kwargs)

    def event_journal_snapshot(self) -> tuple[dict[str, Any], ...]:
        value = self._adapter.event_journal_snapshot()
        return tuple(copy.deepcopy(tuple(value)))

    def last_owned_evidence(self) -> dict[str, Any] | None:
        value = self._adapter.last_owned_evidence()
        return copy.deepcopy(value)

    @staticmethod
    def _promote(report: Any) -> dict[str, Any]:
        if not isinstance(report, Mapping):
            raise B4BAcceptanceError(
                "HOUDINI_UNAVAILABLE", "The live capability report is invalid"
            )
        catalog = report.get("catalog")
        if not isinstance(catalog, list) or len(catalog) != len(_CERTIFIED_TYPES):
            raise B4BAcceptanceError(
                "CAPABILITY_MISMATCH", "The live capability catalog is incomplete"
            )

        indexed: dict[tuple[str, str], Mapping[str, Any]] = {}
        for item in catalog:
            if not isinstance(item, Mapping):
                raise B4BAcceptanceError(
                    "CAPABILITY_MISMATCH", "The live capability catalog is invalid"
                )
            key = (item.get("context"), item.get("requested_name"))
            if key in indexed or key not in _CERTIFIED_TYPES:
                raise B4BAcceptanceError(
                    "CAPABILITY_MISMATCH", "The live capability catalog changed"
                )
            indexed[key] = item
        if set(indexed) != set(_CERTIFIED_TYPES):
            raise B4BAcceptanceError(
                "CAPABILITY_MISMATCH", "The live capability catalog changed"
            )

        promoted: list[dict[str, Any]] = []
        for key, policy in _CERTIFIED_TYPES.items():
            live = indexed[key]
            if (
                live.get("available") is not True
                or live.get("resolved_name") != policy["resolved"]
                or isinstance(live.get("input_count"), bool)
                or not isinstance(live.get("input_count"), int)
                or not 0 <= live["input_count"] <= 9999
                or isinstance(live.get("output_count"), bool)
                or not isinstance(live.get("output_count"), int)
                or not 0 <= live["output_count"] <= 9999
            ):
                raise B4BAcceptanceError(
                    "CAPABILITY_MISMATCH", "A certified node type is unavailable"
                )

            raw_parameters = live.get("parameters")
            if not isinstance(raw_parameters, list):
                raise B4BAcceptanceError(
                    "CAPABILITY_MISMATCH", "A live parameter schema is invalid"
                )
            parameter_index: dict[str, Mapping[str, Any]] = {}
            for parameter in raw_parameters:
                if not isinstance(parameter, Mapping) or not isinstance(
                    parameter.get("name"), str
                ):
                    raise B4BAcceptanceError(
                        "CAPABILITY_MISMATCH", "A live parameter schema is invalid"
                    )
                if parameter["name"] in parameter_index:
                    raise B4BAcceptanceError(
                        "CAPABILITY_MISMATCH", "A live parameter schema is ambiguous"
                    )
                parameter_index[parameter["name"]] = parameter
            if set(parameter_index) != set(policy["parameters"]):
                raise B4BAcceptanceError(
                    "CAPABILITY_MISMATCH", "A certified parameter schema changed"
                )

            parameters: list[dict[str, Any]] = []
            for name, (items_type, tuple_size) in policy["parameters"].items():
                live_parameter = parameter_index[name]
                live_type = live_parameter.get("value_type")
                live_items = live_parameter.get("items_type")
                if live_type == "tuple" and "items_type" not in live_parameter:
                    default_value = live_parameter.get("default_value")
                    if (
                        isinstance(default_value, Mapping)
                        and default_value.get("type") == "tuple"
                    ):
                        live_items = default_value.get("items_type")
                # B2 describes a tuple by scalar component type; the dormant
                # writer catalog describes the same value as tuple+items_type.
                # The live B2 adapter nests that component type in the typed
                # default value, while the earlier test facade exposed it at
                # the parameter level.  Accept only those two exact shapes.
                shape_matches = bool(
                    live_parameter.get("tuple_size") == tuple_size
                    and (
                        (live_type == items_type and live_items is None)
                        or (live_type == "tuple" and live_items == items_type)
                    )
                    and live_parameter.get("allows_expression") is False
                )
                if not shape_matches:
                    raise B4BAcceptanceError(
                        "CAPABILITY_MISMATCH", "A certified parameter schema changed"
                    )
                parameters.append(
                    {
                        "name": name,
                        "value_type": "tuple",
                        "tuple_size": tuple_size,
                        "items_type": items_type,
                        "writable": True,
                        "allows_expression": False,
                    }
                )

            promoted.append(
                {
                    "context": key[0],
                    "requested_name": key[1],
                    "resolved_name": policy["resolved"],
                    "category": key[0],
                    "available": True,
                    "creatable": True,
                    "risk_level": "ordinary_graph_write",
                    "parameters": parameters,
                    "input_count": live["input_count"],
                    "output_count": live["output_count"],
                }
            )

        result = copy.deepcopy(dict(report))
        result["catalog"] = promoted
        return result


class B4BAcceptanceController:
    """Drive one local, exact stairs acceptance transaction."""

    def __init__(
        self,
        hou_module: Any,
        *,
        pyside_version: str,
        read_adapter: Any | None = None,
        clock: Callable[[], float] = time.monotonic,
        process_latch: _ProcessApplyLatch | None = None,
    ) -> None:
        self._hou = hou_module
        self._main_thread_id = threading.get_ident()
        self._clock = clock
        self._latch = process_latch or _PROCESS_APPLY_LATCH
        self._registry = SchemaRegistry()
        base_read = read_adapter or HoudiniReadAdapter(
            hou_module,
            pyside_version=pyside_version,
            main_thread_id=self._main_thread_id,
            clock=clock,
            strict_event_evidence=True,
        )
        self._read = _AcceptanceReadFacade(base_read)

        fixture_path = (
            _PROJECT_ROOT
            / "tests"
            / "fixtures"
            / "p2_v"
            / "stairs_graph.json"
        )
        try:
            if fixture_path.stat().st_size > 262_144:
                raise ValueError("fixture exceeds the bounded acceptance input")
            raw_graph = strict_json_loads(
                fixture_path.read_bytes(), str(fixture_path)
            )
            graph = normalize_graph(raw_graph)
            validate_graph_relations(graph)
        except Exception as exc:
            raise B4BAcceptanceError(
                "CONTRACT_INVALID", "The frozen B4B fixture could not be loaded"
            ) from exc
        digest = graph_digest(graph)
        if digest != _FIXTURE_DIGEST or graph["target"]["name_hint"] != _TARGET_PATH.rsplit("/", 1)[-1]:
            raise B4BAcceptanceError(
                "DIGEST_MISMATCH", "The frozen B4B fixture identity changed"
            )
        self._graph = graph
        self._graph_digest = digest

        self._state = NEW
        self._snapshot: dict[str, Any] = {
            "ok": True,
            "state": NEW,
            "message": "Ready for a read-only Gate B4B preflight",
            "manual_undo_required": False,
            "baseline": None,
            "capability": None,
            "approval": None,
            "apply_result": None,
            "verification": None,
            "event_journal": [],
        }
        self._baseline_private: dict[str, Any] | None = None
        self._queue: SceneQueue | None = None
        self._request: Any | None = None
        self._presentation: Any | None = None
        self._attestation: FakeCapabilityAttestation | None = None
        self._catalog: list[dict[str, Any]] | None = None
        self._deadline: float | None = None
        self._apply_journal_sequence = 0
        self._apply_evidence_ok = False
        self._apply_was_attempted = False
        self._declared_paths = tuple(
            [_TARGET_PATH]
            + [f"{_TARGET_PATH}/{node['name_hint']}" for node in self._graph["nodes"]]
        )

    @property
    def state(self) -> str:
        return self._state

    @property
    def report(self) -> dict[str, Any]:
        return copy.deepcopy(self._snapshot)

    def prepare(self) -> dict[str, Any]:
        """Perform read-only preflight and present the exact approval JSON."""

        stage = "main_thread"
        try:
            self._require_main_thread()
            if self._state != NEW:
                raise B4BAcceptanceError(
                    "INVALID_STATE", "Gate B4B preflight may be prepared only once"
                )
            stage = "clock"
            now = _finite_clock_value(self._clock())
            stage = "live_read"
            report = self._read.start()
            stage = "blank_baseline"
            baseline = self._capture_blank_baseline(report)
            stage = "capability_binding"
            catalog = copy.deepcopy(report["catalog"])
            catalog_digest = canonical_json_sha256(catalog)
            launch_id = f"b4b-{uuid.uuid4().hex}"
            process_nonce = f"p-{uuid.uuid4().hex}"
            attestation = FakeCapabilityAttestation(
                launch_id=launch_id,
                generation=0,
                process_nonce=process_nonce,
                hip_session_id=report["hip_session_id"],
                hip_fingerprint=report["hip_fingerprint"],
                scene_revision=report["scene_revision"],
                catalog_digest=catalog_digest,
                schema_digest=self._registry.manifest_digest,
            )
            queue = SceneQueue(
                launch_id,
                0,
                expected_schema_digest=self._registry.manifest_digest,
                expected_catalog_digest=catalog_digest,
                clock=self._clock,
            )
            queue.install_attestation(attestation)

            stage = "request_schema"
            deadline = now + _APPROVAL_SECONDS
            request_id = f"request-{uuid.uuid4().hex}"
            arguments = {
                "request_id": request_id,
                "thread_id": "b4b-local-thread",
                "turn_id": "b4b-local-turn",
                "hip_session_id": report["hip_session_id"],
                "expected_hip_fingerprint": report["hip_fingerprint"],
                "base_scene_revision": report["scene_revision"],
                "idempotency_key": f"b4b-{uuid.uuid4().hex}",
                "deadline_ms": int(_APPROVAL_SECONDS * 1000),
                "permission_level": "scene_write",
                "graph": copy.deepcopy(self._graph),
                "canonical_graph_digest": self._graph_digest,
            }
            self._registry.validate_input(_WRITE_TOOL, arguments)
            stage = "approval_ledger"
            request = queue.build_request(_WRITE_TOOL, arguments, deadline)
            submitted = queue.submit(request)
            presentation = queue.poll_next(0.0)
            if (
                submitted.state != "awaiting_approval"
                or presentation is None
                or presentation.kind != "approval_required"
                or presentation.request_id != request_id
                or presentation.request_digest != request.request_digest
                or presentation.approval_binding_digest
                != request.approval_binding_digest
            ):
                raise B4BAcceptanceError(
                    "APPROVAL_REQUIRED", "The exact approval could not be presented"
                )

            self._baseline_private = baseline
            self._queue = queue
            self._request = request
            self._presentation = presentation
            self._attestation = attestation
            self._catalog = catalog
            self._deadline = deadline
            self._state = APPROVAL_PRESENTED
            self._snapshot.update(
                {
                    "ok": True,
                    "state": self._state,
                    "message": (
                        "Read-only preflight passed; review the complete normalized "
                        "graph and approval payload before the single Apply"
                    ),
                    "baseline": copy.deepcopy(baseline["public"]),
                    "capability": {
                        "source": "strict_live_houdini_read_facade",
                        "local_ledger_envelope": (
                            "b1_fake_only_local_approval_claim_envelope_not_live_evidence"
                        ),
                        "schema_digest": self._registry.manifest_digest,
                        "catalog_digest": catalog_digest,
                        "certified_type_count": len(catalog),
                        "strict_event_evidence": True,
                    },
                    "approval": {
                        "normalized_graph": copy.deepcopy(self._graph),
                        "canonical_graph_digest": self._graph_digest,
                        "side_effect_summary": graph_side_effect_summary(self._graph),
                        "approval_payload": copy.deepcopy(
                            presentation.approval_payload
                        ),
                        "approval_binding_digest": (
                            presentation.approval_binding_digest
                        ),
                        "request_digest": presentation.request_digest,
                        "target_path": _TARGET_PATH,
                        "deadline_seconds": int(_APPROVAL_SECONDS),
                        "one_apply_only": True,
                    },
                }
            )
            return self.report
        except B4BAcceptanceError as exc:
            return self._fail(exc.code, exc.message)
        except Exception:
            return self._fail(
                "HOUDINI_UNAVAILABLE",
                f"The read-only B4B preflight failed at {stage}",
            )

    def apply_once(self, *, confirmed: bool) -> dict[str, Any]:
        """Consume the one process Apply opportunity and execute exact approval."""

        claim: Any | None = None
        result: dict[str, Any] | None = None
        try:
            self._require_main_thread()
            if self._state != APPROVAL_PRESENTED:
                raise B4BAcceptanceError(
                    "INVALID_STATE", "The exact B4B approval is not active"
                )
            if not self._latch.consume():
                raise B4BAcceptanceError(
                    "APPLY_ALREADY_CONSUMED",
                    "This Houdini process already consumed its Gate B4B Apply",
                )
            if confirmed is not True:
                self._deny_pending_approval()
                raise B4BAcceptanceError(
                    "APPROVAL_DENIED", "The exact B4B approval was not confirmed"
                )

            self._state = APPLYING
            self._snapshot["state"] = self._state
            self._snapshot["message"] = "Applying the exact approved graph once"
            self._snapshot["manual_undo_required"] = False
            self._revalidate_preapply()
            queue, request, presentation, attestation, catalog, deadline = (
                self._prepared_objects()
            )
            queue.decide_approval(
                presentation.request_id,
                "allow",
                presentation.request_digest,
                request.launch_id,
                request.generation,
            )
            claim = queue.claim_next(0.0)
            if claim is None or claim.request_id != request.arguments["request_id"]:
                raise B4BAcceptanceError(
                    "APPROVAL_REQUIRED", "The approved request was not claimed exactly once"
                )

            claim_authority = _ExactClaimAuthority(request, claim)
            baseline = self._require_baseline()
            binding = _ApprovedWriteBinding.from_scene_queue(
                request,
                claim,
                attestation=attestation,
                catalog=catalog,
                obj_fingerprint=baseline["obj_fingerprint"],
                claim_authority=claim_authority,
            )
            writer = HoudiniWriteAdapter(
                self._hou,
                self._read,
                capability_attestation=attestation,
                capability_catalog=catalog,
                main_thread_id=self._main_thread_id,
                clock=self._clock,
                schema_registry=self._registry,
                control_guard=_LocalControlGuard(
                    clock=self._clock, deadline=deadline
                ),
                claim_authority=claim_authority,
                strict_event_evidence=True,
            )
            self._apply_was_attempted = True
            result = writer.apply_prevalidated(binding)
            completion = queue.complete(
                claim.request_id,
                claim.claim_token,
                result,
            )
            independent = self._verify_applied_graph(result)
            journal = self._safe_event_journal()
            self._apply_journal_sequence = _last_sequence(journal)
            self._apply_evidence_ok = bool(
                result.get("ok") is True and independent.get("ok") is True
            )
            root_retained = self._hou.node(_TARGET_PATH) is not None
            self._state = WAIT_MANUAL_UNDO if root_retained else FAILED
            self._snapshot.update(
                {
                    "ok": self._apply_evidence_ok,
                    "state": self._state,
                    "manual_undo_required": root_retained,
                    "message": (
                        "Apply verified; invoke Houdini Undo manually exactly once, "
                        "then run read-only verification"
                        if self._apply_evidence_ok
                        else (
                            "The one-shot Apply did not produce complete acceptance "
                            "evidence; invoke Houdini Undo manually once to remove the "
                            "retained approved root, then run cleanup verification"
                            if root_retained
                            else "The one-shot Apply did not produce complete acceptance evidence"
                        )
                    ),
                    "apply_result": {
                        "adapter_result": copy.deepcopy(result),
                        "queue_ledger": completion.to_dict(),
                        "independent_verification": independent,
                    },
                    "event_journal": journal,
                }
            )
            return self.report
        except WriteControlAbort as exc:
            if claim is not None and result is not None:
                self._complete_best_effort(claim, result)
            return self._fail_after_apply(
                "DEADLINE_EXCEEDED",
                "The one-shot Apply stopped at its guarded control boundary",
                result,
            )
        except B4BAcceptanceError as exc:
            if claim is not None and result is not None:
                self._complete_best_effort(claim, result)
            return self._fail_after_apply(exc.code, exc.message, result)
        except Exception:
            if claim is not None and result is not None:
                self._complete_best_effort(claim, result)
            return self._fail_after_apply(
                "SCENE_STATE_INDETERMINATE",
                "The one-shot Apply could not prove a safe terminal state",
                result,
            )

    def verify_manual_undo(self) -> dict[str, Any]:
        """Read-only proof after the user manually invokes Houdini Undo."""

        try:
            self._require_main_thread()
            if (
                self._state != WAIT_MANUAL_UNDO
                or self._snapshot.get("manual_undo_required") is not True
            ):
                raise B4BAcceptanceError(
                    "INVALID_STATE", "A verified Apply is not awaiting manual Undo"
                )
            baseline = self._require_baseline()
            report = self._read.refresh()
            obj = self._hou.node("/obj")
            root = self._hou.node(_TARGET_PATH)
            journal = self._safe_event_journal()
            delta_events = [
                event
                for event in journal
                if _event_sequence(event) > self._apply_journal_sequence
            ]
            deletion_events = [
                event
                for event in delta_events
                if event.get("event_type") in {"BeingDeleted", "ChildDeleted"}
            ]
            exact_root_deletion = any(
                event.get("event_type") == "ChildDeleted"
                and event.get("source_path") == "/obj"
                and event.get("child_path") == _TARGET_PATH
                for event in deletion_events
            )
            allowed_paths = {"/obj", *self._declared_paths}
            journal_scope_safe = bool(delta_events) and all(
                event.get("source_path") in allowed_paths
                and (
                    event.get("child_path") is None
                    or event.get("child_path") in self._declared_paths
                )
                and (
                    event.get("event_type") != "ChildDeleted"
                    or (
                        (
                            event.get("child_path") == _TARGET_PATH
                            and event.get("source_path") == "/obj"
                        )
                        or (
                            event.get("child_path") in self._declared_paths[1:]
                            and event.get("source_path") == _TARGET_PATH
                        )
                    )
                )
                for event in delta_events
            )

            if root is not None:
                verification = {
                    "ok": False,
                    "manual_undo_observed": False,
                    "reason": "The approved root still exists",
                }
                self._snapshot.update(
                    {
                        "ok": False,
                        "state": WAIT_MANUAL_UNDO,
                        "manual_undo_required": True,
                        "message": (
                            "The approved root still exists; invoke Houdini Undo "
                            "manually once before verifying again"
                        ),
                        "verification": verification,
                        "event_journal": journal,
                    }
                )
                return self.report

            selected, current = self._selection_snapshot()
            has_unsaved_changes = self._hou.hipFile.hasUnsavedChanges()
            proofs = {
                "same_hip_session": (
                    report.get("hip_session_id") == baseline["hip_session_id"]
                ),
                "same_obj_identity": _same_houdini_node(obj, baseline["obj"]),
                "same_obj_children": _same_identity_members(
                    tuple(obj.children()) if obj is not None else (),
                    baseline["obj_children"],
                ),
                "same_obj_fingerprint": bool(
                    obj is not None
                    and HoudiniWriteAdapter._obj_fingerprint(obj)
                    == baseline["obj_fingerprint"]
                ),
                "same_selection": _same_identity_members(
                    selected, baseline["selected"]
                ),
                "same_current": _same_houdini_node(current, baseline["current"]),
                "new_hip": self._hou.hipFile.isNewFile() is True,
                "root_absent": root is None,
                "all_declared_paths_absent": all(
                    self._hou.node(path) is None for path in self._declared_paths
                ),
                "exact_root_deletion_observed": exact_root_deletion,
                "deletion_journal_scope_safe": journal_scope_safe,
                "read_capability_available": report.get("available") is True,
            }
            # A prior strict-evidence failure intentionally latches the live
            # capability unavailable.  That prevents acceptance success, but
            # it must not hide a separately proven manual cleanup: exact HOM
            # identities, blank baseline, and the bounded deletion journal are
            # still sufficient to report that the retained scope was removed.
            cleanup_ok = all(
                value
                for name, value in proofs.items()
                if name != "read_capability_available"
            )
            acceptance_ok = bool(
                cleanup_ok
                and self._apply_evidence_ok
                and proofs["read_capability_available"]
            )
            self._state = VERIFIED if acceptance_ok else FAILED
            self._snapshot.update(
                {
                    "ok": acceptance_ok,
                    "state": self._state,
                    "manual_undo_required": False,
                    "message": (
                        "Manual Undo verified; the blank unsaved HIP baseline is restored"
                        if acceptance_ok
                        else (
                            "Manual cleanup restored the blank baseline, but the Apply "
                            "acceptance evidence was incomplete"
                            if cleanup_ok
                            else "Manual Undo occurred, but the complete baseline proof failed"
                        )
                    ),
                    "verification": {
                        "ok": acceptance_ok,
                        "cleanup_restored": cleanup_ok,
                        "apply_acceptance_evidence": self._apply_evidence_ok,
                        "manual_undo_observed": exact_root_deletion,
                        "has_unsaved_changes": has_unsaved_changes,
                        "proofs": proofs,
                        "deletion_event_count": len(deletion_events),
                        "journal_delta_count": len(delta_events),
                    },
                    "event_journal": journal,
                }
            )
            return self.report
        except B4BAcceptanceError as exc:
            return self._fail(exc.code, exc.message)
        except Exception:
            return self._fail(
                "HOUDINI_UNAVAILABLE",
                "The manual Undo baseline could not be proven by bounded reads",
            )

    def _capture_blank_baseline(self, report: Mapping[str, Any]) -> dict[str, Any]:
        stage = "report"
        try:
            self._validate_capability_report(report)
            if report.get("available") is not True:
                raise B4BAcceptanceError(
                    "HOUDINI_UNAVAILABLE", "Strict live Houdini reads are unavailable"
                )
            stage = "build"
            build = self._hou.applicationVersionString()
            if not isinstance(build, str) or not build or build != report["houdini_build"]:
                raise B4BAcceptanceError(
                    "CAPABILITY_MISMATCH", "The live Houdini build does not match"
                )
            stage = "hip_state"
            if not self._hip_is_new_and_clean():
                raise B4BAcceptanceError(
                    "SCENE_CONFLICT", "Gate B4B requires a new, clean, unsaved HIP"
                )
            stage = "obj_lookup"
            obj = self._hou.node("/obj")
            if obj is None or self._hou.node(_TARGET_PATH) is not None:
                raise B4BAcceptanceError(
                    "SCENE_CONFLICT", "The blank /obj baseline is unavailable"
                )
            stage = "obj_children"
            children = tuple(obj.children())
            if children:
                raise B4BAcceptanceError(
                    "SCENE_CONFLICT", "Gate B4B requires an empty /obj context"
                )
            stage = "selection"
            selected, current = self._selection_snapshot()
            if selected:
                raise B4BAcceptanceError(
                    "SCENE_CONFLICT", "Gate B4B requires an empty node selection"
                )
            stage = "obj_fingerprint"
            obj_fingerprint = HoudiniWriteAdapter._obj_fingerprint(obj)
            stage = "catalog_digest"
            catalog_digest = canonical_json_sha256(report["catalog"])
        except B4BAcceptanceError:
            raise
        except Exception as exc:
            raise B4BAcceptanceError(
                "HOUDINI_UNAVAILABLE",
                f"The read-only blank baseline failed at {stage}",
            ) from exc
        return {
            "obj": obj,
            "obj_children": children,
            "selected": selected,
            "current": current,
            "obj_fingerprint": obj_fingerprint,
            "hip_session_id": report["hip_session_id"],
            "hip_fingerprint": report["hip_fingerprint"],
            "scene_revision": report["scene_revision"],
            "catalog_digest": catalog_digest,
            "public": {
                "houdini_build": report["houdini_build"],
                "hip_session_id": report["hip_session_id"],
                "scene_revision": report["scene_revision"],
                "hip_fingerprint": report["hip_fingerprint"],
                "obj_fingerprint": obj_fingerprint,
                "obj_child_count": 0,
                "new_file": True,
                "dirty": False,
                "selection": [],
                "current": _safe_node_path(current),
            },
        }

    def _revalidate_preapply(self) -> None:
        baseline = self._require_baseline()
        report = self._read.refresh()
        self._validate_capability_report(report)
        obj = self._hou.node("/obj")
        selected, current = self._selection_snapshot()
        if (
            report.get("available") is not True
            or report.get("hip_session_id") != baseline["hip_session_id"]
            or report.get("hip_fingerprint") != baseline["hip_fingerprint"]
            or report.get("scene_revision") != baseline["scene_revision"]
            or canonical_json_sha256(report.get("catalog"))
            != baseline["catalog_digest"]
            or not _same_houdini_node(obj, baseline["obj"])
            or not _same_identity_members(
                tuple(obj.children()) if obj is not None else (),
                baseline["obj_children"],
            )
            or obj is None
            or HoudiniWriteAdapter._obj_fingerprint(obj)
            != baseline["obj_fingerprint"]
            or self._hou.node(_TARGET_PATH) is not None
            or not _same_identity_members(selected, baseline["selected"])
            or not _same_houdini_node(current, baseline["current"])
            or not self._hip_is_new_and_clean()
        ):
            self._deny_pending_approval()
            raise B4BAcceptanceError(
                "SCENE_CONFLICT", "The exact approved baseline changed before Apply"
            )

    def _verify_applied_graph(self, result: Mapping[str, Any]) -> dict[str, Any]:
        baseline = self._require_baseline()
        report = self._read.capability_report()
        self._validate_capability_report(report)
        obj = self._hou.node("/obj")
        root = self._hou.node(_TARGET_PATH)
        selected, current = self._selection_snapshot()
        failures: list[str] = []
        if result.get("ok") is not True:
            failures.append("adapter_result")
        if (
            report.get("available") is not True
            or report.get("hip_session_id") != baseline["hip_session_id"]
            or report.get("scene_revision") != baseline["scene_revision"] + 1
            or report.get("hip_fingerprint") == baseline["hip_fingerprint"]
            or canonical_json_sha256(report.get("catalog"))
            != baseline["catalog_digest"]
        ):
            failures.append("capability")
        if (
            not _same_houdini_node(obj, baseline["obj"])
            or root is None
            or not _same_houdini_node(self._hou.node(_TARGET_PATH), root)
            or not _same_identity_members(tuple(obj.children()), (root,))
        ):
            failures.append("root_identity")
        if root is not None:
            if (
                not _same_houdini_node(root.parent(), obj)
                or root.name() != self._graph["target"]["name_hint"]
                or root.path() != _TARGET_PATH
                or root.type().name() != "geo"
                or root.userData(_OWNERSHIP_KEY) != "hia_owned"
                or root.userData(_GRAPH_DIGEST_KEY) != self._graph_digest
                or not isinstance(root.userData(_TRANSACTION_KEY), str)
                or not root.userData(_TRANSACTION_KEY)
                or tuple(root.errors())
            ):
                failures.append("root_metadata")
            try:
                self._verify_children_and_connections(root)
            except B4BAcceptanceError:
                failures.append("graph_observation")
            if root.inputConnections() or root.outputConnections():
                failures.append("root_connections")
        if (
            not _same_identity_members(selected, baseline["selected"])
            or not _same_houdini_node(current, baseline["current"])
        ):
            failures.append("selection")
        if self._hou.hipFile.isNewFile() is not True:
            failures.append("new_file")
        if self._hou.hipFile.hasUnsavedChanges() is not True:
            failures.append("dirty")

        evidence = self._read.last_owned_evidence()
        expected_operations = self._expected_mutation_operations()
        observed_mutations = (
            evidence.get("mutations", []) if isinstance(evidence, Mapping) else []
        )
        observed_operations = [
            mutation.get("operation")
            for mutation in observed_mutations
            if isinstance(mutation, Mapping)
        ]
        observer_paths = {
            item.get("path")
            for item in (
                evidence.get("observer_installations", [])
                if isinstance(evidence, Mapping)
                else []
            )
            if isinstance(item, Mapping)
        }
        if (
            not isinstance(evidence, Mapping)
            or evidence.get("outcome") != "committed"
            or isinstance(evidence.get("event_count"), bool)
            or not isinstance(evidence.get("event_count"), int)
            or evidence.get("event_count", 0) <= 0
            or any(
                event.get("matched") is not True
                or event.get("main_thread") is not True
                for event in evidence.get("events", [])
                if isinstance(event, Mapping)
            )
            or len(observed_operations) != len(expected_operations)
            or set(observed_operations) != expected_operations
            or observer_paths != {"/obj", *self._declared_paths}
        ):
            failures.append("strict_event_evidence")
        return {
            "ok": not failures,
            "target_path": _TARGET_PATH,
            "expected_node_count": len(self._graph["nodes"]),
            "expected_connection_count": len(self._graph["connections"]),
            "failures": failures,
        }

    def _verify_children_and_connections(self, root: Any) -> None:
        children = tuple(root.children())
        if len(children) != len(self._graph["nodes"]):
            raise B4BAcceptanceError("POSTCONDITION_FAILED", "Child count changed")
        by_id: dict[str, Any] = {}
        for node in self._graph["nodes"]:
            path = f"{_TARGET_PATH}/{node['name_hint']}"
            live = self._hou.node(path)
            policy = _CERTIFIED_TYPES[(node["type"]["context"], node["type"]["name"])]
            if (
                live is None
                or not _same_houdini_node(live.parent(), root)
                or live.path() != path
                or live.name() != node["name_hint"]
                or live.type().name() != policy["resolved"]
                or sum(
                    _same_houdini_node(candidate, live) for candidate in children
                )
                != 1
                or tuple(live.errors())
            ):
                raise B4BAcceptanceError(
                    "POSTCONDITION_FAILED", "A declared child is not exact"
                )
            for assignment in node["parameters"]:
                observed = live.parmTuple(assignment["name"])
                if observed is None or tuple(observed.eval()) != tuple(
                    assignment["value"]["value"]
                ):
                    raise B4BAcceptanceError(
                        "POSTCONDITION_FAILED", "A typed parameter changed"
                    )
            if (
                live.isDisplayFlagSet() is not node["flags"]["display"]
                or live.isRenderFlagSet() is not node["flags"]["render"]
            ):
                raise B4BAcceptanceError(
                    "POSTCONDITION_FAILED", "A declared flag changed"
                )
            by_id[node["id"]] = live

        expected = {
            (
                item["source"]["node"],
                item["source"]["output"],
                item["destination"]["node"],
                item["destination"]["input"],
            )
            for item in self._graph["connections"]
        }
        inputs: set[tuple[str, int, str, int]] = set()
        outputs: set[tuple[str, int, str, int]] = set()
        reverse = tuple((live, local_id) for local_id, live in by_id.items())
        for local_id, live in by_id.items():
            for connection in live.inputConnections():
                source_id = _identity_key(reverse, connection.inputNode())
                destination_id = _identity_key(reverse, connection.outputNode())
                if (
                    source_id is None
                    or destination_id is None
                    or not _same_houdini_node(connection.outputNode(), live)
                ):
                    raise B4BAcceptanceError(
                        "POSTCONDITION_FAILED", "A connection escaped owned scope"
                    )
                inputs.add(
                    (
                        source_id,
                        connection.outputIndex(),
                        destination_id,
                        connection.inputIndex(),
                    )
                )
            for connection in live.outputConnections():
                source_id = _identity_key(reverse, connection.inputNode())
                destination_id = _identity_key(reverse, connection.outputNode())
                if (
                    source_id is None
                    or destination_id is None
                    or not _same_houdini_node(connection.inputNode(), live)
                ):
                    raise B4BAcceptanceError(
                        "POSTCONDITION_FAILED", "A connection escaped owned scope"
                    )
                outputs.add(
                    (
                        source_id,
                        connection.outputIndex(),
                        destination_id,
                        connection.inputIndex(),
                    )
                )
        if inputs != expected or outputs != expected:
            raise B4BAcceptanceError(
                "POSTCONDITION_FAILED", "Observed connections differ from approval"
            )

    def _expected_mutation_operations(self) -> set[str]:
        operations = {
            "create_root:root",
            f"set_user_data:{_OWNERSHIP_KEY}",
            f"set_user_data:{_TRANSACTION_KEY}",
            f"set_user_data:{_GRAPH_DIGEST_KEY}",
        }
        for node in self._graph["nodes"]:
            operations.add(f"create_node:{node['id']}")
            operations.add(f"set_flag:{node['id']}:display")
            operations.add(f"set_flag:{node['id']}:render")
            for assignment in node["parameters"]:
                operations.add(
                    f"set_parameter:{node['id']}:{assignment['name']}"
                )
        for connection in self._graph["connections"]:
            destination = connection["destination"]
            operations.add(
                f"connect:{destination['node']}:{destination['input']}"
            )
        return operations

    def _selection_snapshot(self) -> tuple[tuple[Any, ...], Any | None]:
        try:
            selected = tuple(self._hou.selectedNodes(include_hidden=True))
        except TypeError:
            selected = tuple(self._hou.selectedNodes(True))
        try:
            current = self._hou.pwd()
        except Exception:
            desktop = self._hou.ui.curDesktop()
            editor = desktop.paneTabOfType(self._hou.paneTabType.NetworkEditor)
            if editor is None:
                raise B4BAcceptanceError(
                    "HOUDINI_UNAVAILABLE",
                    "The current Network Editor context is unavailable",
                )
            current = editor.pwd()
        if current is not None:
            _safe_node_path(current)
        return selected, current

    def _hip_is_new_and_clean(self) -> bool:
        return bool(
            self._hou.hipFile.isNewFile() is True
            and self._hou.hipFile.hasUnsavedChanges() is False
        )

    def _safe_event_journal(self) -> list[dict[str, Any]]:
        value = self._read.event_journal_snapshot()
        if not isinstance(value, Sequence) or len(value) > _MAX_EVENT_JOURNAL:
            raise B4BAcceptanceError(
                "HOUDINI_UNAVAILABLE", "The strict event journal is unavailable"
            )
        result: list[dict[str, Any]] = []
        for item in value:
            if not isinstance(item, Mapping):
                raise B4BAcceptanceError(
                    "HOUDINI_UNAVAILABLE", "The strict event journal is invalid"
                )
            result.append(copy.deepcopy(dict(item)))
        return result

    def _validate_capability_report(self, report: Mapping[str, Any]) -> None:
        session = report.get("hip_session_id")
        fingerprint = report.get("hip_fingerprint")
        revision = report.get("scene_revision")
        catalog = report.get("catalog")
        if (
            not isinstance(session, str)
            or not session
            or not _is_sha256(fingerprint)
            or isinstance(revision, bool)
            or not isinstance(revision, int)
            or revision < 0
            or not isinstance(catalog, list)
            or report.get("session_observer_reliable") is not True
            or report.get("revision_observer_reliable") is not True
        ):
            raise B4BAcceptanceError(
                "CAPABILITY_MISMATCH", "The strict live capability is invalid"
            )

    def _prepared_objects(self) -> tuple[Any, Any, Any, Any, Any, float]:
        if (
            self._queue is None
            or self._request is None
            or self._presentation is None
            or self._attestation is None
            or self._catalog is None
            or self._deadline is None
        ):
            raise B4BAcceptanceError(
                "INVALID_STATE", "The exact approval state is incomplete"
            )
        return (
            self._queue,
            self._request,
            self._presentation,
            self._attestation,
            copy.deepcopy(self._catalog),
            self._deadline,
        )

    def _require_baseline(self) -> dict[str, Any]:
        if self._baseline_private is None:
            raise B4BAcceptanceError(
                "INVALID_STATE", "The read-only baseline is unavailable"
            )
        return self._baseline_private

    def _deny_pending_approval(self) -> None:
        if self._queue is None or self._request is None or self._presentation is None:
            return
        try:
            self._queue.decide_approval(
                self._presentation.request_id,
                "deny",
                self._presentation.request_digest,
                self._request.launch_id,
                self._request.generation,
            )
        except Exception:
            return

    def _complete_best_effort(self, claim: Any, result: Mapping[str, Any]) -> None:
        if self._queue is None:
            return
        try:
            self._queue.complete(claim.request_id, claim.claim_token, result)
        except Exception:
            return

    def _require_main_thread(self) -> None:
        if threading.get_ident() != self._main_thread_id:
            raise B4BAcceptanceError(
                "MAIN_THREAD_REQUIRED", "Gate B4B requires the Houdini UI main thread"
            )

    def _fail_after_apply(
        self,
        code: str,
        message: str,
        result: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        root_retained = bool(
            self._apply_was_attempted and self._hou.node(_TARGET_PATH) is not None
        )
        if not root_retained:
            return self._fail(code, message)
        self._state = WAIT_MANUAL_UNDO
        journal: list[dict[str, Any]] = []
        try:
            journal = self._safe_event_journal()
            self._apply_journal_sequence = _last_sequence(journal)
        except Exception:
            pass
        self._snapshot.update(
            {
                "ok": False,
                "state": WAIT_MANUAL_UNDO,
                "manual_undo_required": True,
                "message": (
                    f"{message}. A retained approved root exists; invoke Houdini "
                    "Undo manually once, then run read-only cleanup verification"
                ),
                "apply_result": {
                    "adapter_result": copy.deepcopy(result),
                    "acceptance_evidence_complete": False,
                },
                "verification": {
                    "ok": False,
                    "structured_error": {
                        "code": code,
                        "message": message,
                        "details": [],
                    },
                },
                "event_journal": journal,
            }
        )
        return self.report

    def _fail(self, code: str, message: str) -> dict[str, Any]:
        self._state = FAILED
        self._snapshot.update(
            {
                "ok": False,
                "state": FAILED,
                "manual_undo_required": False,
                "message": message,
                "verification": {
                    "ok": False,
                    "structured_error": {
                        "code": code,
                        "message": message,
                        "details": [],
                    },
                },
            }
        )
        try:
            self._snapshot["event_journal"] = self._safe_event_journal()
        except Exception:
            pass
        return self.report


def _finite_clock_value(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise B4BAcceptanceError(
            "HOUDINI_UNAVAILABLE", "The local transaction clock is invalid"
        )
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise B4BAcceptanceError(
            "HOUDINI_UNAVAILABLE", "The local transaction clock is invalid"
        )
    return result


def _same_identity_members(observed: tuple[Any, ...], expected: tuple[Any, ...]) -> bool:
    return len(observed) == len(expected) and all(
        sum(_same_houdini_node(candidate, item) for candidate in observed) == 1
        for item in expected
    )


def _identity_key(pairs: tuple[tuple[Any, str], ...], value: Any) -> str | None:
    matches = [
        local_id
        for candidate, local_id in pairs
        if _same_houdini_node(candidate, value)
    ]
    return matches[0] if len(matches) == 1 else None


def _safe_node_path(node: Any | None) -> str | None:
    if node is None:
        return None
    value = node.path()
    if (
        not isinstance(value, str)
        or not value.startswith("/")
        or len(value) > 256
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise B4BAcceptanceError(
            "CAPABILITY_MISMATCH", "The current Houdini node path is invalid"
        )
    return value


def _is_sha256(value: Any) -> bool:
    return bool(
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdefABCDEF" for character in value)
    )


def _event_sequence(event: Mapping[str, Any]) -> int:
    value = event.get("sequence")
    return value if type(value) is int and value >= 0 else -1


def _last_sequence(events: Sequence[Mapping[str, Any]]) -> int:
    return max((_event_sequence(event) for event in events), default=0)


__all__ = ["B4BAcceptanceController"]
