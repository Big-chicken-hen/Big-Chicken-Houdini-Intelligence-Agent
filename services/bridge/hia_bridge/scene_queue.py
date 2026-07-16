"""Thread-safe, fake-only scene request queue for P2-V Gate B1.

This module deliberately contains no HTTP server, Houdini import, or live-scene
executor.  It owns only trusted in-memory correlation, approval, idempotency,
claim, cancellation, result, and shutdown state.
"""

from __future__ import annotations

import copy
import hashlib
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


CONTRACT_VERSION = "0.1.0"
ALLOWED_TOOLS = frozenset(
    {
        "houdini_scene_info",
        "houdini_node_type_info",
        "houdini_graph_validate",
        "houdini_graph_apply",
        "houdini_graph_verify",
    }
)
WRITE_TOOL = "houdini_graph_apply"
DEFAULT_CAPACITY = 32
DEFAULT_TERMINAL_RETENTION = 256
MAX_WAIT_SECONDS = 1.0
APPROVAL_TTL_SECONDS = 60.0

_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
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
    ) -> "SceneRequest":
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
        expected_catalog_digest: str,
        capacity: int = DEFAULT_CAPACITY,
        terminal_retention: int = DEFAULT_TERMINAL_RETENTION,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        _require_identifier(launch_id, "launch_id")
        _require_sha256(expected_schema_digest, "expected_schema_digest")
        _require_sha256(expected_catalog_digest, "expected_catalog_digest")
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
        self.expected_schema_digest = expected_schema_digest
        self.expected_catalog_digest = expected_catalog_digest
        self.capacity = capacity
        self.terminal_retention = terminal_retention
        self._clock = clock
        self._condition = threading.Condition(threading.RLock())
        self._attestation: FakeCapabilityAttestation | None = None
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
            return self._attestation.digest if self._attestation is not None else None

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

    def build_request(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
        absolute_deadline: float,
    ) -> SceneRequest:
        with self._condition:
            self._require_open_locked()
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
        if request.tool_name not in ALLOWED_TOOLS:
            raise SceneQueueError(
                "TOOL_NOT_ALLOWED",
                403,
                "Tool is outside the frozen P2-V allowlist",
                {"tool_name": request.tool_name},
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
                absolute_deadline=record.request.absolute_deadline,
                claim_token=record.claim_token,
                cancel_requested=record.cancel_requested,
            )
        return None

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
) -> str:
    return _sha256(
        {
            "contract_version": CONTRACT_VERSION,
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
