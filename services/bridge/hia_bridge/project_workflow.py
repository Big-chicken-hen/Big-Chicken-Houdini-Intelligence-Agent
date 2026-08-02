"""Bounded event-driven host for persisted project effects.

The host owns scheduling only.  ``ProjectRunner`` remains the persistence and
lifecycle boundary, while the supplied executor factory owns external RPC work.
At most one effect for a project is in flight; different projects may run in
parallel.  There is intentionally no autonomous polling loop.
"""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor, wait
import threading
import time
from typing import Callable, Iterable

from .project_contracts import ProjectStatus
from .project_effects import EffectResult
from .project_lifecycle import LifecycleEvent, ProjectEvent
from .project_registry import ProjectRecord, ProjectRegistry
from .project_runner import EffectExecutor, ProjectRunner


ExecutorFactory = Callable[[str], EffectExecutor]
SnapshotCallback = Callable[[ProjectRecord], None]
InterruptHook = Callable[[str], None]


_WAITING_STATUSES = frozenset(
    {
        ProjectStatus.COMPLETED,
        ProjectStatus.BLOCKED,
        ProjectStatus.INTERRUPTED,
        ProjectStatus.FAILED,
        ProjectStatus.NEEDS_ATTENTION,
        ProjectStatus.NOT_APPLICABLE,
    }
)
_STOP_CONTROL_EFFECTS = frozenset({"pause_goal", "show_attention", "record_failure"})
_REPLAN_STATUSES = frozenset(
    {
        ProjectStatus.PLANNING,
        ProjectStatus.AUTHORIZATION,
        ProjectStatus.EXECUTING_STAGE,
        ProjectStatus.REVIEWING_STAGE,
        ProjectStatus.REPAIRING_STAGE,
    }
)


class ProjectWorkflowHost:
    """Schedule persisted effects without becoming another project planner."""

    def __init__(
        self,
        *,
        registry: ProjectRegistry,
        runner: ProjectRunner,
        executor_factory: ExecutorFactory,
        max_workers: int = 4,
        interrupt_hook: InterruptHook | None = None,
        on_snapshot: SnapshotCallback | None = None,
    ) -> None:
        if max_workers < 1:
            raise ValueError("max_workers must be positive")
        self._registry = registry
        self._runner = runner
        self._executor_factory = executor_factory
        self._interrupt_hook = interrupt_hook
        self._on_snapshot = on_snapshot
        self._pool = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="hia-project",
        )
        self._lock = threading.RLock()
        self._idle = threading.Condition(self._lock)
        self._inflight: dict[str, Future[ProjectRecord]] = {}
        self._stopped: set[str] = set()
        self._closed = False
        self._host_errors: dict[str, BaseException] = {}

    def start(self, project_id: str) -> bool:
        """Submit exactly the oldest pending effect when the project is runnable."""

        with self._lock:
            if self._closed or project_id in self._stopped:
                return False
            current = self._inflight.get(project_id)
            if current is not None and not current.done():
                return False
            record = self._registry.require(project_id)
            if (
                record.state.plan_stale
                and record.state.status in _REPLAN_STATUSES
                and (
                    not record.state.pending_effects
                    or record.state.pending_effects[0].kind != "request_plan"
                )
            ):
                record = self._runner.cancel_pending_and_dispatch(
                    project_id,
                    LifecycleEvent(ProjectEvent.MATERIAL_REPLAN_REQUIRED),
                )
            recovery_effect = (
                record.state.status is ProjectStatus.INTERRUPTED
                and bool(record.state.pending_effects)
                and record.state.pending_effects[0].kind == "verify_recovery"
            )
            if record.state.status in _WAITING_STATUSES and not recovery_effect:
                return False
            if not record.state.pending_effects:
                return False
            future = self._pool.submit(self._execute_one, project_id)
            self._inflight[project_id] = future
            future.add_done_callback(
                lambda completed, pid=project_id: self._effect_done(pid, completed)
            )
            return True

    def _start_stop_control(self, project_id: str) -> bool:
        with self._lock:
            if self._closed:
                return False
            current = self._inflight.get(project_id)
            if current is not None and not current.done():
                return False
            record = self._registry.require(project_id)
            if (
                not record.state.pending_effects
                or record.state.pending_effects[0].kind not in _STOP_CONTROL_EFFECTS
            ):
                return False
            future = self._pool.submit(self._execute_one, project_id)
            self._inflight[project_id] = future
            future.add_done_callback(
                lambda completed, pid=project_id: self._effect_done(pid, completed)
            )
            return True

    def recover(self, project_ids: Iterable[str] | None = None) -> tuple[str, ...]:
        """Reschedule persisted work after Bridge startup.

        Waiting and terminal projects remain untouched; recovery never invents a
        lifecycle event when no pending effect was durably recorded.
        """

        selected = None if project_ids is None else frozenset(project_ids)
        scheduled: list[str] = []
        for record in self._registry.list():
            recovery_effect = (
                record.state.status is ProjectStatus.INTERRUPTED
                and bool(record.state.pending_effects)
                and record.state.pending_effects[0].kind == "verify_recovery"
            )
            if (
                (selected is None or record.state.project_id in selected)
                and (
                    record.state.status not in _WAITING_STATUSES
                    or recovery_effect
                )
                and (record.state.pending_effects or record.state.plan_stale)
                and self.start(record.state.project_id)
            ):
                scheduled.append(record.state.project_id)
        return tuple(scheduled)

    def stop(self, project_id: str) -> bool:
        """Prevent further effects and request interruption of current RPC work.

        The return value reports whether an effect was in flight, not whether an
        external call was terminated.  A UI-thread scene write may still finish.
        """

        with self._lock:
            self._stopped.add(project_id)
            future = self._inflight.get(project_id)
            active = future is not None and not future.done()
        if active and self._interrupt_hook is not None:
            self._interrupt_hook(project_id)
        if not active:
            self._transition_to_stopped(project_id)
            self._start_stop_control(project_id)
        return active

    def resume(self, project_id: str) -> bool:
        """Allow scheduling again; lifecycle continuation remains caller-owned."""

        with self._lock:
            if self._closed:
                return False
            self._stopped.discard(project_id)
        return self.start(project_id)

    def is_inflight(self, project_id: str) -> bool:
        with self._lock:
            future = self._inflight.get(project_id)
            return future is not None and not future.done()

    def host_error(self, project_id: str) -> BaseException | None:
        """Expose persistence/callback failures that could not become project state."""

        with self._lock:
            return self._host_errors.get(project_id)

    def close(self, timeout_seconds: float = 5.0) -> bool:
        """Bound shutdown and report honestly whether submitted work finished."""

        if timeout_seconds < 0:
            raise ValueError("timeout_seconds cannot be negative")
        deadline = time.monotonic() + timeout_seconds
        with self._lock:
            self._closed = True
            project_ids = tuple(self._inflight)
            futures = tuple(self._inflight.values())
            self._stopped.update(project_ids)
        if self._interrupt_hook is not None:
            for project_id, future in zip(project_ids, futures):
                if not future.done():
                    try:
                        self._interrupt_hook(project_id)
                    except BaseException as error:
                        # Shutdown must still reap the pool and preserve later
                        # Session cleanup.  The error remains observable.
                        with self._lock:
                            self._host_errors[project_id] = error
        _, unfinished = wait(futures, timeout=timeout_seconds)
        with self._idle:
            while self._inflight and time.monotonic() < deadline:
                self._idle.wait(max(0.0, deadline - time.monotonic()))
            callbacks_unfinished = bool(self._inflight)
        self._pool.shutdown(wait=False, cancel_futures=True)
        return not unfinished and not callbacks_unfinished

    def _execute_one(self, project_id: str) -> ProjectRecord:
        executor = self._executor_factory(project_id)
        return self._runner.execute_next(project_id, executor)

    def _effect_done(
        self, project_id: str, future: Future[ProjectRecord]
    ) -> None:
        try:
            record = future.result()
        except BaseException as error:  # preserve unexpected RPC and adapter failures
            record = self._persist_failure(project_id, error)
            if record is None:
                return

        self._emit_snapshot(record)
        with self._lock:
            if self._inflight.get(project_id) is future:
                self._inflight.pop(project_id, None)
                self._idle.notify_all()
            stopped = project_id in self._stopped
            should_continue = (
                not self._closed
                and not stopped
                and record.state.status not in _WAITING_STATUSES
                and bool(record.state.pending_effects)
            )
        if stopped and record.state.status not in _WAITING_STATUSES:
            record = self._transition_to_stopped(project_id)
            if record is not None:
                self._emit_snapshot(record)
        if stopped:
            self._start_stop_control(project_id)
        if should_continue:
            self.start(project_id)

    def _transition_to_stopped(self, project_id: str) -> ProjectRecord | None:
        try:
            record = self._registry.require(project_id)
            if record.state.status in _WAITING_STATUSES:
                return record
            return self._runner.cancel_pending_and_dispatch(
                project_id,
                LifecycleEvent(
                    ProjectEvent.PROJECT_INTERRUPTED,
                    {"reason": "user_stop"},
                ),
            )
        except BaseException as error:
            with self._lock:
                self._host_errors[project_id] = error
            return None

    def _persist_failure(
        self, project_id: str, error: BaseException
    ) -> ProjectRecord | None:
        """ACK the failed oldest effect with an explicit lifecycle failure."""

        try:
            record = self._registry.require(project_id)
            if not record.state.pending_effects:
                raise ValueError("failed effect is no longer pending")
            effect = record.state.pending_effects[0]
            outcome = EffectResult(
                record.state,
                LifecycleEvent(
                    ProjectEvent.PROJECT_FAILED,
                    {"error": f"{type(error).__name__}: {error}"},
                ),
            )
            return self._runner.acknowledge_effect(
                project_id, effect.effect_id, outcome
            )
        except BaseException as persistence_error:
            with self._lock:
                self._host_errors[project_id] = persistence_error
            return None

    def _emit_snapshot(self, record: ProjectRecord) -> None:
        if self._on_snapshot is None:
            return
        try:
            self._on_snapshot(record)
        except BaseException as error:
            with self._lock:
                self._host_errors[record.state.project_id] = error
