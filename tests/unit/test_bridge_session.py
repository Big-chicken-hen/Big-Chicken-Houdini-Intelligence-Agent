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
from hia_bridge.scene_writer import SceneWriterOwnership  # noqa: E402
from hia_bridge.session import (  # noqa: E402
    MODEL_LIST_MAX_ENTRIES,
    MODEL_LIST_MAX_PAGES,
    MODEL_LIST_PAGE_SIZE,
    MAX_LOCAL_IMAGES,
    STOP_INTERRUPT_GRACE_SECONDS,
    THREAD_PREVIEW_MAX_LENGTH,
    BridgeSession,
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
        self.on_delete: Any = None

    def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self.requests.append((method, dict(params)))
        if method == "thread/list":
            return self.list_response
        if method == "thread/name/set":
            return {}
        if method == "thread/delete":
            if callable(self.on_delete):
                self.on_delete()
            return {"deleted": True}
        return super().request(method, params)


class _PagedThreadHistoryClient(_ThreadHistoryClient):
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        super().__init__({})
        self.responses = list(responses)

    def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method != "thread/list":
            return super().request(method, params)
        self.requests.append((method, dict(params)))
        if not self.responses:
            raise AssertionError("Unexpected extra thread/list page request")
        return self.responses.pop(0)


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


class _ProjectRoleBoundaryClient(_ClientStub):
    def __init__(self, *, contradictory_resume: bool = False) -> None:
        super().__init__()
        self.contradictory_resume = contradictory_resume
        self.requests: list[tuple[str, dict[str, Any]]] = []
        self.read_count = 0

    def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self.requests.append((method, dict(params)))
        if method == "thread/read":
            self.read_count += 1
            source = None if self.contradictory_resume else "hia-project/project-a/planning"
            return {
                "thread": {
                    "id": params["threadId"],
                    "threadSource": source,
                    "turns": [],
                }
            }
        if method == "thread/resume" and self.contradictory_resume:
            return {
                "thread": {
                    "id": params["threadId"],
                    "threadSource": "hia-project/project-a/planning",
                    "turns": [],
                }
            }
        raise AssertionError(f"Unexpected project role mutation request: {method}")


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


class _InterruptClient(_RecordingClient):
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

        self.assertEqual(
            ["thread/read", "thread/resume"],
            [method for method, _ in client.requests],
        )
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

    def test_read_preserves_only_safe_public_ids_and_final_chat(self) -> None:
        client = _ThreadContentClient(
            {
                "thread": {
                    "id": "thread-current",
                    "turns": [
                        {
                            "id": "turn-public-1",
                            "items": [
                                {
                                    "id": "item-user-1",
                                    "type": "userMessage",
                                    "content": [
                                        {"type": "text", "text": "公开问题"},
                                        {
                                            "type": "localImage",
                                            "path": r"E:\private\reference.png",
                                        },
                                    ],
                                },
                                {
                                    "id": "item-commentary-1",
                                    "type": "agentMessage",
                                    "channel": "commentary",
                                    "text": "内部进度",
                                },
                                {
                                    "id": "item-final-1",
                                    "type": "agentMessage",
                                    "text": "公开最终回答",
                                },
                                {
                                    "id": "tool-raw-1",
                                    "type": "commandExecution",
                                    "aggregatedOutput": "secret tool output",
                                },
                            ],
                        },
                        {
                            "id": "../unsafe-turn",
                            "items": [
                                {
                                    "id": "unsafe item id",
                                    "type": "agentMessage",
                                    "text": "第二条最终回答",
                                }
                            ],
                        },
                    ],
                }
            }
        )
        session = BridgeSession(REPOSITORY_ROOT, client, EventBuffer())

        result = session.read_thread("thread-current")

        turns = result["result"]["thread"]["turns"]
        self.assertEqual("turn-public-1", turns[0]["id"])
        self.assertNotIn("id", turns[1])
        self.assertEqual(
            ["userMessage", "agentMessage"],
            [item["type"] for item in turns[0]["items"]],
        )
        self.assertEqual("item-user-1", turns[0]["items"][0]["id"])
        self.assertEqual("item-final-1", turns[0]["items"][1]["id"])
        self.assertNotIn("id", turns[1]["items"][0])
        self.assertNotIn(
            "secret tool output",
            json.dumps(result, ensure_ascii=False),
        )

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
                        "limit": 100,
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

    def test_list_threads_reads_every_native_page(self) -> None:
        def entry(thread_id: str, updated_at: int) -> dict[str, Any]:
            return {
                "id": thread_id,
                "cwd": str(REPOSITORY_ROOT),
                "name": thread_id,
                "preview": thread_id,
                "updatedAt": updated_at,
            }

        client = _PagedThreadHistoryClient(
            [
                {"data": [entry("thread-new", 20)], "nextCursor": "page-2"},
                {"data": [entry("thread-old", 10)], "nextCursor": None},
            ]
        )
        session = BridgeSession(REPOSITORY_ROOT, client, EventBuffer())

        result = session.list_threads()

        self.assertEqual(
            ["thread-new", "thread-old"],
            [thread["thread_id"] for thread in result["threads"]],
        )
        self.assertNotIn("cursor", client.requests[0][1])
        self.assertEqual("page-2", client.requests[1][1]["cursor"])

    def test_list_threads_rejects_repeated_pagination_cursor(self) -> None:
        client = _PagedThreadHistoryClient(
            [
                {"data": [], "nextCursor": "same"},
                {"data": [], "nextCursor": "same"},
            ]
        )
        session = BridgeSession(REPOSITORY_ROOT, client, EventBuffer())

        with self.assertRaises(BridgeError) as raised:
            session.list_threads()

        self.assertEqual("INVALID_THREAD_LIST_RESPONSE", raised.exception.code)
        self.assertEqual("nextCursor", raised.exception.details["field"])
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

    def test_delete_selected_idle_thread_clears_bridge_session(self) -> None:
        client = _ThreadHistoryClient({"data": []})
        session = BridgeSession(REPOSITORY_ROOT, client, EventBuffer())
        session.start_thread()
        client.requests.clear()

        result = session.delete_thread("thread-test")

        self.assertEqual(
            ("thread/delete", {"threadId": "thread-test"}),
            client.requests[-1],
        )
        self.assertTrue(result["deleted"])
        self.assertTrue(result["was_selected"])
        self.assertIsNone(session.snapshot()["thread_id"])
        self.assertFalse(session.snapshot()["turn_active"])

    def test_delete_nonselected_thread_keeps_current_selection(self) -> None:
        client = _ThreadHistoryClient({"data": []})
        session = BridgeSession(REPOSITORY_ROOT, client, EventBuffer())
        session.start_thread()
        client.requests.clear()

        result = session.delete_thread("thread-other")

        self.assertFalse(result["was_selected"])
        self.assertEqual("thread-test", session.snapshot()["thread_id"])

    def test_delete_removes_only_the_exact_thread_attachment_cache(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project_root = Path(directory)
            deleted_cache = (
                project_root / ".runtime" / "attachments" / "thread-delete"
            )
            kept_cache = project_root / ".runtime" / "attachments" / "thread-keep"
            deleted_cache.mkdir(parents=True)
            kept_cache.mkdir(parents=True)
            (deleted_cache / "image.png").write_bytes(b"delete")
            (kept_cache / "image.png").write_bytes(b"keep")
            project_image = (
                project_root
                / ".runtime"
                / "project-attachments"
                / "project-one"
                / "content.png"
            )
            project_image.parent.mkdir(parents=True)
            project_image.write_bytes(b"project")
            client = _ThreadHistoryClient({"data": []})
            session = BridgeSession(project_root, client, EventBuffer())

            result = session.delete_thread("thread-delete")

            self.assertFalse(deleted_cache.exists())
            self.assertTrue((kept_cache / "image.png").is_file())
            self.assertEqual(b"project", project_image.read_bytes())
            self.assertTrue(result["cache_cleanup"]["complete"])
            self.assertEqual(
                [str(deleted_cache.resolve())],
                result["cache_cleanup"]["removed_paths"],
            )

    def test_delete_response_does_not_clear_thread_selected_during_rpc(self) -> None:
        client = _ThreadHistoryClient({"data": []})
        session = BridgeSession(REPOSITORY_ROOT, client, EventBuffer())
        session.start_thread()
        client.requests.clear()
        client.on_delete = lambda: session.resume_thread("thread-new")

        result = session.delete_thread("thread-test")
        client.emit_notification("thread/deleted", {"threadId": "thread-test"})
        client.emit_notification("thread/deleted", {"threadId": "thread-test"})

        self.assertFalse(result["was_selected"])
        self.assertEqual("thread-new", session.snapshot()["thread_id"])
        self.assertEqual(
            1,
            sum(method == "thread/delete" for method, _params in client.requests),
        )

    def test_focus_write_failure_after_delete_returns_success_warning(self) -> None:
        client = _ThreadHistoryClient({"data": []})
        events = EventBuffer()
        session = BridgeSession(REPOSITORY_ROOT, client, events)
        session.start_thread()
        with session._lock:
            session._focus_enabled_threads.add("thread-test")
            session._focus_goal_bindings["thread-test"] = "a" * 64
        client.requests.clear()
        failure = BridgeError(
            "FOCUS_STATE_UNAVAILABLE",
            "Target focus mode could not be persisted inside the project",
            http_status=503,
        )

        with mock.patch.object(
            session,
            "_write_focus_state_locked",
            side_effect=failure,
        ):
            result = session.delete_thread("thread-test")

        self.assertTrue(result["deleted"])
        self.assertEqual(
            "FOCUS_STATE_UNAVAILABLE",
            result["cleanup_warning"]["code"],
        )
        self.assertIsNone(session.snapshot()["thread_id"])
        self.assertNotIn("thread-test", session._focus_enabled_threads)
        self.assertNotIn("thread-test", session._focus_goal_bindings)
        self.assertEqual(
            1,
            sum(method == "thread/delete" for method, _params in client.requests),
        )
        deleted_events = [
            event
            for event in events.poll(0, timeout=0)["events"]
            if event["type"] == "thread_deleted"
        ]
        self.assertEqual(1, len(deleted_events))
        self.assertEqual(
            "FOCUS_STATE_UNAVAILABLE",
            deleted_events[0]["cleanup_warning"]["code"],
        )

    def test_delete_is_rejected_while_a_turn_is_active(self) -> None:
        client = _ThreadHistoryClient({"data": []})
        session = BridgeSession(REPOSITORY_ROOT, client, EventBuffer())
        session.start_thread()
        with session._lock:
            session._turn_id = "turn-active"
            session._turn_status = "inProgress"
            session._turn_active = True
        client.requests.clear()

        with self.assertRaises(BridgeError) as caught:
            session.delete_thread("thread-test")

        self.assertEqual("TURN_ALREADY_ACTIVE", caught.exception.code)
        self.assertEqual([], client.requests)


class BridgeSessionProjectRoleBoundaryTests(unittest.TestCase):
    ROLE_THREAD_ID = "project-role-thread"

    def make_session(
        self,
        *,
        contradictory_resume: bool = False,
        active_turn: bool = False,
        selected_thread_id: str | None = ROLE_THREAD_ID,
    ) -> tuple[BridgeSession, _ProjectRoleBoundaryClient]:
        client = _ProjectRoleBoundaryClient(
            contradictory_resume=contradictory_resume
        )
        session = BridgeSession(REPOSITORY_ROOT, client, EventBuffer())
        with session._lock:
            session._thread_id = selected_thread_id
            if active_turn:
                session._turn_id = "role-turn"
                session._turn_status = "inProgress"
                session._turn_active = True
                session._turn_created = True
        return session, client

    def assert_project_api_required(self, operation: Any, client: Any) -> None:
        with self.assertRaises(BridgeError) as raised:
            operation()
        self.assertEqual("PROJECT_ROLE_REQUIRES_PROJECT_API", raised.exception.code)
        self.assertEqual(409, raised.exception.http_status)
        self.assertEqual(
            {"thread/read"},
            {method for method, _params in client.requests},
        )

    def test_every_generic_role_read_or_mutation_is_rejected_by_backend(self) -> None:
        operations = (
            lambda session: session.resume_thread(self.ROLE_THREAD_ID),
            lambda session: session.read_thread(self.ROLE_THREAD_ID),
            lambda session: session.rename_thread(self.ROLE_THREAD_ID, "new name"),
            lambda session: session.delete_thread(self.ROLE_THREAD_ID),
            lambda session: session.get_goal(self.ROLE_THREAD_ID),
            lambda session: session.set_goal(
                expected_thread_id=self.ROLE_THREAD_ID,
                objective="must not mutate",
                status="active",
                token_budget=None,
            ),
            lambda session: session.clear_goal(self.ROLE_THREAD_ID),
            lambda session: session.set_focus_mode(self.ROLE_THREAD_ID, True),
            lambda session: session.start_turn("must not start"),
        )
        for operation in operations:
            with self.subTest(operation=operation):
                session, client = self.make_session()
                self.assert_project_api_required(lambda: operation(session), client)

        for operation in (
            lambda session: session.steer_turn("must not steer"),
            lambda session: session.interrupt_turn(),
        ):
            with self.subTest(operation=operation):
                session, client = self.make_session(active_turn=True)
                self.assert_project_api_required(lambda: operation(session), client)

    def test_resume_response_cannot_reclassify_project_role_as_ordinary(self) -> None:
        session, client = self.make_session(
            contradictory_resume=True,
            selected_thread_id="ordinary-current",
        )

        with self.assertRaises(BridgeError) as raised:
            session.resume_thread(self.ROLE_THREAD_ID)

        self.assertEqual("PROJECT_ROLE_REQUIRES_PROJECT_API", raised.exception.code)
        self.assertEqual(
            ["thread/read", "thread/resume"],
            [method for method, _params in client.requests],
        )
        self.assertEqual("ordinary-current", session.snapshot()["thread_id"])


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

    def test_interrupt_completion_within_grace_is_terminal(self) -> None:
        client = _InterruptClient(complete_during_interrupt=True)
        events = EventBuffer()
        session = BridgeSession(REPOSITORY_ROOT, client, events)
        session.start_thread()
        turn_id = session.start_turn("stop quickly")["turn_id"]

        result = session.interrupt_turn()

        self.assertEqual("thread-test", result["thread_id"])
        self.assertEqual(turn_id, result["turn_id"])
        self.assertFalse(result["session"]["turn_active"])
        self.assertEqual("interrupted", result["session"]["turn_status"])
        self.assertEqual(STOP_INTERRUPT_GRACE_SECONDS, client.timed_requests[0][2])

    def test_interrupt_timeout_preserves_the_active_turn(self) -> None:
        client = _InterruptClient(complete_during_interrupt=False)
        session = self.make_session(client)
        turn_id = session.start_turn("stop without confirmation")["turn_id"]

        with mock.patch("hia_bridge.session.STOP_INTERRUPT_GRACE_SECONDS", 0.01):
            with self.assertRaises(BridgeError) as raised:
                session.interrupt_turn()

        self.assertEqual("INTERRUPT_NOT_CONFIRMED", raised.exception.code)
        self.assertEqual(
            ["turn/interrupt"],
            [method for method, _params, _timeout in client.timed_requests],
        )
        snapshot = session.snapshot()
        self.assertEqual(turn_id, snapshot["turn_id"])
        self.assertTrue(snapshot["turn_active"])
        self.assertEqual("inProgress", snapshot["turn_status"])

    def test_interrupt_without_bounded_transport_is_not_confirmed(self) -> None:
        client = _RecordingClient()
        session = self.make_session(client)
        turn_id = session.start_turn("stop without transport")["turn_id"]

        with self.assertRaises(BridgeError) as raised:
            session.interrupt_turn()

        self.assertEqual("INTERRUPT_NOT_CONFIRMED", raised.exception.code)
        snapshot = session.snapshot()
        self.assertEqual(turn_id, snapshot["turn_id"])
        self.assertTrue(snapshot["turn_active"])
        self.assertEqual("inProgress", snapshot["turn_status"])

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

    def test_unknown_first_ack_keeps_exclusive_writer_ownership(self) -> None:
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

        start_turn(1, "second")
        self.assertIsInstance(outcomes[1].get("error"), BridgeError)
        self.assertEqual("SCENE_WRITER_BUSY", outcomes[1]["error"].code)
        self.assertFalse(client.second_entered.is_set())

        client.release_first_ack.set()
        first.join(2.0)
        self.assertFalse(first.is_alive())
        after_late_ack = session.snapshot()
        self.assertFalse(after_late_ack["turn_active"])
        self.assertEqual("completed", after_late_ack["turn_status"])
        self.assertNotIn("error", outcomes[0])

    def test_project_execution_blocks_ordinary_then_release_allows_next_writer(self) -> None:
        writer = SceneWriterOwnership()
        project_reservation = writer.reserve("project", "project-active")
        project_owner = writer.bind(
            project_reservation, "thread-project", "turn-project"
        )
        client = _RecordingClient()
        session = BridgeSession(
            REPOSITORY_ROOT,
            client,
            EventBuffer(),
            scene_writer=writer,
        )
        session.start_thread()
        client.requests.clear()

        with self.assertRaises(BridgeError) as raised:
            session.start_turn("ordinary must not queue")

        self.assertEqual("SCENE_WRITER_BUSY", raised.exception.code)
        self.assertEqual(project_owner, writer.snapshot()["owner"])
        self.assertFalse(any(method == "turn/start" for method, _ in client.requests))

        self.assertTrue(writer.turn_terminal(project_owner))
        result = session.start_turn("ordinary after release")
        self.assertEqual("turn-recorded", result["turn_id"])

    def test_missing_hia_item_id_retains_ordinary_writer_and_emits_diagnostic(self) -> None:
        writer = SceneWriterOwnership()
        events = EventBuffer()
        client = _RecordingClient()
        session = BridgeSession(
            REPOSITORY_ROOT,
            client,
            events,
            scene_writer=writer,
        )
        session.start_thread()
        turn_id = session.start_turn("write the scene")["turn_id"]
        owner = writer.snapshot()["owner"]

        client.emit_notification(
            "item/started",
            {
                "threadId": "thread-test",
                "turnId": turn_id,
                "item": {
                    "type": "mcpToolCall",
                    "server": "hia_mcp_v2",
                    "tool": "hia_execute_hom",
                },
            },
        )
        client.emit_notification(
            "turn/completed",
            {
                "threadId": "thread-test",
                "turn": {"id": turn_id, "status": "completed"},
            },
        )

        snapshot = writer.snapshot()
        self.assertEqual(owner, snapshot["owner"])
        self.assertTrue(snapshot["turn_terminal"])
        self.assertEqual(1, snapshot["anonymous_hia_items"])
        with self.assertRaises(BridgeError) as blocked:
            session.start_thread()
        self.assertEqual("SCENE_WRITER_STILL_ACTIVE", blocked.exception.code)
        warnings = [
            event
            for event in events.poll(0, timeout=0)["events"]
            if event.get("type") == "protocol_warning"
        ]
        self.assertEqual("INVALID_HIA_ITEM_ID", warnings[-1]["code"])
        client.emit_notification(
            "item/completed",
            {
                "threadId": "thread-test",
                "turnId": turn_id,
                "item": {
                    "type": "mcpToolCall",
                    "server": "hia_mcp_v2",
                    "tool": "hia_execute_hom",
                },
            },
        )
        self.assertIsNone(writer.snapshot()["owner"])

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
        self.assertEqual("never", params["approvalPolicy"])
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
            "HIA 截图只能用 SceneViewer.flipbook 写入 HIA_CACHE_DIR/screenshots",
            "不可用时明确失败",
            "预览写 previews",
            "中间图写 tmp",
            "附件/知识/模型/索引仍留项目 .runtime",
            "文件名加时间戳和短随机后缀",
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
        self.assertNotIn("自动截图写 HIA_CACHE_DIR/screenshots", instructions)
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
        self.assertLessEqual(len(instructions), 1_320)
        for required_text in (
            "HIA MCP V2 与 HOM",
            "确定操作不枚举",
            "HOM先编译",
            "类别API仅在对象支持时调用",
            "明确小改只读目标值后直接执行",
            "修改既有网络先用 hia_context/hia_inspect",
            "输入输出、两层上游",
            "公共控制、材质入口、引用和允许 scope",
            "优先现有节点和标准原生节点网络",
            "场景内 Python SOP 或直接几何须说明必要性",
            "同一 scope 内一次 hia_execute_hom",
            "fresh cook、hia_scene_diff/hia_validate",
            "plan/revision/tool completed 不算通过",
            "hia_capture_viewport，不固定尺寸",
            "动画/模拟用代表帧或短序列",
            "材质验收读取绑定、MaterialX 连接和正确输入",
            "Stop 后已提交的 HOM 仍可能收尾",
            "失败后先显式 inspect、diff、validate",
            "根据新证据决定下一步，禁止自动重试",
            "unknown/partial/NO_OBSERVED_EFFECT 不 Undo 也不算完成",
            "session/source drift 停写并正常重启，不热加载",
            "仅主任务串行调用 hia_*/HOM 并写 HIP",
            "子任务只研究、草拟、只读审阅",
            "MCP 无 caller lineage",
            "非代码级隔离",
            "多个关键词合并为一次批量查询",
            "同类读取不并发扇出",
            "QUEUE_FULL 不立即重试",
            "阶段结果只按实际场景证据报告",
            "失败阶段先检查当前场景再修正",
            "主任务只保留原生 Goal、决定和子任务短摘要",
            "子任务详情按需查看",
            "不塞入主上下文",
            "HIA 截图只能用 SceneViewer.flipbook 写入 HIA_CACHE_DIR/screenshots",
            "不可用时明确失败",
            "附件/知识/模型/索引留 .runtime",
        ):
            self.assertIn(required_text, instructions)
        for forbidden_text in (
            "FXHoudini MCP",
            "execute_python",
            "capture_screenshot",
            "create_node",
            "set_parameters",
            "截图写 HIA_CACHE_DIR/screenshots，预览写",
            "640x360",
        ):
            self.assertNotIn(forbidden_text, instructions)
        self.assertEqual("hia_v2", session.snapshot()["mcp_backend"])

    def test_complex_scene_writes_require_research_but_small_edits_do_not(
        self,
    ) -> None:
        for backend in ("fxhoudini", "hia_v2"):
            with self.subTest(backend=backend):
                session, client = self.make_session(backend)
                session.start_thread()
                instructions = client.requests[0][1]["developerInstructions"]
                for required_text in (
                    "复杂创建、修改或修复在首次场景写入前先",
                    "一次相关",
                    "批量检索并复用结果",
                    "复杂、参考驱动、材质、FX、模拟、渲染或版本不确定任务",
                    (
                        "发现来源时明确用原生 web search"
                        if backend == "fxhoudini"
                        else "发现来源明确用原生 web search"
                    ),
                    "当前 SideFX 官方与原始来源",
                    "openPage",
                    "简单参数/连接/删除/重命名/布局不强制知识检索或网页研究",
                ):
                    self.assertIn(required_text, instructions)
                self.assertNotIn(
                    "任何创建、修改或修复在首次场景写入前必须先",
                    instructions,
                )
                if backend == "hia_v2":
                    self.assertIn("hia_local_help_search", instructions)
                    self.assertIn("检索不可用时明确说明，禁止静默跳过", instructions)
                    self.assertIn(
                        "多个关键词合并为一次批量查询",
                        instructions,
                    )
                    self.assertIn(
                        "仅主任务串行调用 hia_*/HOM 并写 HIP",
                        instructions,
                    )
                    self.assertIn("不并发扇出", instructions)
                    self.assertIn(
                        "子任务只研究、草拟、只读审阅",
                        instructions,
                    )

    def test_thread_resume_enables_workspace_write_without_runtime_approval(self) -> None:
        session, client = self.make_session()

        session.resume_thread("thread-existing", service_tier="priority")

        self.assertEqual("thread/read", client.requests[0][0])
        method, params = client.requests[1]
        self.assertEqual("thread/resume", method)
        self.assertEqual("workspace-write", params["sandbox"])
        self.assertEqual("never", params["approvalPolicy"])
        self.assertEqual("priority", params["serviceTier"])
        self.assertIn("FXHoudini MCP 与 HOM", params["developerInstructions"])
        self.assertNotIn("baseInstructions", params)
        self.assertNotIn("config", params)

    def test_turn_start_reasserts_workspace_write_without_runtime_approval(self) -> None:
        session, client = self.make_session()
        session.start_thread()
        client.requests.clear()

        session.start_turn("read Houdini state", service_tier="priority")

        method, params = client.requests[0]
        self.assertEqual("turn/start", method)
        self.assertEqual("never", params["approvalPolicy"])
        self.assertEqual("priority", params["serviceTier"])
        self.assertEqual(
            {"type": "workspaceWrite", "networkAccess": True},
            params["sandboxPolicy"],
        )
        self.assertEqual("read Houdini state", params["input"][0]["text"])
        self.assertNotIn("developerInstructions", params)
        self.assertNotIn("baseInstructions", params)
        self.assertNotIn("config", params)


if __name__ == "__main__":
    unittest.main()
