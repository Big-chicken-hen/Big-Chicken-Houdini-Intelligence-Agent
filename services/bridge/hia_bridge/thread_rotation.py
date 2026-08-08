"""Minimal third-compaction rotation for app-server Threads.

The service owns only process-local observation state.  Integration code supplies
the authoritative identity/profile operations and an inexpensive idle predicate.
"""

from __future__ import annotations

import copy
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol


_COMPACTION_LIMIT = 3
_LEGACY_KIND = "thread/compacted"
_ITEM_KIND = "item/completed"


class AppServerClient(Protocol):
    def request(self, method: str, params: Mapping[str, Any]) -> Any: ...


@dataclass(frozen=True)
class ThreadRotationProfile:
    """Direct fork request fields and direct observable response expectations."""

    cwd: str
    developer_instructions: str
    ephemeral: bool
    thread_source: Any
    model: str
    reasoning_effort: str | None
    service_tier: str | None
    fork_sandbox: Any
    response_sandbox: Any
    config: Mapping[str, Any]


@dataclass(frozen=True)
class ThreadRotationAdapters:
    expected_profile: Callable[[str, str], ThreadRotationProfile | None]
    is_idle: Callable[[str], bool]
    rebind: Callable[[str, str], None]
    readback: Callable[[str, str], bool]
    publish: Callable[[Mapping[str, Any]], None]
    validate_role_tools: Callable[[str, Mapping[str, Any]], bool] | None = None


NativeSeenKey = tuple[str, str, str | None, str]


@dataclass
class _ThreadState:
    count: int = 0
    seen: set[NativeSeenKey] = field(default_factory=set)
    pending: bool = False
    running: bool = False
    failed: bool = False
    last_turn_id: str | None = None


class ThreadRotationService:
    """Fork a managed Thread once after its third paired native compaction."""

    def __init__(
        self,
        client: AppServerClient,
        adapters: ThreadRotationAdapters,
    ) -> None:
        self._client = client
        self._adapters = adapters
        self._lock = threading.RLock()
        self._states: dict[str, _ThreadState] = {}
        self._closed = False
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="hia-thread-rotation",
        )

    def observe(self, method: str, params: Mapping[str, Any]) -> None:
        """Observe one allowlisted native notification without issuing RPCs."""

        if not isinstance(params, Mapping):
            return
        if method == _LEGACY_KIND:
            self._observe_legacy(params)
            return
        if method == _ITEM_KIND:
            self._observe_item(params)
            return
        if method == "thread/status/changed":
            status = params.get("status")
            thread_id = params.get("threadId")
            if (
                isinstance(thread_id, str)
                and thread_id
                and isinstance(status, Mapping)
                and status.get("type") == "idle"
            ):
                self.notify_idle(thread_id)

    def notify_idle(self, thread_id: str) -> None:
        """Schedule one rotation only after a native idle boundary is observed."""

        if not isinstance(thread_id, str) or not thread_id:
            return
        with self._lock:
            state = self._states.get(thread_id)
            if (
                self._closed
                or state is None
                or not state.pending
                or state.running
                or state.failed
            ):
                return
        try:
            idle = self._adapters.is_idle(thread_id)
        except Exception:
            self._fail(thread_id, "idle")
            return
        if not idle:
            return
        with self._lock:
            state = self._states.get(thread_id)
            if (
                self._closed
                or state is None
                or not state.pending
                or state.running
                or state.failed
            ):
                return
            state.pending = False
            state.running = True
            try:
                self._executor.submit(self._rotate, thread_id)
            except Exception:
                state.running = False
                state.failed = True
                self._report_failure(thread_id, "schedule")

    def snapshot(self, thread_id: str) -> dict[str, Any] | None:
        """Return non-sensitive process-local state for diagnostics and tests."""

        with self._lock:
            state = self._states.get(thread_id)
            if state is None:
                return None
            return {
                "count": state.count,
                "seenCount": len(state.seen),
                "pending": state.pending,
                "running": state.running,
                "failed": state.failed,
            }

    def close(self, *, wait: bool = True) -> None:
        with self._lock:
            self._closed = True
        self._executor.shutdown(wait=wait, cancel_futures=True)

    def _observe_legacy(self, params: Mapping[str, Any]) -> None:
        thread_id = params.get("threadId")
        turn_id = params.get("turnId")
        if isinstance(thread_id, str) and thread_id and isinstance(turn_id, str) and turn_id:
            self._remember(thread_id, turn_id, None, _LEGACY_KIND)

    def _observe_item(self, params: Mapping[str, Any]) -> None:
        thread_id = params.get("threadId")
        turn_id = params.get("turnId")
        item = params.get("item")
        if not (
            isinstance(thread_id, str)
            and thread_id
            and isinstance(turn_id, str)
            and turn_id
            and isinstance(item, Mapping)
            and item.get("type") == "contextCompaction"
        ):
            return
        item_id = item.get("id")
        if isinstance(item_id, str) and item_id:
            self._remember(thread_id, turn_id, item_id, _ITEM_KIND)

    def _remember(
        self,
        thread_id: str,
        turn_id: str,
        item_id: str | None,
        kind: str,
    ) -> None:
        key = (thread_id, turn_id, item_id, kind)
        with self._lock:
            if self._closed:
                return
            state = self._states.setdefault(thread_id, _ThreadState())
            if state.pending or state.running or state.failed or key in state.seen:
                return
            state.seen.add(key)
            legacy = (thread_id, turn_id, None, _LEGACY_KIND)
            items = tuple(
                candidate
                for candidate in state.seen
                if candidate[0] == thread_id
                and candidate[1] == turn_id
                and candidate[3] == _ITEM_KIND
            )
            if legacy not in state.seen or len(items) != 1:
                return
            state.count = min(_COMPACTION_LIMIT, state.count + 1)
            state.last_turn_id = turn_id
            if state.count == _COMPACTION_LIMIT:
                state.pending = True

    def _rotate(self, old_thread_id: str) -> None:
        stage = "profile"
        new_thread_id: str | None = None
        try:
            with self._lock:
                state = self._states.get(old_thread_id)
                last_turn_id = state.last_turn_id if state is not None else None
            if not isinstance(last_turn_id, str) or not last_turn_id:
                raise ValueError("third compaction Turn identity is missing")
            profile = self._adapters.expected_profile(
                old_thread_id, last_turn_id
            )
            if profile is None:
                self._finish_unmanaged(old_thread_id)
                return
            self._validate_profile(profile)
            params = {
                "threadId": old_thread_id,
                "lastTurnId": last_turn_id,
                "cwd": profile.cwd,
                "approvalPolicy": "never",
                "developerInstructions": profile.developer_instructions,
                "ephemeral": profile.ephemeral,
                "threadSource": copy.deepcopy(profile.thread_source),
                "model": profile.model,
                "reasoningEffort": profile.reasoning_effort,
                "serviceTier": profile.service_tier,
                "sandbox": copy.deepcopy(profile.fork_sandbox),
                "config": copy.deepcopy(dict(profile.config)),
            }
            stage = "fork"
            result = self._client.request("thread/fork", params)
            thread = result.get("thread") if isinstance(result, Mapping) else None
            candidate_id = thread.get("id") if isinstance(thread, Mapping) else None
            if (
                isinstance(candidate_id, str)
                and candidate_id
                and candidate_id != old_thread_id
            ):
                new_thread_id = candidate_id
            stage = "validate"
            new_thread_id = self._validate_fork(old_thread_id, profile, result)
            validator = self._adapters.validate_role_tools
            if validator is not None and not validator(old_thread_id, result):
                raise ValueError("role/tool response validation failed")
            stage = "rebind"
            self._adapters.rebind(old_thread_id, new_thread_id)
            stage = "readback"
            if not self._adapters.readback(old_thread_id, new_thread_id):
                raise ValueError("authoritative identity readback failed")
            stage = "publish"
            self._adapters.publish(
                {
                    "type": "thread_rotated",
                    "oldThreadId": old_thread_id,
                    "newThreadId": new_thread_id,
                }
            )
        except Exception:
            self._fail(old_thread_id, stage, new_thread_id)
            return

        try:
            self._client.request("thread/delete", {"threadId": old_thread_id})
        except Exception:
            self._safe_publish(
                {
                    "type": "thread_rotation_orphaned",
                    "code": "THREAD_ROTATION_OLD_DELETE_FAILED",
                    "oldThreadId": old_thread_id,
                    "newThreadId": new_thread_id,
                }
            )
        finally:
            with self._lock:
                self._states.pop(old_thread_id, None)

    @staticmethod
    def _validate_profile(profile: ThreadRotationProfile) -> None:
        if not profile.cwd:
            raise ValueError("cwd is required")
        if not profile.developer_instructions:
            raise ValueError("developer_instructions is required")
        if profile.ephemeral is not False:
            raise ValueError("rotation forks must be non-ephemeral")
        if not isinstance(profile.thread_source, str) or not profile.thread_source:
            raise ValueError("thread_source is required")
        if not profile.model:
            raise ValueError("model is required")
        if profile.reasoning_effort is not None and not profile.reasoning_effort:
            raise ValueError("reasoning_effort must be non-empty when set")
        if not isinstance(profile.config, Mapping):
            raise ValueError("config must be a mapping")
        if not isinstance(profile.response_sandbox, Mapping):
            raise ValueError("response_sandbox must be a mapping")

    @staticmethod
    def _validate_fork(
        old_thread_id: str,
        profile: ThreadRotationProfile,
        result: Any,
    ) -> str:
        if not isinstance(result, Mapping):
            raise ValueError("thread/fork response must be a mapping")
        thread = result.get("thread")
        if not isinstance(thread, Mapping):
            raise ValueError("thread/fork response Thread is missing")
        new_thread_id = thread.get("id")
        if (
            not isinstance(new_thread_id, str)
            or not new_thread_id
            or new_thread_id == old_thread_id
        ):
            raise ValueError("thread/fork returned an invalid replacement id")
        checks = (
            (thread.get("forkedFromId"), old_thread_id),
            (thread.get("threadSource"), profile.thread_source),
            (result.get("model"), profile.model),
            (result.get("reasoningEffort"), profile.reasoning_effort),
            (result.get("serviceTier"), profile.service_tier),
            (result.get("sandbox"), profile.response_sandbox),
            (result.get("approvalPolicy"), "never"),
        )
        if any(actual != expected for actual, expected in checks):
            raise ValueError("thread/fork direct profile validation failed")
        return new_thread_id

    def _finish_unmanaged(self, thread_id: str) -> None:
        with self._lock:
            self._states.pop(thread_id, None)

    def _fail(
        self,
        thread_id: str,
        stage: str,
        new_thread_id: str | None = None,
    ) -> None:
        with self._lock:
            state = self._states.setdefault(thread_id, _ThreadState())
            if state.failed:
                return
            state.pending = False
            state.running = False
            state.failed = True
        self._report_failure(thread_id, stage, new_thread_id)

    def _report_failure(
        self,
        thread_id: str,
        stage: str,
        new_thread_id: str | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "type": "thread_rotation_failed",
            "code": "THREAD_ROTATION_FAILED",
            "threadId": thread_id,
            "stage": stage,
        }
        if new_thread_id is not None:
            payload["newThreadId"] = new_thread_id
        self._safe_publish(payload)

    def _safe_publish(self, payload: Mapping[str, Any]) -> None:
        try:
            self._adapters.publish(payload)
        except Exception:
            pass
