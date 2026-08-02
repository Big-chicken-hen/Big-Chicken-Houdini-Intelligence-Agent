"""Bounded process-local scheduler for explicit project role actions."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor, wait
import threading
from typing import Callable

from .project_contracts import ProjectStatus
from .project_lifecycle import LifecycleEvent, ProjectEvent
from .project_registry import ProjectRecord, ProjectRegistry
from .project_runner import ActionExecutor, ProjectRunner


ExecutorFactory = Callable[[str], ActionExecutor]
SnapshotCallback = Callable[[ProjectRecord], None]
InterruptHook = Callable[[str], None]
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
    ) -> None:
        if max_workers < 1:
            raise ValueError("max_workers must be positive")
        self._registry = registry
        self._runner = runner
        self._executor_factory = executor_factory
        self._interrupt_hook = interrupt_hook
        self._on_snapshot = on_snapshot
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="hia-project")
        self._lock = threading.RLock()
        self._inflight: dict[str, Future[ProjectRecord]] = {}
        self._stopped: set[str] = set()
        self._closed = False

    def start(self, project_id: str) -> bool:
        with self._lock:
            if self._closed or project_id in self._stopped:
                return False
            current = self._inflight.get(project_id)
            if current is not None and not current.done():
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
            self._stopped.add(project_id)
            future = self._inflight.get(project_id)
            active = future is not None and not future.done()
        if active and self._interrupt_hook is not None:
            self._interrupt_hook(project_id)
        if not active:
            self._mark_stopped(project_id)
        return active

    def resume(self, project_id: str) -> bool:
        with self._lock:
            if self._closed:
                return False
            self._stopped.discard(project_id)
        if self._registry.require(project_id).state.status in {
            ProjectStatus.STOPPED,
            ProjectStatus.WAITING_USER,
        }:
            self._runner.cancel_and_dispatch(
                project_id, LifecycleEvent(ProjectEvent.USER_CONTINUE)
            )
        return self.start(project_id)

    def is_inflight(self, project_id: str) -> bool:
        with self._lock:
            future = self._inflight.get(project_id)
            return future is not None and not future.done()

    def close(self, timeout_seconds: float = 5.0) -> bool:
        if timeout_seconds < 0:
            raise ValueError("timeout_seconds cannot be negative")
        with self._lock:
            self._closed = True
            items = tuple(self._inflight.items())
            self._stopped.update(project_id for project_id, _ in items)
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
        try:
            record = future.result()
        except BaseException as error:
            try:
                record = self._runner.fail(project_id, error)
            except BaseException:
                with self._lock:
                    self._inflight.pop(project_id, None)
                return
        if self._on_snapshot is not None:
            try:
                self._on_snapshot(record)
            except BaseException:
                pass
        with self._lock:
            self._inflight.pop(project_id, None)
            stopped = project_id in self._stopped
            should_continue = (
                not self._closed
                and not stopped
                and record.state.status not in _INACTIVE
                and self._runner.has_pending(project_id)
            )
        if stopped and record.state.status not in _INACTIVE:
            self._mark_stopped(project_id)
        elif should_continue:
            self.start(project_id)

    def _mark_stopped(self, project_id: str) -> None:
        record = self._registry.require(project_id)
        if record.state.status in _INACTIVE:
            return
        stopped = self._runner.cancel_and_dispatch(
            project_id, LifecycleEvent(ProjectEvent.PROJECT_INTERRUPTED)
        )
        if self._on_snapshot is not None:
            try:
                self._on_snapshot(stopped)
            except BaseException:
                pass
