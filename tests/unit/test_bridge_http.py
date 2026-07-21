from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Any, Callable
from unittest import mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen


REPOSITORY_ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))
sys.path.insert(0, str(REPOSITORY_ROOT / "services" / "bridge"))
sys.path.insert(0, str(REPOSITORY_ROOT / "houdini_package" / "python_libs"))

from hia_bridge.codex_stdio import CodexStdioClient  # noqa: E402
from hia_bridge.events import EventBuffer  # noqa: E402
from hia_bridge.http_server import (  # noqa: E402
    BridgeApplication,
    LoopbackHTTPServer,
)
from hia_bridge.protocol import ProtocolPolicy  # noqa: E402
from hia_bridge.session import BridgeSession  # noqa: E402
from hia_panel.turn_state import PanelTurnState, TurnPhase  # noqa: E402


class BridgeHTTPTests(unittest.TestCase):
    TOKEN = "test-token-with-at-least-thirty-two-characters"

    def setUp(self) -> None:
        policy = ProtocolPolicy.from_project_root(REPOSITORY_ROOT)
        environment = os.environ.copy()
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        self.client = CodexStdioClient(
            [
                sys.executable,
                "-B",
                str(REPOSITORY_ROOT / "tests" / "fakes" / "fake_app_server.py"),
            ],
            cwd=REPOSITORY_ROOT,
            environment=environment,
            policy=policy,
            request_timeout=5.0,
        )
        self.events = EventBuffer()
        self.session = BridgeSession(REPOSITORY_ROOT, self.client, self.events)
        self.session.start()
        self.application = BridgeApplication(self.session, self.events, self.TOKEN)
        self.server = LoopbackHTTPServer(("127.0.0.1", 0), self.application)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address
        self.base_url = f"http://{host}:{port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2.0)
        self.session.close()

    def request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        token: str | None = None,
    ) -> dict[str, Any]:
        data = None
        headers = {"Authorization": f"Bearer {token or self.TOKEN}"}
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(
            self.base_url + path,
            data=data,
            headers=headers,
            method=method,
        )
        with urlopen(request, timeout=5.0) as response:
            return json.loads(response.read().decode("utf-8"))

    def wait_for_event(
        self,
        predicate: Callable[[dict[str, Any]], bool],
        *,
        after: int = 0,
    ) -> tuple[dict[str, Any], int]:
        deadline = time.monotonic() + 5.0
        observed: list[dict[str, Any]] = []
        while time.monotonic() < deadline:
            batch = self.request("GET", f"/v1/events?after={after}&timeout=1")
            after = batch["latest"]
            observed.extend(batch["events"])
            for event in batch["events"]:
                if predicate(event):
                    return event, after
        self.fail(f"Expected event was not observed; events={observed!r}")

    def complete_turn(self, turn_id: str, *, after: int = 0) -> int:
        approval, after = self.wait_for_event(
            lambda event: event.get("type") == "server_request"
            and event.get("params", {}).get("turnId") == turn_id,
            after=after,
        )
        resolved = self.request(
            "POST",
            "/v1/approval",
            {"request_id": approval["request_id"], "decision": "allow"},
        )
        self.assertEqual("allow", resolved["decision"])
        completed, after = self.wait_for_event(
            lambda event: event.get("type") == "codex_notification"
            and event.get("method") == "turn/completed"
            and event.get("params", {}).get("turn", {}).get("id") == turn_id,
            after=after,
        )
        self.assertEqual("completed", completed["params"]["turn"]["status"])
        return after

    def test_health_is_authenticated_and_bound_to_loopback(self) -> None:
        health = self.request("GET", "/v1/health")
        self.assertTrue(health["ok"])
        self.assertEqual("127.0.0.1", self.server.server_address[0])
        self.assertEqual("authenticated", health["session"]["authentication"])
        self.assertNotIn("email", health["session"]["account"]["account"])
        self.assertEqual(
            {
                "backend": "fxhoudini",
                "server_id": "houdini_intelligence",
                "display_name": "FXHoudiniMCP 1.3.0",
                "available": False,
            },
            health["houdini_mcp"],
        )
        with self.assertRaises(ValueError):
            LoopbackHTTPServer(("0.0.0.0", 0), self.application)

    def test_hia_v2_health_uses_authenticated_get_and_strict_payload(self) -> None:
        mcp_token = "hia-runtime-" + "x" * 40
        application = BridgeApplication(
            self.session,
            self.events,
            self.TOKEN,
            houdini_mcp_port=45123,
            houdini_mcp_token=mcp_token,
            houdini_mcp_backend="hia_v2",
        )
        payload = {
            "protocol": "hia-mcp-v2/1",
            "ok": True,
            "result": {"server_id": "hia_mcp_v2", "scene_revision": 4},
        }
        with mock.patch(
            "hia_bridge.http_server.urllib_request.urlopen",
            return_value=io.BytesIO(json.dumps(payload).encode("utf-8")),
        ) as open_url:
            status = application.houdini_mcp_status()

        self.assertEqual(
            {
                "backend": "hia_v2",
                "server_id": "hia_mcp_v2",
                "display_name": "HIA MCP V2",
                "available": True,
                "scene_revision": 4,
            },
            status,
        )
        request = open_url.call_args.args[0]
        self.assertEqual(
            "http://127.0.0.1:45123/hia-mcp-v2/v1/health",
            request.full_url,
        )
        self.assertEqual("GET", request.get_method())
        self.assertEqual(f"Bearer {mcp_token}", request.get_header("Authorization"))
        self.assertEqual(0.75, open_url.call_args.kwargs["timeout"])

        invalid_payload = {**payload, "unexpected": True}
        with mock.patch(
            "hia_bridge.http_server.urllib_request.urlopen",
            return_value=io.BytesIO(json.dumps(invalid_payload).encode("utf-8")),
        ):
            unavailable = application.houdini_mcp_status()
        self.assertFalse(unavailable["available"])
        self.assertEqual("hia_v2", unavailable["backend"])
        self.assertIsNone(unavailable["scene_revision"])

    def test_bad_token_is_rejected(self) -> None:
        with self.assertRaises(HTTPError) as raised:
            self.request("GET", "/v1/health", token="wrong-token")
        self.assertEqual(401, raised.exception.code)
        payload = json.loads(raised.exception.read().decode("utf-8"))
        self.assertEqual("UNAUTHORIZED", payload["structured_error"]["code"])

    def test_model_catalog_is_paginated_sanitized_and_filters_hidden(self) -> None:
        response = self.request("GET", "/v1/models")
        self.assertTrue(response["ok"])
        self.assertEqual(
            ["fake-default-model", "fake-secondary-model"],
            [model["model"] for model in response["models"]],
        )
        expected_keys = {
            "model",
            "displayName",
            "description",
            "isDefault",
            "inputModalities",
            "serviceTiers",
            "defaultServiceTier",
            "supportedReasoningEfforts",
            "defaultReasoningEffort",
        }
        for model in response["models"]:
            self.assertEqual(expected_keys, set(model))
            self.assertNotIn("hidden", model)
            self.assertEqual([], model["serviceTiers"])
            self.assertIsNone(model["defaultServiceTier"])
            for effort in model["supportedReasoningEfforts"]:
                self.assertEqual(
                    {"reasoningEffort", "description"},
                    set(effort),
                )

    def test_thread_history_list_and_rename_routes(self) -> None:
        listed = self.request("GET", "/v1/threads")

        self.assertEqual(
            {
                "ok": True,
                "threads": [
                    {
                        "thread_id": "thread-fake",
                        "name": "Fake Thread",
                        "preview": "fake thread",
                        "updated_at": 1_720_000_000,
                        "recency_at": 1_720_000_001,
                    }
                ],
            },
            listed,
        )

        name = "  Houdini lookdev  "
        renamed = self.request(
            "POST",
            "/v1/threads/name",
            {"thread_id": "thread-fake", "name": name},
        )
        self.assertEqual("thread-fake", renamed["thread_id"])
        self.assertEqual(name, renamed["name"])
        self.assertEqual(
            {"threadId": "thread-fake", "name": name},
            renamed["result"]["receivedParams"],
        )

    def test_native_goal_routes_use_the_selected_thread(self) -> None:
        self.request("POST", "/v1/session", {"action": "start"})

        empty = self.request("GET", "/v1/goal?thread_id=thread-fake")
        saved = self.request(
            "POST",
            "/v1/goal",
            {
                "action": "set",
                "thread_id": "thread-fake",
                "objective": "完成当前木屋任务",
                "status": "active",
                "token_budget": 25_000,
            },
        )
        focused = self.request(
            "POST",
            "/v1/focus",
            {"thread_id": "thread-fake", "enabled": True},
        )
        session = self.request("GET", "/v1/session")
        cleared = self.request(
            "POST",
            "/v1/goal",
            {"action": "clear", "thread_id": "thread-fake"},
        )

        self.assertIsNone(empty["goal"])
        self.assertEqual("thread-fake", saved["thread_id"])
        self.assertEqual("完成当前木屋任务", saved["goal"]["objective"])
        self.assertEqual(25_000, saved["goal"]["tokenBudget"])
        self.assertTrue(focused["focus_mode"])
        self.assertTrue(session["session"]["focus_mode"])
        self.assertTrue(cleared["cleared"])
        self.assertFalse(cleared["focus_mode"])

    def test_unicode_model_effort_and_service_tier_are_forwarded_without_loss(
        self,
    ) -> None:
        original = "中文输入测试：请生成一张四条腿的桌子，尺寸为 120×60×75 厘米。"
        model = "fake-default-model"
        started = self.request(
            "POST",
            "/v1/session",
            {
                "action": "start",
                "model": model,
                "service_tier": "priority",
            },
        )
        self.assertEqual(model, started["result"]["receivedParams"]["model"])
        self.assertEqual(
            "priority",
            started["result"]["receivedParams"]["serviceTier"],
        )

        turn = self.request(
            "POST",
            "/v1/turn",
            {
                "text": original,
                "model": model,
                "effort": "high",
                "service_tier": "priority",
            },
        )
        received = turn["result"]["receivedParams"]
        self.assertEqual(original, received["input"][0]["text"])
        self.assertEqual(model, received["model"])
        self.assertEqual("high", received["effort"])
        self.assertEqual("priority", received["serviceTier"])

    def test_turn_endpoint_forwards_local_image_paths_to_session(self) -> None:
        attachments_root = REPOSITORY_ROOT / ".runtime" / "attachments"
        attachments_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="bridge-http-test-",
            dir=attachments_root,
        ) as temporary_thread_directory:
            thread_directory = Path(temporary_thread_directory)
            thread_id = thread_directory.name
            image = thread_directory / "reference.png"
            image.write_bytes(b"test image payload")
            self.request(
                "POST",
                "/v1/session",
                {"action": "resume", "thread_id": thread_id},
            )
            response = self.request(
                "POST",
                "/v1/turn",
                {"text": "参考图片", "local_image_paths": [str(image)]},
            )

            received = response["result"]["receivedParams"]
            self.assertEqual(thread_id, received["threadId"])
            self.assertEqual(
                [
                    {
                        "type": "text",
                        "text": "参考图片",
                        "text_elements": [],
                    },
                    {"type": "localImage", "path": str(image.resolve())},
                ],
                received["input"],
            )

    def test_steer_endpoint_appends_to_the_same_active_turn(self) -> None:
        attachments_root = REPOSITORY_ROOT / ".runtime" / "attachments"
        attachments_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="bridge-steer-test-",
            dir=attachments_root,
        ) as temporary_thread_directory:
            thread_directory = Path(temporary_thread_directory)
            thread_id = thread_directory.name
            image = thread_directory / "follow-up.webp"
            image.write_bytes(b"test image payload")
            self.request(
                "POST",
                "/v1/session",
                {"action": "resume", "thread_id": thread_id},
            )
            started = self.request("POST", "/v1/turn", {"text": "initial"})

            steered = self.request(
                "POST",
                "/v1/steer",
                {
                    "text": "追加要求",
                    "local_image_paths": [str(image)],
                },
            )

            self.assertEqual(started["turn_id"], steered["turn_id"])
            self.assertEqual(started["turn_id"], steered["result"]["turnId"])
            received = steered["result"]["receivedParams"]
            self.assertEqual(thread_id, received["threadId"])
            self.assertEqual(started["turn_id"], received["expectedTurnId"])
            self.assertEqual(
                [
                    {
                        "type": "text",
                        "text": "追加要求",
                        "text_elements": [],
                    },
                    {"type": "localImage", "path": str(image.resolve())},
                ],
                received["input"],
            )

    def test_model_effort_and_service_tier_validation_is_structured(self) -> None:
        for value in ("", "   ", "bad\nmodel", 7, "m" * 257):
            with self.subTest(model=value):
                with self.assertRaises(HTTPError) as raised:
                    self.request(
                        "POST",
                        "/v1/session",
                        {"action": "start", "model": value},
                    )
                self.assertEqual(400, raised.exception.code)
                payload = json.loads(raised.exception.read().decode("utf-8"))
                self.assertEqual(
                    "INVALID_MODEL",
                    payload["structured_error"]["code"],
                )

        self.request("POST", "/v1/session", {"action": "start"})
        for value in ("", "   ", "bad\neffort", 7, "e" * 65):
            with self.subTest(effort=value):
                with self.assertRaises(HTTPError) as raised:
                    self.request(
                        "POST",
                        "/v1/turn",
                        {"text": "hello", "effort": value},
                    )
                self.assertEqual(400, raised.exception.code)
                payload = json.loads(raised.exception.read().decode("utf-8"))
                self.assertEqual(
                    "INVALID_EFFORT",
                    payload["structured_error"]["code"],
                )

        for value in ("", "   ", "bad\ntier", 7, "t" * 257):
            with self.subTest(service_tier=value):
                with self.assertRaises(HTTPError) as raised:
                    self.request(
                        "POST",
                        "/v1/turn",
                        {"text": "hello", "service_tier": value},
                    )
                self.assertEqual(400, raised.exception.code)
                payload = json.loads(raised.exception.read().decode("utf-8"))
                self.assertEqual(
                    "INVALID_SERVICE_TIER",
                    payload["structured_error"]["code"],
                )

    def test_session_turn_events_approval_and_interrupt(self) -> None:
        started = self.request("POST", "/v1/session", {"action": "start"})
        self.assertEqual("thread-fake", started["thread_id"])
        self.assertNotIn("model", started["result"]["receivedParams"])
        self.assertIsNone(started["result"]["receivedParams"]["serviceTier"])
        turn = self.request("POST", "/v1/turn", {"text": "hello"})
        self.assertEqual("turn-fake", turn["turn_id"])
        self.assertNotIn("model", turn["result"]["receivedParams"])
        self.assertNotIn("effort", turn["result"]["receivedParams"])
        self.assertIsNone(turn["result"]["receivedParams"]["serviceTier"])

        _, after = self.wait_for_event(
            lambda event: event.get("type") == "server_request"
            and event.get("params", {}).get("turnId") == turn["turn_id"],
        )
        interrupted = self.request("POST", "/v1/interrupt", {})
        self.assertEqual("turn-fake", interrupted["turn_id"])
        completed, _ = self.wait_for_event(
            lambda event: event.get("type") == "codex_notification"
            and event.get("method") == "turn/completed"
            and event.get("params", {}).get("turn", {}).get("id") == turn["turn_id"],
            after=after,
        )
        self.assertEqual("interrupted", completed["params"]["turn"]["status"])
        session = self.request("GET", "/v1/session")["session"]
        self.assertFalse(session["turn_active"])

    def test_resume_and_deny_approval(self) -> None:
        resumed = self.request(
            "POST",
            "/v1/session",
            {"action": "resume", "thread_id": "thread-resumed"},
        )
        self.assertEqual("thread-resumed", resumed["thread_id"])
        self.request("POST", "/v1/turn", {"text": "request approval"})
        batch = self.request("GET", "/v1/events?after=0&timeout=1")
        approval = next(
            event
            for event in batch["events"]
            if event.get("type") == "server_request"
        )
        denied = self.request(
            "POST",
            "/v1/approval",
            {"request_id": approval["request_id"], "decision": "deny"},
        )
        self.assertEqual("deny", denied["decision"])

    def test_active_turn_rejects_second_start_with_structured_conflict(self) -> None:
        self.request("POST", "/v1/session", {"action": "start"})
        first = self.request("POST", "/v1/turn", {"text": "first"})

        with self.assertRaises(HTTPError) as raised:
            self.request("POST", "/v1/turn", {"text": "second"})
        self.assertEqual(409, raised.exception.code)
        payload = json.loads(raised.exception.read().decode("utf-8"))
        error = payload["structured_error"]
        self.assertEqual("TURN_ALREADY_ACTIVE", error["code"])
        self.assertFalse(error["details"]["turn_created"])
        self.assertTrue(error["details"]["turn_active"])
        self.assertEqual(first["turn_id"], error["details"]["turn_id"])

        session = self.request("GET", "/v1/session")["session"]
        self.assertTrue(session["turn_active"])
        self.assertEqual(first["turn_id"], session["turn_id"])

    def test_visible_delta_then_no_active_interrupt_recovers_panel_state(self) -> None:
        started = self.request("POST", "/v1/session", {"action": "start"})
        state = PanelTurnState()
        self.assertTrue(state.begin_start(started["thread_id"]))
        start_token = state.capture_token()

        turn = self.request("POST", "/v1/turn", {"text": "visible reply"})
        self.assertTrue(
            state.acknowledge_start(
                start_token,
                started["thread_id"],
                turn["turn_id"],
            )
        )

        after = 0
        observed: list[dict[str, Any]] = []
        approval: dict[str, Any] | None = None
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and approval is None:
            batch = self.request("GET", f"/v1/events?after={after}&timeout=1")
            after = batch["latest"]
            observed.extend(batch["events"])
            approval = next(
                (
                    event
                    for event in observed
                    if event.get("type") == "server_request"
                    and event.get("params", {}).get("turnId") == turn["turn_id"]
                ),
                None,
            )
        self.assertIsNotNone(approval)
        displayed_reply = "".join(
            event.get("params", {}).get("delta", "")
            for event in observed
            if event.get("type") == "codex_notification"
            and event.get("method") == "item/agentMessage/delta"
            and event.get("params", {}).get("turnId") == turn["turn_id"]
        )
        self.assertEqual("Hello from fake Codex", displayed_reply)

        resolved = self.request(
            "POST",
            "/v1/approval",
            {"request_id": approval["request_id"], "decision": "allow"},
        )
        self.assertEqual("allow", resolved["decision"])
        self.wait_for_event(
            lambda event: event.get("type") == "codex_notification"
            and event.get("method") == "turn/completed"
            and event.get("params", {}).get("turn", {}).get("id")
            == turn["turn_id"],
            after=after,
        )

        # The completion event was deliberately not applied to PanelTurnState:
        # the visible delta exists, while the simulated Panel remains active.
        self.assertEqual(TurnPhase.IN_PROGRESS, state.phase)
        interrupt_token = state.capture_token()
        with self.assertRaises(HTTPError) as raised:
            self.request("POST", "/v1/interrupt", {})
        self.assertEqual(409, raised.exception.code)
        payload = json.loads(raised.exception.read().decode("utf-8"))
        error = payload["structured_error"]
        self.assertEqual("NO_ACTIVE_TURN", error["code"])
        self.assertFalse(error["details"]["turn_active"])
        self.assertEqual("completed", error["details"]["turn_status"])

        self.assertTrue(
            state.reconcile_no_active_error(interrupt_token, error["details"])
        )
        controls = state.derive_controls(
            connected=True,
            authenticated=True,
            selected_thread_id=started["thread_id"],
        )
        self.assertEqual(TurnPhase.IDLE, state.phase)
        self.assertTrue(controls.send)
        self.assertTrue(controls.new_thread)
        self.assertTrue(controls.resume_thread)
        self.assertFalse(controls.stop)

    def test_four_consecutive_turns_complete_with_distinct_ids(self) -> None:
        self.request("POST", "/v1/session", {"action": "start"})
        after = 0
        observed_turn_ids: list[str] = []
        expected_turn_ids = [
            "turn-fake",
            "turn-fake-2",
            "turn-fake-3",
            "turn-fake-4",
        ]

        for index, expected_turn_id in enumerate(expected_turn_ids, start=1):
            turn = self.request(
                "POST",
                "/v1/turn",
                {"text": f"turn {index}"},
            )
            observed_turn_ids.append(turn["turn_id"])
            self.assertEqual(expected_turn_id, turn["turn_id"])
            active = self.request("GET", "/v1/session")["session"]
            self.assertTrue(active["turn_active"])
            self.assertEqual(turn["turn_id"], active["turn_id"])

            after = self.complete_turn(turn["turn_id"], after=after)
            completed = self.request("GET", "/v1/session")["session"]
            self.assertFalse(completed["turn_active"])
            self.assertEqual("completed", completed["turn_status"])

            if index == 1:
                with self.assertRaises(HTTPError) as raised:
                    self.request("POST", "/v1/interrupt", {})
                self.assertEqual(409, raised.exception.code)
                payload = json.loads(raised.exception.read().decode("utf-8"))
                self.assertEqual(
                    "NO_ACTIVE_TURN",
                    payload["structured_error"]["code"],
                )

        self.assertEqual(expected_turn_ids, observed_turn_ids)
        self.assertEqual(4, len(set(observed_turn_ids)))

    def test_session_close_reaps_owned_child(self) -> None:
        process = self.client.process
        self.session.close()
        self.assertIsNotNone(process.poll())


if __name__ == "__main__":
    unittest.main()
