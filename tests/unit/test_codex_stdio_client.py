from __future__ import annotations

import os
import sys
import threading
import time
import unittest
from pathlib import Path
from typing import Any, Callable


REPOSITORY_ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))
sys.path.insert(0, str(REPOSITORY_ROOT / "services" / "bridge"))

from hia_bridge.codex_stdio import CodexStdioClient  # noqa: E402
from hia_bridge.errors import ProtocolRejected  # noqa: E402
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

    def test_streaming_reply_plan_and_approval(self) -> None:
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
            lambda event: event.get("type") == "server_request"
        )
        self.assertEqual(
            "item/commandExecution/requestApproval",
            approval["method"],
        )
        self.client.respond_to_server_request(
            approval["request_id"],
            {"decision": "accept"},
        )
        self.wait_for(
            lambda event: event.get("method") == "turn/completed"
        )
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


if __name__ == "__main__":
    unittest.main()
