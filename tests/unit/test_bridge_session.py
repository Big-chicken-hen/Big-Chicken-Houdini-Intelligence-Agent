from __future__ import annotations

import sys
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any


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
    BridgeSession,
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
            return {"thread": {"id": params["threadId"]}}
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
            "supportedReasoningEfforts",
            "defaultReasoningEffort",
        }
        for model in result["models"]:
            self.assertEqual(expected_keys, set(model))
            self.assertEqual(
                {"reasoningEffort", "description"},
                set(model["supportedReasoningEfforts"][0]),
            )

    def test_model_list_rejects_cursor_cycle_and_malformed_response(self) -> None:
        scenarios: dict[str, list[Any]] = {
            "non_object_root": [[]],
            "non_array_data": [{"data": {}, "nextCursor": None}],
            "invalid_model": [{"data": [{}], "nextCursor": None}],
            "invalid_cursor": [{"data": [], "nextCursor": 3}],
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

    def test_ack_is_rejected_if_the_turn_completed_before_it_arrived(self) -> None:
        session, _ = self.make_active_session(_CompletingSteerClient())

        with self.assertRaises(BridgeError) as raised:
            session.steer_turn("追加要求")

        self.assertEqual("TURN_CHANGED_DURING_STEER", raised.exception.code)
        snapshot = session.snapshot()
        self.assertFalse(snapshot["turn_active"])
        self.assertEqual("completed", snapshot["turn_status"])

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


class BridgeSessionTurnStateTests(unittest.TestCase):
    def make_session(self, client: _ClientStub) -> BridgeSession:
        session = BridgeSession(REPOSITORY_ROOT, client, EventBuffer())
        session.start_thread()
        return session

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
    def make_session(self) -> tuple[BridgeSession, _RecordingClient]:
        client = _RecordingClient()
        return BridgeSession(REPOSITORY_ROOT, client, EventBuffer()), client

    def test_thread_start_enables_workspace_write_with_native_hython_instructions(
        self,
    ) -> None:
        session, client = self.make_session()

        session.start_thread()

        method, params = client.requests[0]
        self.assertEqual("thread/start", method)
        self.assertEqual("workspace-write", params["sandbox"])
        self.assertEqual("on-request", params["approvalPolicy"])
        instructions = params["developerInstructions"]
        self.assertLessEqual(len(instructions), 512)
        for required_text in (
            "默认作用于当前打开场景",
            "FXHoudini MCP 与 HOM",
            "多节点复杂资产优先用 execute_python 批量执行",
            "不要逐节点或逐参数循环调用 create_node/set_parameters",
            "细粒度工具主要用于读取、单项修改和最终验证",
            "同一工具以相同参数失败后不得盲目重复",
            "读取真实错误并改用兼容方法",
            "capture_screenshot 仅用于阶段性视觉验证",
            "不要每一步都截图",
            "实时 MCP 不可用时直接说明",
            "不得改成离线 HIP",
            "只有用户明确要求离线",
            "PATH 中的 hython.exe",
            "实时代码禁止 hou.hipFile.clear/load/save",
            "生成新资产时放入唯一新根",
            "不要调用 request_user_input",
            "信息不足时自行采用合理默认值继续",
            "只有无法执行时才直接报告原因",
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

    def test_thread_resume_enables_workspace_write_with_on_request_approval(self) -> None:
        session, client = self.make_session()

        session.resume_thread("thread-existing")

        method, params = client.requests[0]
        self.assertEqual("thread/resume", method)
        self.assertEqual("workspace-write", params["sandbox"])
        self.assertEqual("on-request", params["approvalPolicy"])
        self.assertNotIn("developerInstructions", params)
        self.assertNotIn("baseInstructions", params)
        self.assertNotIn("config", params)

    def test_turn_start_reasserts_workspace_write_and_on_request(self) -> None:
        session, client = self.make_session()
        session.start_thread()
        client.requests.clear()

        session.start_turn("read Houdini state")

        method, params = client.requests[0]
        self.assertEqual("turn/start", method)
        self.assertEqual("on-request", params["approvalPolicy"])
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
