"""Fail-closed, main-thread-only Houdini reads for Gate B2.

The module deliberately does not import Houdini or Qt.  The Python Panel is
the only boundary allowed to inject the live HOM module.  Tests inject a small
fake with the same read-only surface.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import math
import platform
import re
import secrets
import threading
import time
import uuid
from collections.abc import Mapping, Sequence
from typing import Any


_READ_TOOLS = frozenset({"houdini_scene_info", "houdini_node_type_info"})
_DISABLED_TOOLS = frozenset(
    {"houdini_graph_validate", "houdini_graph_apply", "houdini_graph_verify"}
)
_ALLOWED_TYPES = (
    ("Object", "geo"),
    ("Sop", "box"),
    ("Sop", "transform"),
    ("Sop", "merge"),
    ("Sop", "null"),
)
_LIVE_TYPE_NAMES = {
    ("Object", "geo"): "geo",
    ("Sop", "box"): "box",
    ("Sop", "transform"): "xform",
    ("Sop", "merge"): "merge",
    ("Sop", "null"): "null",
}
_SAFE_PARAMETERS: dict[tuple[str, str], dict[str, tuple[str, int]]] = {
    ("Object", "geo"): {},
    ("Sop", "box"): {"size": ("float", 3), "t": ("float", 3)},
    ("Sop", "transform"): {"t": ("float", 3)},
    ("Sop", "merge"): {},
    ("Sop", "null"): {},
}
_REQUIRED_NODE_EVENT_NAMES = (
    "BeingDeleted",
    "FlagChanged",
    "NameChanged",
    "AppearanceChanged",
    "PositionChanged",
    "InputRewired",
    "ParmTupleChanged",
    "ParmTupleAnimated",
    "ParmTupleChannelChanged",
    "ParmTupleLockChanged",
    "ChildCreated",
    "ChildDeleted",
    "ChildReordered",
    "ChildSwitched",
    "NetworkBoxCreated",
    "NetworkBoxChanged",
    "NetworkBoxDeleted",
    "StickyNoteCreated",
    "StickyNoteChanged",
    "StickyNoteDeleted",
    "IndirectInputCreated",
    "IndirectInputRewired",
    "IndirectInputDeleted",
    "SpareParmTemplatesChanged",
    "CustomDataChanged",
)
_GRAPH_NAME = re.compile(r"^HIA_Graph_[A-Za-z0-9][A-Za-z0-9_]{0,63}$")
_DIGEST = re.compile(r"^[A-Fa-f0-9]{64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_MAX_OBSERVED_NODES = 4096
_MAX_TEMPLATE_ENTRIES = 4096
_MAX_GRAPH_SUMMARIES = 128
_MAX_GRAPH_NODES = 128
_MAX_GRAPH_CONNECTIONS = 256
_MAX_PARAMETERS = 512
_MAX_INPUTS = 65535
_MAX_OUTPUTS = 64
_MAX_SESSION_ID = 9_007_199_254_740_991
_MAX_BUILD_LENGTH = 128
_MAX_VERSION_LENGTH = 128
_MAX_EVENT_JOURNAL = 512
_MAX_EVENT_OPERATION_LENGTH = 128
_MAX_EVENT_PATH_LENGTH = 256
_HOUDINI_BUILD = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]{0,127}$")
_EVENT_OPERATION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$")
_SILENT_READBACK_OPERATIONS = frozenset(
    {
        "set_user_data:hia_ownership",
        "set_user_data:hia_transaction_id",
        "set_user_data:hia_graph_digest",
    }
)


class HoudiniReadAdapterError(RuntimeError):
    """A structured local failure that never includes HOM data or tracebacks."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "details": [],
        }


class HoudiniReadAdapter:
    """Read a bounded live capability slice without ever mutating the scene."""

    def __init__(
        self,
        hou_module: Any,
        *,
        publisher_id: str | None = None,
        python_version: str | None = None,
        pyside_version: str,
        main_thread_id: int | None = None,
        fingerprint_key: bytes | None = None,
        clock: Any = time.monotonic,
        strict_event_evidence: bool = False,
    ) -> None:
        # Construction stores the injected object but intentionally performs no
        # attribute access on it.  This keeps construction safe for test and
        # worker-thread misuse; start() is the first HOM boundary.
        self._hou = hou_module
        self._main_thread_id = (
            threading.get_ident() if main_thread_id is None else int(main_thread_id)
        )
        self._publisher_id = publisher_id or f"hia-panel-{uuid.uuid4().hex}"
        if _IDENTIFIER.fullmatch(self._publisher_id) is None:
            raise ValueError("publisher_id must be a bounded protocol identifier")
        self._python_version = _bounded_plain_text(
            python_version or platform.python_version(),
            _MAX_VERSION_LENGTH,
            "python version",
        )
        self._pyside_version = _bounded_plain_text(
            pyside_version,
            _MAX_VERSION_LENGTH,
            "PySide version",
        )
        key = fingerprint_key or secrets.token_bytes(32)
        if not isinstance(key, bytes) or len(key) < 16:
            raise ValueError("fingerprint_key must contain at least 16 bytes")
        self._fingerprint_key = bytes(key)
        self._clock = clock
        self._strict_event_evidence = bool(strict_event_evidence)
        self._state_lock = threading.RLock()

        self._started = False
        self._disposed = False
        self._houdini_build = "unknown"
        self._build_valid = False
        self._hip_session_id = f"hip-{uuid.uuid4().hex}"
        self._scene_revision = 0
        self._observer_sequence = 0
        self._session_observer_reliable = False
        self._revision_observer_reliable = False
        self._observer_violation = False
        self._catalog_valid = False
        self._catalog: list[dict[str, Any]] = _unavailable_catalog()
        self._hip_callback_installed = False
        self._lifecycle_events: tuple[Any, ...] = ()
        self._hip_revision_events: tuple[Any, ...] = ()
        self._node_event_types: tuple[Any, ...] = ()
        self._observed_nodes: dict[str, tuple[int, Any]] = {}
        self._last_refresh = 0.0
        # Gate B4A may explicitly arm this dormant revision coordinator around
        # one already-authorized owned write.  No production path constructs or
        # calls it, and its opaque token is deliberately absent from reports.
        self._owned_write: dict[str, Any] | None = None
        # Strict B4B evidence is opt-in.  The default B2/B4A read behavior and
        # capability-report schema remain unchanged.
        self._event_journal: list[dict[str, Any]] = []
        self._event_journal_sequence = 0
        self._last_owned_evidence: dict[str, Any] | None = None

    @property
    def main_thread_id(self) -> int:
        return self._main_thread_id

    @property
    def publisher_id(self) -> str:
        return self._publisher_id

    @property
    def strict_event_evidence(self) -> bool:
        return self._strict_event_evidence

    def event_journal_snapshot(self) -> tuple[dict[str, Any], ...]:
        """Return a bounded, content-safe copy of strict observer evidence."""

        with self._state_lock:
            return tuple(copy.deepcopy(self._event_journal))

    def last_owned_evidence(self) -> dict[str, Any] | None:
        """Return the last completed strict owned-write evidence bundle."""

        with self._state_lock:
            return copy.deepcopy(self._last_owned_evidence)

    def begin_owned_write(
        self,
        transaction_id: str,
        *,
        expected_hip_session_id: str,
        expected_scene_revision: int,
        expected_hip_fingerprint: str,
    ) -> object:
        """Arm callback coalescing for one internal, already-authorized write.

        This method grants no write authority and performs no HOM mutation.  A
        caller must retain the returned object and present that exact object to
        :meth:`finish_owned_write`; request data can never recreate the token.
        """

        self._assert_main_thread()
        if not isinstance(transaction_id, str) or _IDENTIFIER.fullmatch(
            transaction_id
        ) is None:
            raise HoudiniReadAdapterError(
                "INVALID_ARGUMENT",
                "The owned write transaction ID is invalid",
            )
        if (
            not isinstance(expected_hip_session_id, str)
            or _IDENTIFIER.fullmatch(expected_hip_session_id) is None
            or not isinstance(expected_hip_fingerprint, str)
            or _DIGEST.fullmatch(expected_hip_fingerprint) is None
            or isinstance(expected_scene_revision, bool)
            or not isinstance(expected_scene_revision, int)
            or not 0 <= expected_scene_revision < _MAX_SESSION_ID
        ):
            raise HoudiniReadAdapterError(
                "INVALID_ARGUMENT",
                "The expected owned write snapshot is invalid",
            )

        with self._state_lock:
            if self._owned_write is not None:
                raise HoudiniReadAdapterError(
                    "SCENE_CONFLICT",
                    "Another owned write revision transaction is active",
                )
            if (
                not self._started
                or self._disposed
                or self._observer_violation
                or not self._session_observer_reliable
                or not self._revision_observer_reliable
                or not self._build_valid
                or not self._catalog_valid
            ):
                raise HoudiniReadAdapterError(
                    "HOUDINI_UNAVAILABLE",
                    "Reliable Houdini observation is unavailable for an owned write",
                )
            if expected_hip_session_id != self._hip_session_id:
                raise HoudiniReadAdapterError(
                    "HIP_SESSION_MISMATCH",
                    "The expected HIP session is no longer current",
                )
            if expected_scene_revision != self._scene_revision:
                raise HoudiniReadAdapterError(
                    "SCENE_CONFLICT",
                    "The expected scene revision is no longer current",
                )
            if not hmac.compare_digest(
                expected_hip_fingerprint, self._hip_fingerprint()
            ):
                raise HoudiniReadAdapterError(
                    "SCENE_CONFLICT",
                    "The expected HIP fingerprint is no longer current",
                )

            token = object()
            self._owned_write = {
                "token": token,
                "transaction_id": transaction_id,
                "hip_session_id": self._hip_session_id,
                "base_scene_revision": self._scene_revision,
                "base_observer_sequence": self._observer_sequence,
                "pending_node_events": 0,
                "mutation_expectation": None,
                "invalidated": False,
                "strict_event_evidence": self._strict_event_evidence,
                "events": [],
                "mutations": [],
                "observer_installations": [
                    self._strict_node_identity(node)
                    for _path, (_session_id, node) in sorted(
                        self._observed_nodes.items()
                    )
                ]
                if self._strict_event_evidence
                else [],
            }
            return token

    def begin_owned_mutation(
        self,
        token: object,
        *,
        expected_callback_source: object | None = None,
        operation: str | None = None,
        event_source_rules: Mapping[str, Sequence[object]] | None = None,
        allowed_child_subjects: Sequence[object] | None = None,
        required_event_types: Sequence[str] | None = None,
        allow_zero_events: bool = False,
    ) -> object:
        """Expect callbacks from exactly one internal HOM mutator source.

        The expectation is deliberately underlying-node based: a callback may
        use a different Python HOM wrapper, but it must compare equal and have
        the same session ID and path as ``expected_callback_source``.  A
        missing, different, late, or otherwise unmarked callback remains an
        external scene observation and invalidates the write.  This method
        grants no mutation or approval authority.
        """

        self._assert_main_thread()
        if expected_callback_source is None and event_source_rules is None:
            raise HoudiniReadAdapterError(
                "INVALID_ARGUMENT",
                "The owned mutation callback source is invalid",
            )

        with self._state_lock:
            transaction = self._owned_write
            if transaction is None or token is not transaction["token"]:
                raise HoudiniReadAdapterError(
                    "INVALID_ARGUMENT",
                    "The owned write token is invalid",
                )
            if transaction["invalidated"]:
                raise HoudiniReadAdapterError(
                    "SCENE_CONFLICT",
                    "The owned write was invalidated by an external scene change",
                )
            if transaction["mutation_expectation"] is not None:
                raise HoudiniReadAdapterError(
                    "SCENE_CONFLICT",
                    "Another owned mutation callback expectation is active",
                )

            expectation_token = object()
            expectation: dict[str, Any] = {
                "token": expectation_token,
                "callback_source": expected_callback_source,
                "event_count": 0,
            }
            if transaction["strict_event_evidence"]:
                expectation.update(
                    self._strict_mutation_expectation(
                        operation=operation,
                        event_source_rules=event_source_rules,
                        allowed_child_subjects=allowed_child_subjects,
                        required_event_types=required_event_types,
                        allow_zero_events=allow_zero_events,
                    )
                )
            transaction["mutation_expectation"] = expectation
            return expectation_token

    def finish_owned_mutation(
        self,
        token: object,
        expectation_token: object,
        *,
        expected_child_subjects: Sequence[object] | None = None,
        require_all_child_subjects: bool = False,
        exact_readback_proven: bool = False,
    ) -> int:
        """Close one exact callback expectation and return its event count."""

        self._assert_main_thread()
        if type(exact_readback_proven) is not bool:
            raise HoudiniReadAdapterError(
                "INVALID_ARGUMENT", "The exact mutation readback proof is invalid"
            )
        failure: tuple[str, str] | None = None
        with self._state_lock:
            transaction = self._owned_write
            if transaction is None or token is not transaction["token"]:
                raise HoudiniReadAdapterError(
                    "INVALID_ARGUMENT",
                    "The owned write token is invalid",
                )
            expectation = transaction["mutation_expectation"]
            if expectation is None or expectation_token is not expectation["token"]:
                raise HoudiniReadAdapterError(
                    "INVALID_ARGUMENT",
                    "The owned mutation expectation token is invalid",
                )

            event_count = expectation["event_count"]
            transaction["mutation_expectation"] = None
            if transaction["strict_event_evidence"]:
                strict_failure = self._validate_strict_mutation_evidence(
                    expectation,
                    expected_child_subjects=expected_child_subjects,
                    require_all_child_subjects=require_all_child_subjects,
                    exact_readback_proven=exact_readback_proven,
                )
                mutation_record = {
                    "operation": expectation["operation"],
                    "event_count": event_count,
                    "event_types": sorted(expectation["seen_event_types"]),
                    "no_op": False,
                }
                if expectation.get("allow_zero_events") is True:
                    mutation_record["exact_readback_proven"] = exact_readback_proven
                transaction["mutations"].append(mutation_record)
                if strict_failure is not None:
                    transaction["invalidated"] = True
                    failure = strict_failure
            if transaction["invalidated"]:
                failure = (
                    "SCENE_CONFLICT",
                    (
                        "The owned mutation event evidence is incomplete or unsafe"
                        if transaction["strict_event_evidence"]
                        else "The owned mutation observed an external scene change"
                    ),
                )

        if failure is not None:
            raise HoudiniReadAdapterError(*failure)
        return event_count

    def install_owned_node_observer(
        self,
        token: object,
        node: object,
    ) -> dict[str, Any]:
        """Install and read back one strict observer on an exact new node.

        This is an observer-only HOM boundary.  It grants no write authority
        and is available only while an explicitly strict owned write is active.
        """

        self._assert_main_thread()
        with self._state_lock:
            transaction = self._owned_write
            if (
                transaction is None
                or token is not transaction["token"]
                or not transaction["strict_event_evidence"]
                or transaction["invalidated"]
                or transaction["mutation_expectation"] is not None
            ):
                raise HoudiniReadAdapterError(
                    "SCENE_CONFLICT",
                    "The strict owned observer cannot be installed",
                )
        identity = self._strict_node_identity(node)
        path = identity["path"]
        session_id = identity["session_id"]
        if not _same_houdini_node(self._hou.node(path), node):
            raise HoudiniReadAdapterError(
                "CAPABILITY_MISMATCH",
                "The strict observer target is not the exact registered node",
            )
        previous = self._observed_nodes.get(path)
        if previous is not None and (
            previous[0] != session_id
            or not _same_houdini_node(previous[1], node)
        ):
            raise HoudiniReadAdapterError(
                "CAPABILITY_MISMATCH",
                "The strict observer target conflicts with an observed identity",
            )
        try:
            if not self._callback_registration_matches(node):
                node.addEventCallback(self._node_event_types, self._on_node_event)
            if not self._callback_registration_matches(node):
                raise RuntimeError("observer callback readback mismatch")
        except Exception as exc:
            with self._state_lock:
                transaction = self._owned_write
                if transaction is not None and token is transaction["token"]:
                    transaction["invalidated"] = True
                self._revision_observer_reliable = False
            raise HoudiniReadAdapterError(
                "HOUDINI_UNAVAILABLE",
                "The strict observer could not be installed and verified",
            ) from exc
        self._observed_nodes[path] = (session_id, node)
        safe = self._safe_observer_identity(node)
        with self._state_lock:
            transaction = self._owned_write
            if transaction is None or token is not transaction["token"]:
                raise HoudiniReadAdapterError(
                    "SCENE_CONFLICT", "The owned write ended during observer setup"
                )
            transaction["observer_installations"].append(safe)
        return copy.deepcopy(safe)

    def record_owned_noop(
        self,
        token: object,
        *,
        operation: str,
    ) -> None:
        """Record one observed strict no-op without claiming callback evidence."""

        self._assert_main_thread()
        normalized = self._validate_event_operation(operation)
        with self._state_lock:
            transaction = self._owned_write
            if (
                transaction is None
                or token is not transaction["token"]
                or not transaction["strict_event_evidence"]
                or transaction["invalidated"]
                or transaction["mutation_expectation"] is not None
            ):
                raise HoudiniReadAdapterError(
                    "SCENE_CONFLICT", "The strict no-op evidence is invalid"
                )
            transaction["mutations"].append(
                {
                    "operation": normalized,
                    "event_count": 0,
                    "event_types": [],
                    "no_op": True,
                }
            )

    def finish_owned_write(
        self,
        token: object,
        *,
        outcome: str,
    ) -> dict[str, Any]:
        """Finish one owned write and publish its single revision outcome."""

        self._assert_main_thread()
        if outcome not in {"committed", "rolled_back", "indeterminate"}:
            raise HoudiniReadAdapterError(
                "INVALID_ARGUMENT",
                "The owned write outcome is invalid",
            )

        failure: tuple[str, str] | None = None
        with self._state_lock:
            transaction = self._owned_write
            if transaction is None or token is not transaction["token"]:
                raise HoudiniReadAdapterError(
                    "INVALID_ARGUMENT",
                    "The owned write token is invalid",
                )

            base_session = transaction["hip_session_id"]
            base_revision = transaction["base_scene_revision"]
            base_sequence = transaction["base_observer_sequence"]
            if self._disposed or self._observer_violation:
                failure = (
                    "HOUDINI_UNAVAILABLE",
                    "Reliable Houdini observation was lost during the owned write",
                )
            elif self._hip_session_id != base_session:
                failure = (
                    "HIP_SESSION_MISMATCH",
                    "The HIP session changed during the owned write",
                )
            elif (
                transaction["invalidated"]
                or transaction["mutation_expectation"] is not None
                or self._scene_revision != base_revision
                or self._observer_sequence != base_sequence
                or not self._session_observer_reliable
                or not self._revision_observer_reliable
            ):
                failure = (
                    "SCENE_CONFLICT",
                    "The observed scene changed outside the owned write boundary",
                )
            else:
                pending_events = transaction["pending_node_events"]
                if outcome == "rolled_back":
                    self._scene_revision = base_revision
                    if pending_events:
                        self._observer_sequence += 1
                else:
                    self._scene_revision = base_revision + 1
                    self._observer_sequence += 1

            if transaction["strict_event_evidence"]:
                self._last_owned_evidence = {
                    "transaction_id": transaction["transaction_id"],
                    "outcome": outcome,
                    "event_count": len(transaction["events"]),
                    "events": copy.deepcopy(transaction["events"]),
                    "mutations": copy.deepcopy(transaction["mutations"]),
                    "observer_installations": copy.deepcopy(
                        transaction["observer_installations"]
                    ),
                    "invalidated": bool(transaction["invalidated"]),
                }

            # A token is single-use even when observation was invalidated.  In
            # that case the existing callback state remains authoritative; do
            # not overwrite it with the transaction's former base snapshot.
            self._owned_write = None

        if failure is not None:
            raise HoudiniReadAdapterError(*failure)
        return self.capability_report()

    def start(self) -> dict[str, Any]:
        """Install read observers and return the first immutable publication."""

        self._assert_main_thread()
        if self._disposed:
            raise HoudiniReadAdapterError(
                "HOUDINI_UNAVAILABLE", "The Houdini read adapter is disposed"
            )
        if self._started:
            return self.refresh()

        self._started = True
        try:
            build = _bounded_plain_text(
                self._hou.applicationVersionString(), _MAX_BUILD_LENGTH, "Houdini build"
            )
            if _HOUDINI_BUILD.fullmatch(build) is None:
                raise ValueError("Houdini build has an unsafe format")
            self._houdini_build = build
            self._build_valid = True
        except Exception:
            self._houdini_build = "unknown"
            self._build_valid = False

        self._session_observer_reliable = self._install_hip_observer()
        self._revision_observer_reliable = self._refresh_node_observers()
        catalog, valid = self._read_catalog()
        self._catalog = catalog
        self._catalog_valid = valid
        self._last_refresh = float(self._clock())
        self._advance_observer_sequence()
        return self.capability_report()

    def refresh(self) -> dict[str, Any]:
        """Reconcile observers and the five-type catalog on the UI main thread."""

        self._assert_main_thread()
        if self._disposed:
            raise HoudiniReadAdapterError(
                "HOUDINI_UNAVAILABLE", "The Houdini read adapter is disposed"
            )
        if not self._started:
            return self.start()

        # An observer callback delivered outside the Houdini UI thread means
        # this adapter can no longer prove that it saw every intervening scene
        # transition.  Do not let a later main-thread refresh silently restore
        # trust; a new Panel adapter/lease is required.
        with self._state_lock:
            if self._observer_violation:
                return self.capability_report()

        old_reliability = (
            self._session_observer_reliable,
            self._revision_observer_reliable,
        )
        old_catalog = self._catalog
        old_catalog_valid = self._catalog_valid

        if not self._hip_callback_installed:
            self._session_observer_reliable = self._install_hip_observer()
        self._revision_observer_reliable = self._refresh_node_observers()
        catalog, valid = self._read_catalog()
        self._catalog = catalog
        self._catalog_valid = valid
        self._last_refresh = float(self._clock())

        if (
            old_reliability
            != (
                self._session_observer_reliable,
                self._revision_observer_reliable,
            )
            or old_catalog_valid != valid
            or old_catalog != catalog
        ):
            self._advance_observer_sequence()
        return self.capability_report()

    def capability_report(self) -> dict[str, Any]:
        """Return only Panel-published fields; Bridge identity is never accepted here."""

        with self._state_lock:
            available = bool(
                self._started
                and not self._disposed
                and self._owned_write is None
                and self._session_observer_reliable
                and self._revision_observer_reliable
                and self._build_valid
                and self._catalog_valid
            )
            return {
                "available": available,
                "publisher_id": self._publisher_id,
                "houdini_build": self._houdini_build,
                "python_version": self._python_version,
                "pyside_version": self._pyside_version,
                "hip_session_id": self._hip_session_id,
                "hip_fingerprint": self._hip_fingerprint(),
                "scene_revision": self._scene_revision,
                "observer_sequence": self._observer_sequence,
                "session_observer_reliable": self._session_observer_reliable,
                "revision_observer_reliable": self._revision_observer_reliable,
                "catalog": copy.deepcopy(self._catalog),
            }

    def execute(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
        *,
        absolute_deadline: float | None = None,
    ) -> dict[str, Any]:
        """Execute one of the two B2 reads, returning a JSON-serializable result."""

        if tool_name in _DISABLED_TOOLS or tool_name not in _READ_TOOLS:
            return _disabled_tool_error(tool_name)
        if not isinstance(arguments, Mapping):
            raise HoudiniReadAdapterError(
                "INVALID_ARGUMENT", "Tool arguments must be a JSON object"
            )
        if threading.get_ident() != self._main_thread_id:
            try:
                return self._error_output(
                    arguments,
                    "MAIN_THREAD_REQUIRED",
                    "Houdini reads are permitted only on the UI main thread",
                )
            except HoudiniReadAdapterError:
                # Invalid arguments cannot form a schema envelope, but an
                # off-main-thread caller must still receive the authoritative
                # thread failure without any HOM access.
                return {
                    "ok": False,
                    "structured_error": {
                        "code": "MAIN_THREAD_REQUIRED",
                        "message": (
                            "Houdini reads are permitted only on the UI main thread"
                        ),
                        "details": [],
                    },
                }

        if absolute_deadline is not None:
            try:
                deadline = float(absolute_deadline)
            except (TypeError, ValueError):
                return self._error_output(
                    arguments, "INVALID_ARGUMENT", "The trusted deadline is invalid"
                )
            if not math.isfinite(deadline) or float(self._clock()) >= deadline:
                return self._error_output(
                    arguments,
                    "DEADLINE_EXCEEDED",
                    "The read request deadline has expired",
                )

        try:
            report = self.refresh()
        except HoudiniReadAdapterError as exc:
            return self._error_output(arguments, exc.code, exc.message)
        if not report["available"]:
            return self._error_output(
                arguments,
                "HOUDINI_UNAVAILABLE",
                "Reliable read-only Houdini observation is unavailable",
            )

        session_id = arguments.get("hip_session_id")
        if session_id != self._hip_session_id:
            return self._error_output(
                arguments,
                "HIP_SESSION_MISMATCH",
                "The requested HIP session is no longer current",
            )
        revision = arguments.get("base_scene_revision")
        if revision != self._scene_revision:
            return self._error_output(
                arguments,
                "SCENE_CONFLICT",
                "The requested scene revision is no longer current",
            )

        if tool_name == "houdini_scene_info":
            return self._execute_scene_info(arguments)
        return self._execute_node_type_info(arguments)

    def dispose(self) -> None:
        """Remove only callbacks owned by this adapter; never stop the Bridge."""

        self._assert_main_thread()
        if self._disposed:
            return
        with self._state_lock:
            if self._owned_write is not None:
                self._owned_write["invalidated"] = True
        self._remove_node_observers()
        if self._hip_callback_installed:
            try:
                self._hou.hipFile.removeEventCallback(self._on_hip_event)
            except Exception:
                pass
        self._hip_callback_installed = False
        self._started = False
        self._disposed = True
        self._session_observer_reliable = False
        self._revision_observer_reliable = False
        self._advance_observer_sequence()

    def _assert_main_thread(self) -> None:
        if threading.get_ident() != self._main_thread_id:
            raise HoudiniReadAdapterError(
                "MAIN_THREAD_REQUIRED",
                "Houdini reads are permitted only on the UI main thread",
            )

    def _advance_observer_sequence(self) -> None:
        with self._state_lock:
            self._observer_sequence += 1

    def _validate_event_operation(self, operation: object) -> str:
        if (
            not isinstance(operation, str)
            or len(operation) > _MAX_EVENT_OPERATION_LENGTH
            or _EVENT_OPERATION.fullmatch(operation) is None
        ):
            raise HoudiniReadAdapterError(
                "INVALID_ARGUMENT", "The strict event operation is invalid"
            )
        return operation

    def _strict_mutation_expectation(
        self,
        *,
        operation: str | None,
        event_source_rules: Mapping[str, Sequence[object]] | None,
        allowed_child_subjects: Sequence[object] | None,
        required_event_types: Sequence[str] | None,
        allow_zero_events: bool,
    ) -> dict[str, Any]:
        normalized_operation = self._validate_event_operation(operation)
        if type(allow_zero_events) is not bool or (
            allow_zero_events
            and normalized_operation not in _SILENT_READBACK_OPERATIONS
        ):
            raise HoudiniReadAdapterError(
                "INVALID_ARGUMENT", "A zero-event mutation policy is not authorized"
            )
        if not isinstance(event_source_rules, Mapping) or not event_source_rules:
            raise HoudiniReadAdapterError(
                "INVALID_ARGUMENT", "Strict event source rules are required"
            )
        rules: dict[str, tuple[object, ...]] = {}
        for event_name, sources in event_source_rules.items():
            if not self._known_event_name(event_name):
                raise HoudiniReadAdapterError(
                    "INVALID_ARGUMENT", "A strict event type is unavailable"
                )
            if isinstance(sources, (str, bytes, bytearray)) or not isinstance(
                sources, Sequence
            ):
                raise HoudiniReadAdapterError(
                    "INVALID_ARGUMENT", "Strict event sources are invalid"
                )
            identities = tuple(sources)
            if not identities or any(item is None for item in identities):
                raise HoudiniReadAdapterError(
                    "INVALID_ARGUMENT", "Strict event sources are empty"
                )
            rules[event_name] = identities
        required = (
            tuple(required_event_types or ())
            if allow_zero_events
            else tuple(required_event_types or tuple(rules))
        )
        if (not allow_zero_events and not required) or any(
            name not in rules for name in required
        ):
            raise HoudiniReadAdapterError(
                "INVALID_ARGUMENT", "Required strict event types are invalid"
            )
        subjects = tuple(allowed_child_subjects or ())
        if any(item is None for item in subjects):
            raise HoudiniReadAdapterError(
                "INVALID_ARGUMENT", "Strict child subjects are invalid"
            )
        return {
            "operation": normalized_operation,
            "event_source_rules": rules,
            "required_event_types": required,
            "seen_event_types": set(),
            "seen_child_subjects": [],
            "allowed_child_subjects": subjects,
            "allow_zero_events": allow_zero_events,
        }

    def _validate_strict_mutation_evidence(
        self,
        expectation: Mapping[str, Any],
        *,
        expected_child_subjects: Sequence[object] | None,
        require_all_child_subjects: bool,
        exact_readback_proven: bool,
    ) -> tuple[str, str] | None:
        allow_zero_events = expectation.get("allow_zero_events") is True
        if allow_zero_events and exact_readback_proven is not True:
            return (
                "HOUDINI_UNAVAILABLE",
                "The silent mutation exact readback was not proven",
            )
        if expectation.get("event_count", 0) < 1 and not allow_zero_events:
            return (
                "HOUDINI_UNAVAILABLE",
                "The strict mutation produced no observer event",
            )
        seen_types = expectation.get("seen_event_types", set())
        required_types = expectation["required_event_types"]
        if required_types and not any(name in seen_types for name in required_types):
            return (
                "HOUDINI_UNAVAILABLE",
                "The strict mutation produced no required observer event",
            )
        expected = tuple(expected_child_subjects or ())
        allowed = expectation.get("allowed_child_subjects", ()) or expected
        seen_subjects = tuple(expectation.get("seen_child_subjects", ()))
        if allowed and any(
            not _contains_houdini_node(allowed, subject)
            for subject in seen_subjects
        ):
            return (
                "SCENE_CONFLICT",
                "A strict observer event named an unexpected child subject",
            )
        if require_all_child_subjects and (
            not expected
            or any(
                not _contains_houdini_node(seen_subjects, subject)
                for subject in expected
            )
        ):
            return (
                "HOUDINI_UNAVAILABLE",
                "The strict observer did not cover every required child subject",
            )
        return None

    def _known_event_name(self, name: object) -> bool:
        if not isinstance(name, str) or name not in _REQUIRED_NODE_EVENT_NAMES:
            return False
        try:
            return getattr(self._hou.nodeEventType, name) is not None
        except Exception:
            return False

    def _event_type_name(self, event_type: object) -> str | None:
        try:
            namespace = self._hou.nodeEventType
            for name in _REQUIRED_NODE_EVENT_NAMES:
                candidate = getattr(namespace, name, None)
                if candidate is not None and event_type == candidate:
                    return name
        except Exception:
            return None
        return None

    def _strict_node_identity(self, node: object) -> dict[str, Any]:
        try:
            path = str(node.path())
            session_id = _bounded_nonnegative_int(
                node.sessionId(), _MAX_SESSION_ID
            )
        except Exception as exc:
            raise HoudiniReadAdapterError(
                "CAPABILITY_MISMATCH", "The observer node identity is unavailable"
            ) from exc
        if (
            not (path == "/obj" or path.startswith("/obj/"))
            or len(path) > _MAX_EVENT_PATH_LENGTH
            or any(character in path for character in ("\r", "\n", "\x00"))
        ):
            raise HoudiniReadAdapterError(
                "CAPABILITY_MISMATCH", "The observer node path is unsafe"
            )
        return {"path": path, "session_id": session_id}

    def _safe_observer_identity(self, node: object) -> dict[str, Any]:
        try:
            return self._strict_node_identity(node)
        except HoudiniReadAdapterError:
            return {"path": None, "session_id": None}

    def _callback_registration_matches(self, node: object) -> bool:
        if not self._strict_event_evidence or not self._node_event_types:
            return not self._strict_event_evidence
        callbacks = node.eventCallbacks()
        if not isinstance(callbacks, Sequence):
            return False
        registered: list[object] = []
        for entry in callbacks:
            if not isinstance(entry, Sequence) or len(entry) != 2:
                continue
            event_types, callback = entry
            if callback != self._on_node_event or not isinstance(
                event_types, Sequence
            ):
                continue
            registered.extend(event_types)
        return all(
            any(expected == actual for actual in registered)
            for expected in self._node_event_types
        )

    def _append_strict_event(
        self,
        transaction: dict[str, Any] | None,
        *,
        operation: str,
        event_name: str | None,
        callback_source: object | None,
        child_subject: object | None,
        matched: bool,
        main_thread: bool,
    ) -> None:
        source = (
            self._safe_observer_identity(callback_source)
            if callback_source is not None and main_thread
            else {"path": None, "session_id": None}
        )
        subject = (
            self._safe_observer_identity(child_subject)
            if child_subject is not None and main_thread
            else {"path": None, "session_id": None}
        )
        self._event_journal_sequence += 1
        record = {
            "sequence": self._event_journal_sequence,
            "operation": operation,
            "event_type": event_name or "unknown",
            "source_path": source["path"],
            "source_session_id": source["session_id"],
            "child_path": subject["path"],
            "child_session_id": subject["session_id"],
            "main_thread": bool(main_thread),
            "matched": bool(matched),
        }
        if len(self._event_journal) >= _MAX_EVENT_JOURNAL:
            if transaction is not None:
                transaction["invalidated"] = True
            self._observer_violation = True
            self._revision_observer_reliable = False
            return
        self._event_journal.append(record)
        if transaction is not None:
            transaction["events"].append(copy.deepcopy(record))

    def _install_hip_observer(self) -> bool:
        self._assert_main_thread()
        try:
            event_namespace = self._hou.hipFileEventType
            lifecycle = (
                getattr(event_namespace, "AfterLoad"),
                getattr(event_namespace, "AfterClear"),
            )
            revision_events = (
                getattr(event_namespace, "AfterSave"),
                getattr(event_namespace, "AfterMerge"),
            )
            if any(value is None for value in lifecycle + revision_events):
                return False
            self._hou.hipFile.addEventCallback(self._on_hip_event)
        except Exception:
            return False
        self._lifecycle_events = lifecycle
        self._hip_revision_events = revision_events
        self._hip_callback_installed = True
        return True

    def _refresh_node_observers(self) -> bool:
        self._assert_main_thread()
        try:
            event_namespace = self._hou.nodeEventType
            event_types = tuple(
                getattr(event_namespace, name) for name in _REQUIRED_NODE_EVENT_NAMES
            )
            if any(value is None for value in event_types):
                return False
            # B2's active live contexts are Object/SOP only.  Observing the
            # bounded /obj subtree avoids touching unrelated DCC contexts.
            root = self._hou.node("/obj")
            if root is None:
                return False
            nodes = self._bounded_node_tree(root)
        except Exception:
            return False

        seen: dict[str, tuple[int, Any]] = {}
        seen_session_ids: set[int] = set()
        # Publish the exact subscription set before strict add/readback checks.
        # The enum surface is immutable for one live Houdini process.
        previous_event_types = self._node_event_types
        self._node_event_types = event_types
        previous_by_session_id = {
            session_id: (path, node)
            for path, (session_id, node) in self._observed_nodes.items()
        }
        reliable = True
        for node in nodes:
            try:
                path = str(node.path())
                session_id = _bounded_nonnegative_int(
                    node.sessionId(),
                    _MAX_SESSION_ID,
                )
            except Exception:
                reliable = False
                continue
            if not path or path in seen or session_id in seen_session_ids:
                reliable = False
                continue
            previous = previous_by_session_id.get(session_id)
            if previous is not None:
                if not self._strict_event_evidence:
                    seen[path] = (session_id, previous[1])
                    seen_session_ids.add(session_id)
                    continue
                if not _same_houdini_node(previous[1], node):
                    reliable = False
                    continue
                try:
                    if not self._callback_registration_matches(node):
                        node.addEventCallback(event_types, self._on_node_event)
                    if not self._callback_registration_matches(node):
                        raise RuntimeError("observer callback readback mismatch")
                except Exception:
                    reliable = False
                    continue
                seen[path] = (session_id, node)
                seen_session_ids.add(session_id)
                continue
            try:
                node.addEventCallback(event_types, self._on_node_event)
                if self._strict_event_evidence and not self._callback_registration_matches(
                    node
                ):
                    raise RuntimeError("observer callback readback mismatch")
            except Exception:
                reliable = False
                continue
            seen[path] = (session_id, node)
            seen_session_ids.add(session_id)

        for _path, (session_id, node) in tuple(self._observed_nodes.items()):
            if session_id in seen_session_ids:
                continue
            try:
                node.removeEventCallback(
                    previous_event_types or self._node_event_types,
                    self._on_node_event,
                )
            except Exception:
                # A deleted node can reject callback removal.  Its parent delete
                # event already advanced the revision, so no live node remains
                # unobserved because of this cleanup failure.
                pass
        self._observed_nodes = seen
        return reliable and len(seen) == len(nodes)

    def _bounded_node_tree(self, root: Any) -> list[Any]:
        stack = [root]
        nodes: list[Any] = []
        while stack:
            node = stack.pop()
            nodes.append(node)
            if len(nodes) > _MAX_OBSERVED_NODES:
                raise HoudiniReadAdapterError(
                    "HOUDINI_UNAVAILABLE",
                    "The scene exceeds the bounded observer capacity",
                )
            children = tuple(node.children())
            stack.extend(reversed(children))
        return nodes

    def _remove_node_observers(self) -> None:
        self._assert_main_thread()
        for _session_id, node in tuple(self._observed_nodes.values()):
            try:
                node.removeEventCallback(self._node_event_types, self._on_node_event)
            except Exception:
                pass
        self._observed_nodes.clear()

    def _on_hip_event(self, event_type: Any, **event_details: Any) -> None:
        # AfterLoad/AfterClear may include old/new HIP path keywords.  They are
        # deliberately ignored so no user file path enters state or logs.
        del event_details
        if self._disposed:
            return
        if threading.get_ident() != self._main_thread_id:
            with self._state_lock:
                if self._owned_write is not None:
                    self._owned_write["invalidated"] = True
                changed = (
                    self._session_observer_reliable
                    or self._revision_observer_reliable
                    or not self._observer_violation
                )
                self._observer_violation = True
                self._session_observer_reliable = False
                self._revision_observer_reliable = False
                if changed:
                    self._observer_sequence += 1
            return
        if any(event_type == value for value in self._lifecycle_events):
            # The old node wrappers may already be invalid after a load/clear;
            # exact callback removal is nevertheless attempted and bounded.
            # Never use remove-all APIs, which could remove user callbacks.
            self._remove_node_observers()
            with self._state_lock:
                if self._owned_write is not None:
                    self._owned_write["invalidated"] = True
                self._hip_session_id = f"hip-{uuid.uuid4().hex}"
                self._scene_revision = 0
                self._revision_observer_reliable = False
                self._observer_sequence += 1
        elif any(event_type == value for value in self._hip_revision_events):
            # Save/merge do not replace the HIP session, but they can change
            # observable dirty or scene state.  Advance the read snapshot
            # without ever retaining the old/new file paths passed by HOM.
            with self._state_lock:
                if self._owned_write is not None:
                    self._owned_write["invalidated"] = True
                self._scene_revision += 1
                self._observer_sequence += 1

    def _on_node_event(self, *args: Any, **kwargs: Any) -> None:
        del args
        callback_source = kwargs.get("node")
        event_type = kwargs.get("event_type")
        child_subject = kwargs.get("child_node")
        if self._disposed:
            return
        if threading.get_ident() != self._main_thread_id:
            with self._state_lock:
                if self._owned_write is not None:
                    self._owned_write["invalidated"] = True
                if self._strict_event_evidence:
                    expectation = (
                        None
                        if self._owned_write is None
                        else self._owned_write["mutation_expectation"]
                    )
                    self._append_strict_event(
                        self._owned_write,
                        operation=(
                            "external"
                            if expectation is None
                            else expectation.get("operation", "external")
                        ),
                        event_name=self._event_type_name(event_type),
                        callback_source=None,
                        child_subject=None,
                        matched=False,
                        main_thread=False,
                    )
                changed = (
                    self._revision_observer_reliable
                    or not self._observer_violation
                )
                self._observer_violation = True
                self._revision_observer_reliable = False
                if changed:
                    self._observer_sequence += 1
            return
        event_name = self._event_type_name(event_type)
        with self._state_lock:
            transaction = self._owned_write
            if transaction is not None and not transaction["invalidated"]:
                expectation = transaction["mutation_expectation"]
                if transaction["strict_event_evidence"]:
                    rules = (
                        {}
                        if expectation is None
                        else expectation.get("event_source_rules", {})
                    )
                    sources = rules.get(event_name, ())
                    matched = bool(
                        expectation is not None
                        and event_name is not None
                        and _contains_houdini_node(sources, callback_source)
                    )
                    if matched and event_name in {
                        "ChildCreated",
                        "ChildDeleted",
                        "ChildSwitched",
                    }:
                        matched = child_subject is not None
                        allowed_subjects = expectation.get(
                            "allowed_child_subjects", ()
                        )
                        if matched and allowed_subjects:
                            matched = _contains_houdini_node(
                                allowed_subjects, child_subject
                            )
                    self._append_strict_event(
                        transaction,
                        operation=(
                            "external"
                            if expectation is None
                            else expectation["operation"]
                        ),
                        event_name=event_name,
                        callback_source=callback_source,
                        child_subject=child_subject,
                        matched=matched,
                        main_thread=True,
                    )
                    if matched:
                        expectation["event_count"] += 1
                        expectation["seen_event_types"].add(event_name)
                        if child_subject is not None:
                            expectation["seen_child_subjects"].append(
                                child_subject
                            )
                        transaction["pending_node_events"] += 1
                        return
                    transaction["invalidated"] = True
                    self._scene_revision += 1
                    self._observer_sequence += 1
                    return
                if (
                    expectation is not None
                    and _same_houdini_node(
                        callback_source, expectation["callback_source"]
                    )
                ):
                    expectation["event_count"] += 1
                    transaction["pending_node_events"] += 1
                    return
                transaction["invalidated"] = True
            if self._strict_event_evidence:
                self._append_strict_event(
                    transaction,
                    operation="external",
                    event_name=event_name,
                    callback_source=callback_source,
                    child_subject=child_subject,
                    matched=False,
                    main_thread=True,
                )
            self._scene_revision += 1
            self._observer_sequence += 1

    def _read_catalog(self) -> tuple[list[dict[str, Any]], bool]:
        self._assert_main_thread()
        try:
            categories = self._hou.nodeTypeCategories()
            if not isinstance(categories, Mapping):
                return _unavailable_catalog(), False
        except Exception:
            return _unavailable_catalog(), False

        records: list[dict[str, Any]] = []
        valid = True
        for context, requested_name in _ALLOWED_TYPES:
            record = self._read_node_type(categories, context, requested_name)
            records.append(record)
            valid = valid and bool(record["available"])
        return records, valid

    def _read_node_type(
        self,
        categories: Mapping[str, Any],
        context: str,
        requested_name: str,
    ) -> dict[str, Any]:
        unavailable = _unavailable_node_type(context, requested_name)
        try:
            category = categories.get(context)
            if category is None:
                return unavailable
            node_types = category.nodeTypes()
            if not isinstance(node_types, Mapping):
                return unavailable
            live_name = _LIVE_TYPE_NAMES[(context, requested_name)]
            node_type = node_types.get(live_name)
            if node_type is None:
                return unavailable
            resolved_name = _bounded_plain_text(
                node_type.name(), 256, "canonical node type"
            )
            if resolved_name != live_name:
                return unavailable
            input_count = _bounded_nonnegative_int(
                node_type.maxNumInputs(), _MAX_INPUTS
            )
            output_count = _bounded_nonnegative_int(
                node_type.maxNumOutputs(), _MAX_OUTPUTS
            )
            templates = self._template_map(node_type.parmTemplateGroup())
            parameters: list[dict[str, Any]] = []
            for name, (expected_type, expected_size) in _SAFE_PARAMETERS[
                (context, requested_name)
            ].items():
                template = templates.get(name)
                if template is None:
                    return unavailable
                parameters.append(
                    self._parameter_record(
                        template,
                        expected_name=name,
                        expected_type=expected_type,
                        expected_size=expected_size,
                    )
                )
            if len(parameters) > _MAX_PARAMETERS:
                return unavailable
        except Exception:
            return unavailable

        return {
            "context": context,
            "requested_name": requested_name,
            "resolved_name": resolved_name,
            "available": True,
            "creatable": False,
            "schema_source": "live_houdini_instance",
            "parameters": parameters,
            "input_count": input_count,
            "output_count": output_count,
        }

    def _template_map(self, group: Any) -> dict[str, Any]:
        entries = list(group.entries())
        templates: dict[str, Any] = {}
        visited = 0
        while entries:
            template = entries.pop(0)
            visited += 1
            if visited > _MAX_TEMPLATE_ENTRIES:
                raise ValueError("Parameter template hierarchy is too large")
            name = str(template.name())
            nested = getattr(template, "parmTemplates", None)
            if callable(nested):
                children = tuple(nested())
                if children:
                    entries[0:0] = list(children)
                    continue
            if name in templates:
                raise ValueError("Parameter template names are not unique")
            templates[name] = template
        return templates

    def _parameter_record(
        self,
        template: Any,
        *,
        expected_name: str,
        expected_type: str,
        expected_size: int,
    ) -> dict[str, Any]:
        if str(template.name()) != expected_name:
            raise ValueError("Parameter name mismatch")
        type_name = _enum_name(template.type())
        if expected_type == "float" and type_name.casefold() != "float":
            raise ValueError("Parameter type mismatch")
        tuple_size = _bounded_positive_int(template.numComponents(), 16)
        if tuple_size != expected_size:
            raise ValueError("Parameter tuple size mismatch")

        label = _bounded_plain_text(template.label(), 256, "parameter label")
        defaults = tuple(template.defaultValue())
        if len(defaults) != tuple_size:
            raise ValueError("Parameter default tuple size mismatch")
        values = [_bounded_float(value) for value in defaults]
        minimum = _bounded_float(template.minValue())
        maximum = _bounded_float(template.maxValue())
        if minimum > maximum:
            raise ValueError("Parameter numeric range is inverted")
        numeric_range = {
            "min_value": minimum,
            "max_value": maximum,
            "min_is_strict": bool(template.minIsStrict()),
            "max_is_strict": bool(template.maxIsStrict()),
        }
        default_value: dict[str, Any]
        value_type: str
        if tuple_size == 1:
            value_type = expected_type
            default_value = {"type": expected_type, "value": values[0]}
        else:
            value_type = "tuple"
            default_value = {
                "type": "tuple",
                "items_type": expected_type,
                "value": values,
            }
        return {
            "name": expected_name,
            "label": label,
            "value_type": value_type,
            "tuple_size": tuple_size,
            "writable": False,
            "allows_expression": False,
            "default_value": default_value,
            "numeric_range": numeric_range,
        }

    def _execute_scene_info(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        include_summaries = arguments.get("include_graph_summaries")
        if not isinstance(include_summaries, bool):
            return self._error_output(
                arguments,
                "INVALID_ARGUMENT",
                "include_graph_summaries must be a boolean",
            )
        try:
            frame = _bounded_float(self._hou.frame(), -1_000_000, 1_000_000)
            fps = _bounded_float(self._hou.fps(), 0, 1_000, exclusive_minimum=True)
            dirty = self._hou.hipFile.hasUnsavedChanges()
            if not isinstance(dirty, bool):
                raise ValueError("HIP dirty state is not boolean")
            summaries, truncated = self._read_hia_graph_summaries(include_summaries)
        except Exception:
            return self._error_output(
                arguments,
                "HOUDINI_UNAVAILABLE",
                "The bounded scene information could not be read safely",
            )

        output = self._common_output(arguments, ok=True)
        output["result"] = {
            "houdini_build": self._houdini_build,
            "hip_fingerprint": self._hip_fingerprint(),
            "current_frame": frame,
            "fps": fps,
            "dirty": dirty,
            "enabled_contexts": ["Object", "Sop"],
            "hia_graphs": summaries,
            "graph_summaries_truncated": truncated,
        }
        return output

    def _read_hia_graph_summaries(
        self, include_summaries: bool
    ) -> tuple[list[dict[str, Any]], bool]:
        if not include_summaries:
            return [], False
        object_root = self._hou.node("/obj")
        if object_root is None:
            raise ValueError("Object context is unavailable")
        children = tuple(object_root.children())
        summaries: list[dict[str, Any]] = []
        truncated = False
        for child in children:
            name = str(child.name())
            if _GRAPH_NAME.fullmatch(name) is None:
                continue
            if child.userData("hia_ownership") != "hia_owned":
                continue
            digest = child.userData("hia_graph_digest")
            if not isinstance(digest, str) or _DIGEST.fullmatch(digest) is None:
                continue
            graph_nodes = tuple(child.children())
            if not 1 <= len(graph_nodes) <= _MAX_GRAPH_NODES:
                truncated = True
                continue
            connection_count = 0
            for graph_node in graph_nodes:
                connection_count += len(tuple(graph_node.inputConnections()))
                if connection_count > _MAX_GRAPH_CONNECTIONS:
                    truncated = True
                    break
            if connection_count > _MAX_GRAPH_CONNECTIONS:
                continue
            if len(summaries) >= _MAX_GRAPH_SUMMARIES:
                truncated = True
                continue
            summaries.append(
                {
                    "root_path": f"/obj/{name}",
                    "context": "Object",
                    "ownership": "hia_owned",
                    "graph_digest": digest.casefold(),
                    "node_count": len(graph_nodes),
                    "connection_count": connection_count,
                    # Gate B2 never cooks and deliberately does not query
                    # potentially blocking cook/error state.
                    "cook_state": "unknown",
                }
            )
        summaries.sort(key=lambda item: item["root_path"])
        return summaries, truncated

    def _execute_node_type_info(
        self, arguments: Mapping[str, Any]
    ) -> dict[str, Any]:
        requested = arguments.get("node_types")
        if (
            not isinstance(requested, Sequence)
            or isinstance(requested, (str, bytes, bytearray))
            or not 1 <= len(requested) <= 16
        ):
            return self._error_output(
                arguments,
                "INVALID_ARGUMENT",
                "node_types must contain between one and sixteen queries",
            )
        catalog = {
            (item["context"], item["requested_name"]): item for item in self._catalog
        }
        results: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for query in requested:
            if not isinstance(query, Mapping) or set(query) != {"context", "name"}:
                return self._error_output(
                    arguments, "INVALID_ARGUMENT", "A node-type query is malformed"
                )
            key = (query.get("context"), query.get("name"))
            if key not in catalog:
                return self._error_output(
                    arguments,
                    "NODE_TYPE_NOT_ALLOWED",
                    "The requested node type is outside the B2 allowlist",
                )
            if key in seen:
                return self._error_output(
                    arguments, "INVALID_ARGUMENT", "Node-type queries must be unique"
                )
            seen.add(key)
            results.append(copy.deepcopy(catalog[key]))

        output = self._common_output(arguments, ok=True)
        output["result"] = {"node_types": results}
        return output

    def _common_output(
        self, arguments: Mapping[str, Any], *, ok: bool
    ) -> dict[str, Any]:
        required = (
            "request_id",
            "thread_id",
            "turn_id",
            "hip_session_id",
            "base_scene_revision",
            "idempotency_key",
        )
        if any(name not in arguments for name in required):
            raise HoudiniReadAdapterError(
                "INVALID_ARGUMENT", "Required correlation fields are missing"
            )
        return {
            "ok": ok,
            "request_id": arguments["request_id"],
            "thread_id": arguments["thread_id"],
            "turn_id": arguments["turn_id"],
            "hip_session_id": arguments["hip_session_id"],
            "base_scene_revision": arguments["base_scene_revision"],
            "idempotency_key": arguments["idempotency_key"],
            "scene_revision": self._scene_revision,
            "result": None,
            "warnings": [],
            "structured_error": None,
        }

    def _error_output(
        self,
        arguments: Mapping[str, Any],
        code: str,
        message: str,
    ) -> dict[str, Any]:
        output = self._common_output(arguments, ok=False)
        output["structured_error"] = {
            "code": code,
            "message": message,
            "details": [],
        }
        return output

    def _hip_fingerprint(self) -> str:
        payload = "\x1f".join(
            (
                "hia-b2-safe-hip-fingerprint-v1",
                self._publisher_id,
                self._houdini_build,
                self._hip_session_id,
                str(self._scene_revision),
            )
        ).encode("utf-8")
        return hmac.new(self._fingerprint_key, payload, hashlib.sha256).hexdigest()


def _unavailable_catalog() -> list[dict[str, Any]]:
    return [_unavailable_node_type(context, name) for context, name in _ALLOWED_TYPES]


def _unavailable_node_type(context: str, requested_name: str) -> dict[str, Any]:
    return {
        "context": context,
        "requested_name": requested_name,
        "resolved_name": None,
        "available": False,
        "creatable": False,
        "schema_source": "live_houdini_instance",
        "parameters": [],
        "input_count": 0,
        "output_count": 0,
    }


def _disabled_tool_error(tool_name: Any) -> dict[str, Any]:
    safe_name = str(tool_name)
    if len(safe_name) > 128 or any(ord(character) < 32 for character in safe_name):
        safe_name = "<invalid>"
    return {
        "ok": False,
        "structured_error": {
            "code": "TOOL_NOT_ALLOWED",
            "message": f"Tool {safe_name} is disabled in Gate B2",
            "details": [],
        },
    }


def _enum_name(value: Any) -> str:
    name = getattr(value, "name", None)
    if callable(name):
        name = name()
    if isinstance(name, str) and name:
        return name.rsplit(".", 1)[-1]
    return str(value).rsplit(".", 1)[-1]


def _bounded_plain_text(value: Any, maximum: int, field_name: str) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ValueError(f"{field_name} is not a bounded string")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError(f"{field_name} contains control characters")
    return value


def _bounded_nonnegative_int(value: Any, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum:
        raise ValueError("Integer value is outside the allowed range")
    return value


def _bounded_positive_int(value: Any, maximum: int) -> int:
    result = _bounded_nonnegative_int(value, maximum)
    if result == 0:
        raise ValueError("Integer value must be positive")
    return result


def _same_houdini_node(left: object, right: object) -> bool:
    """Compare the underlying HOM node without trusting Python wrapper identity.

    Houdini may return multiple Python ``hou.Node`` wrappers for one live node.
    Equality alone is also insufficient for this safety boundary, so a
    non-identical wrapper is accepted only when ``==`` returns the exact
    singleton ``True`` and both bounded session IDs and exact paths agree.  Any
    unavailable or adversarial comparison fails closed.
    """

    if left is right:
        return True
    if left is None or right is None:
        return False
    try:
        if (left == right) is not True:
            return False
        left_session = left.sessionId()  # type: ignore[attr-defined]
        right_session = right.sessionId()  # type: ignore[attr-defined]
        left_path = left.path()  # type: ignore[attr-defined]
        right_path = right.path()  # type: ignore[attr-defined]
    except Exception:
        return False
    return bool(
        type(left_session) is int
        and type(right_session) is int
        and 0 <= left_session <= _MAX_SESSION_ID
        and left_session == right_session
        and type(left_path) is str
        and type(right_path) is str
        and left_path == right_path
    )


def _contains_houdini_node(
    values: Sequence[object], candidate: object
) -> bool:
    return any(_same_houdini_node(value, candidate) for value in values)


def _bounded_float(
    value: Any,
    minimum: float = -1_000_000_000,
    maximum: float = 1_000_000_000,
    *,
    exclusive_minimum: bool = False,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Numeric value has the wrong type")
    result = float(value)
    if not math.isfinite(result) or result > maximum:
        raise ValueError("Numeric value is outside the allowed range")
    if exclusive_minimum and result <= minimum:
        raise ValueError("Numeric value is outside the allowed range")
    if not exclusive_minimum and result < minimum:
        raise ValueError("Numeric value is outside the allowed range")
    return result


__all__ = ["HoudiniReadAdapter", "HoudiniReadAdapterError"]
