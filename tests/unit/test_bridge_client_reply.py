from __future__ import annotations

import importlib.util
import sys
import threading
import time
import types
import unittest
from pathlib import Path
from typing import Any
from unittest import mock


REPOSITORY_ROOT = Path(__file__).parents[2]
PANEL_LIB_ROOT = REPOSITORY_ROOT / "houdini_package" / "python_libs"
BRIDGE_CLIENT_PATH = PANEL_LIB_ROOT / "hia_panel" / "bridge_client.py"


class _BoundSignal:
    def __init__(self, owner: object | None = None) -> None:
        self.owner = owner
        self.callbacks: list[Any] = []
        self.connect_count = 0
        self.emissions: list[tuple[Any, ...]] = []
        self.emission_threads: list[int] = []

    def connect(self, callback: Any) -> None:
        self.connect_count += 1
        self.callbacks.append(callback)

    def emit(self, *arguments: Any) -> None:
        self.emissions.append(arguments)
        self.emission_threads.append(threading.get_ident())
        previous_sender = _QObject._active_sender
        _QObject._active_sender = self.owner
        try:
            for callback in tuple(self.callbacks):
                callback(*arguments)
        finally:
            _QObject._active_sender = previous_sender


class _SignalDescriptor:
    def __init__(self, *_types: object) -> None:
        self._name = ""

    def __set_name__(self, _owner: type, name: str) -> None:
        self._name = f"_test_signal_{name}"

    def __get__(self, instance: object, _owner: type | None = None) -> Any:
        if instance is None:
            return self
        signal = instance.__dict__.get(self._name)
        if signal is None:
            signal = _BoundSignal(instance)
            instance.__dict__[self._name] = signal
        return signal


class _QObject:
    _active_sender: object | None = None

    def __init__(self, parent: object | None = None) -> None:
        self.parent = parent

    def sender(self) -> object | None:
        return self._active_sender


def _slot(*_types: object, **_options: object) -> Any:
    del _types, _options

    def decorate(callback: Any) -> Any:
        callback._is_test_qt_slot = True
        return callback

    return decorate


class _DrainTimer(_QObject):
    instances: list["_DrainTimer"] = []

    def __init__(self, parent: object | None = None) -> None:
        super().__init__(parent)
        self.timeout = _BoundSignal(self)
        self.interval: int | None = None
        self.start_count = 0
        self.stop_count = 0
        self.__class__.instances.append(self)

    def setInterval(self, interval_ms: int) -> None:
        self.interval = interval_ms

    def start(self) -> None:
        self.start_count += 1

    def stop(self) -> None:
        self.stop_count += 1


class _QueueOnlyTransport:
    instances: list["_QueueOnlyTransport"] = []

    def __init__(
        self,
        base_url: str,
        token: str,
        result_queue: Any,
        *,
        fail_submit: bool = False,
    ) -> None:
        self.base_url = base_url
        self.token = token
        self.result_queue = result_queue
        self.fail_submit = fail_submit
        self.submissions: list[dict[str, Any]] = []
        self.close_calls: list[float] = []
        self.__class__.instances.append(self)

    def submit(self, **request: Any) -> str:
        self.submissions.append(dict(request))
        if self.fail_submit:
            raise RuntimeError("synthetic submit failure")
        return str(request["request_id"])

    def close(self, max_wait_seconds: float = 0.0) -> None:
        self.close_calls.append(max_wait_seconds)


def _load_transport_bridge_client(
    *,
    fail_submit: bool = False,
    scene_executor_token: str | None = None,
) -> tuple[Any, _QueueOnlyTransport]:
    pyside = types.ModuleType("PySide6")
    qt_core = types.ModuleType("PySide6.QtCore")
    qt_core.QObject = _QObject
    qt_core.QTimer = _DrainTimer
    qt_core.Signal = _SignalDescriptor
    qt_core.Slot = _slot
    pyside.QtCore = qt_core

    sys.path.insert(0, str(PANEL_LIB_ROOT))
    replacements = {"PySide6": pyside, "PySide6.QtCore": qt_core}
    missing = object()
    saved = {name: sys.modules.get(name, missing) for name in replacements}
    module_name = "hia_panel._headless_bridge_client_transport"
    saved_module = sys.modules.get(module_name, missing)

    def factory(base_url: str, token: str, result_queue: Any) -> _QueueOnlyTransport:
        return _QueueOnlyTransport(
            base_url,
            token,
            result_queue,
            fail_submit=fail_submit,
        )

    try:
        sys.modules.update(replacements)
        spec = importlib.util.spec_from_file_location(module_name, BRIDGE_CLIENT_PATH)
        if spec is None or spec.loader is None:
            raise RuntimeError("Unable to load hia_panel.bridge_client")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        client = module.BridgeClient(
            "http://127.0.0.1:49152",
            "secret",
            transport_factory=factory,
            scene_executor_token=scene_executor_token,
        )
        return client, client._transport
    finally:
        if saved_module is missing:
            sys.modules.pop(module_name, None)
        else:
            sys.modules[module_name] = saved_module
        for name, original in saved.items():
            if original is missing:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original


def _result_for(
    submission: dict[str, Any],
    *,
    raw: bytes = b'{"ok":true}',
    error_kind: str | None = None,
    error_message: str = "",
    http_status: int | None = 200,
) -> dict[str, Any]:
    return {
        "request_id": submission["request_id"],
        "generation": submission["generation"],
        "context": submission["context"],
        "method": submission["method"],
        "path": submission["path"],
        "raw": raw,
        "http_status": http_status,
        "error_kind": error_kind,
        "error_message": error_message,
        "elapsed_ms": 1,
    }


class BridgeClientQueueTests(unittest.TestCase):
    def setUp(self) -> None:
        _DrainTimer.instances.clear()
        _QueueOnlyTransport.instances.clear()

    def test_houdini_package_has_no_qt_network_transport_symbols(self) -> None:
        package_root = REPOSITORY_ROOT / "houdini_package"
        forbidden = (
            "QtNetwork",
            "QNetworkAccessManager",
            "QNetworkReply",
            "reply.finished",
        )
        checked: list[Path] = []
        for path in package_root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in {
                ".json",
                ".py",
                ".pypanel",
            }:
                continue
            checked.append(path)
            source = path.read_text(encoding="utf-8")
            for symbol in forbidden:
                self.assertNotIn(symbol, source, str(path))
        self.assertTrue(checked)

        bridge_source = BRIDGE_CLIENT_PATH.read_text(encoding="utf-8")
        transport_source = (
            PANEL_LIB_ROOT / "hia_panel" / "http_transport.py"
        ).read_text(encoding="utf-8")
        self.assertIn("from PySide6 import QtCore", bridge_source)
        self.assertNotIn("PySide6", transport_source)
        self.assertNotIn("QtCore", transport_source)
        self.assertNotIn("Signal", transport_source)

    def test_single_timer_drains_plain_dict_and_emits_on_calling_thread(self) -> None:
        client, transport = _load_transport_bridge_client()
        self.assertEqual(1, len(_DrainTimer.instances))
        timer = _DrainTimer.instances[0]
        self.assertEqual(25, timer.interval)
        self.assertEqual(1, timer.start_count)
        self.assertEqual([client._drain_results], timer.timeout.callbacks)

        request_id = client.get_health()
        self.assertIsNotNone(request_id)
        self.assertEqual([], client.healthReceived.emissions)
        self.assertFalse(hasattr(transport, "healthReceived"))
        self.assertFalse(hasattr(transport, "requestFailed"))

        transport.result_queue.put(_result_for(transport.submissions[-1]))
        calling_thread = threading.get_ident()
        timer.timeout.emit()

        self.assertEqual([({"ok": True},)], client.healthReceived.emissions)
        self.assertEqual(
            calling_thread,
            client.healthReceived.emission_threads[-1],
        )

    def test_turn_start_forwards_local_image_paths_in_one_request(self) -> None:
        client, transport = _load_transport_bridge_client()
        image_paths = [
            r"E:\houdini-intelligence-agent\.runtime\attachments\thread-1\one.png",
            r"E:\houdini-intelligence-agent\.runtime\attachments\thread-1\two.webp",
        ]

        client.start_turn(
            "参考这些图片",
            model="model-a",
            effort="high",
            service_tier="priority",
            local_image_paths=image_paths,
        )

        submission = transport.submissions[-1]
        self.assertEqual("POST", submission["method"])
        self.assertEqual("/v1/turn", submission["path"])
        self.assertEqual(
            {
                "text": "参考这些图片",
                "model": "model-a",
                "effort": "high",
                "service_tier": "priority",
                "local_image_paths": image_paths,
            },
            submission["payload"],
        )

    def test_thread_start_and_resume_forward_dynamic_service_tier(self) -> None:
        client, transport = _load_transport_bridge_client()

        client.start_thread(model="model-a", service_tier="priority")
        self.assertEqual(
            {
                "action": "start",
                "model": "model-a",
                "service_tier": "priority",
            },
            transport.submissions[-1]["payload"],
        )

        client.resume_thread("thread-a", service_tier=None)
        self.assertEqual(
            {
                "action": "resume",
                "thread_id": "thread-a",
                "service_tier": None,
            },
            transport.submissions[-1]["payload"],
        )

    def test_long_session_and_bounded_interrupt_keep_other_control_timeouts(
        self,
    ) -> None:
        client, transport = _load_transport_bridge_client()

        client.start_thread(model=None, service_tier=None)
        client.resume_thread(
            "thread-a",
            service_tier=None,
            context="session_auto_resume",
        )
        client.read_thread("thread-a", context="thread_read:initial")
        client.get_health()
        client.interrupt(context="interrupt:test")
        client.poll_events(0)
        client.get_session(context="session_reconcile:1:1:test")

        by_context = {
            submission["context"]: submission
            for submission in transport.submissions
        }
        for context in (
            "session_start",
            "session_auto_resume",
            "thread_read:initial",
        ):
            self.assertEqual(50_000, by_context[context]["timeout_ms"])
        self.assertEqual(15_000, by_context["health"]["timeout_ms"])
        self.assertEqual(7_000, by_context["interrupt:test"]["timeout_ms"])
        self.assertEqual(20_000, by_context["events"]["timeout_ms"])
        self.assertEqual(
            5_000,
            by_context["session_reconcile:1:1:test"]["timeout_ms"],
        )

        timed_client, timed_transport = _load_transport_bridge_client()
        client_time = timed_client._request.__globals__["time"]
        with mock.patch.object(
            client_time,
            "monotonic",
            side_effect=(100.0, 116.0),
        ):
            timed_client.resume_thread(
                "thread-a",
                service_tier=None,
                context="session_auto_resume",
            )
            resume = timed_transport.submissions[-1]
            timed_client._result_queue.put(
                _result_for(
                    resume,
                    raw=b'{"ok":true,"thread_id":"thread-a","read":{}}',
                )
            )
            timed_client._drain_results()

        self.assertEqual(
            [("session_auto_resume", {"ok": True, "thread_id": "thread-a", "read": {}})],
            timed_client.actionCompleted.emissions,
        )
        self.assertEqual([], timed_client.requestFailed.emissions)

    def test_structured_codex_timeout_arrives_before_session_deadline(self) -> None:
        client, transport = _load_transport_bridge_client()
        client_time = client._request.__globals__["time"]
        with mock.patch.object(
            client_time,
            "monotonic",
            side_effect=(200.0, 245.0),
        ):
            client.resume_thread("thread-a", context="session_resume")
            submission = transport.submissions[-1]
            client._result_queue.put(
                _result_for(
                    submission,
                    raw=(
                        b'{"ok":false,"structured_error":{"code":'
                        b'"CODEX_REQUEST_TIMEOUT","message":'
                        b'"Codex request timed out"}}'
                    ),
                    http_status=504,
                )
            )
            client._drain_results()

        self.assertEqual(50_000, submission["timeout_ms"])
        self.assertGreater(submission["timeout_ms"], 45_000)
        self.assertEqual(1, len(client.requestFailed.emissions))
        context, payload = client.requestFailed.emissions[0]
        self.assertEqual("session_resume", context)
        self.assertEqual(
            "CODEX_REQUEST_TIMEOUT",
            payload["structured_error"]["code"],
        )
        self.assertEqual([], client.actionCompleted.emissions)

    def test_thread_history_requests_preserve_paths_payloads_and_contexts(self) -> None:
        client, transport = _load_transport_bridge_client()

        client.get_threads()
        client.read_thread("thread-a", context="history_read:thread-a")
        client.rename_thread(
            "thread-a",
            "Houdini lookdev",
            context="history_rename:thread-a",
        )
        client.resume_thread(
            "thread-a",
            service_tier="priority",
            context="history_resume:thread-a",
        )

        self.assertEqual(
            [
                ("GET", "/v1/threads", None, "threads"),
                (
                    "POST",
                    "/v1/session",
                    {"action": "read", "thread_id": "thread-a"},
                    "history_read:thread-a",
                ),
                (
                    "POST",
                    "/v1/threads/name",
                    {"thread_id": "thread-a", "name": "Houdini lookdev"},
                    "history_rename:thread-a",
                ),
                (
                    "POST",
                    "/v1/session",
                    {
                        "action": "resume",
                        "thread_id": "thread-a",
                        "service_tier": "priority",
                    },
                    "history_resume:thread-a",
                ),
            ],
            [
                (
                    submission["method"],
                    submission["path"],
                    submission["payload"],
                    submission["context"],
                )
                for submission in transport.submissions
            ],
        )

    def test_goal_requests_are_thin_control_calls(self) -> None:
        client, transport = _load_transport_bridge_client()

        client.get_goal("thread-a")
        client.set_goal(
            "完成当前场景",
            "active",
            thread_id="thread-a",
            token_budget=12_000,
        )
        client.clear_goal("thread-a")
        client.set_focus_mode("thread-a", True)

        self.assertEqual(
            [
                ("GET", "/v1/goal?thread_id=thread-a", None, "goal_get"),
                (
                    "POST",
                    "/v1/goal",
                    {
                        "action": "set",
                        "thread_id": "thread-a",
                        "objective": "完成当前场景",
                        "status": "active",
                        "token_budget": 12_000,
                    },
                    "goal_set",
                ),
                (
                    "POST",
                    "/v1/goal",
                    {"action": "clear", "thread_id": "thread-a"},
                    "goal_clear",
                ),
                (
                    "POST",
                    "/v1/focus",
                    {"thread_id": "thread-a", "enabled": True},
                    "focus_set",
                ),
            ],
            [
                (
                    submission["method"],
                    submission["path"],
                    submission["payload"],
                    submission["context"],
                )
                for submission in transport.submissions
            ],
        )
        self.assertTrue(
            all(submission["timeout_ms"] == 50_000 for submission in transport.submissions)
        )

    def test_turn_steer_forwards_text_and_local_images_without_starting_turn(self) -> None:
        client, transport = _load_transport_bridge_client()
        image_paths = [
            r"E:\houdini-intelligence-agent\.runtime\attachments\thread-1\follow-up.png"
        ]

        client.steer_turn(
            "追加要求",
            local_image_paths=image_paths,
            context="turn_steer:1:2:test",
        )

        submission = transport.submissions[-1]
        self.assertEqual("POST", submission["method"])
        self.assertEqual("/v1/steer", submission["path"])
        self.assertEqual(
            {
                "text": "追加要求",
                "local_image_paths": image_paths,
            },
            submission["payload"],
        )
        self.assertEqual("turn_steer:1:2:test", submission["context"])

    def test_old_generation_unknown_request_and_old_same_context_are_dropped(self) -> None:
        client, transport = _load_transport_bridge_client()
        first_id = client.get_health()
        first = dict(transport.submissions[-1])
        second_id = client.get_health()
        second = dict(transport.submissions[-1])
        self.assertNotEqual(first_id, second_id)

        old_generation = _result_for(first)
        old_generation["generation"] = first["generation"] - 1
        unknown = _result_for(first)
        unknown["request_id"] = "unknown-request"
        client._result_queue.put(old_generation)
        client._result_queue.put(unknown)
        client._result_queue.put(_result_for(first))
        client._drain_results()

        self.assertEqual([], client.healthReceived.emissions)
        self.assertIn(second_id, client._pending)

        client._result_queue.put(_result_for(second))
        client._drain_results()
        self.assertEqual([({"ok": True},)], client.healthReceived.emissions)

    def test_event_request_active_is_atomic_and_released_by_completion(self) -> None:
        client, transport = _load_transport_bridge_client()
        barrier = threading.Barrier(9)
        returned: list[str | None] = []
        returned_lock = threading.Lock()

        def poll() -> None:
            barrier.wait()
            value = client.poll_events(0)
            with returned_lock:
                returned.append(value)

        threads = [threading.Thread(target=poll) for _ in range(8)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(1.0)
            self.assertFalse(thread.is_alive())

        accepted = [value for value in returned if value is not None]
        event_submissions = [
            request
            for request in transport.submissions
            if request["context"] == "events"
        ]
        self.assertEqual(1, len(accepted))
        self.assertEqual(1, len(event_submissions))
        self.assertTrue(client._event_request_active)

        client._result_queue.put(_result_for(event_submissions[0]))
        client._drain_results()
        self.assertFalse(client._event_request_active)
        self.assertEqual(1, len(client.eventsReceived.emissions))
        self.assertIsNotNone(client.poll_events(0))

    def test_scene_poll_is_atomic_and_uses_separate_secret_header(self) -> None:
        client, transport = _load_transport_bridge_client(
            scene_executor_token="executor-secret",
        )
        barrier = threading.Barrier(9)
        returned: list[str | None] = []
        returned_lock = threading.Lock()

        def poll() -> None:
            barrier.wait()
            value = client.poll_scene_work(250)
            with returned_lock:
                returned.append(value)

        threads = [threading.Thread(target=poll) for _ in range(8)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(1.0)
            self.assertFalse(thread.is_alive())

        accepted = [value for value in returned if value is not None]
        submissions = [
            request
            for request in transport.submissions
            if request["context"] == "scene_work"
        ]
        self.assertEqual(1, len(accepted))
        self.assertEqual(1, len(submissions))
        self.assertEqual(
            {"X-HIA-Executor-Token": "executor-secret"},
            submissions[0]["secret_headers"],
        )
        self.assertTrue(client._scene_request_active)

        transport.result_queue.put(
            _result_for(submissions[0], raw=b'{"ok":true,"work":null}')
        )
        client._drain_results()
        self.assertFalse(client._scene_request_active)
        self.assertIsNotNone(client.poll_scene_work(0))

    def test_capability_and_result_requests_keep_executor_secrets_out_of_context(self) -> None:
        client, transport = _load_transport_bridge_client(
            scene_executor_token="executor-secret",
        )
        capability_id = client.publish_houdini_capabilities(
            {"available": False, "reason": "observer unavailable"}
        )
        result_id = client.complete_scene_work(
            "scene-request-1",
            "one-request-claim",
            {"ok": False},
        )

        self.assertIsNotNone(capability_id)
        self.assertIsNotNone(result_id)
        capability, result = transport.submissions[-2:]
        self.assertEqual("/v1/scene/capabilities", capability["path"])
        self.assertEqual({"report": {"available": False, "reason": "observer unavailable"}}, capability["payload"])
        self.assertEqual("scene_result:scene-request-1", result["context"])
        self.assertEqual(("one-request-claim",), result["sensitive_values"])
        for submission in (capability, result):
            self.assertNotIn("executor-secret", submission["path"])
            self.assertNotIn("executor-secret", submission["context"])

        client.dispose()
        self.assertIsNone(client._scene_executor_token)
        self.assertIsNone(client.poll_scene_work())

    def test_event_deadline_recovers_polling_and_late_result_is_ignored(self) -> None:
        client, transport = _load_transport_bridge_client()
        request_id = client.poll_events(0)
        self.assertIsNotNone(request_id)
        submission = dict(transport.submissions[-1])
        client._pending[str(request_id)]["deadline"] = time.monotonic() - 1.0

        client._drain_results()

        self.assertFalse(client._event_request_active)
        self.assertEqual(1, len(client.requestFailed.emissions))
        context, payload = client.requestFailed.emissions[0]
        self.assertEqual("events", context)
        self.assertEqual(
            "NETWORK_TIMEOUT",
            payload["structured_error"]["code"],
        )

        client._result_queue.put(_result_for(submission))
        client._drain_results()
        self.assertEqual([], client.eventsReceived.emissions)
        self.assertEqual(1, len(client.requestFailed.emissions))
        self.assertIsNotNone(client.poll_events(0))

    def test_submit_failure_is_emitted_only_by_timer_drain(self) -> None:
        client, transport = _load_transport_bridge_client(fail_submit=True)
        request_id = client.get_health()

        self.assertIsNotNone(request_id)
        self.assertEqual(1, len(transport.submissions))
        self.assertEqual([], client.requestFailed.emissions)

        _DrainTimer.instances[0].timeout.emit()

        self.assertEqual(1, len(client.requestFailed.emissions))
        context, payload = client.requestFailed.emissions[0]
        self.assertEqual("health", context)
        self.assertEqual("NETWORK_ERROR", payload["structured_error"]["code"])

    def test_dispose_is_idempotent_bounded_and_never_shuts_down_bridge(self) -> None:
        client, transport = _load_transport_bridge_client()
        client.get_health()
        health_submission = dict(transport.submissions[-1])

        client.dispose()
        client.dispose()

        self.assertEqual([0.0], transport.close_calls)
        self.assertEqual(1, _DrainTimer.instances[0].stop_count)
        self.assertTrue(client._closed)
        self.assertEqual({}, client._pending)
        self.assertIsNone(client.get_health())
        self.assertNotIn(
            "/v1/shutdown",
            [submission["path"] for submission in transport.submissions],
        )

        client._result_queue.put(_result_for(health_submission))
        client._drain_results()
        self.assertEqual([], client.healthReceived.emissions)
        self.assertEqual([], client.requestFailed.emissions)

    def test_disposed_panel_a_does_not_affect_panel_b_health_or_events(self) -> None:
        panel_a, transport_a = _load_transport_bridge_client()
        panel_b, transport_b = _load_transport_bridge_client()

        panel_a.dispose()
        health_id = panel_b.get_health()
        events_id = panel_b.poll_events(0)

        self.assertIsNotNone(health_id)
        self.assertIsNotNone(events_id)
        self.assertEqual([], transport_a.submissions)
        self.assertEqual(
            ["/v1/health", "/v1/events?after=0&timeout=15"],
            [submission["path"] for submission in transport_b.submissions],
        )
        for submission in transport_b.submissions:
            transport_b.result_queue.put(_result_for(submission))
        panel_b._drain_results()
        self.assertEqual([({"ok": True},)], panel_b.healthReceived.emissions)
        self.assertEqual([({"ok": True},)], panel_b.eventsReceived.emissions)
        self.assertNotIn(
            "/v1/shutdown",
            [
                submission["path"]
                for transport in (transport_a, transport_b)
                for submission in transport.submissions
            ],
        )

    def test_dispose_then_reopen_same_session_health_succeeds(self) -> None:
        first_panel, first_transport = _load_transport_bridge_client()
        first_panel.dispose()

        reopened_panel, reopened_transport = _load_transport_bridge_client()
        reopened_panel.get_health()
        health_submission = dict(reopened_transport.submissions[-1])
        reopened_transport.result_queue.put(_result_for(health_submission))
        reopened_panel._drain_results()

        self.assertEqual([], first_transport.submissions)
        self.assertEqual([({"ok": True},)], reopened_panel.healthReceived.emissions)
        self.assertEqual("/v1/health", health_submission["path"])


if __name__ == "__main__":
    unittest.main()
