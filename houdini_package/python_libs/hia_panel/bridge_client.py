"""Asynchronous standard-library client for the authenticated local Bridge."""

from __future__ import annotations

import queue
import re
import threading
import time
import uuid
from collections.abc import Callable, Iterable, Mapping
from typing import Any
from urllib.parse import quote

from PySide6 import QtCore

from .http_transport import HttpTransport
from .network_response import normalize_bridge_response


_RECONCILIATION_TIMEOUT_MS = 5_000
_EVENT_POLL_TIMEOUT_MS = 20_000
_DEFAULT_REQUEST_TIMEOUT_MS = 15_000
_SESSION_ACTION_TIMEOUT_MS = 50_000
_INTERRUPT_TIMEOUT_MS = 7_000
_RESULT_DRAIN_INTERVAL_MS = 25
_MAX_RESULTS_PER_TICK = 128
_RESULT_QUEUE_LIMIT = 256
_SCENE_CONTROL_TIMEOUT_MS = 5_000
_SCENE_POLL_TIMEOUT_MS = 3_000
_SCENE_REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_LONG_SESSION_ACTIONS = frozenset({"start", "resume", "read"})
_GOAL_ACTION_CONTEXTS = frozenset(
    {"goal_get", "goal_set", "goal_clear", "focus_set"}
)


class BridgeClient(QtCore.QObject):
    healthReceived = QtCore.Signal(dict)
    sessionReceived = QtCore.Signal(dict)
    eventsReceived = QtCore.Signal(dict)
    actionCompleted = QtCore.Signal(str, dict)
    requestFailed = QtCore.Signal(str, dict)

    def __init__(
        self,
        base_url: str,
        token: str,
        parent: QtCore.QObject | None = None,
        *,
        transport_factory: Callable[..., HttpTransport] = HttpTransport,
        scene_executor_token: str | None = None,
    ) -> None:
        super().__init__(parent)
        self._result_queue: queue.Queue[dict[str, Any]] = queue.Queue(
            maxsize=_RESULT_QUEUE_LIMIT
        )
        self._state_lock = threading.Lock()
        self._generation = 1
        self._closed = False
        self._event_request_active = False
        self._active_event_request_id: str | None = None
        self._scene_request_active = False
        self._active_scene_request_id: str | None = None
        self._pending: dict[str, dict[str, Any]] = {}
        self._latest_request_by_context: dict[str, str] = {}
        if scene_executor_token is not None and (
            not isinstance(scene_executor_token, str)
            or not scene_executor_token
            or len(scene_executor_token) > 512
            or "\r" in scene_executor_token
            or "\n" in scene_executor_token
        ):
            raise ValueError("Scene executor token is invalid")
        self._scene_executor_token = scene_executor_token
        self._transport = transport_factory(base_url, token, self._result_queue)

        # This is the only UI-thread timer used by the transport.  Workers put
        # plain dictionaries into the queue; only this slot emits Qt signals.
        self._drain_timer = QtCore.QTimer(self)
        self._drain_timer.setInterval(_RESULT_DRAIN_INTERVAL_MS)
        self._drain_timer.timeout.connect(self._drain_results)
        self._drain_timer.start()

    def get_health(self) -> str | None:
        return self._request("GET", "/v1/health", context="health")

    def get_houdini_status(self) -> str | None:
        """Refresh only the Panel's local Houdini status from the health route."""

        return self._request("GET", "/v1/health", context="houdini_status")

    def get_session(self, *, context: str = "session") -> str | None:
        """Read Bridge session state with a caller-supplied correlation context."""

        return self._request("GET", "/v1/session", context=context)

    def get_models(self) -> str | None:
        """Read the Bridge-sanitized stable Codex model catalog."""

        return self._request("GET", "/v1/models", context="models")

    def get_threads(self) -> str | None:
        return self._request("GET", "/v1/threads", context="threads")

    def get_goal(self, thread_id: str) -> str | None:
        return self._request(
            "GET",
            f"/v1/goal?thread_id={quote(thread_id, safe='')}",
            context="goal_get",
        )

    def set_goal(
        self,
        objective: str,
        status: str,
        *,
        thread_id: str,
        token_budget: int | None = None,
    ) -> str | None:
        return self._request(
            "POST",
            "/v1/goal",
            {
                "action": "set",
                "thread_id": thread_id,
                "objective": objective,
                "status": status,
                "token_budget": token_budget,
            },
            context="goal_set",
        )

    def clear_goal(self, thread_id: str) -> str | None:
        return self._request(
            "POST",
            "/v1/goal",
            {"action": "clear", "thread_id": thread_id},
            context="goal_clear",
        )

    def set_focus_mode(self, thread_id: str, enabled: bool) -> str | None:
        return self._request(
            "POST",
            "/v1/focus",
            {"thread_id": thread_id, "enabled": enabled},
            context="focus_set",
        )

    def read_thread(
        self,
        thread_id: str,
        *,
        context: str = "thread_read",
    ) -> str | None:
        return self._request(
            "POST",
            "/v1/session",
            {"action": "read", "thread_id": thread_id},
            context=context,
        )

    def rename_thread(
        self,
        thread_id: str,
        name: str,
        *,
        context: str = "thread_rename",
    ) -> str | None:
        return self._request(
            "POST",
            "/v1/threads/name",
            {"thread_id": thread_id, "name": name},
            context=context,
        )

    def start_thread(
        self,
        *,
        model: str | None = None,
        service_tier: str | None = None,
    ) -> str | None:
        payload: dict[str, Any] = {
            "action": "start",
            "service_tier": service_tier,
        }
        if model is not None:
            payload["model"] = model
        return self._request(
            "POST",
            "/v1/session",
            payload,
            context="session_start",
        )

    def resume_thread(
        self,
        thread_id: str,
        *,
        service_tier: str | None = None,
        context: str = "session_resume",
    ) -> str | None:
        return self._request(
            "POST",
            "/v1/session",
            {
                "action": "resume",
                "thread_id": thread_id,
                "service_tier": service_tier,
            },
            context=context,
        )

    def start_turn(
        self,
        text: str,
        *,
        model: str | None = None,
        effort: str | None = None,
        service_tier: str | None = None,
        local_image_paths: list[str] | None = None,
        context: str = "turn_start",
    ) -> str | None:
        payload: dict[str, Any] = {
            "text": text,
            "service_tier": service_tier,
        }
        if model is not None:
            payload["model"] = model
        if effort is not None:
            payload["effort"] = effort
        if local_image_paths is not None:
            payload["local_image_paths"] = list(local_image_paths)
        return self._request(
            "POST",
            "/v1/turn",
            payload,
            context=context,
        )

    def steer_turn(
        self,
        text: str,
        *,
        local_image_paths: list[str] | None = None,
        context: str = "turn_steer",
    ) -> str | None:
        payload: dict[str, Any] = {"text": text}
        if local_image_paths is not None:
            payload["local_image_paths"] = list(local_image_paths)
        return self._request(
            "POST",
            "/v1/steer",
            payload,
            context=context,
        )

    def interrupt(self, *, context: str = "interrupt") -> str | None:
        return self._request("POST", "/v1/interrupt", {}, context=context)

    def resolve_approval(self, request_id: Any, decision: str) -> str | None:
        return self._request(
            "POST",
            "/v1/approval",
            {"request_id": request_id, "decision": decision},
            context=f"approval_{decision}",
        )

    def poll_events(self, after: int, timeout: int = 15) -> str | None:
        # Normalize caller values before claiming the single event slot so an
        # invalid value cannot leave polling permanently marked active.
        after_value = int(after)
        timeout_value = int(timeout)
        path = f"/v1/events?after={after_value}&timeout={timeout_value}"
        with self._state_lock:
            if self._closed or self._event_request_active:
                return None
            self._event_request_active = True
        return self._request("GET", path, context="events", event_request=True)

    def publish_houdini_capabilities(
        self,
        report: Mapping[str, Any],
    ) -> str | None:
        """Publish one main-thread Houdini report on the executor trust path."""

        headers = self._scene_headers()
        if headers is None or not isinstance(report, Mapping):
            return None
        return self._request(
            "POST",
            "/v1/scene/capabilities",
            {"report": dict(report)},
            context="scene_capabilities",
            secret_headers=headers,
        )

    def poll_scene_work(self, wait_ms: int = 250) -> str | None:
        """Claim at most one bounded live-read work poll at a time."""

        headers = self._scene_headers()
        if headers is None:
            return None
        wait_value = int(wait_ms)
        if not 0 <= wait_value <= 1_000:
            raise ValueError("scene wait_ms must be between 0 and 1000")
        with self._state_lock:
            if self._closed or self._scene_request_active:
                return None
            self._scene_request_active = True
        request_id = self._request(
            "GET",
            f"/v1/scene/requests/next?wait_ms={wait_value}",
            context="scene_work",
            scene_request=True,
            secret_headers=headers,
        )
        if request_id is None:
            with self._state_lock:
                self._scene_request_active = False
                self._active_scene_request_id = None
        return request_id

    def complete_scene_work(
        self,
        request_id: str,
        executor_token: str,
        result: Mapping[str, Any],
    ) -> str | None:
        """Return one read result without exposing either executor credential."""

        headers = self._scene_headers()
        if (
            headers is None
            or not isinstance(request_id, str)
            or _SCENE_REQUEST_ID.fullmatch(request_id) is None
            or not isinstance(executor_token, str)
            or not executor_token
            or len(executor_token) > 512
            or "\r" in executor_token
            or "\n" in executor_token
            or not isinstance(result, Mapping)
        ):
            return None
        return self._request(
            "POST",
            f"/v1/scene/requests/{request_id}/result",
            {"executor_token": executor_token, "result": dict(result)},
            context=f"scene_result:{request_id}",
            secret_headers=headers,
            sensitive_values=(executor_token,),
        )

    def _scene_headers(self) -> dict[str, str] | None:
        token = self._scene_executor_token
        if not isinstance(token, str) or not token:
            return None
        return {"X-HIA-Executor-Token": token}

    def dispose(self) -> None:
        """Idempotently release only this Panel's local client resources.

        The Bridge belongs to the launcher and may be shared by more than one
        Panel instance.  Disposing a Panel must therefore never request
        process-wide Bridge termination.
        """

        with self._state_lock:
            if self._closed:
                return
            self._closed = True
            self._generation += 1
            self._pending.clear()
            self._latest_request_by_context.clear()
            self._event_request_active = False
            self._active_event_request_id = None
            self._scene_request_active = False
            self._active_scene_request_id = None
            self._scene_executor_token = None
        self._drain_timer.stop()
        self._transport.close(max_wait_seconds=0.0)

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        context: str,
        event_request: bool = False,
        scene_request: bool = False,
        secret_headers: Mapping[str, str] | None = None,
        sensitive_values: Iterable[str] | None = None,
    ) -> str | None:
        if context.startswith("session_reconcile:"):
            timeout_ms = _RECONCILIATION_TIMEOUT_MS
        elif method == "POST" and path == "/v1/interrupt":
            timeout_ms = _INTERRUPT_TIMEOUT_MS
        elif context in _GOAL_ACTION_CONTEXTS:
            timeout_ms = _SESSION_ACTION_TIMEOUT_MS
        elif (
            method == "POST"
            and path == "/v1/session"
            and isinstance(payload, dict)
            and payload.get("action") in _LONG_SESSION_ACTIONS
        ):
            timeout_ms = _SESSION_ACTION_TIMEOUT_MS
        elif context == "events":
            timeout_ms = _EVENT_POLL_TIMEOUT_MS
        elif context == "scene_work":
            timeout_ms = _SCENE_POLL_TIMEOUT_MS
        elif context == "scene_capabilities" or context.startswith("scene_result:"):
            timeout_ms = _SCENE_CONTROL_TIMEOUT_MS
        else:
            timeout_ms = _DEFAULT_REQUEST_TIMEOUT_MS

        request_id = uuid.uuid4().hex
        deadline = time.monotonic() + (timeout_ms / 1000.0)
        with self._state_lock:
            if self._closed:
                if event_request:
                    self._event_request_active = False
                if scene_request:
                    self._scene_request_active = False
                return None
            generation = self._generation
            self._pending[request_id] = {
                "generation": generation,
                "context": context,
                "method": method,
                "path": path,
                "deadline": deadline,
            }
            self._latest_request_by_context[context] = request_id
            if event_request:
                self._active_event_request_id = request_id
            if scene_request:
                self._active_scene_request_id = request_id
        try:
            self._transport.submit(
                method=method,
                path=path,
                payload=payload,
                context=context,
                request_id=request_id,
                generation=generation,
                timeout_ms=timeout_ms,
                deadline_monotonic=deadline,
                event_request=event_request,
                secret_headers=secret_headers,
                sensitive_values=sensitive_values,
            )
        except Exception:
            # Keep the same result path even for a local submission failure so
            # only the main-thread drain timer emits Qt signals.
            try:
                self._result_queue.put_nowait(
                    {
                        "request_id": request_id,
                        "generation": generation,
                        "context": context,
                        "method": method,
                        "path": path,
                        "raw": b"",
                        "http_status": None,
                        "error_kind": "transport_error",
                        "error_message": "Unable to queue Bridge request",
                        "elapsed_ms": 0,
                    }
                )
            except queue.Full:
                # The main-thread deadline will produce the bounded failure.
                pass
        return request_id

    @QtCore.Slot()
    def _drain_results(self) -> None:
        self._expire_deadlines()
        for _ in range(_MAX_RESULTS_PER_TICK):
            try:
                result = self._result_queue.get_nowait()
            except queue.Empty:
                return
            try:
                self._consume_result(result)
            finally:
                self._result_queue.task_done()

    def _expire_deadlines(self) -> None:
        now = time.monotonic()
        with self._state_lock:
            expired = [
                (request_id, dict(pending))
                for request_id, pending in self._pending.items()
                if float(pending.get("deadline", now + 1.0)) <= now
            ]
        for request_id, pending in expired:
            self._consume_result(
                {
                    "request_id": request_id,
                    "generation": pending["generation"],
                    "context": pending["context"],
                    "method": pending["method"],
                    "path": pending["path"],
                    "raw": b"",
                    "http_status": None,
                    "error_kind": "timeout",
                    "error_message": "Bridge request timed out",
                    "elapsed_ms": 0,
                }
            )

    def _consume_result(self, result: object) -> None:
        if not isinstance(result, dict):
            return
        request_id = result.get("request_id")
        generation = result.get("generation")
        if not isinstance(request_id, str) or not isinstance(generation, int):
            return
        with self._state_lock:
            pending = self._pending.get(request_id)
            if pending is None or pending.get("generation") != generation:
                return
            self._pending.pop(request_id, None)
            context = str(pending.get("context", ""))
            is_latest = self._latest_request_by_context.get(context) == request_id
            if is_latest:
                self._latest_request_by_context.pop(context, None)
            if self._active_event_request_id == request_id:
                self._active_event_request_id = None
                self._event_request_active = False
            if self._active_scene_request_id == request_id:
                self._active_scene_request_id = None
                self._scene_request_active = False
            if self._closed or generation != self._generation or not is_latest:
                return

        method = str(pending.get("method", ""))
        path = str(pending.get("path", ""))
        raw = result.get("raw", b"")
        if not isinstance(raw, bytes):
            raw = b""
        payload = normalize_bridge_response(
            raw,
            error_kind=(
                str(result["error_kind"])
                if result.get("error_kind") is not None
                else None
            ),
            error_message=str(result.get("error_message", "")),
            http_status=self._integer_value(result.get("http_status")),
            context=context,
            method=method,
            path=path,
            request_id=request_id,
            generation=generation,
        )
        if not payload.get("ok", False):
            self.requestFailed.emit(context, payload)
        elif context == "health":
            self.healthReceived.emit(payload)
        elif context == "session":
            self.sessionReceived.emit(payload)
        elif context == "events":
            self.eventsReceived.emit(payload)
        else:
            self.actionCompleted.emit(context, payload)

    @staticmethod
    def _integer_value(value: Any) -> int | None:
        if value is None:
            return None
        raw_value = getattr(value, "value", value)
        try:
            return int(raw_value)
        except (TypeError, ValueError):
            return None
