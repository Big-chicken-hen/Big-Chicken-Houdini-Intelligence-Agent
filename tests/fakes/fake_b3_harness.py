"""Explicit pure-Python Gate B3 harness.

Only tests or a deliberately imported offline harness may construct this
class.  It is not referenced by MCP, Bridge HTTP, Panel, launcher, or runtime
configuration, and it never imports Houdini.
"""

from __future__ import annotations

import copy
import threading
import time
from typing import Any, Callable, Mapping

from hia_bridge.scene_queue import (
    FakeCapabilityAttestation,
    PanelWork,
    RequestSnapshot,
    SceneQueue,
    SceneQueueError,
    SceneRequest,
    WRITE_TOOL,
)
from hia_core.houdini_contract import SchemaRegistry
from hia_core.houdini_contract import canonical_json_sha256

from fake_scene_executor import (
    FAKE_CATALOG_DIGEST,
    FakeExecutionGuardAbort,
    FakeSceneExecutor,
)


class _GateB3SceneQueue(SceneQueue):
    """Test-only queue facade for proven post-claim rollback outcomes."""

    def __init__(self, *args: Any, arbiter: threading.RLock, **kwargs: Any) -> None:
        self._b3_arbiter = arbiter
        super().__init__(*args, **kwargs)

    def cancel(self, request_id: str) -> RequestSnapshot:
        with self._b3_arbiter:
            return super().cancel(request_id)

    def shutdown(self) -> None:
        with self._b3_arbiter, self._condition:
            if self._shutdown:
                return
            self._shutdown = True
            terminal_states = {
                "completed",
                "cancelled",
                "denied",
                "expired",
                "indeterminate",
                "shutdown",
            }
            for record in list(self._records.values()):
                if record.state in terminal_states:
                    continue
                if (
                    record.state == "claimed"
                    and record.request.tool_name == WRITE_TOOL
                ):
                    # The shutdown latch is already authoritative, but the
                    # externally visible terminal state waits for the guard's
                    # confined rollback proof.
                    continue
                self._terminalize_error_locked(
                    record,
                    "shutdown",
                    SceneQueueError(
                        "SHUTTING_DOWN",
                        503,
                        "Fake scene queue is shutting down",
                        {"request_id": record.request_id},
                    ),
                )
            self._condition.notify_all()

    def execution_snapshot(self, request_id: str) -> RequestSnapshot:
        """Read a claim without production deadline reconciliation side effects."""

        with self._condition:
            return self._snapshot(self._record_locked(request_id))

    def get_result(self, request_id: str, wait_timeout: float = 0.0) -> RequestSnapshot:
        # Claimed fake writes are reconciled by the execution guard after its
        # confined rollback proof, not by production's conservative unknown
        # live-Houdini deadline path.
        with self._b3_arbiter, self._condition:
            record = self._record_locked(request_id)
            if record.state == "claimed" and record.request.tool_name == "houdini_graph_apply":
                return self._snapshot(record)
        return super().get_result(request_id, wait_timeout)

    def complete_after_guard(
        self,
        request_id: str,
        executor_token: str,
        result: Mapping[str, Any],
        authority_time: float,
    ) -> RequestSnapshot:
        """Commit the queue result at the guard's already-won authority time."""

        with self._b3_arbiter, self._condition:
            original_clock = self._clock
            self._clock = lambda: authority_time
            try:
                return super().complete(request_id, executor_token, result)
            finally:
                self._clock = original_clock

    def _expire_locked(self, now: float) -> None:
        """Leave claimed fake writes to the rollback-aware execution guard."""

        terminal_states = {
            "completed",
            "cancelled",
            "denied",
            "expired",
            "indeterminate",
            "shutdown",
        }
        for record in list(self._records.values()):
            if record.state in terminal_states:
                continue
            if (
                record.state == "claimed"
                and record.request.tool_name == WRITE_TOOL
            ):
                continue
            if now >= record.request.absolute_deadline:
                if record.state == "claimed":
                    record.cancel_requested = True
                self._terminalize_error_locked(
                    record,
                    "expired",
                    SceneQueueError(
                        "DEADLINE_EXCEEDED",
                        408,
                        "Scene request deadline has expired",
                        {"request_id": record.request_id},
                    ),
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

    def execution_abort_reason(
        self, request_id: str, *, observed_time: float | None = None
    ) -> str | None:
        with self._condition:
            record = self._record_locked(request_id)
            if self._shutdown or record.state == "shutdown":
                return "shutdown"
            if record.state in {"cancelled", "denied"} or record.cancel_requested:
                return "cancel"
            now = self._clock() if observed_time is None else observed_time
            if now >= record.request.absolute_deadline:
                return "deadline"
            if record.state != "claimed":
                return "terminal" if record.state in {
                    "completed",
                    "expired",
                    "indeterminate",
                } else "invalid"
            return None

    def resolve_claimed_abort(
        self,
        request_id: str,
        executor_token: str,
        reason: str,
        *,
        rollback_proven: bool,
    ) -> RequestSnapshot:
        """Terminalize control-plane state only after the fake rollback proof."""

        with self._b3_arbiter, self._condition:
            record = self._record_locked(request_id)
            if not rollback_proven and record.state != "completed":
                error = SceneQueueError(
                    "SCENE_STATE_INDETERMINATE",
                    409,
                    "Fake transaction rollback could not be proven",
                )
                if record.state in {
                    "cancelled",
                    "denied",
                    "expired",
                    "shutdown",
                    "indeterminate",
                    "failed",
                }:
                    record.state = "indeterminate"
                    record.structured_error = error.to_dict()
                    record.result = None
                    record.result_digest = canonical_json_sha256(
                        record.structured_error
                    )
                    self._condition.notify_all()
                else:
                    self._terminalize_error_locked(
                        record,
                        "indeterminate",
                        error,
                        retain_write_reservation=True,
                    )
                    self._freeze_other_requests_for_indeterminate_write_locked(record)
                return self._snapshot(record)
            if record.state in {
                "completed",
                "cancelled",
                "denied",
                "expired",
                "shutdown",
                "indeterminate",
                "failed",
            }:
                return self._snapshot(record)
            if record.claim_token != executor_token or record.state != "claimed":
                raise SceneQueueError(
                    "INVALID_CLAIM",
                    409,
                    "Fake execution abort does not match the claimed request",
                )
            if reason == "cancel":
                state = "cancelled"
                error = SceneQueueError(
                    "CANCELLED",
                    409,
                    "Scene request was cancelled during fake execution",
                )
            elif reason == "deadline":
                state = "expired"
                error = SceneQueueError(
                    "DEADLINE_EXCEEDED",
                    408,
                    "Scene request deadline expired during fake execution",
                )
            elif reason == "shutdown":
                state = "shutdown"
                error = SceneQueueError(
                    "SHUTTING_DOWN",
                    503,
                    "Fake scene queue stopped during execution",
                )
            else:
                state = "indeterminate"
                error = SceneQueueError(
                    "SCENE_STATE_INDETERMINATE",
                    409,
                    "Fake execution terminal state could not be reconciled",
                )
            self._terminalize_error_locked(record, state, error)
            return self._snapshot(record)


class _ExecutionGuard:
    """Content-blind cancel/deadline/shutdown and final-commit arbiter."""

    def __init__(
        self,
        queue: _GateB3SceneQueue,
        arbiter: threading.RLock,
        work: PanelWork,
        validate_output: Callable[[dict[str, Any]], dict[str, Any]],
        complete_output: Callable[[dict[str, Any], float], RequestSnapshot],
    ) -> None:
        self.queue = queue
        self.arbiter = arbiter
        self.work = work
        self._validate_output = validate_output
        self._complete_output = complete_output
        self.completed_snapshot: RequestSnapshot | None = None

    def checkpoint(self, boundary: str) -> None:
        del boundary
        with self.arbiter:
            reason = self.queue.execution_abort_reason(self.work.request_id)
            if reason is not None:
                raise FakeExecutionGuardAbort(reason)

    def mutate(self, boundary: str, mutation: Callable[[], None]) -> None:
        """Make the guard check and one fake mutation an atomic authority step."""

        del boundary
        with self.arbiter:
            reason = self.queue.execution_abort_reason(self.work.request_id)
            if reason is not None:
                raise FakeExecutionGuardAbort(reason)
            mutation()

    def finalize(
        self,
        preview: dict[str, Any],
        commit_scene: Callable[[], dict[str, Any]],
    ) -> dict[str, Any]:
        with self.arbiter:
            authority_time = self.queue._clock()
            reason = self.queue.execution_abort_reason(
                self.work.request_id,
                observed_time=authority_time,
            )
            if reason is not None:
                raise FakeExecutionGuardAbort(reason)
            # Validate before publication so a schema failure cannot strand a
            # committed fake graph.  Queue completion then shares this arbiter
            # with cancellation and shutdown.
            checked = self._validate_output(preview)
            committed = commit_scene()
            if committed != checked:
                raise RuntimeError("fake commit changed the validated result")
            snapshot = self._complete_output(checked, authority_time)
            self.completed_snapshot = snapshot
            return checked


class GateB3OfflineHarness:
    """Headless approval/queue/transaction chain for fake-only acceptance."""

    def __init__(
        self,
        executor: FakeSceneExecutor,
        *,
        registry: SchemaRegistry | None = None,
        clock: Callable[[], float] = time.monotonic,
        launch_id: str = "b3-offline-launch",
        generation: int = 1,
        process_nonce: str = "b3-offline-process",
    ) -> None:
        self.executor = executor
        self.registry = registry or SchemaRegistry()
        self.clock = clock
        self.launch_id = launch_id
        self.generation = generation
        self.process_nonce = process_nonce
        self._execution_arbiter = threading.RLock()
        self.__claim_authority = object()
        self.executor._bind_claim_authority(self.__claim_authority)
        self._trusted_presentations: dict[str, dict[str, str]] = {}
        self._claimed_work: dict[str, tuple[PanelWork, str]] = {}
        self.queue = _GateB3SceneQueue(
            launch_id,
            generation,
            arbiter=self._execution_arbiter,
            expected_schema_digest=self.registry.manifest_digest,
            expected_catalog_digest=FAKE_CATALOG_DIGEST,
            clock=clock,
        )
        self._attestation_digest = self.queue.install_attestation(
            self._attestation()
        )

    def _attestation(
        self, snapshot: Mapping[str, Any] | None = None
    ) -> FakeCapabilityAttestation:
        stable = self.executor.capability_snapshot() if snapshot is None else snapshot
        return FakeCapabilityAttestation(
            launch_id=self.launch_id,
            generation=self.generation,
            process_nonce=self.process_nonce,
            hip_session_id=stable["hip_session_id"],
            hip_fingerprint=stable["hip_fingerprint"],
            scene_revision=stable["scene_revision"],
            catalog_digest=FAKE_CATALOG_DIGEST,
            schema_digest=self.registry.manifest_digest,
        )

    def refresh_attestation(self) -> str:
        def replace(snapshot: dict[str, Any]) -> str:
            # Fixed order: scene snapshot lock -> execution arbiter -> queue.
            with self._execution_arbiter:
                replacement = self._attestation(snapshot)
                self._attestation_digest = self.queue.replace_attestation(
                    replacement,
                    self._attestation_digest,
                )
                return self._attestation_digest

        return self.executor.with_stable_capability_snapshot(replace)

    def submit(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
        *,
        absolute_deadline: float | None = None,
    ) -> tuple[SceneRequest, RequestSnapshot]:
        if tool_name == "houdini_graph_apply" and self.executor.writes_indeterminate:
            raise SceneQueueError(
                "SCENE_STATE_INDETERMINATE",
                409,
                "Fake scene writes are frozen pending trusted inspection",
            )
        checked = self.registry.validate_input(
            tool_name, copy.deepcopy(dict(arguments))
        )
        request = self.queue.build_request(
            tool_name,
            checked,
            self.clock() + 30.0 if absolute_deadline is None else absolute_deadline,
        )
        snapshot = self.queue.submit(request)
        if tool_name == "houdini_graph_apply" and not snapshot.terminal:
            if (
                request.approval_payload is None
                or request.approval_binding_digest is None
            ):
                raise RuntimeError("fake apply request has no approval binding")
            self._trusted_presentations[request.arguments["request_id"]] = {
                "request_digest": request.request_digest,
                "approval_binding_digest": request.approval_binding_digest,
                "approval_payload_digest": canonical_json_sha256(
                    request.approval_payload
                ),
                "arguments_digest": canonical_json_sha256(request.arguments),
            }
        return request, snapshot

    def poll(self) -> PanelWork | None:
        work = self.queue.poll_next(timeout=0.0)
        if work is not None and work.kind == "execute":
            self._claimed_work[work.request_id] = (
                work,
                canonical_json_sha256(work.to_dict()),
            )
        return work

    def decide(
        self,
        presentation: PanelWork,
        decision: str,
        *,
        request_digest: str | None = None,
        launch_id: str | None = None,
        generation: int | None = None,
    ) -> RequestSnapshot:
        if decision == "allow":
            trusted = self._trusted_presentations.get(presentation.request_id)
            try:
                displayed_payload_digest = canonical_json_sha256(
                    presentation.approval_payload
                )
                displayed_arguments_digest = canonical_json_sha256(
                    presentation.arguments
                )
            except (TypeError, ValueError) as exc:
                raise SceneQueueError(
                    "APPROVAL_DIGEST_MISMATCH",
                    409,
                    "Displayed fake approval is not canonical",
                ) from exc
            if (
                trusted is None
                or presentation.request_digest != trusted["request_digest"]
                or presentation.approval_binding_digest
                != trusted["approval_binding_digest"]
                or displayed_payload_digest
                != trusted["approval_payload_digest"]
                or displayed_arguments_digest != trusted["arguments_digest"]
            ):
                raise SceneQueueError(
                    "APPROVAL_DIGEST_MISMATCH",
                    409,
                    "Displayed fake approval does not match the trusted request",
                )
        return self.queue.decide_approval(
            presentation.request_id,
            decision,
            presentation.request_digest
            if request_digest is None
            else request_digest,
            self.launch_id if launch_id is None else launch_id,
            self.generation if generation is None else generation,
        )

    def dismiss(self, presentation: PanelWork) -> RequestSnapshot:
        """Model a headless Panel dismissal as pre-claim cancellation."""

        return self.queue.cancel(presentation.request_id)

    def cancel(self, request_id: str) -> RequestSnapshot:
        """Request fake control-plane cancellation under the commit arbiter."""

        return self.queue.cancel(request_id)

    def disconnect_panel(self) -> None:
        """Model loss of the only fake Panel without touching production UI."""

        self.queue.shutdown()

    def execute_work(
        self,
        work: PanelWork,
        *,
        refresh_attestation: bool = True,
    ) -> RequestSnapshot:
        if work.kind != "execute" or work.executor_token is None:
            raise ValueError("Gate B3 execution requires one execute work item")
        trusted_claim = self._claimed_work.get(work.request_id)
        try:
            supplied_claim_digest = canonical_json_sha256(work.to_dict())
        except (TypeError, ValueError) as exc:
            raise ValueError("Gate B3 execution claim is not canonical") from exc
        if (
            trusted_claim is None
            or trusted_claim[0] is not work
            or trusted_claim[1] != supplied_claim_digest
        ):
            raise ValueError("Gate B3 execution requires the exact claimed work object")
        with self._execution_arbiter:
            current = self.queue.execution_snapshot(work.request_id)
            if current.terminal:
                self._claimed_work.pop(work.request_id, None)
                return current
            abort_reason = self.queue.execution_abort_reason(work.request_id)
            if abort_reason in {"cancel", "deadline", "shutdown"}:
                self._claimed_work.pop(work.request_id, None)
                return self.queue.resolve_claimed_abort(
                    work.request_id,
                    work.executor_token,
                    abort_reason,
                    rollback_proven=True,
                )
            if current.state != "claimed":
                raise ValueError("Gate B3 execution requires a claimed queue request")
            self._claimed_work.pop(work.request_id, None)

        guard: _ExecutionGuard | None = None
        if work.tool_name == "houdini_graph_apply":
            guard = _ExecutionGuard(
                self.queue,
                self._execution_arbiter,
                work,
                validate_output=lambda candidate: self.registry.validate_output(
                    work.tool_name,
                    work.arguments,
                    candidate,
                ),
                complete_output=lambda checked, authority_time: self.queue.complete_after_guard(
                    work.request_id,
                    work.executor_token or "",
                    checked,
                    authority_time,
                ),
            )
        try:
            output = self.executor._execute_authorized_claim(
                work,
                authority=self.__claim_authority,
                execution_guard=guard,
            )
        except FakeExecutionGuardAbort as abort:
            return self.queue.resolve_claimed_abort(
                work.request_id,
                work.executor_token,
                abort.reason,
                rollback_proven=abort.rollback_proven,
            )

        if guard is not None and guard.completed_snapshot is not None:
            completed = guard.completed_snapshot
            checked = output
        else:
            with self._execution_arbiter:
                reason = self.queue.execution_abort_reason(work.request_id)
                if reason is not None:
                    error = output.get("structured_error")
                    rollback_uncertain = (
                        isinstance(error, Mapping)
                        and error.get("code")
                        in {"ROLLBACK_FAILED", "SCENE_STATE_INDETERMINATE"}
                    )
                    return self.queue.resolve_claimed_abort(
                        work.request_id,
                        work.executor_token,
                        reason,
                        rollback_proven=not rollback_uncertain,
                    )
                checked = self.registry.validate_output(
                    work.tool_name,
                    work.arguments,
                    output,
                )
                completed = self.queue.complete(
                    work.request_id,
                    work.executor_token,
                    checked,
                )
        if (
            work.tool_name == "houdini_graph_apply"
            and checked.get("ok") is True
            and refresh_attestation
        ):
            self.refresh_attestation()
        return completed

    def run_read(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
    ) -> RequestSnapshot:
        if tool_name == "houdini_graph_apply":
            raise ValueError("run_read cannot execute a write")
        _, submitted = self.submit(tool_name, arguments)
        if submitted.terminal:
            return submitted
        work = self.poll()
        if work is None:
            raise RuntimeError("fake read was not claimable")
        return self.execute_work(work)

    def present_apply(
        self,
        arguments: Mapping[str, Any],
        *,
        absolute_deadline: float | None = None,
    ) -> tuple[SceneRequest, RequestSnapshot, PanelWork]:
        request, submitted = self.submit(
            "houdini_graph_apply",
            arguments,
            absolute_deadline=absolute_deadline,
        )
        if submitted.terminal:
            raise RuntimeError("terminal replay has no new approval presentation")
        presentation = self.poll()
        if presentation is None or presentation.kind != "approval_required":
            raise RuntimeError("fake apply did not produce an approval presentation")
        return request, submitted, presentation

    def allow_and_execute(self, presentation: PanelWork) -> RequestSnapshot:
        decision = self.decide(presentation, "allow")
        if decision.terminal:
            return decision
        work = self.poll()
        if work is None:
            return self.queue.get_result(presentation.request_id)
        return self.execute_work(work)

    def replay(self, request: SceneRequest) -> RequestSnapshot:
        return self.queue.submit(request)
