from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any
from unittest import mock


REPOSITORY_ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))
sys.path.insert(0, str(REPOSITORY_ROOT / "services" / "bridge"))

from hia_bridge.errors import BridgeError, CodexRPCError  # noqa: E402
from hia_bridge.events import EventBuffer  # noqa: E402
from hia_bridge.session import (  # noqa: E402
    MODEL_LIST_MAX_ENTRIES,
    MODEL_LIST_MAX_PAGES,
    MODEL_LIST_PAGE_SIZE,
    MAX_LOCAL_IMAGES,
    STOP_INTERRUPT_GRACE_SECONDS,
    STOP_RECOVERY_TOTAL_SECONDS,
    THREAD_PREVIEW_MAX_LENGTH,
    BridgeSession,
    _requires_system_drive_approval,
    _thread_cwd_filters,
)


class _ClientStub:
    def __init__(self) -> None:
        self._event_sink = None
        self._request_lock = threading.Lock()
        self.turn_request_count = 0

    def set_event_sink(self, event_sink: Any) -> None:
        self._event_sink = event_sink

    @property
    def is_running(self) -> bool:
        return True

    @property
    def process_id(self) -> int:
        return 4242

    def emit_notification(self, method: str, params: dict[str, Any]) -> None:
        assert self._event_sink is not None
        self._event_sink(
            {
                "type": "codex_notification",
                "method": method,
                "params": params,
            }
        )

    def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method == "thread/start":
            return {"thread": {"id": "thread-test"}}
        if method == "thread/resume":
            return {"thread": {"id": params["threadId"], "turns": []}}
        if method == "thread/read":
            return {"thread": {"id": params["threadId"], "turns": []}}
        if method == "turn/interrupt":
            return {}
        raise AssertionError(f"Unexpected request: {method}")


class _ScriptedModelClient(_ClientStub):
    def __init__(self, responses: list[Any]) -> None:
        super().__init__()
        self.responses = list(responses)
        self.model_requests: list[dict[str, Any]] = []

    def request(self, method: str, params: dict[str, Any]) -> Any:
        if method != "model/list":
            return super().request(method, params)
        self.model_requests.append(dict(params))
        if not self.responses:
            raise AssertionError("Unexpected extra model/list page request")
        return self.responses.pop(0)


class _ThreadHistoryClient(_ClientStub):
    def __init__(self, list_response: dict[str, Any]) -> None:
        super().__init__()
        self.list_response = list_response
        self.requests: list[tuple[str, dict[str, Any]]] = []

    def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self.requests.append((method, dict(params)))
        if method == "thread/list":
            return self.list_response
        if method == "thread/name/set":
            return {}
        return super().request(method, params)


class _ThreadContentClient(_ClientStub):
    def __init__(self, response: dict[str, Any]) -> None:
        super().__init__()
        self.response = response
        self.requests: list[tuple[str, dict[str, Any]]] = []

    def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self.requests.append((method, dict(params)))
        if method in {"thread/resume", "thread/read"}:
            return self.response
        if method == "turn/start":
            return {"turn": {"id": "turn-after-resume", "status": "inProgress"}}
        return super().request(method, params)


class _RecordingClient(_ClientStub):
    def __init__(self) -> None:
        super().__init__()
        self.requests: list[tuple[str, dict[str, Any]]] = []

    def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self.requests.append((method, dict(params)))
        if method == "turn/start":
            return {"turn": {"id": "turn-recorded", "status": "inProgress"}}
        if method == "turn/steer":
            return {"turnId": params["expectedTurnId"]}
        return super().request(method, params)


class _AdvancingClock:
    def __init__(self) -> None:
        self.value = 0.0

    def monotonic(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += float(seconds)


class _StopRecoveryClient(_RecordingClient):
    def __init__(
        self,
        *,
        complete_during_interrupt: bool,
        complete_during_resume: bool = False,
        timeout_during_resume: bool = False,
        resume_gate: threading.Event | None = None,
        clock: Any = None,
    ) -> None:
        super().__init__()
        self.complete_during_interrupt = complete_during_interrupt
        self.complete_during_resume = complete_during_resume
        self.timeout_during_resume = timeout_during_resume
        self.resume_gate = resume_gate
        self.clock = clock
        self.restart_count = 0
        self.restart_deadlines: list[float | None] = []
        self.initialize_timeouts: list[float] = []
        self.timed_requests: list[tuple[str, dict[str, Any], float]] = []
        self.resume_entered = threading.Event()
        self.resume_finished = threading.Event()

    def request_with_timeout(
        self,
        method: str,
        params: dict[str, Any],
        *,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        self.timed_requests.append((method, dict(params), timeout_seconds))
        if method == "turn/interrupt":
            if self.complete_during_interrupt:
                self.emit_notification(
                    "turn/completed",
                    {
                        "threadId": params["threadId"],
                        "turn": {
                            "id": params["turnId"],
                            "status": "interrupted",
                        },
                    },
                )
                return {}
            if self.clock is not None:
                self.clock.advance(timeout_seconds)
            raise BridgeError(
                "CODEX_REQUEST_TIMEOUT",
                "interrupt timed out",
                http_status=504,
            )
        if method == "thread/resume":
            self.resume_entered.set()
            try:
                if self.resume_gate is not None and not self.resume_gate.wait(2.0):
                    raise AssertionError("resume gate was not released")
                if self.timeout_during_resume:
                    if self.clock is not None:
                        self.clock.advance(timeout_seconds)
                    raise BridgeError(
                        "CODEX_REQUEST_TIMEOUT",
                        "resume timed out",
                        http_status=504,
                    )
                if self.complete_during_resume:
                    self.emit_notification(
                        "turn/completed",
                        {
                            "threadId": params["threadId"],
                            "turn": {
                                "id": "turn-recorded",
                                "status": "completed",
                            },
                        },
                    )
                return {"thread": {"id": params["threadId"], "turns": []}}
            finally:
                self.resume_finished.set()
        raise AssertionError(f"Unexpected timed request: {method}")

    def restart(
        self,
        grace_seconds: float = 1.0,
        *,
        deadline: float | None = None,
    ) -> None:
        self.restart_count += 1
        self.restart_deadlines.append(deadline)

    def initialize_with_timeout(self, timeout_seconds: float) -> dict[str, Any]:
        self.initialize_timeouts.append(timeout_seconds)
        return {"userAgent": "fake-codex/0.144.3"}


class _GoalClient(_RecordingClient):
    def __init__(self) -> None:
        super().__init__()
        self.goal: dict[str, Any] | None = None

    @staticmethod
    def make_goal(thread_id: str, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "threadId": thread_id,
            "objective": params.get("objective", "Build the current scene"),
            "status": params.get("status", "active"),
            "tokenBudget": params.get("tokenBudget"),
            "tokensUsed": 120,
            "timeUsedSeconds": 8,
            "createdAt": 100,
            "updatedAt": 101,
        }

    def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method == "thread/goal/get":
            self.requests.append((method, dict(params)))
            return {"goal": self.goal}
        if method == "thread/goal/set":
            self.requests.append((method, dict(params)))
            self.goal = self.make_goal(params["threadId"], params)
            return {"goal": self.goal}
        if method == "thread/goal/clear":
            self.requests.append((method, dict(params)))
            cleared = self.goal is not None
            self.goal = None
            return {"cleared": cleared}
        return super().request(method, params)


class _ApprovalClient(_RecordingClient):
    def __init__(self, *, fail_response: bool = False) -> None:
        super().__init__()
        self.pending: dict[Any, dict[str, Any]] = {}
        self.approval_responses: list[tuple[Any, dict[str, Any]]] = []
        self.fail_response = fail_response

    def emit_approval(
        self,
        request_id: str,
        method: str,
        params: dict[str, Any],
    ) -> None:
        request = {"method": method, "params": dict(params)}
        self.pending[request_id] = request
        assert self._event_sink is not None
        self._event_sink(
            {
                "type": "server_request",
                "request_id": request_id,
                **request,
            }
        )

    def pending_server_request(self, request_id: Any) -> dict[str, Any] | None:
        request = self.pending.get(request_id)
        return dict(request) if request is not None else None

    def respond_to_server_request(
        self,
        request_id: Any,
        response: dict[str, Any],
    ) -> str:
        if self.fail_response:
            raise BridgeError("TEST_RESPONSE_FAILED", "test response failed")
        request = self.pending.pop(request_id)
        self.approval_responses.append((request_id, dict(response)))
        return request["method"]


class _SteerClient(_RecordingClient):
    def __init__(self, steer_result: Any) -> None:
        super().__init__()
        self.steer_result = steer_result

    def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method != "turn/steer":
            return super().request(method, params)
        self.requests.append((method, dict(params)))
        if isinstance(self.steer_result, Exception):
            raise self.steer_result
        return self.steer_result


class _CompletingSteerClient(_RecordingClient):
    def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method != "turn/steer":
            return super().request(method, params)
        self.requests.append((method, dict(params)))
        self.emit_notification(
            "turn/completed",
            {
                "threadId": params["threadId"],
                "turn": {
                    "id": params["expectedTurnId"],
                    "status": "completed",
                },
            },
        )
        return {"turnId": params["expectedTurnId"]}


class _LateOldTurnStartClient(_RecordingClient):
    def __init__(self) -> None:
        super().__init__()
        self.start_count = 0

    def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method != "turn/start":
            return super().request(method, params)
        self.requests.append((method, dict(params)))
        self.start_count += 1
        if self.start_count == 1:
            return {"turn": {"id": "turn-old", "status": "inProgress"}}
        self.emit_notification(
            "turn/started",
            {
                "threadId": params["threadId"],
                "turn": {"id": "turn-old", "status": "inProgress"},
            },
        )
        self.emit_notification(
            "turn/completed",
            {
                "threadId": params["threadId"],
                "turn": {"id": "turn-old", "status": "completed"},
            },
        )
        return {"turn": {"id": "turn-new", "status": "inProgress"}}


def _model_entry(
    model: str,
    *,
    hidden: bool = False,
) -> dict[str, Any]:
    return {
        "id": model,
        "model": model,
        "displayName": f"Display {model}",
        "description": f"Description {model}",
        "hidden": hidden,
        "isDefault": model == "model-a",
        "inputModalities": ["text", "image"],
        "serviceTiers": [
            {
                "id": "priority",
                "name": "Fast",
                "description": "Faster responses",
            }
        ],
        "defaultServiceTier": "priority" if model == "model-a" else None,
        "supportedReasoningEfforts": [
            {"reasoningEffort": "low", "description": "Faster"},
            {"reasoningEffort": "high", "description": "Deeper"},
        ],
        "defaultReasoningEffort": "low",
    }


class BridgeSessionApprovalRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.system_drive = os.environ.get("SystemDrive") or "C:"

    def make_session(
        self,
        *,
        fail_response: bool = False,
        project_root: Path = REPOSITORY_ROOT,
    ) -> tuple[BridgeSession, _ApprovalClient, EventBuffer]:
        client = _ApprovalClient(fail_response=fail_response)
        events = EventBuffer()
        return BridgeSession(project_root, client, events), client, events

    @staticmethod
    def command_params(command: str, *, cwd: str | None = None) -> dict[str, Any]:
        return {
            "command": command,
            "cwd": cwd or str(REPOSITORY_ROOT),
            "itemId": "item-approval",
            "startedAtMs": 1,
            "threadId": "thread-approval",
            "turnId": "turn-approval",
        }

    def test_only_explicit_system_drive_changes_require_manual_approval(self) -> None:
        project_file = str(REPOSITORY_ROOT / ".runtime" / "tmp" / "approval.txt")
        system_file = self.system_drive + "\\Users\\Public\\approval.txt"
        auto_commands = (
            "[DateTimeOffset]::FromUnixTimeSeconds(1).ToString('u')",
            (
                "$r = Invoke-WebRequest -UseBasicParsing "
                "'https://www.shadertoy.com/view/ltffzl'; "
                "$r.Content.Substring(0, [Math]::Min(5000, $r.Content.Length))"
            ),
            (
                "$r = Invoke-WebRequest -UseBasicParsing "
                "'https://www.sidefx.com/docs/houdini/nodes/cop/wrangle.html'; "
                "$r.Content | Select-String -Pattern 'VEX|kernel|pixel'"
            ),
            f"Get-Content -LiteralPath '{system_file}'",
            f"Get-Content -LiteralPath '{self.system_drive}\\README.md'",
            f"Set-Content -LiteralPath '{project_file}' -Value test",
            (
                f"Set-Content -LiteralPath '{project_file}' "
                "-Value '$env:USERPROFILE'"
            ),
            f"Get-Content '{system_file}' > '{project_file}'",
            "curl.exe 'https://www.shadertoy.com/view/ltffzl'",
            (
                f"Copy-Item -LiteralPath '{system_file}' -Destination "
                f"'{project_file}'"
            ),
            (
                "Copy-Item -LiteralPath '$env:USERPROFILE\\input.png' "
                f"-Destination '{project_file}'"
            ),
        )
        for command in auto_commands:
            with self.subTest(command=command):
                self.assertFalse(
                    _requires_system_drive_approval(
                        "item/commandExecution/requestApproval",
                        self.command_params(command),
                        project_root=REPOSITORY_ROOT,
                        system_drive=self.system_drive,
                    )
                )

        for command in (
            f"Set-Content -LiteralPath '{system_file}' -Value test",
            f"Remove-Item -LiteralPath '{system_file}'",
            f"rm '{system_file}'",
            f"rd '{self.system_drive}\\Users\\Public\\HIA-Test'",
            f"python -c \"import os; os.remove(r'{system_file}')\"",
            f"Set-Item -LiteralPath '{system_file}' -Value test",
            (
                "Invoke-RestMethod 'https://example.com/data' -OutFile "
                f"'{system_file}'"
            ),
            f"curl.exe 'https://example.com/data' -o '{system_file}'",
            f"[IO.File]::AppendAllText('{system_file}', 'test')",
            f"shutil.copyfile(r'{project_file}', r'{system_file}')",
            f"'test' | Tee-Object -FilePath '{system_file}'",
            f"open(r'{system_file}', 'wb')",
            f"'test' > '{system_file}'",
            "Set-Content -LiteralPath '$env:SystemDrive\\HIA-Test.txt' -Value test",
            "Set-Content -LiteralPath '${env:SystemDrive}\\HIA-Test.txt' -Value test",
            "Set-Content -LiteralPath '%SystemDrive%\\HIA-Test.txt' -Value test",
            "Set-Content -LiteralPath '~\\HIA-Test.txt' -Value test",
            (
                f"Copy-Item -LiteralPath '{project_file}' -Destination "
                "'$env:SystemDrive\\HIA-Test.txt'"
            ),
            (
                f"Move-Item -LiteralPath '{system_file}' -Destination "
                f"'{project_file}'"
            ),
        ):
            with self.subTest(command=command):
                self.assertTrue(
                    _requires_system_drive_approval(
                        "item/commandExecution/requestApproval",
                        self.command_params(command),
                        project_root=REPOSITORY_ROOT,
                        system_drive=self.system_drive,
                    )
                )

    def test_command_actions_take_precedence_over_broken_serialized_command(self) -> None:
        params = self.command_params(
            "broken fallback; Set-Content C:\\Users\\Public\\wrong.txt"
        )
        params["commandActions"] = [
            {
                "command": (
                    "Invoke-WebRequest -UseBasicParsing "
                    "'https://www.shadertoy.com/view/ltffzl'"
                )
            }
        ]

        self.assertFalse(
            _requires_system_drive_approval(
                "item/commandExecution/requestApproval",
                params,
                project_root=REPOSITORY_ROOT,
                system_drive=self.system_drive,
            )
        )

    def test_project_root_remains_auto_allowed_when_it_is_on_system_drive(self) -> None:
        project_root = Path(self.system_drive + "\\HIA-Portable")
        params = self.command_params(
            "Set-Content -LiteralPath "
            f"'{project_root}\\.runtime\\tmp\\approval.txt' -Value test",
            cwd=str(project_root),
        )
        self.assertFalse(
            _requires_system_drive_approval(
                "item/commandExecution/requestApproval",
                params,
                project_root=project_root,
                system_drive=self.system_drive,
            )
        )

    def test_file_and_permission_approvals_only_prompt_for_system_write(self) -> None:
        system_root = self.system_drive + "\\ProgramData\\HIA"
        project_root = str(REPOSITORY_ROOT / ".runtime")
        self.assertTrue(
            _requires_system_drive_approval(
                "item/fileChange/requestApproval",
                {"grantRoot": system_root},
                project_root=REPOSITORY_ROOT,
                system_drive=self.system_drive,
            )
        )
        for grant_root in (None, project_root):
            self.assertFalse(
                _requires_system_drive_approval(
                    "item/fileChange/requestApproval",
                    {"grantRoot": grant_root},
                    project_root=REPOSITORY_ROOT,
                    system_drive=self.system_drive,
                )
            )

        def permission(access: str, path: str) -> dict[str, Any]:
            return {
                "cwd": str(REPOSITORY_ROOT),
                "permissions": {
                    "fileSystem": {
                        "entries": [
                            {
                                "access": access,
                                "path": {"type": "path", "path": path},
                            }
                        ]
                    }
                },
            }

        self.assertTrue(
            _requires_system_drive_approval(
                "item/permissions/requestApproval",
                permission("write", system_root),
                project_root=REPOSITORY_ROOT,
                system_drive=self.system_drive,
            )
        )
        for params in (
            permission("read", system_root),
            permission("write", project_root),
            {
                "cwd": str(REPOSITORY_ROOT),
                "permissions": {"network": {"enabled": True}},
            },
        ):
            self.assertFalse(
                _requires_system_drive_approval(
                    "item/permissions/requestApproval",
                    params,
                    project_root=REPOSITORY_ROOT,
                    system_drive=self.system_drive,
                )
            )

    def test_auto_allow_uses_existing_accept_and_never_publishes_card(self) -> None:
        _session, client, events = self.make_session()
        for request_id, url in (
            ("approval-auto-shadertoy", "https://www.shadertoy.com/view/ltffzl"),
            (
                "approval-auto-sidefx",
                "https://www.sidefx.com/docs/houdini/nodes/cop/wrangle.html",
            ),
        ):
            client.emit_approval(
                request_id,
                "item/commandExecution/requestApproval",
                self.command_params(
                    f"Invoke-WebRequest -UseBasicParsing '{url}'"
                ),
            )

        self.assertEqual(
            [
                ("approval-auto-shadertoy", {"decision": "accept"}),
                ("approval-auto-sidefx", {"decision": "accept"}),
            ],
            client.approval_responses,
        )
        self.assertEqual({}, client.pending)
        published = events.poll(0, timeout=0)["events"]
        self.assertFalse(
            any(event.get("type") == "server_request" for event in published)
        )
        self.assertTrue(
            any(
                event.get("type") == "approval_resolved"
                and event.get("decision") == "allow"
                for event in published
            )
        )

    def test_persistent_rule_is_only_sent_when_the_protocol_offers_it(self) -> None:
        session, client, _events = self.make_session()
        system_file = self.system_drive + "\\Users\\Public\\approval.txt"
        params = self.command_params(
            f"Set-Content -LiteralPath '{system_file}' -Value test"
        )
        params["proposedExecpolicyAmendment"] = [
            "Set-Content",
            "-LiteralPath",
        ]
        client.emit_approval(
            "approval-rule",
            "item/commandExecution/requestApproval",
            params,
        )

        session.resolve_approval("approval-rule", "allow_rule")

        self.assertEqual(
            [
                (
                    "approval-rule",
                    {
                        "decision": {
                            "acceptWithExecpolicyAmendment": {
                                "execpolicy_amendment": [
                                    "Set-Content",
                                    "-LiteralPath",
                                ]
                            }
                        }
                    },
                )
            ],
            client.approval_responses,
        )

        params_without_rule = self.command_params(
            f"Remove-Item -LiteralPath '{system_file}'"
        )
        params_without_rule["proposedExecpolicyAmendment"] = ["Remove-Item"]
        params_without_rule["availableDecisions"] = ["accept", "decline"]
        client.emit_approval(
            "approval-no-rule",
            "item/commandExecution/requestApproval",
            params_without_rule,
        )
        with self.assertRaises(BridgeError) as raised:
            session.resolve_approval("approval-no-rule", "allow_rule")
        self.assertEqual("INVALID_APPROVAL_DECISION", raised.exception.code)
        self.assertIn("approval-no-rule", client.pending)

    def test_system_write_and_auto_response_failure_fall_back_to_panel(self) -> None:
        system_file = self.system_drive + "\\Users\\Public\\approval.txt"
        _session, client, events = self.make_session()
        client.emit_approval(
            "approval-manual-system",
            "item/commandExecution/requestApproval",
            self.command_params(
                f"Set-Content -LiteralPath '{system_file}' -Value test"
            ),
        )
        self.assertEqual([], client.approval_responses)
        self.assertIn("approval-manual-system", client.pending)
        self.assertTrue(
            any(
                event.get("type") == "server_request"
                for event in events.poll(0, timeout=0)["events"]
            )
        )

        _session, failed_client, failed_events = self.make_session(
            fail_response=True
        )
        failed_client.emit_approval(
            "approval-auto-failed",
            "item/commandExecution/requestApproval",
            self.command_params("[DateTimeOffset]::FromUnixTimeSeconds(1)"),
        )
        self.assertIn("approval-auto-failed", failed_client.pending)
        self.assertTrue(
            any(
                event.get("type") == "server_request"
                and event.get("request_id") == "approval-auto-failed"
                for event in failed_events.poll(0, timeout=0)["events"]
            )
        )


class _BlockingTurnClient(_ClientStub):
    def __init__(self) -> None:
        super().__init__()
        self.turn_entered = threading.Event()
        self.release_turn = threading.Event()

    def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method != "turn/start":
            return super().request(method, params)
        with self._request_lock:
            self.turn_request_count += 1
            turn_number = self.turn_request_count
        self.turn_entered.set()
        if not self.release_turn.wait(5.0):
            raise AssertionError("Test did not release the blocked turn/start")
        return {"turn": {"id": f"turn-{turn_number}", "status": "inProgress"}}


class _GenerationClient(_ClientStub):
    def __init__(self) -> None:
        super().__init__()
        self.first_completed = threading.Event()
        self.release_first_ack = threading.Event()
        self.second_entered = threading.Event()
        self.release_second_ack = threading.Event()

    def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method != "turn/start":
            return super().request(method, params)
        with self._request_lock:
            self.turn_request_count += 1
            turn_number = self.turn_request_count
        turn_id = f"turn-{turn_number}"
        if turn_number == 1:
            self.emit_notification(
                "turn/started",
                {
                    "threadId": params["threadId"],
                    "turn": {"id": turn_id, "status": "inProgress"},
                },
            )
            self.emit_notification(
                "turn/completed",
                {
                    "threadId": params["threadId"],
                    "turn": {"id": turn_id, "status": "completed"},
                },
            )
            self.first_completed.set()
            if not self.release_first_ack.wait(5.0):
                raise AssertionError("Test did not release the first acknowledgement")
        elif turn_number == 2:
            self.second_entered.set()
            if not self.release_second_ack.wait(5.0):
                raise AssertionError("Test did not release the second acknowledgement")
        else:
            raise AssertionError("Unexpected third turn/start")
        return {"turn": {"id": turn_id, "status": "inProgress"}}


class _FailingTurnClient(_ClientStub):
    def __init__(self, failure: str) -> None:
        super().__init__()
        self.failure = failure

    def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method != "turn/start":
            return super().request(method, params)
        self.turn_request_count += 1
        if self.failure == "rpc":
            raise CodexRPCError("turn/start", {"message": "explicit rejection"})
        if self.failure == "rpc_after_started":
            self.emit_notification(
                "turn/started",
                {
                    "threadId": params["threadId"],
                    "turn": {"id": "turn-observed", "status": "inProgress"},
                },
            )
            raise CodexRPCError("turn/start", {"message": "late explicit rejection"})
        if self.failure == "transport":
            raise BridgeError(
                "CODEX_REQUEST_TIMEOUT",
                "Codex request timed out: turn/start",
                http_status=504,
            )
        if self.failure == "invalid_ack":
            return {}
        raise AssertionError(f"Unexpected failure mode: {self.failure}")


class BridgeSessionThreadHistoryTests(unittest.TestCase):
    def test_list_threads_sanitizes_preview_but_keeps_identity_fields_strict(
        self,
    ) -> None:
        preview = "first line\nsecond\tline\x00" + "x" * 20_000
        client = _ThreadHistoryClient(
            {
                "data": [
                    {
                        "id": "thread-current",
                        "cwd": str(REPOSITORY_ROOT),
                        "name": None,
                        "preview": preview,
                        "updatedAt": 20,
                    }
                ]
            }
        )
        session = BridgeSession(REPOSITORY_ROOT, client, EventBuffer())

        result = session.list_threads()

        sanitized = result["threads"][0]["preview"]
        self.assertLessEqual(len(sanitized), THREAD_PREVIEW_MAX_LENGTH)
        self.assertTrue(sanitized.startswith("first line second line"))
        self.assertFalse(any(ord(character) < 32 for character in sanitized))

        for field, value in (
            ("id", "bad\nthread"),
            ("cwd", str(REPOSITORY_ROOT) + "\nforeign"),
        ):
            with self.subTest(field=field):
                entry = {
                    "id": "thread-current",
                    "cwd": str(REPOSITORY_ROOT),
                    "name": None,
                    "preview": "valid",
                    "updatedAt": 20,
                }
                entry[field] = value
                strict_session = BridgeSession(
                    REPOSITORY_ROOT,
                    _ThreadHistoryClient({"data": [entry]}),
                    EventBuffer(),
                )
                with self.assertRaises(BridgeError) as raised:
                    strict_session.list_threads()
                self.assertEqual("INVALID_THREAD_LIST_RESPONSE", raised.exception.code)
                self.assertEqual(field, raised.exception.details["field"])

    def test_resume_projects_complete_chat_once_and_continues_same_thread(self) -> None:
        large_tool_output = "x" * (4 * 1024 * 1024 + 1)
        client = _ThreadContentClient(
            {
                "thread": {
                    "id": "thread-current",
                    "turns": [
                        {
                            "items": [
                                {
                                    "type": "userMessage",
                                    "content": [
                                        {"type": "text", "text": "第一条完整问题"},
                                        {
                                            "type": "localImage",
                                            "path": r"E:\refs\cabin.png",
                                        },
                                        {"type": "skill", "name": "ignored"},
                                    ],
                                },
                                {
                                    "type": "commandExecution",
                                    "aggregatedOutput": large_tool_output,
                                },
                                {"type": "agentMessage", "text": "第一条完整回答"},
                            ]
                        },
                        {
                            "items": [
                                {
                                    "type": "userMessage",
                                    "content": [
                                        {"type": "text", "text": "继续修改木屋"}
                                    ],
                                },
                                {"type": "agentMessage", "text": "第二条完整回答"},
                            ]
                        },
                    ],
                }
            }
        )
        session = BridgeSession(REPOSITORY_ROOT, client, EventBuffer())

        result = session.resume_thread("thread-current")

        self.assertEqual(["thread/resume"], [method for method, _ in client.requests])
        self.assertNotIn("resume", result)
        self.assertEqual("thread-current", result["thread_id"])
        self.assertEqual("thread-current", result["read"]["thread"]["id"])
        self.assertEqual(
            [
                "userMessage",
                "agentMessage",
                "userMessage",
                "agentMessage",
            ],
            [
                item["type"]
                for turn in result["read"]["thread"]["turns"]
                for item in turn["items"]
            ],
        )
        self.assertEqual(
            [
                {"type": "text", "text": "第一条完整问题"},
                {"type": "localImage", "path": r"E:\refs\cabin.png"},
            ],
            result["read"]["thread"]["turns"][0]["items"][0]["content"],
        )
        self.assertLess(
            len(json.dumps(result, ensure_ascii=False).encode("utf-8")),
            4 * 1024 * 1024,
        )

        session.start_turn("继续同一会话")
        self.assertEqual("thread-current", client.requests[-1][1]["threadId"])

    def test_read_projects_all_chat_messages_in_original_order(self) -> None:
        messages = [
            {"type": "agentMessage", "text": f"message-{index}"}
            for index in range(172)
        ]
        client = _ThreadContentClient(
            {
                "thread": {
                    "id": "thread-current",
                    "turns": [{"items": messages}],
                }
            }
        )
        session = BridgeSession(REPOSITORY_ROOT, client, EventBuffer())

        result = session.read_thread("thread-current")

        projected = result["result"]["thread"]["turns"][0]["items"]
        self.assertEqual(172, len(projected))
        self.assertEqual("message-0", projected[0]["text"])
        self.assertEqual("message-171", projected[-1]["text"])

    def test_list_threads_filters_response_after_dual_cwd_query(self) -> None:
        client = _ThreadHistoryClient(
            {
                "data": [
                    {
                        "id": "thread-foreign",
                        "cwd": str(REPOSITORY_ROOT.parent / "other-project"),
                        "name": "Other project",
                        "preview": "must not leak",
                        "updatedAt": 1,
                    },
                    {
                        "id": "thread-current",
                        "cwd": str(REPOSITORY_ROOT),
                        "name": "Current project",
                        "preview": "latest Houdini work",
                        "updatedAt": 20,
                        "recencyAt": 21,
                        "path": "must-not-be-forwarded",
                    },
                ]
            }
        )
        session = BridgeSession(REPOSITORY_ROOT, client, EventBuffer())

        result = session.list_threads()

        self.assertEqual(
            [
                (
                    "thread/list",
                    {
                        "cwd": [
                            str(REPOSITORY_ROOT),
                            "\\\\?\\" + str(REPOSITORY_ROOT),
                        ],
                        "archived": False,
                        "limit": 20,
                        "modelProviders": [],
                        "useStateDbOnly": True,
                        "sortKey": "recency_at",
                        "sortDirection": "desc",
                    },
                )
            ],
            client.requests,
        )
        self.assertEqual(
            {
                "threads": [
                    {
                        "thread_id": "thread-current",
                        "name": "Current project",
                        "preview": "latest Houdini work",
                        "updated_at": 20,
                        "recency_at": 21,
                    }
                ]
            },
            result,
        )
    @unittest.skipUnless(os.name == "nt", "Windows extended paths only")
    def test_list_threads_accepts_windows_extended_cwd(self) -> None:
        client = _ThreadHistoryClient(
            {
                "data": [
                    {
                        "id": "thread-current",
                        "cwd": "\\\\?\\" + str(REPOSITORY_ROOT),
                        "name": "Recovered thread",
                        "preview": "survived a Houdini crash",
                        "updatedAt": 30,
                    }
                ]
            }
        )
        session = BridgeSession(REPOSITORY_ROOT, client, EventBuffer())

        result = session.list_threads()

        self.assertEqual("thread-current", result["threads"][0]["thread_id"])

    def test_thread_cwd_filters_cover_drive_and_unc_extended_forms(self) -> None:
        self.assertEqual(
            [r"E:\portable-project", r"\\?\E:\portable-project"],
            _thread_cwd_filters(r"E:\portable-project"),
        )
        self.assertEqual(
            [r"\\server\share\portable-project", r"\\?\UNC\server\share\portable-project"],
            _thread_cwd_filters(r"\\?\UNC\server\share\portable-project"),
        )

    def test_rename_thread_forwards_the_original_name(self) -> None:
        client = _ThreadHistoryClient({"data": []})
        session = BridgeSession(REPOSITORY_ROOT, client, EventBuffer())
        name = "  Houdini lookdev  "

        result = session.rename_thread("thread-current", name)

        self.assertEqual(
            ("thread/name/set", {"threadId": "thread-current", "name": name}),
            client.requests[-1],
        )
        self.assertEqual(
            {
                "thread_id": "thread-current",
                "name": name,
                "result": {},
            },
            result,
        )


class BridgeSessionGoalTests(unittest.TestCase):
    def make_session(self) -> tuple[BridgeSession, _GoalClient]:
        client = _GoalClient()
        session = BridgeSession(REPOSITORY_ROOT, client, EventBuffer())
        session.start_thread()
        client.requests.clear()
        return session, client

    def test_goal_round_trip_uses_selected_native_thread(self) -> None:
        session, client = self.make_session()

        empty = session.get_goal("thread-test")
        saved = session.set_goal(
            expected_thread_id="thread-test",
            objective="完成木屋材质与灯光",
            status="active",
            token_budget=50_000,
        )
        fetched = session.get_goal("thread-test")
        cleared = session.clear_goal("thread-test")

        self.assertIsNone(empty["goal"])
        self.assertEqual("thread-test", saved["thread_id"])
        self.assertEqual("完成木屋材质与灯光", saved["goal"]["objective"])
        self.assertEqual(50_000, fetched["goal"]["tokenBudget"])
        self.assertTrue(cleared["cleared"])
        self.assertEqual(
            [
                ("thread/goal/get", {"threadId": "thread-test"}),
                (
                    "thread/goal/set",
                    {
                        "threadId": "thread-test",
                        "objective": "完成木屋材质与灯光",
                        "status": "active",
                        "tokenBudget": 50_000,
                    },
                ),
                ("thread/goal/get", {"threadId": "thread-test"}),
                ("thread/goal/clear", {"threadId": "thread-test"}),
            ],
            client.requests,
        )

    def test_goal_rejects_missing_thread_and_mismatched_response(self) -> None:
        client = _GoalClient()
        session = BridgeSession(REPOSITORY_ROOT, client, EventBuffer())
        with self.assertRaises(BridgeError) as missing:
            session.get_goal("thread-test")
        self.assertEqual("MISSING_IDENTIFIER", missing.exception.code)

        session.start_thread()
        client.requests.clear()
        with self.assertRaises(BridgeError) as changed:
            session.get_goal("thread-other")
        self.assertEqual("THREAD_SELECTION_CHANGED", changed.exception.code)
        self.assertEqual([], client.requests)

        client.goal = client.make_goal("different-thread", {})
        with self.assertRaises(BridgeError) as mismatched:
            session.get_goal("thread-test")
        self.assertEqual("INVALID_GOAL_RESPONSE", mismatched.exception.code)
        self.assertEqual("threadId", mismatched.exception.details["field"])

    def test_focus_mode_requires_active_goal_and_persists_per_thread(self) -> None:
        with tempfile.TemporaryDirectory(
            dir=REPOSITORY_ROOT / ".runtime" / "tmp"
        ) as directory:
            focus_path = Path(directory) / "focus-mode.json"
            client = _GoalClient()
            session = BridgeSession(
                REPOSITORY_ROOT,
                client,
                EventBuffer(),
                focus_state_path=focus_path,
            )
            session.start_thread()

            with self.assertRaises(BridgeError) as missing_goal:
                session.set_focus_mode("thread-test", True)
            self.assertEqual("ACTIVE_GOAL_REQUIRED", missing_goal.exception.code)
            self.assertFalse(session.snapshot()["focus_mode"])

            session.set_goal(
                expected_thread_id="thread-test",
                objective="完成木屋",
                status="active",
                token_budget=None,
            )
            enabled = session.set_focus_mode("thread-test", True)
            self.assertTrue(enabled["focus_mode"])
            self.assertTrue(session.snapshot()["focus_mode"])
            focused_goal = dict(client.goal or {})

            persisted = json.loads(focus_path.read_text(encoding="utf-8"))
            self.assertEqual("thread-test", persisted["active_thread_id"])
            self.assertEqual(["thread-test"], persisted["enabled_thread_ids"])
            self.assertRegex(
                persisted["goal_bindings"]["thread-test"], r"^[0-9a-f]{64}$"
            )
            self.assertNotIn(
                str(focused_goal["objective"]),
                focus_path.read_text(encoding="utf-8"),
            )

            resumed_client = _GoalClient()
            resumed_client.goal = focused_goal
            resumed = BridgeSession(
                REPOSITORY_ROOT,
                resumed_client,
                EventBuffer(),
                focus_state_path=focus_path,
            )
            result = resumed.resume_thread("thread-test")
            self.assertTrue(result["focus_mode"])
            self.assertTrue(resumed.snapshot()["focus_mode"])

            cleared = resumed.clear_goal("thread-test")
            self.assertTrue(cleared["cleared"])
            self.assertFalse(cleared["focus_mode"])
            self.assertEqual(
                [],
                json.loads(focus_path.read_text(encoding="utf-8"))[
                    "enabled_thread_ids"
                ],
            )

    def test_focus_binding_closes_on_goal_content_change_not_progress(self) -> None:
        with tempfile.TemporaryDirectory(
            dir=REPOSITORY_ROOT / ".runtime" / "tmp"
        ) as directory:
            focus_path = Path(directory) / "focus-mode.json"
            client = _GoalClient()
            session = BridgeSession(
                REPOSITORY_ROOT,
                client,
                EventBuffer(),
                focus_state_path=focus_path,
            )
            session.start_thread()
            session.set_goal(
                expected_thread_id="thread-test",
                objective="Build cabin A",
                status="active",
                token_budget=25_000,
            )
            session.set_focus_mode("thread-test", True)
            original_binding = session.get_goal("thread-test")["goal_binding"]

            client.emit_notification(
                "thread/goal/updated",
                {
                    "threadId": "thread-test",
                    "goal": {
                        "status": "active",
                        "tokensUsed": 500,
                        "timeUsedSeconds": 12,
                    },
                },
            )
            self.assertTrue(session.snapshot()["focus_mode"])
            self.assertEqual(
                original_binding,
                session.get_goal("thread-test")["goal_binding"],
            )

            changed = session.set_goal(
                expected_thread_id="thread-test",
                objective="Build cabin B",
                status="active",
                token_budget=25_000,
            )
            self.assertFalse(changed["focus_mode"])
            self.assertNotIn(
                "thread-test",
                json.loads(focus_path.read_text(encoding="utf-8"))["goal_bindings"],
            )

            session.set_focus_mode("thread-test", True)
            client.emit_notification(
                "thread/goal/updated",
                {
                    "threadId": "thread-test",
                    "goal": {
                        "threadId": "thread-test",
                        "objective": "Build cabin B",
                        "status": "active",
                        "tokenBudget": 30_000,
                        "tokensUsed": 0,
                    },
                },
            )
            self.assertFalse(session.snapshot()["focus_mode"])

            session.set_focus_mode("thread-test", True)
            client.emit_notification(
                "thread/goal/updated",
                {
                    "threadId": "thread-test",
                    "goal": {
                        "threadId": "thread-test",
                        "objective": "Build cabin B",
                        "status": "blocked",
                        "tokenBudget": 25_000,
                    },
                },
            )
            self.assertFalse(session.snapshot()["focus_mode"])
            self.assertNotIn(
                "thread-test",
                json.loads(focus_path.read_text(encoding="utf-8"))[
                    "enabled_thread_ids"
                ],
            )

class BridgeSessionModelCatalogTests(unittest.TestCase):
    def make_session(self, responses: list[Any]) -> tuple[BridgeSession, _ScriptedModelClient]:
        client = _ScriptedModelClient(responses)
        return BridgeSession(REPOSITORY_ROOT, client, EventBuffer()), client

    def test_model_list_paginates_filters_and_sanitizes(self) -> None:
        session, client = self.make_session(
            [
                {
                    "data": [
                        _model_entry("model-a"),
                        _model_entry("model-hidden", hidden=True),
                    ],
                    "nextCursor": "cursor-2",
                },
                {"data": [_model_entry("model-b")], "nextCursor": None},
            ]
        )
        result = session.list_models()
        self.assertEqual(
            ["model-a", "model-b"],
            [model["model"] for model in result["models"]],
        )
        self.assertEqual(
            [
                {"includeHidden": False, "limit": MODEL_LIST_PAGE_SIZE},
                {
                    "includeHidden": False,
                    "limit": MODEL_LIST_PAGE_SIZE,
                    "cursor": "cursor-2",
                },
            ],
            client.model_requests,
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
        for model in result["models"]:
            self.assertEqual(expected_keys, set(model))
            self.assertEqual(
                {"reasoningEffort", "description"},
                set(model["supportedReasoningEfforts"][0]),
            )
            self.assertEqual(
                {"id", "name", "description"},
                set(model["serviceTiers"][0]),
            )
        self.assertEqual("priority", result["models"][0]["defaultServiceTier"])
        self.assertIsNone(result["models"][1]["defaultServiceTier"])

    def test_model_list_rejects_cursor_cycle_and_malformed_response(self) -> None:
        invalid_service_tiers = _model_entry("invalid-service-tiers")
        invalid_service_tiers["serviceTiers"] = "priority"
        duplicate_service_tiers = _model_entry("duplicate-service-tiers")
        duplicate_service_tiers["serviceTiers"] = [
            duplicate_service_tiers["serviceTiers"][0],
            dict(duplicate_service_tiers["serviceTiers"][0]),
        ]
        invalid_default_service_tier = _model_entry("invalid-default-service-tier")
        invalid_default_service_tier["defaultServiceTier"] = "unadvertised"
        scenarios: dict[str, list[Any]] = {
            "non_object_root": [[]],
            "non_array_data": [{"data": {}, "nextCursor": None}],
            "invalid_model": [{"data": [{}], "nextCursor": None}],
            "invalid_cursor": [{"data": [], "nextCursor": 3}],
            "invalid_service_tiers": [
                {"data": [invalid_service_tiers], "nextCursor": None}
            ],
            "duplicate_service_tiers": [
                {"data": [duplicate_service_tiers], "nextCursor": None}
            ],
            "invalid_default_service_tier": [
                {"data": [invalid_default_service_tier], "nextCursor": None}
            ],
            "cursor_cycle": [
                {"data": [], "nextCursor": "same"},
                {"data": [], "nextCursor": "same"},
            ],
            "duplicate_model": [
                {
                    "data": [_model_entry("duplicate"), _model_entry("duplicate")],
                    "nextCursor": None,
                }
            ],
        }
        for name, responses in scenarios.items():
            with self.subTest(name=name):
                session, _ = self.make_session(responses)
                with self.assertRaises(BridgeError) as raised:
                    session.list_models()
                self.assertEqual("INVALID_MODEL_LIST_RESPONSE", raised.exception.code)
                self.assertEqual(502, raised.exception.http_status)

    def test_model_list_enforces_page_and_total_entry_limits(self) -> None:
        session, _ = self.make_session(
            [{"data": [{}] * (MODEL_LIST_MAX_ENTRIES + 1), "nextCursor": None}]
        )
        with self.assertRaises(BridgeError) as raised:
            session.list_models()
        self.assertEqual("MODEL_CATALOG_LIMIT_EXCEEDED", raised.exception.code)
        self.assertEqual(
            {"max_entries": MODEL_LIST_MAX_ENTRIES},
            raised.exception.details,
        )

        responses = [
            {"data": [], "nextCursor": f"cursor-{index}"}
            for index in range(1, MODEL_LIST_MAX_PAGES + 1)
        ]
        session, client = self.make_session(responses)
        with self.assertRaises(BridgeError) as raised:
            session.list_models()
        self.assertEqual("MODEL_CATALOG_LIMIT_EXCEEDED", raised.exception.code)
        self.assertEqual(
            {"max_pages": MODEL_LIST_MAX_PAGES},
            raised.exception.details,
        )
        self.assertEqual(MODEL_LIST_MAX_PAGES, len(client.model_requests))


class BridgeSessionImageInputTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary_project = tempfile.TemporaryDirectory(
            dir=REPOSITORY_ROOT / ".runtime" / "tmp"
        )
        self.project_root = Path(self._temporary_project.name)
        self.thread_directory = (
            self.project_root / ".runtime" / "attachments" / "thread-test"
        )
        self.thread_directory.mkdir(parents=True)

    def tearDown(self) -> None:
        self._temporary_project.cleanup()

    def make_session(self) -> tuple[BridgeSession, _RecordingClient]:
        client = _RecordingClient()
        session = BridgeSession(self.project_root, client, EventBuffer())
        session.start_thread()
        client.requests.clear()
        return session, client

    def make_image(self, name: str) -> Path:
        path = self.thread_directory / name
        path.write_bytes(b"test image payload")
        return path.resolve()

    def test_text_and_images_share_turn_start_input_in_order(self) -> None:
        first = self.make_image("first.PNG")
        second = self.make_image("second.webp")
        session, client = self.make_session()

        session.start_turn(
            "参考图片建立模型",
            local_image_paths=[str(first), str(second)],
        )

        method, params = client.requests[0]
        self.assertEqual("turn/start", method)
        self.assertEqual(
            [
                {
                    "type": "text",
                    "text": "参考图片建立模型",
                    "text_elements": [],
                },
                {"type": "localImage", "path": str(first)},
                {"type": "localImage", "path": str(second)},
            ],
            params["input"],
        )

    def test_images_without_text_are_valid_turn_input(self) -> None:
        image = self.make_image("only.jpeg")
        session, client = self.make_session()

        session.start_turn("", local_image_paths=[str(image)])

        self.assertEqual(
            [{"type": "localImage", "path": str(image)}],
            client.requests[0][1]["input"],
        )

    def test_images_are_bounded_to_current_thread_directory_and_supported_types(self) -> None:
        valid = self.make_image("valid.jpg")
        outside = self.project_root / ".runtime" / "attachments" / "other-thread"
        outside.mkdir(parents=True)
        outside_image = outside / "outside.png"
        outside_image.write_bytes(b"outside")
        unsupported = self.make_image("unsupported.gif")

        invalid_cases = (
            ("not-an-array", "INVALID_LOCAL_IMAGES"),
            ([str(outside_image.resolve())], "INVALID_LOCAL_IMAGE_PATH"),
            ([str(unsupported)], "INVALID_LOCAL_IMAGE"),
            ([str(self.thread_directory / "missing.png")], "INVALID_LOCAL_IMAGE_PATH"),
            ([str(valid)] * (MAX_LOCAL_IMAGES + 1), "TOO_MANY_LOCAL_IMAGES"),
        )
        for local_image_paths, expected_code in invalid_cases:
            with self.subTest(expected_code=expected_code):
                session, client = self.make_session()
                with self.assertRaises(BridgeError) as raised:
                    session.start_turn(
                        "参考图片",
                        local_image_paths=local_image_paths,  # type: ignore[arg-type]
                    )
                self.assertEqual(expected_code, raised.exception.code)
                self.assertEqual([], client.requests)


class BridgeSessionSteerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary_project = tempfile.TemporaryDirectory(
            dir=REPOSITORY_ROOT / ".runtime" / "tmp"
        )
        self.project_root = Path(self._temporary_project.name)
        self.thread_directory = (
            self.project_root / ".runtime" / "attachments" / "thread-test"
        )
        self.thread_directory.mkdir(parents=True)

    def tearDown(self) -> None:
        self._temporary_project.cleanup()

    def make_active_session(
        self,
        client: _RecordingClient | None = None,
    ) -> tuple[BridgeSession, _RecordingClient]:
        active_client = client or _RecordingClient()
        session = BridgeSession(self.project_root, active_client, EventBuffer())
        session.start_thread()
        session.start_turn("initial request")
        active_client.requests.clear()
        return session, active_client

    def test_steer_uses_expected_turn_and_does_not_change_lifecycle(self) -> None:
        image = self.thread_directory / "follow-up.png"
        image.write_bytes(b"image")
        session, client = self.make_active_session()
        before = (
            session._turn_generation,
            session._turn_id,
            session._turn_status,
            session._turn_active,
            session._turn_created,
        )

        result = session.steer_turn(
            "追加要求",
            local_image_paths=[str(image)],
        )

        self.assertEqual("turn-recorded", result["turn_id"])
        self.assertEqual(
            (
                session._turn_generation,
                session._turn_id,
                session._turn_status,
                session._turn_active,
                session._turn_created,
            ),
            before,
        )
        self.assertEqual(1, len(client.requests))
        method, params = client.requests[0]
        self.assertEqual("turn/steer", method)
        self.assertEqual("thread-test", params["threadId"])
        self.assertEqual("turn-recorded", params["expectedTurnId"])
        self.assertEqual(
            [
                {"type": "text", "text": "追加要求", "text_elements": []},
                {"type": "localImage", "path": str(image.resolve())},
            ],
            params["input"],
        )

    def test_steer_rejects_mismatched_ack_without_mutating_turn(self) -> None:
        client = _SteerClient({"turnId": "different-turn"})
        session, _ = self.make_active_session(client)
        before = session.snapshot()

        with self.assertRaises(BridgeError) as raised:
            session.steer_turn("追加要求")

        self.assertEqual("INVALID_CODEX_RESPONSE", raised.exception.code)
        self.assertEqual(before, session.snapshot())

    def test_same_turn_ack_is_accepted_if_completion_arrives_first(self) -> None:
        session, _ = self.make_active_session(_CompletingSteerClient())

        result = session.steer_turn("追加要求")

        self.assertEqual("turn-recorded", result["turn_id"])
        snapshot = session.snapshot()
        self.assertFalse(snapshot["turn_active"])
        self.assertEqual("completed", snapshot["turn_status"])

    def test_matching_ack_remains_accepted_after_local_lifecycle_changes(self) -> None:
        client = _SteerClient({"turnId": "turn-recorded"})
        session, _ = self.make_active_session(client)

        def changed_lifecycle(_method: str, params: dict[str, Any]) -> dict[str, Any]:
            with session._lock:
                session._turn_generation += 1
                session._thread_id = "thread-other"
                session._turn_id = "turn-other"
            return {"turnId": params["expectedTurnId"]}

        with mock.patch.object(client, "request", side_effect=changed_lifecycle):
            result = session.steer_turn("追加要求")

        self.assertEqual("thread-test", result["thread_id"])
        self.assertEqual("turn-recorded", result["turn_id"])

    def test_canonical_no_active_steer_is_a_terminal_conflict(self) -> None:
        messages = (
            "no active turn to steer",
            "  NO   ACTIVE TURN TO STEER.  ",
        )
        for message in messages:
            with self.subTest(message=message):
                client = _SteerClient(
                    CodexRPCError(
                        "turn/steer",
                        {"code": -32600, "message": message},
                    )
                )
                session, _ = self.make_active_session(client)

                with self.assertRaises(BridgeError) as raised:
                    session.steer_turn("追加要求")

                self.assertEqual("NO_ACTIVE_TURN", raised.exception.code)
                self.assertEqual(409, raised.exception.http_status)
                self.assertFalse(raised.exception.details["turn_active"])
                self.assertEqual(
                    "thread-test", raised.exception.details["thread_id"]
                )
                self.assertEqual(
                    "turn-recorded", raised.exception.details["turn_id"]
                )
                self.assertFalse(session.snapshot()["turn_active"])

    def test_no_active_steer_matcher_rejects_nearby_rpc_errors(self) -> None:
        errors = (
            {"code": -32001, "message": "no active turn to steer"},
            {"code": -32600, "message": "no active turn available to steer"},
            {"code": -32600, "message": "turn/steer timed out"},
        )
        for error in errors:
            with self.subTest(error=error):
                client = _SteerClient(CodexRPCError("turn/steer", error))
                session, _ = self.make_active_session(client)
                before = session.snapshot()

                with self.assertRaises(CodexRPCError):
                    session.steer_turn("追加要求")

                self.assertEqual(before, session.snapshot())

        timeout = BridgeError(
            "CODEX_REQUEST_TIMEOUT",
            "turn/steer timed out",
            http_status=504,
        )
        session, _ = self.make_active_session(_SteerClient(timeout))
        before = session.snapshot()
        with self.assertRaises(BridgeError) as raised:
            session.steer_turn("追加要求")
        self.assertEqual("CODEX_REQUEST_TIMEOUT", raised.exception.code)
        self.assertEqual(before, session.snapshot())

    def test_no_active_rpc_does_not_end_a_newer_turn_generation(self) -> None:
        client = _SteerClient({"turnId": "turn-recorded"})
        session, _ = self.make_active_session(client)

        def newer_turn_then_error(
            _method: str,
            _params: dict[str, Any],
        ) -> dict[str, Any]:
            with session._lock:
                session._turn_generation += 1
                session._turn_id = "turn-newer"
                session._turn_status = "inProgress"
                session._turn_active = True
            raise CodexRPCError(
                "turn/steer",
                {"code": -32600, "message": "no active turn to steer"},
            )

        with mock.patch.object(client, "request", side_effect=newer_turn_then_error):
            with self.assertRaises(BridgeError) as raised:
                session.steer_turn("追加要求")

        self.assertEqual("NO_ACTIVE_TURN", raised.exception.code)
        self.assertIsNone(raised.exception.details["turn_active"])
        self.assertEqual("changed", raised.exception.details["turn_status"])
        snapshot = session.snapshot()
        self.assertTrue(snapshot["turn_active"])
        self.assertEqual("turn-newer", snapshot["turn_id"])

    def test_stale_active_turn_rpc_updates_the_authoritative_snapshot(self) -> None:
        client = _SteerClient(
            CodexRPCError(
                "turn/steer",
                {
                    "code": -32600,
                    "message": (
                        "expected active turn id `turn-recorded` "
                        "but found `turn-authoritative`"
                    ),
                },
            )
        )
        session, _ = self.make_active_session(client)
        generation = session._turn_generation

        with self.assertRaises(BridgeError) as raised:
            session.steer_turn("追加要求")

        self.assertEqual("STALE_ACTIVE_TURN", raised.exception.code)
        self.assertEqual(409, raised.exception.http_status)
        self.assertEqual("turn-recorded", raised.exception.details["expected_turn_id"])
        self.assertEqual(
            "turn-authoritative",
            raised.exception.details["active_turn_id"],
        )
        self.assertTrue(raised.exception.details["turn_active"])
        snapshot = session.snapshot()
        self.assertEqual("turn-authoritative", snapshot["turn_id"])
        self.assertTrue(snapshot["turn_active"])
        self.assertEqual(generation + 1, session._turn_generation)
        self.assertEqual("turn-recorded", session._start_source_turn_id)

    def test_stale_active_turn_rpc_cas_never_overwrites_a_newer_turn(self) -> None:
        client = _SteerClient({"turnId": "turn-recorded"})
        session, _ = self.make_active_session(client)

        def newer_turn_then_stale_error(
            _method: str,
            _params: dict[str, Any],
        ) -> dict[str, Any]:
            with session._lock:
                session._turn_generation += 1
                session._turn_id = "turn-newer"
                session._turn_status = "inProgress"
                session._turn_active = True
            raise CodexRPCError(
                "turn/steer",
                {
                    "code": -32600,
                    "message": (
                        "expected active turn id `turn-recorded` "
                        "but found `turn-reported`"
                    ),
                },
            )

        with mock.patch.object(client, "request", side_effect=newer_turn_then_stale_error):
            with self.assertRaises(BridgeError) as raised:
                session.steer_turn("追加要求")

        self.assertEqual("STALE_ACTIVE_TURN", raised.exception.code)
        self.assertIsNone(raised.exception.details["turn_active"])
        self.assertEqual("changed", raised.exception.details["turn_status"])
        self.assertEqual("turn-newer", session.snapshot()["turn_id"])

    def test_stale_active_turn_matcher_rejects_other_codes_and_nearby_text(self) -> None:
        errors = (
            {
                "code": -32001,
                "message": (
                    "expected active turn id `turn-recorded` "
                    "but found `turn-other`"
                ),
            },
            {
                "code": -32600,
                "message": "expected turn id `turn-recorded` but found `turn-other`",
            },
        )
        for error in errors:
            with self.subTest(error=error):
                session, _ = self.make_active_session(
                    _SteerClient(CodexRPCError("turn/steer", error))
                )
                before = session.snapshot()
                with self.assertRaises(CodexRPCError):
                    session.steer_turn("追加要求")
                self.assertEqual(before, session.snapshot())

    def test_review_and_compact_rejections_are_short_structured_conflicts(self) -> None:
        for turn_kind in ("review", "compact"):
            with self.subTest(turn_kind=turn_kind):
                client = _SteerClient(
                    CodexRPCError(
                        "turn/steer",
                        {
                            "message": "active turn cannot be steered",
                            "data": {
                                "codexErrorInfo": {
                                    "activeTurnNotSteerable": {
                                        "turnKind": turn_kind
                                    }
                                }
                            },
                        },
                    )
                )
                session, _ = self.make_active_session(client)
                before = session.snapshot()

                with self.assertRaises(BridgeError) as raised:
                    session.steer_turn("追加要求")

                self.assertEqual("TURN_NOT_STEERABLE", raised.exception.code)
                self.assertEqual(409, raised.exception.http_status)
                self.assertEqual(turn_kind, raised.exception.details["turn_kind"])
                self.assertEqual(before, session.snapshot())

    def test_steer_requires_an_active_turn(self) -> None:
        client = _RecordingClient()
        session = BridgeSession(self.project_root, client, EventBuffer())
        session.start_thread()
        client.requests.clear()

        with self.assertRaises(BridgeError) as raised:
            session.steer_turn("追加要求")

        self.assertEqual("NO_ACTIVE_TURN", raised.exception.code)
        self.assertEqual([], client.requests)

    def test_local_terminal_steer_rejection_never_calls_app_server(self) -> None:
        session, client = self.make_active_session()
        client.emit_notification(
            "turn/completed",
            {
                "threadId": "thread-test",
                "turn": {"id": "turn-recorded", "status": "completed"},
            },
        )
        client.requests.clear()

        with self.assertRaises(BridgeError) as raised:
            session.steer_turn("作为下一轮发送")

        self.assertEqual("NO_ACTIVE_TURN", raised.exception.code)
        self.assertEqual(False, raised.exception.details["turn_active"])
        self.assertEqual("turn-recorded", raised.exception.details["turn_id"])
        self.assertEqual([], client.requests)


class BridgeSessionTurnStateTests(unittest.TestCase):
    def make_session(self, client: _ClientStub) -> BridgeSession:
        session = BridgeSession(REPOSITORY_ROOT, client, EventBuffer())
        session.start_thread()
        return session

    def test_new_start_ignores_late_started_and_completed_from_terminal_turn(self) -> None:
        client = _LateOldTurnStartClient()
        session = self.make_session(client)
        first_turn_id = session.start_turn("first")["turn_id"]
        client.emit_notification(
            "turn/completed",
            {
                "threadId": "thread-test",
                "turn": {"id": first_turn_id, "status": "completed"},
            },
        )

        result = session.start_turn("second")

        self.assertEqual("turn-new", result["turn_id"])
        snapshot = session.snapshot()
        self.assertTrue(snapshot["turn_active"])
        self.assertEqual("turn-new", snapshot["turn_id"])
        self.assertEqual("inProgress", snapshot["turn_status"])
        self.assertIsNone(session._start_source_turn_id)

    def test_child_thread_started_never_replaces_the_active_main_thread(self) -> None:
        client = _RecordingClient()
        session = self.make_session(client)
        turn_id = session.start_turn("main task")["turn_id"]

        client.emit_notification(
            "thread/started",
            {
                "thread": {
                    "id": "thread-child",
                    "parentThreadId": "thread-test",
                }
            },
        )

        active = session.snapshot()
        self.assertEqual("thread-test", active["thread_id"])
        self.assertEqual(turn_id, active["turn_id"])
        self.assertTrue(active["turn_active"])
        self.assertTrue(
            any(
                event.get("method") == "thread/started"
                for event in session._events.poll(0, timeout=0)["events"]
            )
        )

        client.emit_notification(
            "turn/completed",
            {
                "threadId": "thread-test",
                "turn": {"id": turn_id, "status": "completed"},
            },
        )

        completed = session.snapshot()
        self.assertEqual("thread-test", completed["thread_id"])
        self.assertEqual("completed", completed["turn_status"])
        self.assertFalse(completed["turn_active"])

    def test_last_tool_tracks_only_mcp_tool_calls(self) -> None:
        client = _RecordingClient()
        session = self.make_session(client)
        turn_id = session.start_turn("build")["turn_id"]

        for item_type in ("reasoning", "agentMessage", "commandExecution"):
            client.emit_notification(
                "item/started",
                {
                    "threadId": "thread-test",
                    "turnId": turn_id,
                    "item": {"type": item_type, "status": "inProgress"},
                },
            )
        self.assertIsNone(session.snapshot()["last_tool_name"])

        client.emit_notification(
            "item/started",
            {
                "threadId": "thread-test",
                "turnId": turn_id,
                "item": {
                    "type": "mcpToolCall",
                    "tool": "hia_execute_hom",
                    "status": "inProgress",
                },
            },
        )
        client.emit_notification(
            "item/completed",
            {
                "threadId": "thread-test",
                "turnId": turn_id,
                "item": {"type": "agentMessage", "status": "completed"},
            },
        )
        snapshot = session.snapshot()
        self.assertEqual("hia_execute_hom", snapshot["last_tool_name"])
        self.assertEqual("inProgress", snapshot["last_tool_status"])

    def test_interrupt_completion_within_grace_does_not_restart_codex(self) -> None:
        client = _StopRecoveryClient(complete_during_interrupt=True)
        events = EventBuffer()
        session = BridgeSession(REPOSITORY_ROOT, client, events)
        session.start_thread()
        turn_id = session.start_turn("stop quickly")["turn_id"]

        result = session.interrupt_turn()

        self.assertFalse(result["restarted_app_server"])
        self.assertEqual(0, client.restart_count)
        self.assertEqual("thread-test", result["thread_id"])
        self.assertEqual(turn_id, result["turn_id"])
        self.assertFalse(result["session"]["turn_active"])
        self.assertEqual("interrupted", result["session"]["turn_status"])
        self.assertEqual(STOP_INTERRUPT_GRACE_SECONDS, client.timed_requests[0][2])

    def test_interrupt_timeout_restarts_and_resumes_exact_thread_without_replay(self) -> None:
        client = _StopRecoveryClient(
            complete_during_interrupt=False,
        )
        events = EventBuffer()
        session = BridgeSession(REPOSITORY_ROOT, client, events)
        session.start_thread()
        turn_id = session.start_turn("stop and recover")["turn_id"]
        client.emit_notification(
            "item/started",
            {
                "threadId": "thread-test",
                "turnId": turn_id,
                "item": {
                    "type": "mcpToolCall",
                    "tool": "hia_execute_hom",
                    "status": "inProgress",
                },
            },
        )

        with mock.patch("hia_bridge.session.STOP_INTERRUPT_GRACE_SECONDS", 0.01):
            result = session.interrupt_turn()

        self.assertTrue(result["recovery_pending"])
        self.assertFalse(result["restarted_app_server"])
        self.assertTrue(result["houdini_may_still_be_finishing"])
        self.assertEqual("stopRecovering", result["session"]["turn_status"])
        self.assertFalse(result["session"]["turn_active"])
        self.assertTrue(client.resume_finished.wait(2.0))
        worker = session._stop_recovery_thread
        if worker is not None:
            worker.join(2.0)
        self.assertEqual(1, client.restart_count)
        self.assertEqual(1, len(client.restart_deadlines))
        self.assertIsNotNone(client.restart_deadlines[0])
        self.assertEqual(1, len(client.initialize_timeouts))
        timed_methods = [method for method, _params, _timeout in client.timed_requests]
        self.assertEqual(["turn/interrupt", "thread/resume"], timed_methods)
        resume_params = client.timed_requests[1][1]
        self.assertEqual("thread-test", resume_params["threadId"])
        self.assertEqual(
            1,
            sum(method == "turn/start" for method, _params in client.requests),
        )
        snapshot = session.snapshot()
        self.assertEqual("thread-test", snapshot["thread_id"])
        self.assertIsNone(snapshot["turn_id"])
        self.assertTrue(snapshot["connected"])
        self.assertFalse(snapshot["turn_active"])
        self.assertEqual("interrupted", snapshot["turn_status"])
        session_states = [
            event["session"]
            for event in events.poll(0, timeout=0)["events"]
            if event.get("type") == "session_state"
        ]
        self.assertEqual("stopRequested", session_states[0]["turn_status"])
        self.assertIn(
            "stopRecovering",
            [state["turn_status"] for state in session_states],
        )
        self.assertEqual("interrupted", session_states[-1]["turn_status"])

    def test_stop_background_recovery_failure_is_bounded_and_releases_the_turn(self) -> None:
        client = _StopRecoveryClient(
            complete_during_interrupt=False,
            timeout_during_resume=True,
        )
        events = EventBuffer()
        session = BridgeSession(REPOSITORY_ROOT, client, events)
        session.start_thread()
        turn_id = session.start_turn("resume must stay bounded")["turn_id"]

        with mock.patch("hia_bridge.session.STOP_INTERRUPT_GRACE_SECONDS", 0.01):
            result = session.interrupt_turn()

        self.assertTrue(result["recovery_pending"])
        self.assertTrue(client.resume_finished.wait(2.0))
        worker = session._stop_recovery_thread
        if worker is not None:
            worker.join(2.0)
        self.assertEqual(50.0, STOP_RECOVERY_TOTAL_SECONDS)
        self.assertEqual(1.0, STOP_INTERRUPT_GRACE_SECONDS)
        self.assertEqual(1, client.restart_count)
        resume_timeout = next(
            timeout
            for method, _params, timeout in client.timed_requests
            if method == "thread/resume"
        )
        self.assertLessEqual(resume_timeout, STOP_RECOVERY_TOTAL_SECONDS)
        snapshot = session.snapshot()
        self.assertFalse(snapshot["connected"])
        self.assertFalse(snapshot["turn_active"])
        self.assertEqual("stopRecoveryFailed", snapshot["turn_status"])
        self.assertIsNone(snapshot["turn_id"])
        self.assertIsNone(session._stop_recovery_thread)
        session_states = [
            event["session"]
            for event in events.poll(0, timeout=0)["events"]
            if event.get("type") == "session_state"
        ]
        self.assertFalse(session_states[-1]["connected"])
        self.assertFalse(session_states[-1]["turn_active"])

    def test_old_completion_during_restart_cannot_revive_the_stopped_turn(self) -> None:
        client = _StopRecoveryClient(
            complete_during_interrupt=False,
            complete_during_resume=True,
        )
        session = self.make_session(client)
        turn_id = session.start_turn("complete while restarting")["turn_id"]

        with mock.patch("hia_bridge.session.STOP_INTERRUPT_GRACE_SECONDS", 0.01):
            result = session.interrupt_turn()

        self.assertTrue(result["recovery_pending"])
        self.assertTrue(client.resume_finished.wait(2.0))
        worker = session._stop_recovery_thread
        if worker is not None:
            worker.join(2.0)
        snapshot = session.snapshot()
        self.assertIsNone(snapshot["turn_id"])
        self.assertFalse(snapshot["turn_active"])
        self.assertEqual("interrupted", snapshot["turn_status"])

    def test_slow_stop_recovery_has_one_worker_and_one_exact_resume(self) -> None:
        resume_gate = threading.Event()
        client = _StopRecoveryClient(
            complete_during_interrupt=False,
            resume_gate=resume_gate,
        )
        session = self.make_session(client)
        session.start_turn("recover once")

        try:
            with mock.patch("hia_bridge.session.STOP_INTERRUPT_GRACE_SECONDS", 0.01):
                result = session.interrupt_turn()
            self.assertTrue(result["recovery_pending"])
            self.assertTrue(client.resume_entered.wait(2.0))
            recovering = session.snapshot()
            self.assertFalse(recovering["connected"])
            self.assertFalse(recovering["turn_active"])
            self.assertEqual("stopRecovering", recovering["turn_status"])
            with self.assertRaises(BridgeError) as raised:
                session.interrupt_turn()
            self.assertEqual("NO_ACTIVE_TURN", raised.exception.code)
            self.assertEqual(1, client.restart_count)
        finally:
            resume_gate.set()
        self.assertTrue(client.resume_finished.wait(2.0))
        worker = session._stop_recovery_thread
        if worker is not None:
            worker.join(2.0)
        self.assertEqual(1, client.restart_count)
        self.assertEqual(
            ["thread-test"],
            [
                params["threadId"]
                for method, params, _timeout in client.timed_requests
                if method == "thread/resume"
            ],
        )
        self.assertEqual(
            1,
            sum(method == "turn/start" for method, _params in client.requests),
        )

    def test_turn_start_claim_is_atomic_and_completion_must_match(self) -> None:
        client = _BlockingTurnClient()
        session = self.make_session(client)
        outcome: dict[str, Any] = {}

        def start_first_turn() -> None:
            try:
                outcome["result"] = session.start_turn("first")
            except Exception as exc:  # pragma: no cover - reported below
                outcome["error"] = exc

        worker = threading.Thread(target=start_first_turn)
        worker.start()
        self.assertTrue(client.turn_entered.wait(2.0))

        starting = session.snapshot()
        self.assertTrue(starting["turn_active"])
        self.assertEqual("starting", starting["turn_status"])
        self.assertIsNone(starting["turn_id"])

        with self.assertRaises(BridgeError) as raised:
            session.start_turn("second")
        self.assertEqual("TURN_ALREADY_ACTIVE", raised.exception.code)
        self.assertEqual(409, raised.exception.http_status)
        self.assertEqual(
            {
                "turn_created": False,
                "turn_active": True,
                "thread_id": "thread-test",
                "turn_id": None,
                "turn_status": "starting",
            },
            raised.exception.details,
        )
        self.assertEqual(1, client.turn_request_count)

        with self.assertRaises(BridgeError) as raised:
            session.start_thread()
        self.assertEqual("TURN_ALREADY_ACTIVE", raised.exception.code)
        with self.assertRaises(BridgeError) as raised:
            session.resume_thread("thread-other")
        self.assertEqual("TURN_ALREADY_ACTIVE", raised.exception.code)

        with self.assertRaises(BridgeError) as raised:
            session.interrupt_turn()
        self.assertEqual("NO_ACTIVE_TURN", raised.exception.code)
        self.assertEqual(409, raised.exception.http_status)

        client.release_turn.set()
        worker.join(2.0)
        self.assertFalse(worker.is_alive())
        self.assertNotIn("error", outcome)

        active = session.snapshot()
        self.assertTrue(active["turn_active"])
        self.assertEqual("turn-1", active["turn_id"])
        self.assertEqual("inProgress", active["turn_status"])

        client.emit_notification(
            "turn/completed",
            {
                "threadId": "thread-other",
                "turn": {"id": "turn-1", "status": "completed"},
            },
        )
        client.emit_notification(
            "turn/completed",
            {
                "threadId": "thread-test",
                "turn": {"id": "turn-other", "status": "completed"},
            },
        )
        self.assertTrue(session.snapshot()["turn_active"])

        client.emit_notification(
            "turn/completed",
            {
                "threadId": "thread-test",
                "turn": {"id": "turn-1", "status": "completed"},
            },
        )
        completed = session.snapshot()
        self.assertFalse(completed["turn_active"])
        self.assertEqual("completed", completed["turn_status"])

        with self.assertRaises(BridgeError) as raised:
            session.interrupt_turn()
        self.assertEqual("NO_ACTIVE_TURN", raised.exception.code)
        self.assertEqual(409, raised.exception.http_status)

    def test_late_ack_cannot_regress_a_newer_turn_generation(self) -> None:
        client = _GenerationClient()
        session = self.make_session(client)
        outcomes: list[dict[str, Any]] = [{}, {}]

        def start_turn(index: int, text: str) -> None:
            try:
                outcomes[index]["result"] = session.start_turn(text)
            except Exception as exc:  # pragma: no cover - reported below
                outcomes[index]["error"] = exc

        first = threading.Thread(target=start_turn, args=(0, "first"))
        first.start()
        self.assertTrue(client.first_completed.wait(2.0))
        self.assertFalse(session.snapshot()["turn_active"])
        self.assertEqual("completed", session.snapshot()["turn_status"])

        second = threading.Thread(target=start_turn, args=(1, "second"))
        second.start()
        self.assertTrue(client.second_entered.wait(2.0))
        self.assertEqual("starting", session.snapshot()["turn_status"])

        client.release_first_ack.set()
        first.join(2.0)
        self.assertFalse(first.is_alive())
        after_late_ack = session.snapshot()
        self.assertTrue(after_late_ack["turn_active"])
        self.assertEqual("starting", after_late_ack["turn_status"])
        self.assertIsNone(after_late_ack["turn_id"])

        client.release_second_ack.set()
        second.join(2.0)
        self.assertFalse(second.is_alive())
        self.assertNotIn("error", outcomes[0])
        self.assertNotIn("error", outcomes[1])
        current = session.snapshot()
        self.assertTrue(current["turn_active"])
        self.assertEqual("turn-2", current["turn_id"])
        self.assertEqual("inProgress", current["turn_status"])

    def test_only_explicit_rpc_rejection_releases_an_uncreated_turn(self) -> None:
        rpc_client = _FailingTurnClient("rpc")
        rpc_session = self.make_session(rpc_client)
        with self.assertRaises(BridgeError) as raised:
            rpc_session.start_turn("rejected")
        self.assertEqual("CODEX_RPC_ERROR", raised.exception.code)
        self.assertEqual(False, raised.exception.details["turn_created"])
        self.assertEqual(False, raised.exception.details["turn_active"])
        rejected = rpc_session.snapshot()
        self.assertFalse(rejected["turn_active"])
        self.assertIsNone(rejected["turn_status"])

        observed_client = _FailingTurnClient("rpc_after_started")
        observed_session = self.make_session(observed_client)
        with self.assertRaises(CodexRPCError):
            observed_session.start_turn("observed before rejection")
        observed = observed_session.snapshot()
        self.assertTrue(observed["turn_active"])
        self.assertEqual("turn-observed", observed["turn_id"])
        self.assertEqual("inProgress", observed["turn_status"])

        for failure in ("transport", "invalid_ack"):
            with self.subTest(failure=failure):
                client = _FailingTurnClient(failure)
                session = self.make_session(client)
                with self.assertRaises(BridgeError):
                    session.start_turn("uncertain")
                uncertain = session.snapshot()
                self.assertTrue(uncertain["turn_active"])
                self.assertEqual("startUnknown", uncertain["turn_status"])
                with self.assertRaises(BridgeError) as raised:
                    session.start_turn("must remain blocked")
                self.assertEqual("TURN_ALREADY_ACTIVE", raised.exception.code)
                self.assertEqual(1, client.turn_request_count)


class BridgeSessionNativeToolPolicyTests(unittest.TestCase):
    def make_session(
        self,
        backend: str = "fxhoudini",
    ) -> tuple[BridgeSession, _RecordingClient]:
        client = _RecordingClient()
        return (
            BridgeSession(
                REPOSITORY_ROOT,
                client,
                EventBuffer(),
                mcp_backend=backend,
            ),
            client,
        )

    def test_thread_start_enables_workspace_write_with_native_hython_instructions(
        self,
    ) -> None:
        session, client = self.make_session()

        session.start_thread()

        method, params = client.requests[0]
        self.assertEqual("thread/start", method)
        self.assertEqual("workspace-write", params["sandbox"])
        self.assertEqual("on-request", params["approvalPolicy"])
        self.assertIsNone(params["serviceTier"])
        instructions = params["developerInstructions"]
        self.assertLessEqual(len(instructions), 1_000)
        for required_text in (
            "当前场景的创建、修改、连接、材质和动画默认使用",
            "FXHoudini MCP 与 HOM",
            "复杂操作优先用 execute_python 批量执行",
            "细粒度工具用于读取、单项修改和最终验证",
            "不要逐节点循环",
            "相同调用失败后先读真实错误再改用兼容方法",
            "capture_screenshot 只做阶段性验证",
            "主代理负责当前 HIP 写入",
            "子代理只做研究、草案和审阅",
            "FX fallback 同样非代码级隔离",
            "实时 MCP 不可用时直接说明",
            "不得改成离线 HIP",
            "只有用户明确要求离线",
            "PATH 中的 hython.exe",
            "普通场景请求不先搜索项目源码/文档",
            "仅诊断或修改 Panel、Bridge、MCP/项目代码时读取",
            "上下文仅用 app-server 自动整理",
            "不手动 compact",
            "不创建本地摘要或记忆",
            "实时代码禁止 hou.hipFile.clear/load/save",
            "新资产放入唯一新根",
            "不要调用 request_user_input",
            "信息不足时采用合理默认值",
            "无法执行才报告原因",
            "自动截图写 HIA_CACHE_DIR/screenshots",
            "预览写 previews",
            "中间图写 tmp",
            "文件名加时间戳和短随机后缀",
            "插件源码、内部缓存、自动截图/预览/附件/临时/诊断必须留项目内",
            "用户明确指定的最终渲染、EXR、视频、USD、模拟缓存或导出是用户交付物",
            "可写所选普通本地项目外目录",
            "未指定才用 HIA_RENDER_OUTPUT_DIR",
            "始终报告最终路径",
            "禁止屏幕接管",
        ):
            with self.subTest(required_text=required_text):
                self.assertIn(required_text, instructions)
        self.assertNotIn("不使用 fxhoudinimcp", instructions)
        self.assertNotIn(".runtime/jobs", instructions)
        for asset_specific_text in ("售货机", "桌子", "楼梯", "vending_machine"):
            self.assertNotIn(asset_specific_text, instructions)
        self.assertNotIn("baseInstructions", params)
        self.assertNotIn("config", params)

    def test_hia_v2_thread_instructions_use_only_hia_batch_and_validation_tools(
        self,
    ) -> None:
        session, client = self.make_session("hia_v2")

        session.start_thread()

        instructions = client.requests[0][1]["developerInstructions"]
        self.assertLessEqual(len(instructions), 1_000)
        for required_text in (
            "HIA MCP V2 与 HOM",
            "hia_execute_hom 批量执行",
            "hia_context/hia_inspect",
            "hia_scene_diff/hia_validate",
            "hia_capture_viewport 仅按需视觉核对",
            "仅主代理调用当前会话 hia_*/HOM 并写当前 HIP",
            "子代理只做研究、脚本草案和审阅",
            "MCP 无 caller lineage",
            "非代码级隔离",
            "hia_search_node_types/help 等同类读取由主代理串行或少量调用",
            "不并发扇出",
            "遇到 QUEUE_FULL 不立即重试",
            "hia_execute_hom 等场景写入始终由主代理执行",
            "goal_focus_mode=true 且有意义阶段成功才设一次 checkpoint_label",
            "普通聊天、专注关闭或逐节点/参数不设",
            "主任务只保留原生 Goal、决定和子任务短摘要",
            "子任务详情按需查看",
            "不塞入主上下文",
        ):
            self.assertIn(required_text, instructions)
        for forbidden_text in (
            "FXHoudini MCP",
            "execute_python",
            "capture_screenshot",
            "create_node",
            "set_parameters",
        ):
            self.assertNotIn(forbidden_text, instructions)
        self.assertEqual("hia_v2", session.snapshot()["mcp_backend"])

    def test_research_instructions_batch_public_pages_without_weakening_hia_serial_io(
        self,
    ) -> None:
        for backend in ("fxhoudini", "hia_v2"):
            with self.subTest(backend=backend):
                session, client = self.make_session(backend)
                session.start_thread()
                instructions = client.requests[0][1]["developerInstructions"]
                for required_text in (
                    "先定本阶段必需 URL",
                    "优先原生 web/search",
                    "同阶段公开页合为一次 PowerShell 只读批量读取",
                    "不逐页审批",
                    "复用已取内容",
                    "不重复抓取相近页面",
                ):
                    self.assertIn(required_text, instructions)
                if backend == "hia_v2":
                    self.assertIn(
                        "hia_search_node_types/help 等同类读取由主代理串行或少量调用",
                        instructions,
                    )
                    self.assertIn("不并发扇出", instructions)
                    self.assertIn(
                        "hia_execute_hom 等场景写入始终由主代理执行",
                        instructions,
                    )

    def test_thread_resume_enables_workspace_write_with_on_request_approval(self) -> None:
        session, client = self.make_session()

        session.resume_thread("thread-existing", service_tier="priority")

        method, params = client.requests[0]
        self.assertEqual("thread/resume", method)
        self.assertEqual("workspace-write", params["sandbox"])
        self.assertEqual("on-request", params["approvalPolicy"])
        self.assertEqual("priority", params["serviceTier"])
        self.assertIn("FXHoudini MCP 与 HOM", params["developerInstructions"])
        self.assertNotIn("baseInstructions", params)
        self.assertNotIn("config", params)

    def test_turn_start_reasserts_workspace_write_and_on_request(self) -> None:
        session, client = self.make_session()
        session.start_thread()
        client.requests.clear()

        session.start_turn("read Houdini state", service_tier="priority")

        method, params = client.requests[0]
        self.assertEqual("turn/start", method)
        self.assertEqual("on-request", params["approvalPolicy"])
        self.assertEqual("priority", params["serviceTier"])
        self.assertEqual(
            {"type": "workspaceWrite", "networkAccess": False},
            params["sandboxPolicy"],
        )
        self.assertEqual("read Houdini state", params["input"][0]["text"])
        self.assertNotIn("developerInstructions", params)
        self.assertNotIn("baseInstructions", params)
        self.assertNotIn("config", params)


if __name__ == "__main__":
    unittest.main()
