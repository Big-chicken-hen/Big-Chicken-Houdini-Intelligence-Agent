"""Thread-safe, fake-only scene request queue for P2-V Gate B1.

This module deliberately contains no HTTP server, Houdini import, or live-scene
executor.  It owns only trusted in-memory correlation, approval, idempotency,
claim, cancellation, result, and shutdown state.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
import math
import re
import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from hia_core.houdini_contract import (
    ContractError,
    approval_binding_digest,
    approval_binding_payload,
    graph_digest,
    graph_side_effect_summary,
    normalize_graph,
)


B1_CONTRACT_VERSION = "0.1.0"
B2_CONTRACT_VERSION = "0.2.0"
# Backwards-compatible public name for the frozen Gate B1 request contract.
CONTRACT_VERSION = B1_CONTRACT_VERSION
B1_OFFLINE_PROFILE = "b1_offline"
B2_READ_ONLY_PROFILE = "b2_read_only"
ALLOWED_TOOLS = frozenset(
    {
        "houdini_scene_info",
        "houdini_node_type_info",
        "houdini_graph_validate",
        "houdini_graph_apply",
        "houdini_graph_verify",
    }
)
B2_READ_ONLY_TOOLS = frozenset(
    {
        "houdini_scene_info",
        "houdini_node_type_info",
    }
)
WRITE_TOOL = "houdini_graph_apply"
DEFAULT_CAPACITY = 32
DEFAULT_TERMINAL_RETENTION = 256
MAX_WAIT_SECONDS = 1.0
APPROVAL_TTL_SECONDS = 60.0
DEFAULT_LIVE_CAPABILITY_LEASE_SECONDS = 10.0
MAX_LIVE_CAPABILITY_LEASE_SECONDS = 60.0
MAX_LIVE_CAPABILITY_REPORT_BYTES = 262_144
MAX_RETIRED_HIP_SESSIONS = 256

LIVE_CAPABILITY_REPORT_FIELDS = frozenset(
    {
        "available",
        "publisher_id",
        "observer_sequence",
        "houdini_build",
        "python_version",
        "pyside_version",
        "hip_session_id",
        "hip_fingerprint",
        "scene_revision",
        "session_observer_reliable",
        "revision_observer_reliable",
        "catalog",
    }
)

_B2_CATALOG_TYPES = (
    ("Object", "geo"),
    ("Sop", "box"),
    ("Sop", "transform"),
    ("Sop", "merge"),
    ("Sop", "null"),
)
_B2_CATALOG_INDEX = {value: index for index, value in enumerate(_B2_CATALOG_TYPES)}
_B2_RESOLVED_TYPE_NAMES = {
    ("Object", "geo"): "geo",
    ("Sop", "box"): "box",
    ("Sop", "transform"): "xform",
    ("Sop", "merge"): "merge",
    ("Sop", "null"): "null",
}
_NODE_TYPE_FIELDS = frozenset(
    {
        "context",
        "requested_name",
        "resolved_name",
        "available",
        "creatable",
        "schema_source",
        "parameters",
        "input_count",
        "output_count",
    }
)
_PARAMETER_FIELDS = frozenset(
    {
        "name",
        "label",
        "value_type",
        "tuple_size",
        "writable",
        "allows_expression",
        "default_value",
        "numeric_range",
    }
)
_NUMERIC_RANGE_FIELDS = frozenset(
    {"min_value", "max_value", "min_is_strict", "max_is_strict"}
)

_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
_PARAMETER_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,127}$")
_RESOLVED_TYPE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,255}$")
_HOUDINI_BUILD_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]{0,127}$")
_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")
_ENVIRONMENT_REFERENCE_RE = re.compile(
    r"(?:\$[A-Za-z_][A-Za-z0-9_]*|%[A-Za-z_][A-Za-z0-9_]*%)"
)
_SECRET_TEXT_RE = re.compile(
    r"(?i)(?:\bbearer\s+\S+|\b(?:token|api[_-]?key|authorization)\s*[:=])"
)
_TERMINAL_STATES = frozenset(
    {"completed", "cancelled", "denied", "expired", "indeterminate", "shutdown"}
)


class SceneQueueError(Exception):
    """Safe structured queue error; its details never contain credentials."""

    def __init__(
        self,
        code: str,
        status: int,
        message: str,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.message = message
        self.details = _plain_copy(dict(details or {}), field_name="error details")

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "status": self.status,
            "message": self.message,
            "details": copy.deepcopy(self.details),
        }


@dataclass(frozen=True)
class FakeCapabilityAttestation:
    """Trusted B1 fixture identity.  It is never accepted from tool arguments."""

    launch_id: str
    generation: int
    process_nonce: str
    hip_session_id: str
    hip_fingerprint: str
    scene_revision: int
    catalog_digest: str
    schema_digest: str
    fake_only: bool = True

    def __post_init__(self) -> None:
        for name in ("launch_id", "process_nonce", "hip_session_id"):
            _require_identifier(getattr(self, name), name)
        for name in ("hip_fingerprint", "catalog_digest", "schema_digest"):
            _require_sha256(getattr(self, name), name)
        if isinstance(self.generation, bool) or not isinstance(self.generation, int):
            raise ValueError("generation must be a non-negative integer")
        if self.generation < 0:
            raise ValueError("generation must be a non-negative integer")
        if isinstance(self.scene_revision, bool) or not isinstance(self.scene_revision, int):
            raise ValueError("scene_revision must be a non-negative integer")
        if self.scene_revision < 0:
            raise ValueError("scene_revision must be a non-negative integer")
        if self.fake_only is not True:
            raise ValueError("Gate B1 accepts fake-only capability attestations")

    @property
    def digest(self) -> str:
        return _sha256(
            {
                "contract_version": CONTRACT_VERSION,
                "fake_only": True,
                "launch_id": self.launch_id,
                "generation": self.generation,
                "process_nonce": self.process_nonce,
                "hip_session_id": self.hip_session_id,
                "hip_fingerprint": self.hip_fingerprint,
                "scene_revision": self.scene_revision,
                "catalog_digest": self.catalog_digest,
                "schema_digest": self.schema_digest,
            }
        )


@dataclass(frozen=True)
class LiveCapabilityAttestation:
    """Bridge-created identity for the B2 read-only Houdini capability.

    The HTTP publisher never supplies launch, generation, process nonce,
    schema digest, catalog digest, or an attestation digest.  Those values are
    injected or recomputed by the Bridge before this object can exist.
    """

    profile: str
    launch_id: str
    generation: int
    process_nonce: str
    publisher_id: str
    observer_sequence: int
    houdini_build: str
    python_version: str
    pyside_version: str
    hip_session_id: str
    hip_fingerprint: str
    scene_revision: int
    catalog_digest: str
    schema_digest: str
    session_observer_reliable: bool
    revision_observer_reliable: bool

    def __post_init__(self) -> None:
        if self.profile != B2_READ_ONLY_PROFILE:
            raise ValueError("Live capability profile must be b2_read_only")
        for name in ("launch_id", "process_nonce", "publisher_id", "hip_session_id"):
            _require_identifier(getattr(self, name), name)
        for name in ("hip_fingerprint", "catalog_digest", "schema_digest"):
            _require_sha256(getattr(self, name), name)
        for name in ("generation", "observer_sequence", "scene_revision"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        for name in ("houdini_build", "python_version", "pyside_version"):
            _require_safe_catalog_text(getattr(self, name), name, 128)
        if self.session_observer_reliable is not True:
            raise ValueError("HIP session observation must be reliable")
        if self.revision_observer_reliable is not True:
            raise ValueError("Scene revision observation must be reliable")

    @property
    def digest(self) -> str:
        return _sha256(
            {
                "profile": self.profile,
                "capability_version": "b2-read-only-v1",
                "launch_id": self.launch_id,
                "generation": self.generation,
                "process_nonce": self.process_nonce,
                "publisher_id": self.publisher_id,
                "observer_sequence": self.observer_sequence,
                "houdini_build": self.houdini_build,
                "python_version": self.python_version,
                "pyside_version": self.pyside_version,
                "hip_session_id": self.hip_session_id,
                "hip_fingerprint": self.hip_fingerprint,
                "scene_revision": self.scene_revision,
                "catalog_digest": self.catalog_digest,
                "schema_digest": self.schema_digest,
                "session_observer_reliable": self.session_observer_reliable,
                "revision_observer_reliable": self.revision_observer_reliable,
            }
        )


@dataclass(frozen=True)
class SceneRequest:
    tool_name: str
    arguments: dict[str, Any]
    absolute_deadline: float
    launch_id: str
    generation: int
    attestation_digest: str
    approval_payload: dict[str, Any] | None
    approval_binding_digest: str | None
    request_digest: str

    @classmethod
    def build(
        cls,
        tool_name: str,
        arguments: Mapping[str, Any],
        absolute_deadline: float,
        launch_id: str,
        generation: int,
        attestation_digest: str,
        *,
        contract_version: str = B1_CONTRACT_VERSION,
    ) -> "SceneRequest":
        if contract_version not in {B1_CONTRACT_VERSION, B2_CONTRACT_VERSION}:
            raise ValueError("contract_version is not supported")
        if not isinstance(arguments, Mapping):
            raise ValueError("arguments must be a JSON object")
        plain_arguments = _plain_copy(arguments, field_name="arguments")
        if not isinstance(plain_arguments, dict):
            raise ValueError("arguments must be a JSON object")
        deadline = _require_deadline(absolute_deadline)
        approval_payload: dict[str, Any] | None = None
        binding_digest: str | None = None
        if tool_name == WRITE_TOOL:
            graph = normalize_graph(plain_arguments.get("graph"))
            canonical_digest = graph_digest(graph)
            supplied_digest = plain_arguments.get("canonical_graph_digest")
            if (
                not isinstance(supplied_digest, str)
                or supplied_digest.casefold() != canonical_digest
            ):
                raise ContractError(
                    "DIGEST_MISMATCH",
                    "Apply graph digest does not match the normalized graph",
                    {"path": "$.canonical_graph_digest"},
                )
            plain_arguments["graph"] = graph
            plain_arguments["canonical_graph_digest"] = canonical_digest
            side_effects = graph_side_effect_summary(graph)
            approval_payload = approval_binding_payload(
                plain_arguments,
                graph,
                canonical_digest,
                side_effects,
            )
            binding_digest = approval_binding_digest(
                plain_arguments,
                graph,
                canonical_digest,
                side_effects,
            )
        digest = _request_digest(
            tool_name,
            plain_arguments,
            deadline,
            launch_id,
            generation,
            attestation_digest,
            approval_payload,
            binding_digest,
            contract_version=contract_version,
        )
        return cls(
            tool_name=tool_name,
            arguments=plain_arguments,
            absolute_deadline=deadline,
            launch_id=launch_id,
            generation=generation,
            attestation_digest=attestation_digest,
            approval_payload=approval_payload,
            approval_binding_digest=binding_digest,
            request_digest=digest,
        )


@dataclass(frozen=True)
class RequestSnapshot:
    request_id: str
    tool_name: str
    state: str
    request_digest: str
    idempotency_key: str
    absolute_deadline: float
    replayed: bool = False
    result: dict[str, Any] | None = None
    structured_error: dict[str, Any] | None = None
    cancel_requested: bool = False

    @property
    def terminal(self) -> bool:
        return self.state in _TERMINAL_STATES

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "request_id": self.request_id,
            "tool_name": self.tool_name,
            "state": self.state,
            "request_digest": self.request_digest,
            "idempotency_key": self.idempotency_key,
            "absolute_deadline": self.absolute_deadline,
            "replayed": self.replayed,
            "terminal": self.terminal,
            "cancel_requested": self.cancel_requested,
        }
        if self.result is not None:
            payload["result"] = copy.deepcopy(self.result)
        if self.structured_error is not None:
            payload["structured_error"] = copy.deepcopy(self.structured_error)
        return payload


@dataclass(frozen=True)
class Claim:
    """Executor-only claim.  ``claim_token`` must never be returned to clients."""

    request_id: str
    tool_name: str
    arguments: dict[str, Any]
    request_digest: str
    attestation_digest: str
    absolute_deadline: float
    claim_token: str
    cancel_requested: bool


@dataclass(frozen=True)
class PanelWork:
    """One safe Panel delivery.

    Approval presentations never carry an executor token.  Execute deliveries
    carry an opaque, one-request token used only when posting the result; that
    token is intentionally absent from every public request snapshot.
    """

    kind: str
    request_id: str
    tool_name: str
    arguments: dict[str, Any]
    request_digest: str
    attestation_digest: str
    absolute_deadline: float
    approval_payload: dict[str, Any] | None = None
    approval_binding_digest: str | None = None
    executor_token: str | None = None
    cancel_requested: bool = False

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "kind": self.kind,
            "request_id": self.request_id,
            "tool_name": self.tool_name,
            "arguments": copy.deepcopy(self.arguments),
            "request_digest": self.request_digest,
            "attestation_digest": self.attestation_digest,
            "absolute_deadline": self.absolute_deadline,
            "cancel_requested": self.cancel_requested,
        }
        if self.kind == "execute" and self.executor_token is not None:
            payload["executor_token"] = self.executor_token
        if self.kind == "approval_required":
            payload["approval_payload"] = copy.deepcopy(self.approval_payload)
            payload["approval_binding_digest"] = self.approval_binding_digest
        return payload


@dataclass
class _ApprovalProof:
    request_digest: str
    approval_binding_digest: str
    expires_at: float
    used: bool = False


@dataclass
class _Record:
    request: SceneRequest
    request_id: str
    idempotency_key: str
    state: str
    approval: _ApprovalProof | None = None
    approval_presented: bool = False
    approval_resolved: bool = False
    claim_token: str | None = None
    cancel_requested: bool = False
    result: dict[str, Any] | None = None
    result_digest: str | None = None
    structured_error: dict[str, Any] | None = None


class SceneQueue:
    """Bounded, single-writer, in-memory queue scoped to one Bridge launch."""

    def __init__(
        self,
        launch_id: str,
        generation: int,
        *,
        expected_schema_digest: str,
        expected_catalog_digest: str | None,
        profile: str = B1_OFFLINE_PROFILE,
        expected_process_nonce: str | None = None,
        live_capability_lease_seconds: float = DEFAULT_LIVE_CAPABILITY_LEASE_SECONDS,
        capacity: int = DEFAULT_CAPACITY,
        terminal_retention: int = DEFAULT_TERMINAL_RETENTION,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        _require_identifier(launch_id, "launch_id")
        _require_sha256(expected_schema_digest, "expected_schema_digest")
        if profile not in {B1_OFFLINE_PROFILE, B2_READ_ONLY_PROFILE}:
            raise ValueError("profile must be b1_offline or b2_read_only")
        if profile == B1_OFFLINE_PROFILE:
            _require_sha256(expected_catalog_digest, "expected_catalog_digest")
            if expected_process_nonce is not None:
                raise ValueError("B1 offline profile cannot bind a live process nonce")
        else:
            if expected_catalog_digest is not None:
                _require_sha256(expected_catalog_digest, "expected_catalog_digest")
            _require_identifier(expected_process_nonce, "expected_process_nonce")
        try:
            lease_seconds = float(live_capability_lease_seconds)
        except (TypeError, ValueError) as exc:
            raise ValueError("live_capability_lease_seconds must be finite") from exc
        if (
            not math.isfinite(lease_seconds)
            or not 0.1 <= lease_seconds <= MAX_LIVE_CAPABILITY_LEASE_SECONDS
        ):
            raise ValueError(
                "live_capability_lease_seconds must be between 0.1 and 60 seconds"
            )
        if isinstance(generation, bool) or not isinstance(generation, int) or generation < 0:
            raise ValueError("generation must be a non-negative integer")
        if isinstance(capacity, bool) or not isinstance(capacity, int) or not 1 <= capacity <= DEFAULT_CAPACITY:
            raise ValueError(f"capacity must be between 1 and {DEFAULT_CAPACITY}")
        if (
            isinstance(terminal_retention, bool)
            or not isinstance(terminal_retention, int)
            or not 1 <= terminal_retention <= DEFAULT_TERMINAL_RETENTION
        ):
            raise ValueError(
                f"terminal_retention must be between 1 and {DEFAULT_TERMINAL_RETENTION}"
            )

        self.launch_id = launch_id
        self.generation = generation
        self.profile = profile
        self.contract_version = (
            B2_CONTRACT_VERSION
            if profile == B2_READ_ONLY_PROFILE
            else B1_CONTRACT_VERSION
        )
        self.allowed_tools = (
            B2_READ_ONLY_TOOLS if profile == B2_READ_ONLY_PROFILE else ALLOWED_TOOLS
        )
        self.expected_schema_digest = expected_schema_digest
        self.expected_catalog_digest = expected_catalog_digest
        self.expected_process_nonce = expected_process_nonce
        self.live_capability_lease_seconds = lease_seconds
        self.capacity = capacity
        self.terminal_retention = terminal_retention
        self._clock = clock
        self._condition = threading.Condition(threading.RLock())
        self._attestation: (
            FakeCapabilityAttestation | LiveCapabilityAttestation | None
        ) = None
        self._attestation_expires_at: float | None = None
        self._live_report: dict[str, Any] | None = None
        self._live_report_digest: str | None = None
        self._live_publisher_id: str | None = None
        self._live_observer_sequence: int | None = None
        self._live_identity: tuple[str, str, str, str] | None = None
        self._current_hip_session_id: str | None = None
        self._current_scene_revision: int | None = None
        self._current_hip_fingerprint: str | None = None
        self._retired_hip_sessions: set[str] = set()
        self._retired_hip_session_order: deque[str] = deque()
        self._session_history_exhausted = False
        self._records: dict[str, _Record] = {}
        self._idempotency: dict[str, str] = {}
        self._claimable: deque[str] = deque()
        self._approval_presentable: deque[str] = deque()
        self._terminal_order: deque[str] = deque()
        self._active_count = 0
        self._active_write_request_id: str | None = None
        self._shutdown = False

    @property
    def current_attestation_digest(self) -> str | None:
        with self._condition:
            self._expire_live_capability_locked(self._clock())
            return self._attestation.digest if self._attestation is not None else None

    @property
    def capability_lease_expires_at(self) -> float | None:
        with self._condition:
            self._expire_live_capability_locked(self._clock())
            return self._attestation_expires_at

    def tool_enabled(self, tool_name: str) -> bool:
        return isinstance(tool_name, str) and tool_name in self.allowed_tools

    def live_capability_status(self) -> dict[str, Any]:
        """Return the minimal authenticated discovery context for a first B2 read.

        This is Bridge control-plane state, not an additional MCP tool.  It
        deliberately omits the process nonce, publisher identity, observer
        sequence, executor credential, and full parameter catalog.
        """

        if self.profile != B2_READ_ONLY_PROFILE:
            raise SceneQueueError(
                "TOOL_NOT_ALLOWED",
                403,
                "Live capability status is available only in the B2 read-only profile",
            )
        with self._condition:
            self._require_open_locked()
            self._expire_live_capability_locked(self._clock())
            current = self._attestation
            report = self._live_report
            if not isinstance(current, LiveCapabilityAttestation) or not isinstance(
                report, dict
            ):
                raise SceneQueueError(
                    "HOUDINI_UNAVAILABLE",
                    503,
                    "No current live Houdini capability attestation is available",
                )
            catalog = report.get("catalog")
            if not isinstance(catalog, list) or len(catalog) != len(
                _B2_CATALOG_TYPES
            ):
                self._invalidate_live_capability_locked(
                    "The current live catalog is unavailable"
                )
                raise SceneQueueError(
                    "CAPABILITY_MISMATCH",
                    409,
                    "The current live catalog is unavailable",
                )
            allowed_node_types = [
                {
                    "context": item["context"],
                    "requested_name": item["requested_name"],
                    "resolved_name": item["resolved_name"],
                    "available": item["available"],
                }
                for item in catalog
            ]
            return {
                "available": True,
                "profile": B2_READ_ONLY_PROFILE,
                "schema_version": self.contract_version,
                "schema_digest": self.expected_schema_digest,
                "launch_id": self.launch_id,
                "generation": self.generation,
                "attestation_digest": current.digest,
                "houdini_build": current.houdini_build,
                "hip_session_id": current.hip_session_id,
                "hip_fingerprint": current.hip_fingerprint,
                "scene_revision": current.scene_revision,
                "catalog_digest": current.catalog_digest,
                "enabled_tools": [
                    "houdini_scene_info",
                    "houdini_node_type_info",
                ],
                "allowed_node_types": allowed_node_types,
            }

    @property
    def is_shutdown(self) -> bool:
        with self._condition:
            return self._shutdown

    def request_context(self, request_id: str) -> tuple[str, dict[str, Any]]:
        """Return trusted correlation for Bridge-side validation and result shaping."""

        _queue_identifier(request_id, "request_id")
        with self._condition:
            record = self._record_locked(request_id)
            return record.request.tool_name, copy.deepcopy(record.request.arguments)

    def install_attestation(self, attestation: FakeCapabilityAttestation) -> str:
        if self.profile != B1_OFFLINE_PROFILE:
            raise SceneQueueError(
                "CAPABILITY_MISMATCH",
                409,
                "Fake capability attestations are disabled in the B2 live profile",
            )
        if not isinstance(attestation, FakeCapabilityAttestation):
            raise SceneQueueError(
                "CAPABILITY_MISMATCH", 409, "Capability attestation is invalid"
            )
        with self._condition:
            self._require_open_locked()
            if (
                attestation.launch_id != self.launch_id
                or attestation.generation != self.generation
                or attestation.catalog_digest != self.expected_catalog_digest
                or attestation.schema_digest != self.expected_schema_digest
                or not attestation.fake_only
            ):
                raise SceneQueueError(
                    "CAPABILITY_MISMATCH",
                    409,
                    "Capability attestation does not match this Bridge launch",
                    {"generation": self.generation},
                )
            if self._attestation is not None and self._attestation.digest != attestation.digest:
                raise SceneQueueError(
                    "CAPABILITY_MISMATCH",
                    409,
                    "A different capability attestation is already active",
                    {"generation": self.generation},
                )
            self._attestation = attestation
            return attestation.digest

    def replace_attestation(
        self,
        attestation: FakeCapabilityAttestation,
        expected_current_digest: str,
    ) -> str:
        """Atomically advance the trusted fake scene snapshot.

        This is a compare-and-swap operation for the same fake process and HIP
        session.  Active work bound to the prior snapshot is failed closed;
        terminal idempotency records remain available for exact replay.
        """

        if self.profile != B1_OFFLINE_PROFILE:
            raise SceneQueueError(
                "CAPABILITY_MISMATCH",
                409,
                "Fake capability attestations are disabled in the B2 live profile",
            )
        if not isinstance(attestation, FakeCapabilityAttestation):
            raise SceneQueueError(
                "CAPABILITY_MISMATCH", 409, "Capability attestation is invalid"
            )
        _queue_sha256(expected_current_digest, "expected_current_digest")
        with self._condition:
            self._require_open_locked()
            current = self._attestation
            if current is None:
                raise SceneQueueError(
                    "HOUDINI_UNAVAILABLE",
                    503,
                    "No current fake capability attestation is available",
                )
            if current.digest != expected_current_digest:
                raise SceneQueueError(
                    "CAPABILITY_MISMATCH",
                    409,
                    "Capability attestation changed before replacement",
                    {"generation": self.generation},
                )
            if (
                attestation.launch_id != current.launch_id
                or attestation.generation != current.generation
                or attestation.process_nonce != current.process_nonce
                or attestation.hip_session_id != current.hip_session_id
                or attestation.catalog_digest != self.expected_catalog_digest
                or attestation.schema_digest != self.expected_schema_digest
                or not attestation.fake_only
            ):
                raise SceneQueueError(
                    "CAPABILITY_MISMATCH",
                    409,
                    "Replacement is not the same trusted fake process and HIP session",
                    {"generation": self.generation},
                )
            if attestation.scene_revision < current.scene_revision:
                raise SceneQueueError(
                    "SCENE_CONFLICT",
                    409,
                    "Capability scene revision cannot regress",
                    {"scene_revision": current.scene_revision},
                )
            if (
                attestation.scene_revision == current.scene_revision
                and attestation.hip_fingerprint != current.hip_fingerprint
            ):
                raise SceneQueueError(
                    "SCENE_CONFLICT",
                    409,
                    "HIP fingerprint cannot change without a scene revision change",
                    {"scene_revision": current.scene_revision},
                )

            reserved_write = (
                self._records.get(self._active_write_request_id)
                if self._active_write_request_id is not None
                else None
            )
            if reserved_write is not None and reserved_write.state == "claimed":
                if attestation.digest == current.digest:
                    return current.digest
                raise SceneQueueError(
                    "WRITE_IN_PROGRESS",
                    409,
                    "A claimed graph apply must finish or become indeterminate before attestation replacement",
                    {"request_id": reserved_write.request_id},
                )

            clear_indeterminate_reservation = bool(
                reserved_write is not None
                and reserved_write.state in _TERMINAL_STATES
                and reserved_write.structured_error is not None
                and reserved_write.structured_error.get("code")
                == "SCENE_STATE_INDETERMINATE"
            )
            if attestation.digest == current.digest:
                if clear_indeterminate_reservation:
                    self._active_write_request_id = None
                    self._condition.notify_all()
                return current.digest

            for record in list(self._records.values()):
                if record.state not in _TERMINAL_STATES:
                    self._terminalize_error_locked(
                        record,
                        "expired",
                        SceneQueueError(
                            "CAPABILITY_MISMATCH",
                            409,
                            "Scene request was bound to an obsolete capability attestation",
                            {"request_id": record.request_id},
                        ),
                    )
            self._attestation = attestation
            if clear_indeterminate_reservation:
                self._active_write_request_id = None
            self._condition.notify_all()
            return attestation.digest

    def publish_live_capability(
        self,
        report: Mapping[str, Any],
    ) -> LiveCapabilityAttestation | None:
        """Publish one authenticated Panel capability report for B2.

        The report is deliberately incapable of supplying trusted Bridge
        identity fields or any digest.  A monotonically increasing observer
        sequence prevents an out-of-order HTTP completion from restoring an
        obsolete HIP session (session ABA).  An identical same-sequence report
        is the only accepted lease heartbeat.
        """

        if self.profile != B2_READ_ONLY_PROFILE:
            raise SceneQueueError(
                "CAPABILITY_MISMATCH",
                409,
                "Live capability publication is disabled in the B1 offline profile",
            )
        try:
            normalized, report_digest, catalog_digest = normalize_live_capability_report(
                report
            )
        except ValueError as exc:
            identity_hint = _live_report_identity_hint(report)
            if identity_hint is not None:
                publisher_id, observer_sequence = identity_hint
                with self._condition:
                    self._require_open_locked()
                    self._expire_live_capability_locked(self._clock())
                    self._reject_bound_live_state_locked(
                        publisher_id,
                        observer_sequence,
                        "The bound Houdini observer published an invalid capability state",
                    )
            raise SceneQueueError(
                "CAPABILITY_MISMATCH",
                409,
                "Live Houdini capability report is invalid",
            ) from exc

        with self._condition:
            self._require_open_locked()
            now = self._clock()
            self._expire_live_capability_locked(now)
            publisher_id = normalized["publisher_id"]
            sequence = normalized["observer_sequence"]

            if not _publisher_matches_process_nonce(
                str(self.expected_process_nonce), publisher_id
            ) or not hmac.compare_digest(
                normalized["hip_fingerprint"],
                _expected_live_fingerprint(
                    str(self.expected_process_nonce), normalized
                ),
            ):
                self._reject_bound_live_state_locked(
                    publisher_id,
                    sequence,
                    "Live capability lost its launched-process binding",
                )
                raise SceneQueueError(
                    "CAPABILITY_MISMATCH",
                    409,
                    "Live capability is not bound to the launched Houdini process",
                )

            if (
                self._live_publisher_id is not None
                and publisher_id != self._live_publisher_id
            ):
                raise SceneQueueError(
                    "CAPABILITY_MISMATCH",
                    409,
                    "Live capability publisher does not match the bound Houdini process",
                )
            if self._live_observer_sequence is not None:
                if sequence < self._live_observer_sequence:
                    raise SceneQueueError(
                        "CAPABILITY_MISMATCH",
                        409,
                        "Live capability observer sequence regressed",
                    )
                if sequence == self._live_observer_sequence:
                    if report_digest != self._live_report_digest:
                        self._reject_bound_live_state_locked(
                            publisher_id,
                            sequence,
                            "A live capability observer sequence was reused with different state",
                        )
                        raise SceneQueueError(
                            "CAPABILITY_MISMATCH",
                            409,
                            "A live capability observer sequence was reused with different state",
                        )
                    if normalized["available"] is not True:
                        self._attestation_expires_at = (
                            now + self.live_capability_lease_seconds
                        )
                        return None
                    current = self._attestation
                    if not isinstance(current, LiveCapabilityAttestation):
                        current = self._build_live_attestation(
                            normalized, catalog_digest
                        )
                        self._attestation = current
                    self._attestation_expires_at = (
                        now + self.live_capability_lease_seconds
                    )
                    self._condition.notify_all()
                    return current

            if normalized["available"] is not True:
                if (
                    normalized["session_observer_reliable"] is True
                    and normalized["hip_session_id"]
                    != self._current_hip_session_id
                ):
                    try:
                        self._accept_live_session_locked(
                            normalized,
                            require_reliable_revision=False,
                        )
                    except SceneQueueError:
                        self._reject_bound_live_state_locked(
                            publisher_id,
                            sequence,
                            "The unavailable capability reported an invalid HIP transition",
                        )
                        raise
                self._invalidate_live_capability_locked(
                    "Live Houdini capability was explicitly marked unavailable",
                    unavailable=True,
                )
                self._record_live_report_locked(normalized, report_digest)
                self._attestation_expires_at = (
                    now + self.live_capability_lease_seconds
                )
                return None

            if (
                normalized["session_observer_reliable"] is not True
                or normalized["revision_observer_reliable"] is not True
            ):
                raise SceneQueueError(
                    "CAPABILITY_MISMATCH",
                    409,
                    "Live HIP session and revision observers must both be reliable",
                )

            identity = (
                normalized["houdini_build"],
                normalized["python_version"],
                normalized["pyside_version"],
                catalog_digest,
            )
            if self._live_identity is not None and identity != self._live_identity:
                self._reject_bound_live_state_locked(
                    publisher_id,
                    sequence,
                    "Live Houdini build, runtime, or catalog drifted within one Bridge launch",
                )
                raise SceneQueueError(
                    "CAPABILITY_MISMATCH",
                    409,
                    "Live Houdini build, runtime, or catalog drifted within one Bridge launch",
                )
            if (
                self.expected_catalog_digest is not None
                and catalog_digest != self.expected_catalog_digest
            ):
                self._reject_bound_live_state_locked(
                    publisher_id,
                    sequence,
                    "Live Houdini catalog does not match the reviewed catalog digest",
                )
                raise SceneQueueError(
                    "CAPABILITY_MISMATCH",
                    409,
                    "Live Houdini catalog does not match the reviewed catalog digest",
                )

            current = self._attestation
            try:
                self._accept_live_session_locked(
                    normalized,
                    require_reliable_revision=True,
                )
            except SceneQueueError:
                self._reject_bound_live_state_locked(
                    publisher_id,
                    sequence,
                    "The capability reported an invalid HIP session or revision transition",
                )
                raise

            try:
                attestation = self._build_live_attestation(normalized, catalog_digest)
            except SceneQueueError:
                self._reject_bound_live_state_locked(
                    publisher_id,
                    sequence,
                    "The capability could not be bound to trusted Bridge state",
                )
                raise
            if current is not None and current.digest != attestation.digest:
                self._invalidate_active_requests_locked(
                    "Scene request was bound to an obsolete live capability attestation"
                )
            self._attestation = attestation
            self._attestation_expires_at = now + self.live_capability_lease_seconds
            self._live_identity = identity
            if self.expected_catalog_digest is None:
                self.expected_catalog_digest = catalog_digest
            self._record_live_report_locked(normalized, report_digest)
            self._condition.notify_all()
            return attestation

    def _build_live_attestation(
        self,
        report: Mapping[str, Any],
        catalog_digest: str,
    ) -> LiveCapabilityAttestation:
        try:
            return LiveCapabilityAttestation(
                profile=B2_READ_ONLY_PROFILE,
                launch_id=self.launch_id,
                generation=self.generation,
                process_nonce=str(self.expected_process_nonce),
                publisher_id=report["publisher_id"],
                observer_sequence=report["observer_sequence"],
                houdini_build=report["houdini_build"],
                python_version=report["python_version"],
                pyside_version=report["pyside_version"],
                hip_session_id=report["hip_session_id"],
                hip_fingerprint=report["hip_fingerprint"],
                scene_revision=report["scene_revision"],
                catalog_digest=catalog_digest,
                schema_digest=self.expected_schema_digest,
                session_observer_reliable=report["session_observer_reliable"],
                revision_observer_reliable=report["revision_observer_reliable"],
            )
        except ValueError as exc:
            raise SceneQueueError(
                "CAPABILITY_MISMATCH",
                409,
                "Live capability cannot be bound to trusted Bridge state",
            ) from exc

    def _record_live_report_locked(
        self,
        report: dict[str, Any],
        report_digest: str,
    ) -> None:
        self._live_report = copy.deepcopy(report)
        self._live_report_digest = report_digest
        self._live_publisher_id = report["publisher_id"]
        self._live_observer_sequence = report["observer_sequence"]

    def _reject_bound_live_state_locked(
        self,
        publisher_id: str,
        observer_sequence: int,
        message: str,
    ) -> bool:
        """Revoke and latch a rejected state attributable to the current publisher."""

        if (
            self._live_publisher_id is None
            or not hmac.compare_digest(publisher_id, self._live_publisher_id)
            or self._live_observer_sequence is None
            or observer_sequence < self._live_observer_sequence
        ):
            return False
        self._invalidate_live_capability_locked(message)
        self._live_observer_sequence = observer_sequence
        self._live_report = None
        self._live_report_digest = None
        return True

    def _accept_live_session_locked(
        self,
        report: Mapping[str, Any],
        *,
        require_reliable_revision: bool,
    ) -> None:
        """Advance the bounded HIP session/revision ledger without session ABA."""

        session_id = str(report["hip_session_id"])
        revision = int(report["scene_revision"])
        fingerprint = str(report["hip_fingerprint"])
        current_session = self._current_hip_session_id

        if self._session_history_exhausted:
            self._invalidate_live_capability_locked(
                "HIP session history capacity was exhausted"
            )
            raise SceneQueueError(
                "CAPABILITY_MISMATCH",
                409,
                "HIP session history capacity was exhausted; restart the Bridge",
            )

        if current_session is None:
            if session_id in self._retired_hip_sessions:
                self._invalidate_live_capability_locked(
                    "A retired HIP session was presented again"
                )
                raise SceneQueueError(
                    "CAPABILITY_MISMATCH",
                    409,
                    "A retired HIP session cannot become current again",
                )
            self._current_hip_session_id = session_id
            self._current_scene_revision = revision
            self._current_hip_fingerprint = fingerprint
            return

        if session_id == current_session:
            if not require_reliable_revision:
                return
            current_revision = self._current_scene_revision
            if current_revision is None or revision < current_revision:
                self._invalidate_live_capability_locked(
                    "Live scene revision regressed within one HIP session"
                )
                raise SceneQueueError(
                    "CAPABILITY_MISMATCH",
                    409,
                    "Live scene revision regressed within one HIP session",
                )
            if (
                revision == current_revision
                and self._current_hip_fingerprint is not None
                and not hmac.compare_digest(
                    fingerprint, self._current_hip_fingerprint
                )
            ):
                self._invalidate_live_capability_locked(
                    "HIP fingerprint changed without a revision advance"
                )
                raise SceneQueueError(
                    "CAPABILITY_MISMATCH",
                    409,
                    "HIP fingerprint changed without a revision advance",
                )
            self._current_scene_revision = revision
            self._current_hip_fingerprint = fingerprint
            return

        if session_id in self._retired_hip_sessions:
            self._invalidate_live_capability_locked(
                "A retired HIP session was presented again"
            )
            raise SceneQueueError(
                "CAPABILITY_MISMATCH",
                409,
                "A retired HIP session cannot become current again",
            )
        if revision != 0:
            self._invalidate_live_capability_locked(
                "A replacement HIP session did not begin at revision zero"
            )
            raise SceneQueueError(
                "CAPABILITY_MISMATCH",
                409,
                "A replacement HIP session must begin at revision zero",
            )
        self._retire_current_session_locked()
        if self._session_history_exhausted:
            self._invalidate_live_capability_locked(
                "HIP session history capacity was exhausted"
            )
            raise SceneQueueError(
                "CAPABILITY_MISMATCH",
                409,
                "HIP session history capacity was exhausted; restart the Bridge",
            )
        self._current_hip_session_id = session_id
        self._current_scene_revision = revision
        self._current_hip_fingerprint = fingerprint

    def _retire_current_session_locked(self) -> None:
        session_id = self._current_hip_session_id
        if session_id is None:
            return
        if session_id not in self._retired_hip_sessions:
            if len(self._retired_hip_sessions) >= MAX_RETIRED_HIP_SESSIONS:
                self._session_history_exhausted = True
            else:
                self._retired_hip_sessions.add(session_id)
                self._retired_hip_session_order.append(session_id)
        self._current_hip_session_id = None
        self._current_scene_revision = None
        self._current_hip_fingerprint = None

    def build_request(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
        absolute_deadline: float,
    ) -> SceneRequest:
        with self._condition:
            self._require_open_locked()
            if (
                self.profile == B2_READ_ONLY_PROFILE
                and tool_name not in self.allowed_tools
            ):
                raise SceneQueueError(
                    "TOOL_NOT_ALLOWED",
                    403,
                    "Tool is outside the active P2-V capability profile",
                    {"tool_name": tool_name, "profile": self.profile},
                )
            self._expire_live_capability_locked(self._clock())
            if self.profile == B2_READ_ONLY_PROFILE and self._attestation is None:
                raise SceneQueueError(
                    "HOUDINI_UNAVAILABLE",
                    503,
                    "No current live Houdini capability attestation is available",
                )
            try:
                plain_arguments = _plain_copy(arguments, field_name="arguments")
            except ValueError as exc:
                raise SceneQueueError("INVALID_ARGUMENT", 400, str(exc)) from exc
            if not isinstance(plain_arguments, dict):
                raise SceneQueueError(
                    "INVALID_ARGUMENT", 400, "arguments must be a JSON object"
                )
            idempotency_key = plain_arguments.get("idempotency_key")
            existing_id = (
                self._idempotency.get(idempotency_key)
                if isinstance(idempotency_key, str)
                else None
            )
            existing = self._records.get(existing_id) if existing_id is not None else None
            if existing is not None:
                try:
                    candidate = SceneRequest.build(
                        tool_name,
                        plain_arguments,
                        existing.request.absolute_deadline,
                        existing.request.launch_id,
                        existing.request.generation,
                        existing.request.attestation_digest,
                        contract_version=self.contract_version,
                    )
                except (ContractError, ValueError):
                    candidate = None
                if (
                    candidate is not None
                    and candidate.request_digest == existing.request.request_digest
                ):
                    return copy.deepcopy(existing.request)
                raise SceneQueueError(
                    "IDEMPOTENCY_CONFLICT",
                    409,
                    "Idempotency key was already used for different request content",
                    {"idempotency_key": idempotency_key},
                )
            if self._attestation is None:
                raise SceneQueueError(
                    "HOUDINI_UNAVAILABLE",
                    503,
                    "No current fake capability attestation is available",
                )
            try:
                return SceneRequest.build(
                    tool_name,
                    plain_arguments,
                    absolute_deadline,
                    self.launch_id,
                    self.generation,
                    self._attestation.digest,
                    contract_version=self.contract_version,
                )
            except ContractError as exc:
                status = 409 if exc.code in {"DIGEST_MISMATCH", "APPROVAL_MISMATCH"} else 400
                raise SceneQueueError(
                    exc.code, status, exc.message, exc.details
                ) from exc
            except ValueError as exc:
                raise SceneQueueError(
                    "INVALID_ARGUMENT", 400, str(exc)
                ) from exc

    def submit(self, request: SceneRequest) -> RequestSnapshot:
        if not isinstance(request, SceneRequest):
            raise SceneQueueError("INVALID_ARGUMENT", 400, "Scene request is invalid")
        with self._condition:
            self._require_open_locked()
            if (
                self.profile == B2_READ_ONLY_PROFILE
                and request.tool_name not in self.allowed_tools
            ):
                raise SceneQueueError(
                    "TOOL_NOT_ALLOWED",
                    403,
                    "Tool is outside the active P2-V capability profile",
                    {"tool_name": request.tool_name, "profile": self.profile},
                )
            self._expire_live_capability_locked(self._clock())
            if self.profile == B2_READ_ONLY_PROFILE and self._attestation is None:
                raise SceneQueueError(
                    "HOUDINI_UNAVAILABLE",
                    503,
                    "No current live Houdini capability attestation is available",
                )
            self._expire_locked(self._clock())
            self._validate_request_integrity_locked(request)
            request_id = _argument_identifier(request.arguments, "request_id")
            idempotency_key = _argument_identifier(request.arguments, "idempotency_key")

            existing_id = self._idempotency.get(idempotency_key)
            if existing_id is not None:
                existing = self._records.get(existing_id)
                if existing is not None and existing.request.request_digest == request.request_digest:
                    return self._snapshot(existing, replayed=True)
                raise SceneQueueError(
                    "IDEMPOTENCY_CONFLICT",
                    409,
                    "Idempotency key was already used for different request content",
                    {"idempotency_key": idempotency_key},
                )

            existing_request = self._records.get(request_id)
            if existing_request is not None:
                if existing_request.request.request_digest == request.request_digest:
                    return self._snapshot(existing_request, replayed=True)
                raise SceneQueueError(
                    "REQUEST_ID_CONFLICT",
                    409,
                    "Request identifier was already used for different request content",
                    {"request_id": request_id},
                )
            reserved_write = (
                self._records.get(self._active_write_request_id)
                if self._active_write_request_id is not None
                else None
            )
            if (
                reserved_write is not None
                and reserved_write.state == "indeterminate"
                and reserved_write.structured_error is not None
                and reserved_write.structured_error.get("code")
                == "SCENE_STATE_INDETERMINATE"
            ):
                raise SceneQueueError(
                    "SCENE_STATE_INDETERMINATE",
                    409,
                    "Scene requests are blocked until the indeterminate graph apply is reconciled",
                    {"request_id": reserved_write.request_id},
                )
            self._validate_request_locked(request)
            if (
                request.tool_name == WRITE_TOOL
                and self._active_write_request_id is not None
            ):
                raise SceneQueueError(
                    "WRITE_IN_PROGRESS",
                    409,
                    "Only one graph apply may be reserved at a time",
                    {"request_id": self._active_write_request_id},
                )
            if len(self._records) >= self.terminal_retention:
                raise SceneQueueError(
                    "QUEUE_FULL",
                    429,
                    "Retained request ledger is at capacity; refusing unsafe idempotency reuse",
                    {"terminal_retention": self.terminal_retention},
                )
            if self._active_count >= self.capacity:
                raise SceneQueueError(
                    "QUEUE_FULL",
                    429,
                    "Scene request queue is at capacity",
                    {"capacity": self.capacity},
                )

            state = "awaiting_approval" if request.tool_name == WRITE_TOOL else "queued"
            trusted_request = SceneRequest(
                tool_name=request.tool_name,
                arguments=_plain_copy(request.arguments, field_name="arguments"),
                absolute_deadline=request.absolute_deadline,
                launch_id=request.launch_id,
                generation=request.generation,
                attestation_digest=request.attestation_digest,
                approval_payload=copy.deepcopy(request.approval_payload),
                approval_binding_digest=request.approval_binding_digest,
                request_digest=request.request_digest,
            )
            record = _Record(
                request=trusted_request,
                request_id=request_id,
                idempotency_key=idempotency_key,
                state=state,
            )
            self._records[request_id] = record
            self._idempotency[idempotency_key] = request_id
            self._active_count += 1
            if request.tool_name == WRITE_TOOL:
                self._active_write_request_id = request_id
            if state == "queued":
                self._claimable.append(request_id)
            else:
                self._approval_presentable.append(request_id)
            self._condition.notify_all()
            return self._snapshot(record)

    def decide_approval(
        self,
        request_id: str,
        decision: str,
        request_digest: str,
        launch_id: str,
        generation: int,
    ) -> RequestSnapshot:
        if self.profile == B2_READ_ONLY_PROFILE:
            raise SceneQueueError(
                "TOOL_NOT_ALLOWED",
                403,
                "Scene approvals are disabled in the B2 read-only profile",
            )
        _queue_identifier(request_id, "request_id")
        _queue_sha256(request_digest, "request_digest")
        if decision not in {"allow", "deny"}:
            raise SceneQueueError(
                "INVALID_APPROVAL_DECISION",
                400,
                "Approval decision must be 'allow' or 'deny'",
            )
        with self._condition:
            self._require_open_locked()
            now = self._clock()
            self._expire_locked(now)
            if launch_id != self.launch_id or generation != self.generation:
                raise SceneQueueError(
                    "CAPABILITY_MISMATCH",
                    409,
                    "Approval does not match this Bridge launch",
                    {"generation": self.generation},
                )
            record = self._record_locked(request_id)
            if record.request.tool_name != WRITE_TOOL:
                raise SceneQueueError(
                    "APPROVAL_NOT_REQUIRED", 409, "Read-only requests cannot receive approval"
                )
            if not record.approval_presented:
                raise SceneQueueError(
                    "APPROVAL_NOT_PRESENTED",
                    409,
                    "Scene write must be presented to the Panel before approval",
                    {"request_id": request_id},
                )
            if record.request.request_digest != request_digest:
                raise SceneQueueError(
                    "APPROVAL_DIGEST_MISMATCH",
                    409,
                    "Approval digest does not match the exact request",
                    {"request_id": request_id},
                )
            if record.state != "awaiting_approval" or record.approval_resolved:
                raise SceneQueueError(
                    "APPROVAL_ALREADY_RESOLVED",
                    409,
                    "Approval has already been resolved",
                    {"request_id": request_id, "state": record.state},
                )
            record.approval_resolved = True
            if decision == "deny":
                self._terminalize_error_locked(
                    record,
                    "denied",
                    SceneQueueError(
                        "APPROVAL_DENIED",
                        403,
                        "Scene write approval was denied",
                        {"request_id": request_id},
                    ),
                )
                return self._snapshot(record)

            expires_at = min(record.request.absolute_deadline, now + APPROVAL_TTL_SECONDS)
            if expires_at <= now:
                self._terminalize_error_locked(
                    record,
                    "expired",
                    SceneQueueError(
                        "APPROVAL_EXPIRED",
                        408,
                        "Scene write approval expired before execution",
                        {"request_id": request_id},
                    ),
                )
                return self._snapshot(record)
            if record.request.approval_binding_digest is None:
                raise SceneQueueError(
                    "APPROVAL_MISMATCH",
                    409,
                    "Scene write has no trusted approval binding",
                    {"request_id": request_id},
                )
            record.approval = _ApprovalProof(
                request_digest,
                record.request.approval_binding_digest,
                expires_at,
            )
            record.state = "queued"
            self._claimable.append(request_id)
            self._condition.notify_all()
            return self._snapshot(record)

    def claim_next(self, timeout: float = 0.0) -> Claim | None:
        timeout = _require_wait_timeout(timeout)
        wait_deadline = time.monotonic() + timeout
        with self._condition:
            while True:
                if self._shutdown:
                    return None
                self._expire_live_capability_locked(self._clock())
                self._expire_locked(self._clock())
                claim = self._claim_one_locked()
                if claim is not None:
                    return claim
                remaining = wait_deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._condition.wait(remaining)

    def poll_next(self, timeout: float = 0.0) -> PanelWork | None:
        """Return one approval presentation or one claimed execution item.

        Each approval is presented at most once.  An execute item is claimed
        atomically and therefore also delivered at most once.
        """

        timeout = _require_wait_timeout(timeout)
        wait_deadline = time.monotonic() + timeout
        with self._condition:
            while True:
                if self._shutdown:
                    return None
                self._expire_live_capability_locked(self._clock())
                self._expire_locked(self._clock())
                while self._approval_presentable:
                    request_id = self._approval_presentable.popleft()
                    record = self._records.get(request_id)
                    if (
                        record is None
                        or record.state != "awaiting_approval"
                        or record.approval_presented
                    ):
                        continue
                    record.approval_presented = True
                    return PanelWork(
                        kind="approval_required",
                        request_id=record.request_id,
                        tool_name=record.request.tool_name,
                        arguments=copy.deepcopy(record.request.arguments),
                        request_digest=record.request.request_digest,
                        attestation_digest=record.request.attestation_digest,
                        absolute_deadline=record.request.absolute_deadline,
                        approval_payload=copy.deepcopy(
                            record.request.approval_payload
                        ),
                        approval_binding_digest=(
                            record.request.approval_binding_digest
                        ),
                    )
                claim = self._claim_one_locked()
                if claim is not None:
                    return PanelWork(
                        kind="execute",
                        request_id=claim.request_id,
                        tool_name=claim.tool_name,
                        arguments=claim.arguments,
                        request_digest=claim.request_digest,
                        attestation_digest=claim.attestation_digest,
                        absolute_deadline=claim.absolute_deadline,
                        executor_token=claim.claim_token,
                        cancel_requested=claim.cancel_requested,
                    )
                remaining = wait_deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._condition.wait(remaining)

    def complete(
        self,
        request_id: str,
        executor_token: str,
        result: Mapping[str, Any],
    ) -> RequestSnapshot:
        _queue_identifier(request_id, "request_id")
        if not isinstance(executor_token, str) or not executor_token:
            raise SceneQueueError("INVALID_CLAIM", 409, "Executor claim is invalid")
        try:
            plain_result = _plain_copy(result, field_name="result")
        except ValueError as exc:
            raise SceneQueueError("INVALID_RESULT", 400, str(exc)) from exc
        if not isinstance(plain_result, dict):
            raise SceneQueueError("INVALID_RESULT", 400, "result must be a JSON object")
        result_digest = _sha256(plain_result)
        with self._condition:
            self._expire_live_capability_locked(self._clock())
            record = self._record_locked(request_id)
            if record.claim_token != executor_token:
                raise SceneQueueError(
                    "INVALID_CLAIM",
                    409,
                    "Executor claim does not match the request",
                    {"request_id": request_id},
                )
            if record.state in _TERMINAL_STATES:
                if record.result_digest == result_digest:
                    return self._snapshot(record, replayed=True)
                raise SceneQueueError(
                    "RESULT_CONFLICT",
                    409,
                    "A different terminal result was already recorded",
                    {"request_id": request_id},
                )
            if record.state != "claimed":
                raise SceneQueueError(
                    "INVALID_CLAIM_STATE",
                    409,
                    "Request is not claimed by an executor",
                    {"request_id": request_id, "state": record.state},
                )
            if self._clock() >= record.request.absolute_deadline:
                is_write = record.request.tool_name == WRITE_TOOL
                self._terminalize_error_locked(
                    record,
                    "indeterminate" if is_write else "expired",
                    SceneQueueError(
                        (
                            "SCENE_STATE_INDETERMINATE"
                            if is_write
                            else "DEADLINE_EXCEEDED"
                        ),
                        408,
                        (
                            "Graph apply deadline expired after executor claim; "
                            "scene state requires trusted reconciliation"
                            if is_write
                            else "Scene request deadline expired before result commit"
                        ),
                        {"request_id": request_id},
                    ),
                    retain_write_reservation=is_write,
                )
                if is_write:
                    self._freeze_other_requests_for_indeterminate_write_locked(
                        record
                    )
                return self._snapshot(record)
            if self.profile == B2_READ_ONLY_PROFILE:
                self._validate_live_result_locked(record, plain_result)
            if record.request.tool_name == WRITE_TOOL and plain_result.get("ok") is True:
                tool_result = plain_result.get("result")
                expected_binding = record.request.approval_binding_digest
                expected_graph = record.request.arguments.get("canonical_graph_digest")
                if (
                    not isinstance(tool_result, Mapping)
                    or not isinstance(expected_binding, str)
                    or not isinstance(tool_result.get("approval_binding_digest"), str)
                    or tool_result["approval_binding_digest"].casefold()
                    != expected_binding.casefold()
                    or not isinstance(expected_graph, str)
                    or not isinstance(tool_result.get("canonical_graph_digest"), str)
                    or tool_result["canonical_graph_digest"].casefold()
                    != expected_graph.casefold()
                ):
                    raise SceneQueueError(
                        "APPROVAL_MISMATCH",
                        409,
                        "Successful graph apply result does not match its trusted approval binding",
                        {"request_id": request_id},
                    )
            record.result = plain_result
            record.result_digest = result_digest
            self._terminalize_locked(record, "completed")
            return self._snapshot(record)

    def _validate_live_result_locked(
        self,
        record: _Record,
        result: Mapping[str, Any],
    ) -> None:
        current = self._attestation
        if (
            not isinstance(current, LiveCapabilityAttestation)
            or record.request.attestation_digest != current.digest
        ):
            raise SceneQueueError(
                "CAPABILITY_MISMATCH",
                409,
                "Live read result is not bound to the current capability attestation",
                {"request_id": record.request_id},
            )
        if result.get("ok") is not True:
            return
        payload = result.get("result")
        if not isinstance(payload, Mapping):
            raise SceneQueueError(
                "CAPABILITY_MISMATCH",
                409,
                "Live read result has no attested payload",
                {"request_id": record.request_id},
            )
        if record.request.tool_name == "houdini_scene_info":
            if (
                payload.get("houdini_build") != current.houdini_build
                or not isinstance(payload.get("hip_fingerprint"), str)
                or payload["hip_fingerprint"].casefold()
                != current.hip_fingerprint.casefold()
            ):
                raise SceneQueueError(
                    "CAPABILITY_MISMATCH",
                    409,
                    "Live scene information contradicts its capability attestation",
                    {"request_id": record.request_id},
                )
            return
        if record.request.tool_name != "houdini_node_type_info":
            raise SceneQueueError(
                "TOOL_NOT_ALLOWED",
                403,
                "Only the two B2 read tools may produce a live result",
            )
        catalog = self._live_report.get("catalog") if self._live_report else None
        observed = payload.get("node_types")
        requested = record.request.arguments.get("node_types")
        if not isinstance(catalog, list) or not isinstance(observed, list) or not isinstance(
            requested, list
        ):
            raise SceneQueueError(
                "CAPABILITY_MISMATCH",
                409,
                "Live node-type result cannot be checked against the attested catalog",
                {"request_id": record.request_id},
            )
        catalog_by_key = {
            (item.get("context"), item.get("requested_name")): item
            for item in catalog
            if isinstance(item, Mapping)
        }
        expected = []
        for item in requested:
            if not isinstance(item, Mapping):
                expected = []
                break
            candidate = catalog_by_key.get((item.get("context"), item.get("name")))
            if candidate is None:
                expected = []
                break
            expected.append(candidate)
        observed_by_key = {
            (item.get("context"), item.get("requested_name")): item
            for item in observed
            if isinstance(item, Mapping)
        }
        expected_sorted = sorted(
            expected, key=lambda item: (item.get("context"), item.get("requested_name"))
        )
        observed_sorted = sorted(
            observed_by_key.values(),
            key=lambda item: (item.get("context"), item.get("requested_name")),
        )
        if (
            len(expected) != len(requested)
            or len(observed_by_key) != len(observed)
            or _sha256(expected_sorted) != _sha256(observed_sorted)
        ):
            raise SceneQueueError(
                "CAPABILITY_MISMATCH",
                409,
                "Live node-type result differs from the attested catalog",
                {"request_id": record.request_id},
            )

    def cancel(self, request_id: str) -> RequestSnapshot:
        _queue_identifier(request_id, "request_id")
        with self._condition:
            record = self._record_locked(request_id)
            if record.state in _TERMINAL_STATES:
                return self._snapshot(record, replayed=True)
            if record.state == "claimed":
                record.cancel_requested = True
                self._condition.notify_all()
                return self._snapshot(record)
            self._terminalize_error_locked(
                record,
                "cancelled",
                SceneQueueError(
                    "CANCELLED",
                    409,
                    "Scene request was cancelled before executor claim",
                    {"request_id": request_id},
                ),
            )
            return self._snapshot(record)

    def get_result(self, request_id: str, wait_timeout: float = 0.0) -> RequestSnapshot:
        _queue_identifier(request_id, "request_id")
        wait_timeout = _require_wait_timeout(wait_timeout)
        wait_deadline = time.monotonic() + wait_timeout
        with self._condition:
            while True:
                self._expire_live_capability_locked(self._clock())
                self._expire_locked(self._clock())
                record = self._record_locked(request_id)
                if record.state in _TERMINAL_STATES:
                    return self._snapshot(record)
                remaining = wait_deadline - time.monotonic()
                if remaining <= 0:
                    return self._snapshot(record)
                self._condition.wait(remaining)

    def shutdown(self) -> None:
        with self._condition:
            if self._shutdown:
                return
            self._shutdown = True
            for record in list(self._records.values()):
                if record.state not in _TERMINAL_STATES:
                    self._terminalize_error_locked(
                        record,
                        "shutdown",
                        SceneQueueError(
                            "SHUTTING_DOWN",
                            503,
                            "Bridge scene queue is shutting down",
                            {"request_id": record.request_id},
                        ),
                    )
            self._condition.notify_all()

    def _validate_request_locked(self, request: SceneRequest) -> None:
        self._validate_request_integrity_locked(request)
        if self._attestation is None:
            raise SceneQueueError(
                "HOUDINI_UNAVAILABLE",
                503,
                "No current fake capability attestation is available",
            )
        if request.attestation_digest != self._attestation.digest:
            raise SceneQueueError(
                "CAPABILITY_MISMATCH",
                409,
                "Scene request is not bound to the current capability attestation",
                {"generation": self.generation},
            )
        now = self._clock()
        if request.absolute_deadline <= now:
            raise SceneQueueError(
                "DEADLINE_EXCEEDED", 408, "Scene request deadline has expired"
            )
        permission = request.arguments.get("permission_level")
        expected_permission = "scene_write" if request.tool_name == WRITE_TOOL else "scene_read"
        if permission != expected_permission:
            raise SceneQueueError(
                "PERMISSION_MISMATCH",
                403,
                "Tool permission does not match its frozen access level",
                {"tool_name": request.tool_name},
            )
        if request.arguments.get("hip_session_id") != self._attestation.hip_session_id:
            raise SceneQueueError(
                "HIP_SESSION_MISMATCH", 409, "HIP session does not match the current fixture"
            )
        if request.arguments.get("base_scene_revision") != self._attestation.scene_revision:
            raise SceneQueueError(
                "SCENE_CONFLICT",
                409,
                "Scene revision does not match the current fixture",
                {"scene_revision": self._attestation.scene_revision},
            )
        supplied_fingerprint = request.arguments.get("expected_hip_fingerprint")
        if "expected_hip_fingerprint" in request.arguments and (
            not isinstance(supplied_fingerprint, str)
            or supplied_fingerprint.casefold()
            != self._attestation.hip_fingerprint.casefold()
        ):
            raise SceneQueueError(
                "CAPABILITY_MISMATCH", 409, "HIP fingerprint does not match the current fixture"
            )

    def _validate_request_integrity_locked(self, request: SceneRequest) -> None:
        if request.tool_name not in self.allowed_tools:
            raise SceneQueueError(
                "TOOL_NOT_ALLOWED",
                403,
                "Tool is outside the active P2-V capability profile",
                {
                    "tool_name": request.tool_name,
                    "profile": self.profile,
                },
            )
        if request.launch_id != self.launch_id or request.generation != self.generation:
            raise SceneQueueError(
                "CAPABILITY_MISMATCH",
                409,
                "Scene request is not bound to this Bridge launch",
                {"generation": self.generation},
            )
        try:
            _require_deadline(request.absolute_deadline)
            _require_sha256(request.attestation_digest, "attestation_digest")
            _require_sha256(request.request_digest, "request_digest")
            if not isinstance(request.arguments, Mapping):
                raise ValueError("arguments must be a JSON object")
            expected_request = SceneRequest.build(
                request.tool_name,
                request.arguments,
                request.absolute_deadline,
                request.launch_id,
                request.generation,
                request.attestation_digest,
                contract_version=self.contract_version,
            )
        except ContractError as exc:
            raise SceneQueueError(
                exc.code,
                409 if exc.code in {"DIGEST_MISMATCH", "APPROVAL_MISMATCH"} else 400,
                exc.message,
                exc.details,
            ) from exc
        except ValueError as exc:
            raise SceneQueueError("INVALID_ARGUMENT", 400, str(exc)) from exc
        if request != expected_request:
            raise SceneQueueError(
                "REQUEST_DIGEST_MISMATCH",
                409,
                "Scene request or approval binding is not canonical",
            )

    def _claim_one_locked(self) -> Claim | None:
        for _ in range(len(self._claimable)):
            request_id = self._claimable.popleft()
            record = self._records.get(request_id)
            if record is None or record.state != "queued":
                continue
            is_write = record.request.tool_name == WRITE_TOOL
            if is_write and self._active_write_request_id != request_id:
                self._claimable.append(request_id)
                continue
            if is_write:
                proof = record.approval
                now = self._clock()
                if (
                    proof is None
                    or proof.used
                    or proof.request_digest != record.request.request_digest
                    or proof.approval_binding_digest
                    != record.request.approval_binding_digest
                    or proof.expires_at <= now
                ):
                    self._terminalize_error_locked(
                        record,
                        "expired",
                        SceneQueueError(
                            "APPROVAL_EXPIRED",
                            408,
                            "Scene write approval is absent, expired, or already used",
                            {"request_id": request_id},
                        ),
                    )
                    continue
                proof.used = True
            record.claim_token = secrets.token_hex(32)
            record.state = "claimed"
            return Claim(
                request_id=record.request_id,
                tool_name=record.request.tool_name,
                arguments=copy.deepcopy(record.request.arguments),
                request_digest=record.request.request_digest,
                attestation_digest=record.request.attestation_digest,
                absolute_deadline=record.request.absolute_deadline,
                claim_token=record.claim_token,
                cancel_requested=record.cancel_requested,
            )
        return None

    def _expire_live_capability_locked(self, now: float) -> None:
        if self.profile != B2_READ_ONLY_PROFILE:
            return
        expires_at = self._attestation_expires_at
        if expires_at is None or now < expires_at:
            return
        self._retire_current_session_locked()
        self._invalidate_live_capability_locked(
            "Live Houdini capability lease expired",
            release_publisher=True,
            unavailable=True,
        )

    def _invalidate_live_capability_locked(
        self,
        message: str,
        *,
        release_publisher: bool = False,
        unavailable: bool = False,
    ) -> None:
        self._attestation = None
        self._attestation_expires_at = (
            self._clock() + self.live_capability_lease_seconds
            if (
                not release_publisher
                and self.profile == B2_READ_ONLY_PROFILE
                and self._live_publisher_id is not None
            )
            else None
        )
        if release_publisher:
            # A closed/reopened Panel is a new local executor lease.  Keep the
            # Bridge-pinned build/runtime/catalog identity, but release the
            # expired publisher sequence so a fresh observer can establish a
            # new HIP session instead of remaining unavailable forever.
            self._live_report = None
            self._live_report_digest = None
            self._live_publisher_id = None
            self._live_observer_sequence = None
        self._invalidate_active_requests_locked(message, unavailable=unavailable)
        self._condition.notify_all()

    def _invalidate_active_requests_locked(
        self,
        message: str,
        *,
        unavailable: bool = False,
    ) -> None:
        code = "HOUDINI_UNAVAILABLE" if unavailable else "CAPABILITY_MISMATCH"
        status = 503 if unavailable else 409
        for record in list(self._records.values()):
            if record.state in _TERMINAL_STATES:
                continue
            self._terminalize_error_locked(
                record,
                "expired",
                SceneQueueError(
                    code,
                    status,
                    message,
                    {"request_id": record.request_id},
                ),
            )

    def _expire_locked(self, now: float) -> None:
        for record in list(self._records.values()):
            if record.state in _TERMINAL_STATES:
                continue
            if now >= record.request.absolute_deadline:
                is_claimed_write = (
                    record.state == "claimed"
                    and record.request.tool_name == WRITE_TOOL
                )
                if record.state == "claimed":
                    record.cancel_requested = True
                self._terminalize_error_locked(
                    record,
                    "indeterminate" if is_claimed_write else "expired",
                    SceneQueueError(
                        (
                            "SCENE_STATE_INDETERMINATE"
                            if is_claimed_write
                            else "DEADLINE_EXCEEDED"
                        ),
                        408,
                        (
                            "Graph apply deadline expired after executor claim; "
                            "scene state requires trusted reconciliation"
                            if is_claimed_write
                            else "Scene request deadline has expired"
                        ),
                        {"request_id": record.request_id},
                    ),
                    retain_write_reservation=is_claimed_write,
                )
                if is_claimed_write:
                    self._freeze_other_requests_for_indeterminate_write_locked(
                        record
                    )
            elif (
                record.request.tool_name == WRITE_TOOL
                and record.state == "queued"
                and record.approval is not None
                and now >= record.approval.expires_at
            ):
                self._terminalize_error_locked(
                    record,
                    "expired",
                    SceneQueueError(
                        "APPROVAL_EXPIRED",
                        408,
                        "Scene write approval has expired",
                        {"request_id": record.request_id},
                    ),
                )

    def _terminalize_error_locked(
        self,
        record: _Record,
        state: str,
        error: SceneQueueError,
        *,
        retain_write_reservation: bool = False,
    ) -> None:
        if record.state in _TERMINAL_STATES:
            return
        record.structured_error = error.to_dict()
        record.result = None
        record.result_digest = _sha256(record.structured_error)
        self._terminalize_locked(
            record,
            state,
            release_write_reservation=not retain_write_reservation,
        )

    def _freeze_other_requests_for_indeterminate_write_locked(
        self, write_record: _Record
    ) -> None:
        """Fail every old-snapshot request after a claimed write becomes unknown."""

        for record in list(self._records.values()):
            if record is write_record or record.state in _TERMINAL_STATES:
                continue
            self._terminalize_error_locked(
                record,
                "indeterminate",
                SceneQueueError(
                    "SCENE_STATE_INDETERMINATE",
                    409,
                    "Request snapshot became unsafe after an indeterminate graph apply",
                    {
                        "request_id": record.request_id,
                        "write_request_id": write_record.request_id,
                    },
                ),
            )

    def _terminalize_locked(
        self,
        record: _Record,
        state: str,
        *,
        release_write_reservation: bool = True,
    ) -> None:
        if record.state in _TERMINAL_STATES:
            return
        was_active = record.state not in _TERMINAL_STATES
        record.state = state
        if (
            release_write_reservation
            and self._active_write_request_id == record.request_id
        ):
            self._active_write_request_id = None
        if was_active:
            self._active_count -= 1
        self._terminal_order.append(record.request_id)
        self._condition.notify_all()

    def _record_locked(self, request_id: str) -> _Record:
        record = self._records.get(request_id)
        if record is None:
            raise SceneQueueError(
                "REQUEST_NOT_FOUND",
                404,
                "Scene request was not found",
                {"request_id": request_id},
            )
        return record

    def _require_open_locked(self) -> None:
        if self._shutdown:
            raise SceneQueueError(
                "SHUTTING_DOWN", 503, "Bridge scene queue is shutting down"
            )

    @staticmethod
    def _snapshot(record: _Record, replayed: bool = False) -> RequestSnapshot:
        return RequestSnapshot(
            request_id=record.request_id,
            tool_name=record.request.tool_name,
            state=record.state,
            request_digest=record.request.request_digest,
            idempotency_key=record.idempotency_key,
            absolute_deadline=record.request.absolute_deadline,
            replayed=replayed,
            result=copy.deepcopy(record.result),
            structured_error=copy.deepcopy(record.structured_error),
            cancel_requested=record.cancel_requested,
        )


def _argument_identifier(arguments: Mapping[str, Any], name: str) -> str:
    value = arguments.get(name)
    try:
        return _require_identifier(value, name)
    except ValueError as exc:
        raise SceneQueueError(
            "INVALID_ARGUMENT", 400, str(exc), {"field": name}
        ) from exc


def _queue_identifier(value: Any, name: str) -> str:
    try:
        return _require_identifier(value, name)
    except ValueError as exc:
        raise SceneQueueError(
            "INVALID_ARGUMENT", 400, str(exc), {"field": name}
        ) from exc


def _queue_sha256(value: Any, name: str) -> str:
    try:
        return _require_sha256(value, name)
    except ValueError as exc:
        raise SceneQueueError(
            "INVALID_ARGUMENT", 400, str(exc), {"field": name}
        ) from exc


def _request_digest(
    tool_name: str,
    arguments: Mapping[str, Any],
    absolute_deadline: float,
    launch_id: str,
    generation: int,
    attestation_digest: str,
    approval_payload: Mapping[str, Any] | None = None,
    approval_binding_digest_value: str | None = None,
    *,
    contract_version: str = B1_CONTRACT_VERSION,
) -> str:
    return _sha256(
        {
            "contract_version": contract_version,
            "tool_name": tool_name,
            "arguments": arguments,
            "absolute_deadline_hex": float(absolute_deadline).hex(),
            "launch_id": launch_id,
            "generation": generation,
            "attestation_digest": attestation_digest,
            "approval_payload": approval_payload,
            "approval_binding_digest": approval_binding_digest_value,
        }
    )


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _publisher_matches_process_nonce(process_nonce: str, publisher_id: str) -> bool:
    prefix = "panel-" + hashlib.sha256(process_nonce.encode("utf-8")).hexdigest()[:16] + "-"
    if not publisher_id.startswith(prefix):
        return False
    suffix = publisher_id[len(prefix) :]
    return bool(re.fullmatch(r"[a-f0-9]{16}", suffix))


def _live_report_identity_hint(value: Any) -> tuple[str, int] | None:
    """Extract only enough valid identity to revoke a malformed newer report."""

    if not isinstance(value, Mapping):
        return None
    try:
        publisher_id = value.get("publisher_id")
        observer_sequence = value.get("observer_sequence")
    except Exception:
        return None
    if (
        not isinstance(publisher_id, str)
        or _IDENTIFIER_RE.fullmatch(publisher_id) is None
        or isinstance(observer_sequence, bool)
        or not isinstance(observer_sequence, int)
        or not 0 <= observer_sequence <= 9_007_199_254_740_991
    ):
        return None
    return publisher_id, observer_sequence


def _expected_live_fingerprint(
    process_nonce: str,
    report: Mapping[str, Any],
) -> str:
    key = hashlib.sha256(
        b"hia-b2-fingerprint\0" + process_nonce.encode("utf-8")
    ).digest()
    payload = "\x1f".join(
        (
            "hia-b2-safe-hip-fingerprint-v1",
            str(report["publisher_id"]),
            str(report["houdini_build"]),
            str(report["hip_session_id"]),
            str(report["scene_revision"]),
        )
    ).encode("utf-8")
    return hmac.new(key, payload, hashlib.sha256).hexdigest()


def _canonical_bytes(value: Any) -> bytes:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError, UnicodeEncodeError, RecursionError) as exc:
        raise ValueError("value must be finite JSON data") from exc
    return encoded.encode("utf-8")


def _plain_copy(value: Any, *, field_name: str) -> Any:
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
        return json.loads(encoded)
    except (TypeError, ValueError, UnicodeEncodeError, RecursionError) as exc:
        raise ValueError(f"{field_name} must contain only finite JSON values") from exc


def normalize_live_capability_report(
    value: Mapping[str, Any],
) -> tuple[dict[str, Any], str, str]:
    """Validate and canonicalize the complete untrusted Panel report."""

    if not isinstance(value, Mapping) or set(value) != LIVE_CAPABILITY_REPORT_FIELDS:
        raise ValueError("live capability report has an unexpected field set")
    if len(_canonical_bytes(value)) > MAX_LIVE_CAPABILITY_REPORT_BYTES:
        raise ValueError("live capability report exceeds the fixed byte limit")
    report = _plain_copy(value, field_name="live capability report")
    if not isinstance(report, dict):
        raise ValueError("live capability report must be an object")
    if not isinstance(report["available"], bool):
        raise ValueError("available must be boolean")
    report["publisher_id"] = _require_identifier(
        report["publisher_id"], "publisher_id"
    )
    report["observer_sequence"] = _bounded_integer(
        report["observer_sequence"], "observer_sequence", 0, 9007199254740991
    )
    report["houdini_build"] = _require_safe_catalog_text(
        report["houdini_build"], "houdini_build", 128
    )
    if _HOUDINI_BUILD_RE.fullmatch(report["houdini_build"]) is None:
        raise ValueError("houdini_build does not match the B2 output contract")
    for field in ("python_version", "pyside_version"):
        report[field] = _require_safe_catalog_text(report[field], field, 128)
    report["hip_session_id"] = _require_identifier(
        report["hip_session_id"], "hip_session_id"
    )
    report["hip_fingerprint"] = _require_sha256(
        report["hip_fingerprint"], "hip_fingerprint"
    )
    report["scene_revision"] = _bounded_integer(
        report["scene_revision"], "scene_revision", 0, 9007199254740991
    )
    for field in ("session_observer_reliable", "revision_observer_reliable"):
        if not isinstance(report[field], bool):
            raise ValueError(f"{field} must be boolean")

    catalog = _normalize_live_catalog(report["catalog"])
    if report["available"] is True:
        if (
            report["session_observer_reliable"] is not True
            or report["revision_observer_reliable"] is not True
        ):
            raise ValueError("available capability requires reliable observers")
        if any(item["available"] is not True for item in catalog):
            raise ValueError("available capability requires all five reviewed node types")
    report["catalog"] = catalog
    catalog_digest = _sha256(catalog)
    return report, _sha256(report), catalog_digest


def _normalize_live_catalog(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) != len(_B2_CATALOG_TYPES):
        raise ValueError("catalog must contain exactly five node types")
    normalized: dict[tuple[str, str], dict[str, Any]] = {}
    for raw in value:
        if not isinstance(raw, dict) or set(raw) != _NODE_TYPE_FIELDS:
            raise ValueError("catalog node type has an unexpected field set")
        context = raw.get("context")
        requested_name = raw.get("requested_name")
        key = (context, requested_name)
        if key not in _B2_CATALOG_INDEX or key in normalized:
            raise ValueError("catalog node types must exactly match the B2 allowlist")
        available = raw.get("available")
        if not isinstance(available, bool):
            raise ValueError("catalog availability must be boolean")
        if raw.get("creatable") is not False:
            raise ValueError("B2 node types must not be advertised as creatable")
        if raw.get("schema_source") != "live_houdini_instance":
            raise ValueError("catalog schema source must be the live Houdini instance")
        resolved_name = raw.get("resolved_name")
        parameters = raw.get("parameters")
        if not isinstance(parameters, list) or len(parameters) > 512:
            raise ValueError("catalog parameters must be a bounded array")
        input_count = _bounded_integer(
            raw.get("input_count"), "input_count", 0, 65535
        )
        output_count = _bounded_integer(
            raw.get("output_count"), "output_count", 0, 64
        )
        if available:
            if (
                not isinstance(resolved_name, str)
                or _RESOLVED_TYPE_RE.fullmatch(resolved_name) is None
            ):
                raise ValueError("available node type must have a safe canonical name")
            if resolved_name != _B2_RESOLVED_TYPE_NAMES[key]:
                raise ValueError(
                    "available node type does not match the reviewed live type"
                )
        elif resolved_name is not None or parameters or input_count or output_count:
            raise ValueError("unavailable node type cannot expose live metadata")
        normalized_parameters = sorted(
            (_normalize_live_parameter(item) for item in parameters),
            key=lambda item: item["name"],
        )
        names = [item["name"] for item in normalized_parameters]
        if len(names) != len(set(names)):
            raise ValueError("live parameter names must be unique")
        normalized[key] = {
            "context": context,
            "requested_name": requested_name,
            "resolved_name": resolved_name,
            "available": available,
            "creatable": False,
            "schema_source": "live_houdini_instance",
            "parameters": normalized_parameters,
            "input_count": input_count,
            "output_count": output_count,
        }
    return [normalized[key] for key in _B2_CATALOG_TYPES]


def _normalize_live_parameter(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != _PARAMETER_FIELDS:
        raise ValueError("live parameter has an unexpected field set")
    name = value.get("name")
    if not isinstance(name, str) or _PARAMETER_NAME_RE.fullmatch(name) is None:
        raise ValueError("live parameter name is invalid")
    label = _require_safe_catalog_text(value.get("label"), "parameter label", 256)
    value_type = value.get("value_type")
    if value_type not in {"float", "int", "bool", "string", "tuple"}:
        raise ValueError("live parameter value type is not admitted")
    tuple_size = _bounded_integer(value.get("tuple_size"), "tuple_size", 1, 16)
    if value.get("writable") is not False or value.get("allows_expression") is not False:
        raise ValueError("B2 parameters must be read-only and expression-free")
    default_value = _normalize_typed_default(value.get("default_value"))
    if default_value is not None:
        default_type = default_value["type"]
        if value_type == "tuple":
            if default_type != "tuple" or len(default_value["value"]) != tuple_size:
                raise ValueError("tuple default contradicts live tuple metadata")
        elif default_type != value_type or tuple_size != 1:
            raise ValueError("scalar default contradicts live parameter metadata")
    numeric_range = _normalize_numeric_range(value.get("numeric_range"))
    numeric_type = value_type in {"float", "int"}
    integer_type = value_type == "int"
    if value_type == "tuple" and default_value is not None:
        numeric_type = default_value.get("items_type") in {"float", "int"}
        integer_type = default_value.get("items_type") == "int"
    if numeric_type and numeric_range is None:
        raise ValueError("numeric parameters require a bounded live range")
    if not numeric_type and numeric_range is not None:
        raise ValueError("non-numeric parameters cannot advertise numeric ranges")
    if integer_type and numeric_range is not None:
        if isinstance(numeric_range["min_value"], bool) or isinstance(
            numeric_range["max_value"], bool
        ):
            raise ValueError("integer parameter ranges must use integers")
        if not isinstance(numeric_range["min_value"], int) or not isinstance(
            numeric_range["max_value"], int
        ):
            raise ValueError("integer parameter ranges must use integers")
    return {
        "name": name,
        "label": label,
        "value_type": value_type,
        "tuple_size": tuple_size,
        "writable": False,
        "allows_expression": False,
        "default_value": default_value,
        "numeric_range": numeric_range,
    }


def _normalize_typed_default(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("default_value must be a typed value or null")
    value_type = value.get("type")
    if value_type == "tuple":
        if set(value) != {"type", "items_type", "value"}:
            raise ValueError("tuple default has an unexpected field set")
        items_type = value.get("items_type")
        items = value.get("value")
        if items_type not in {"float", "int", "bool", "string"}:
            raise ValueError("tuple default item type is not admitted")
        if not isinstance(items, list) or not 1 <= len(items) <= 16:
            raise ValueError("tuple default must be bounded")
        normalized_items = [_normalize_scalar(item, items_type) for item in items]
        return {"type": "tuple", "items_type": items_type, "value": normalized_items}
    if set(value) != {"type", "value"} or value_type not in {
        "float",
        "int",
        "bool",
        "string",
    }:
        raise ValueError("scalar default has an unexpected shape")
    return {"type": value_type, "value": _normalize_scalar(value["value"], value_type)}


def _normalize_scalar(value: Any, value_type: str) -> Any:
    if value_type == "bool":
        if not isinstance(value, bool):
            raise ValueError("boolean default is invalid")
        return value
    if value_type == "int":
        return _bounded_integer(value, "integer default", -2147483648, 2147483647)
    if value_type == "float":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("floating default is invalid")
        number = float(value)
        if not math.isfinite(number) or not -1_000_000_000 <= number <= 1_000_000_000:
            raise ValueError("floating default is out of bounds")
        return value
    return _require_safe_catalog_text(value, "string default", 1024)


def _normalize_numeric_range(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != _NUMERIC_RANGE_FIELDS:
        raise ValueError("numeric range has an unexpected field set")
    minimum = _finite_catalog_number(value.get("min_value"), "min_value")
    maximum = _finite_catalog_number(value.get("max_value"), "max_value")
    if minimum > maximum:
        raise ValueError("numeric range minimum exceeds maximum")
    if not isinstance(value.get("min_is_strict"), bool) or not isinstance(
        value.get("max_is_strict"), bool
    ):
        raise ValueError("numeric range strictness must be boolean")
    return {
        "min_value": minimum,
        "max_value": maximum,
        "min_is_strict": value["min_is_strict"],
        "max_is_strict": value["max_is_strict"],
    }


def _finite_catalog_number(value: Any, name: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    number = float(value)
    if not math.isfinite(number) or not -1_000_000_000 <= number <= 1_000_000_000:
        raise ValueError(f"{name} is outside the reviewed numeric bound")
    return value


def _bounded_integer(value: Any, name: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer between {minimum} and {maximum}")
    return value


def _require_safe_catalog_text(value: Any, name: str, maximum: int) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ValueError(f"{name} must be a bounded non-empty string")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError(f"{name} contains control characters")
    folded = value.casefold()
    if (
        _WINDOWS_DRIVE_RE.match(value)
        or value.startswith(("/", "\\\\"))
        or "\\" in value
        or "://" in folded
        or _ENVIRONMENT_REFERENCE_RE.search(value) is not None
        or _SECRET_TEXT_RE.search(value) is not None
    ):
        raise ValueError(f"{name} contains path-like or environment-derived content")
    return value


def _require_identifier(value: Any, name: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be a bounded identifier")
    return value


def _require_sha256(value: Any, name: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return value


def _require_deadline(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("absolute_deadline must be a finite monotonic timestamp")
    deadline = float(value)
    if not math.isfinite(deadline) or deadline <= 0:
        raise ValueError("absolute_deadline must be a finite monotonic timestamp")
    return deadline


def _require_wait_timeout(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("wait timeout must be a finite number")
    timeout = float(value)
    if not math.isfinite(timeout) or not 0 <= timeout <= MAX_WAIT_SECONDS:
        raise ValueError(f"wait timeout must be between 0 and {MAX_WAIT_SECONDS} seconds")
    return timeout
