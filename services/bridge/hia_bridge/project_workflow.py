"""Bounded process-local scheduler for explicit project role actions."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor, wait
import threading
from typing import Callable

from .errors import BridgeError
from .project_contracts import ProjectStatus
from .project_lifecycle import LifecycleEvent, ProjectEvent
from .project_registry import ProjectRecord, ProjectRegistry
from .project_runner import ActionExecutor, ProjectRunner


ExecutorFactory = Callable[[str], ActionExecutor]
SnapshotCallback = Callable[[ProjectRecord], None]
InterruptHook = Callable[[str], None]
IdleCallback = Callable[[str], None]
_INACTIVE = frozenset(
    {
        ProjectStatus.WAITING_USER,
        ProjectStatus.COMPLETED,
        ProjectStatus.FAILED,
        ProjectStatus.STOPPED,
    }
)


class ProjectWorkflowHost:
    def __init__(
        self,
        *,
        registry: ProjectRegistry,
        runner: ProjectRunner,
        executor_factory: ExecutorFactory,
        max_workers: int = 4,
        interrupt_hook: InterruptHook | None = None,
        on_snapshot: SnapshotCallback | None = None,
        on_idle: IdleCallback | None = None,
    ) -> None:
        if max_workers < 1:
            raise ValueError("max_workers must be positive")
        self._registry = registry
        self._runner = runner
        self._executor_factory = executor_factory
        self._interrupt_hook = interrupt_hook
        self._on_snapshot = on_snapshot
        self._on_idle = on_idle
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="hia-project")
        self._lock = threading.RLock()
        self._inflight: dict[str, Future[ProjectRecord]] = {}
        self._closed = False

    def start(self, project_id: str) -> bool:
        with self._lock:
            if self._closed:
                return False
            current = self._inflight.get(project_id)
            if current is not None:
                return False
            record = self._registry.require(project_id)
            if record.state.status in _INACTIVE or not self._runner.has_pending(project_id):
                return False
            future = self._pool.submit(self._execute_one, project_id)
            self._inflight[project_id] = future
            future.add_done_callback(
                lambda completed, pid=project_id: self._done(pid, completed)
            )
            return True

    def stop(self, project_id: str) -> bool:
        with self._lock:
            future = self._inflight.get(project_id)
            active = future is not None and not future.done()
        if active and self._interrupt_hook is not None:
            self._interrupt_hook(project_id)
        self._mark_stopped(project_id)
        return active

    def resume(self, project_id: str, continuation: dict[str, object]) -> bool:
        with self._lock:
            if self._closed:
                return False
            current = self._inflight.get(project_id)
            if current is not None:
                raise BridgeError(
                    "PROJECT_TURN_STILL_STOPPING",
                    "The previous project Turn is still stopping",
                    http_status=409,
                    details={"project_id": project_id},
                )
            if self._registry.require(project_id).state.status in {
                ProjectStatus.STOPPED,
                ProjectStatus.WAITING_USER,
            }:
                resumed = self._runner.cancel_and_dispatch(
                    project_id,
                    LifecycleEvent(ProjectEvent.USER_CONTINUE, continuation),
                )
                if resumed.state.status is ProjectStatus.WAITING_USER:
                    return True
            return self.start(project_id)

    def is_inflight(self, project_id: str) -> bool:
        with self._lock:
            return project_id in self._inflight

    def close(self, timeout_seconds: float = 5.0) -> bool:
        if timeout_seconds < 0:
            raise ValueError("timeout_seconds cannot be negative")
        with self._lock:
            self._closed = True
            items = tuple(self._inflight.items())
        if self._interrupt_hook is not None:
            for project_id, future in items:
                if not future.done():
                    try:
                        self._interrupt_hook(project_id)
                    except BaseException:
                        pass
        _, unfinished = wait(tuple(future for _, future in items), timeout=timeout_seconds)
        self._pool.shutdown(wait=False, cancel_futures=True)
        return not unfinished

    def _execute_one(self, project_id: str) -> ProjectRecord:
        return self._runner.execute_next(project_id, self._executor_factory(project_id))

    def _done(self, project_id: str, future: Future[ProjectRecord]) -> None:
        with self._lock:
            if self._inflight.get(project_id) is not future:
                return
        try:
            record = future.result()
        except BaseException as error:
            try:
                current = self._registry.require(project_id)
                record = (
                    current
                    if current.state.status is ProjectStatus.STOPPED
                    else self._runner.fail(project_id, error)
                )
            except BaseException:
                with self._lock:
                    self._inflight.pop(project_id, None)
                return
        try:
            current = self._registry.require(project_id)
            if current.state.status is ProjectStatus.STOPPED:
                record = current
        except BaseException:
            pass
        if self._on_snapshot is not None:
            try:
                self._on_snapshot(record)
            except BaseException:
                pass
        with self._lock:
            if self._inflight.get(project_id) is not future:
                return
            self._inflight.pop(project_id, None)
            should_continue = (
                not self._closed
                and record.state.status not in _INACTIVE
                and self._runner.has_pending(project_id)
            )
        if should_continue:
            self.start(project_id)
        elif self._on_idle is not None:
            try:
                self._on_idle(project_id)
            except BaseException:
                pass

    def _mark_stopped(self, project_id: str) -> None:
        record = self._registry.require(project_id)
        if record.state.status in {
            ProjectStatus.COMPLETED,
            ProjectStatus.FAILED,
            ProjectStatus.STOPPED,
        }:
            return
        stopped = self._runner.cancel_and_dispatch(
            project_id, LifecycleEvent(ProjectEvent.PROJECT_INTERRUPTED)
        )
        if self._on_snapshot is not None:
            try:
                self._on_snapshot(stopped)
            except BaseException:
                pass
