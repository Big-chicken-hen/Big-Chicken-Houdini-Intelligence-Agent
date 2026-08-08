from __future__ import annotations

import os
import sys
import threading
import time
import unittest
from pathlib import Path
from typing import Any, Callable
from unittest import mock


REPOSITORY_ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))
sys.path.insert(0, str(REPOSITORY_ROOT / "services" / "bridge"))

from hia_bridge.codex_stdio import CodexStdioClient, _PendingResponse  # noqa: E402
from hia_bridge.errors import BridgeError, ProtocolRejected  # noqa: E402
from hia_bridge.protocol import ProtocolPolicy  # noqa: E402


class CodexStdioClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.policy = ProtocolPolicy.from_project_root(REPOSITORY_ROOT)

    def setUp(self) -> None:
        self.events: list[dict[str, Any]] = []
        self.condition = threading.Condition()
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
            policy=self.policy,
            event_sink=self._record_event,
            request_timeout=5.0,
        )
        self.client.start()

    def tearDown(self) -> None:
        self.client.close()

    def _record_event(self, event: dict[str, Any]) -> None:
        with self.condition:
            self.events.append(event)
            self.condition.notify_all()

    def wait_for(self, predicate: Callable[[dict[str, Any]], bool]) -> dict[str, Any]:
        deadline = time.monotonic() + 5.0
        with self.condition:
            while True:
                for event in self.events:
                    if predicate(event):
                        return event
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self.fail(f"Expected event was not observed; events={self.events!r}")
                self.condition.wait(remaining)

    def initialize(self) -> None:
        response = self.client.initialize()
        self.assertEqual("fake-codex/0.144.3", response["userAgent"])

    def test_initialize_account_and_unknown_notification(self) -> None:
        self.initialize()
        account = self.client.request("account/read", {"refreshToken": False})
        self.assertEqual("chatgpt", account["account"]["type"])
        warning = self.wait_for(
            lambda event: event.get("code") == "UNKNOWN_NOTIFICATION_IGNORED"
        )
        self.assertEqual("future/unknownNotification", warning["method"])
        stderr = self.wait_for(lambda event: event.get("type") == "codex_stderr")
        self.assertIn("initialized", stderr["line"])

    def test_passive_status_notifications_are_forwarded_without_warning(self) -> None:
        notifications = {
            "account/rateLimits/updated": {"rateLimits": {}},
            "mcpServer/startupStatus/updated": {
                "name": "project-server",
                "status": "ready",
            },
            "remoteControl/status/changed": {
                "installationId": "installation-test",
                "serverName": "project-codex",
                "status": "disabled",
            },
            "skills/changed": {},
        }

        for method, params in notifications.items():
            self.client._handle_message({"method": method, "params": params})

        for method, params in notifications.items():
            observed = self.wait_for(
                lambda event, expected=method: event.get("type")
                == "codex_notification"
                and event.get("method") == expected
            )
            self.assertEqual(params, observed["params"])

        warnings = [
            event
            for event in self.events
            if event.get("type") == "protocol_warning"
            and event.get("method") in notifications
        ]
        self.assertEqual([], warnings)

    def test_stable_model_list_request_is_allowlisted(self) -> None:
        self.initialize()
        first = self.client.request(
            "model/list",
            {"includeHidden": False, "limit": 100},
        )
        self.assertEqual("fake-model-page-2", first["nextCursor"])
        self.assertEqual("fake-default-model", first["data"][0]["model"])
        second = self.client.request(
            "model/list",
            {
                "includeHidden": False,
                "limit": 100,
                "cursor": first["nextCursor"],
            },
        )
        self.assertIsNone(second["nextCursor"])
        self.assertEqual("fake-secondary-model", second["data"][0]["model"])

    def test_stable_thread_history_requests_and_name_notification(self) -> None:
        self.initialize()
        list_params = {
            "cwd": str(REPOSITORY_ROOT),
            "archived": False,
            "limit": 20,
            "sortKey": "recency_at",
            "sortDirection": "desc",
        }

        listed = self.client.request("thread/list", list_params)

        self.assertEqual(list_params, listed["receivedParams"])
        self.assertEqual("thread-fake", listed["data"][0]["id"])
        self.assertEqual(str(REPOSITORY_ROOT), listed["data"][0]["cwd"])

        name = "  Houdini lookdev  "
        renamed = self.client.request(
            "thread/name/set",
            {"threadId": "thread-fake", "name": name},
        )
        self.assertEqual(
            {"threadId": "thread-fake", "name": name},
            renamed["receivedParams"],
        )
        notification = self.wait_for(
            lambda event: event.get("type") == "codex_notification"
            and event.get("method") == "thread/name/updated"
        )
        self.assertEqual(
            {"threadId": "thread-fake", "threadName": name},
            notification["params"],
        )

    def test_streaming_reply_rejects_unexpected_approval_without_input(self) -> None:
        self.initialize()
        thread = self.client.request("thread/start", {})
        self.assertEqual("thread-fake", thread["thread"]["id"])
        turn = self.client.request(
            "turn/start",
            {
                "threadId": "thread-fake",
                "input": [{"type": "text", "text": "hello", "text_elements": []}],
            },
        )
        self.assertEqual("turn-fake", turn["turn"]["id"])
        self.wait_for(
            lambda event: event.get("method") == "turn/plan/updated"
        )
        approval = self.wait_for(
            lambda event: event.get("code")
            == "UNEXPECTED_RUNTIME_APPROVAL_REQUEST"
        )
        self.assertEqual(
            "item/commandExecution/requestApproval",
            approval["method"],
        )
        self.wait_for(
            lambda event: event.get("method") == "turn/completed"
        )
        self.assertFalse(any(event.get("type") == "server_request" for event in self.events))
        deltas = [
            event["params"]["delta"]
            for event in self.events
            if event.get("method") == "item/agentMessage/delta"
        ]
        self.assertEqual("Hello from fake Codex", "".join(deltas))

    def test_resume_read_and_interrupt(self) -> None:
        self.initialize()
        resumed = self.client.request(
            "thread/resume",
            {"threadId": "thread-resumed"},
        )
        self.assertEqual("thread-resumed", resumed["thread"]["id"])
        read = self.client.request(
            "thread/read",
            {"threadId": "thread-resumed", "includeTurns": True},
        )
        self.assertEqual("thread-resumed", read["thread"]["id"])
        self.client.request(
            "turn/start",
            {
                "threadId": "thread-resumed",
                "input": [{"type": "text", "text": "stop", "text_elements": []}],
            },
        )
        self.client.request(
            "turn/interrupt",
            {"threadId": "thread-resumed", "turnId": "turn-fake"},
        )
        completed = self.wait_for(
            lambda event: event.get("method") == "turn/completed"
            and event.get("params", {}).get("turn", {}).get("status") == "interrupted"
        )
        self.assertEqual("interrupted", completed["params"]["turn"]["status"])

    def test_unknown_client_method_is_rejected_before_send(self) -> None:
        with self.assertRaises(ProtocolRejected):
            self.client.request("thread/shellCommand", {})

    def test_close_reaps_child_process(self) -> None:
        process = self.client.process
        self.assertIsNotNone(process)
        self.client.close()
        self.assertIsNotNone(process.poll())

    def test_request_specific_timeout_does_not_change_default_timeout(self) -> None:
        with mock.patch.object(self.client, "_send_json"):
            started = time.monotonic()
            with self.assertRaises(BridgeError) as captured:
                self.client.request_with_timeout(
                    "account/read",
                    {"refreshToken": False},
                    timeout_seconds=0.01,
                )
            elapsed = time.monotonic() - started

        self.assertEqual("CODEX_REQUEST_TIMEOUT", captured.exception.code)
        self.assertLess(elapsed, 1.0)
        self.assertEqual(5.0, self.client._request_timeout)

    def test_late_recovery_responses_are_silent_and_tombstones_are_bounded(
        self,
    ) -> None:
        methods = ("turn/interrupt", "initialize", "thread/resume")
        with mock.patch.object(self.client, "_send_json"):
            for index in range(18):
                request_id = self.client._next_request_id
                with self.assertRaises(BridgeError) as captured:
                    self.client.request_with_timeout(
                        methods[index % len(methods)],
                        {},
                        timeout_seconds=0.001,
                    )
                self.assertEqual("CODEX_REQUEST_TIMEOUT", captured.exception.code)
                if index == 0:
                    first_request_id = request_id
                last_request_id = request_id

        self.assertEqual(16, len(self.client._late_response_tombstones))
        self.client._handle_response({"id": last_request_id, "result": {}})
        self.client._handle_response({"id": first_request_id, "result": {}})
        self.client._handle_response({"id": "never-issued", "result": {}})

        unknown_ids = [
            event.get("request_id")
            for event in self.events
            if event.get("code") == "UNKNOWN_RESPONSE_ID"
        ]
        self.assertEqual([first_request_id, "never-issued"], unknown_ids)

    def test_stale_stdout_reader_does_not_fail_new_process_requests(self) -> None:
        client = CodexStdioClient(
            [sys.executable, "-B", "-c", "pass"],
            cwd=REPOSITORY_ROOT,
            environment=os.environ.copy(),
            policy=self.policy,
            event_sink=self._record_event,
        )
        old_process = mock.Mock()
        new_process = mock.Mock()

        def exhausted_old_stdout():
            client._process = new_process
            if False:
                yield ""

        old_process.stdout = exhausted_old_stdout()
        client._process = old_process
        pending = _PendingResponse(method="initialize")
        client._pending[1] = pending

        client._read_stdout()

        self.assertFalse(pending.event.is_set())
        self.assertFalse(
            any(event.get("type") == "process_exit" for event in self.events)
        )

    def test_environment_overlay_is_rejected_after_process_start(self) -> None:
        with self.assertRaises(BridgeError) as captured:
            self.client.set_environment_overlay(
                {"HIA_BRIDGE_URL": "http://127.0.0.1:54321"}
            )
        self.assertEqual("CODEX_ENVIRONMENT_LOCKED", captured.exception.code)

    def test_pre_start_environment_overlay_is_inherited_and_stderr_is_redacted(
        self,
    ) -> None:
        secret = "bridge_" + "s" * 40
        url = "http://127.0.0.1:54321"
        code = (
            "import os,sys;"
            "sys.stderr.write('url=' + os.environ['HIA_BRIDGE_URL'] + "
            "' token=' + os.environ['HIA_BRIDGE_TOKEN'] + '\\n');"
            "sys.stderr.flush()"
        )
        environment = os.environ.copy()
        client = CodexStdioClient(
            [sys.executable, "-B", "-c", code],
            cwd=REPOSITORY_ROOT,
            environment=environment,
            policy=self.policy,
            event_sink=self._record_event,
            request_timeout=1.0,
        )
        client.set_environment_overlay(
            {
                "HIA_BRIDGE_URL": url,
                "HIA_BRIDGE_TOKEN": secret,
            }
        )
        try:
            client.start()
            stderr = self.wait_for(
                lambda event: event.get("type") == "codex_stderr"
                and event.get("line", "").startswith("url=")
            )
        finally:
            client.close()

        self.assertNotIn(url, stderr["line"])
        self.assertGreaterEqual(stderr["line"].count("[REDACTED]"), 2)
        self.assertNotIn(secret, repr(self.events))

    def test_start_error_redacts_sensitive_environment_values(self) -> None:
        secret = "bridge_" + "z" * 40
        client = CodexStdioClient(
            [sys.executable, "-B", "-c", "pass"],
            cwd=REPOSITORY_ROOT,
            environment={
                "HIA_BRIDGE_TOKEN": secret,
                "HIA_BRIDGE_URL": "http://127.0.0.1:54321",
            },
            policy=self.policy,
        )
        with mock.patch(
            "hia_bridge.codex_stdio.subprocess.Popen",
            side_effect=OSError(
                f"failed near {secret} http://127.0.0.1:54321"
            ),
        ), self.assertRaises(BridgeError) as captured:
            client.start()

        serialized = repr(captured.exception.to_dict())
        self.assertNotIn(secret, serialized)
        self.assertNotIn("http://127.0.0.1:54321", serialized)
        self.assertIn("[REDACTED]", serialized)

    def test_response_results_are_redacted_on_entry(
        self,
    ) -> None:
        secret = "bridge_" + "q" * 40
        url = "http://127.0.0.1:54321"
        client = CodexStdioClient(
            [sys.executable, "-B", "-c", "pass"],
            cwd=REPOSITORY_ROOT,
            environment={
                "HIA_BRIDGE_TOKEN": secret,
                "HIA_BRIDGE_URL": url,
            },
            policy=self.policy,
            event_sink=self._record_event,
        )
        pending = _PendingResponse(method="account/read")
        client._pending[77] = pending
        client._handle_response(
            {
                "id": 77,
                "result": {
                    "echo": f"{url} {secret}",
                    secret: {url: "nested"},
                },
            }
        )
        response_encoded = repr(pending.result)
        self.assertNotIn(secret, response_encoded)
        self.assertNotIn(url, response_encoded)
        self.assertIn("[REDACTED]", response_encoded)

    def test_all_runtime_approval_requests_are_immediately_refused(self) -> None:
        expected = {
            "item/commandExecution/requestApproval": {"decision": "decline"},
            "item/fileChange/requestApproval": {"decision": "decline"},
            "item/permissions/requestApproval": {"permissions": {}, "scope": "turn"},
        }
        for index, (method, response) in enumerate(expected.items(), start=1):
            with self.subTest(method=method), mock.patch.object(
                self.client, "_send_json"
            ) as send:
                request_id = f"approval-{index}"
                self.client._handle_server_request(
                    {
                        "id": request_id,
                        "method": method,
                        "params": {"authorization": "Bearer runtime-secret-token"},
                    }
                )
                send.assert_called_once_with({"id": request_id, "result": response})
                diagnostic = self.events[-1]
                self.assertEqual("protocol_warning", diagnostic["type"])
                self.assertEqual(
                    "UNEXPECTED_RUNTIME_APPROVAL_REQUEST", diagnostic["code"]
                )
                self.assertEqual(method, diagnostic["method"])
                self.assertNotIn("params", diagnostic)
        self.assertFalse(any(event.get("type") == "server_request" for event in self.events))
        self.assertFalse(hasattr(self.client, "pending_server_request"))


if __name__ == "__main__":
    unittest.main()
