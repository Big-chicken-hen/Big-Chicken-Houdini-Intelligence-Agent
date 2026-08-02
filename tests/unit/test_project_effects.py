from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest

from services.bridge.hia_bridge.project_contracts import (
    ProjectState,
    ProjectStatus,
    Role,
    RoleThread,
    authoritative_task_identity,
)
from services.bridge.hia_bridge.project_effects import (
    CompletedTurn,
    ProjectRoleError,
    ProjectRoleExecutor,
    _review_coverage_proof,
    _stage_evidence_needs,
)
from services.bridge.hia_bridge.project_lifecycle import LifecycleEvent, ProjectEvent
from services.bridge.hia_bridge.project_registry import ProjectRecord, ProjectRegistry
from services.bridge.hia_bridge.project_runner import ProjectAction, ProjectActionResult


def _state(status: ProjectStatus = ProjectStatus.PLANNING, *, guidance_revision: int = 0) -> ProjectState:
    task_id, digest = authoritative_task_identity("build a Houdini asset")
    return ProjectState(
        project_id="p1",
        authoritative_task_id=task_id,
        authoritative_task_sha256=digest,
        status=status,
        roles={role: RoleThread(role, f"thread-{role.value}") for role in Role},
        guidance_revision=guidance_revision,
    )


def _turns(*items: dict) -> dict:
    return {"thread": {"id": "thread-supervisor", "turns": [{"items": list(items)}]}}


class _Client:
    def __init__(self, payloads=(), *, histories=None) -> None:
        self.payloads = list(payloads)
        self.histories = dict(histories or {})
        self.calls: list[tuple[str, dict]] = []
        self.turn_counter = 0

    def request(self, method: str, params: dict):
        self.calls.append((method, dict(params)))
        if method == "turn/start":
            self.turn_counter += 1
            return {"turn": {"id": f"turn-{self.turn_counter}"}}
        if method == "thread/read":
            thread_id = params["threadId"]
            return self.histories.get(thread_id, {"thread": {"id": thread_id, "turns": []}})
        raise AssertionError(f"unexpected RPC: {method}")

    def start_turn_when_idle(self, params: dict, timeout_seconds: float):
        return self.request("turn/start", params)

    def wait_for_turn(self, thread_id: str, turn_id: str, timeout_seconds: float) -> CompletedTurn:
        if not self.payloads:
            raise AssertionError("no queued payload")
        payload = self.payloads.pop(0)
        return CompletedTurn(thread_id, turn_id, "completed", payload)


class _OwnedExecutionProbe(ProjectRoleExecutor):
    def __init__(self, *args, entered, release, active, **kwargs):
        super().__init__(*args, **kwargs)
        self.entered = entered
        self.release = release
        self.active = active

    def _execute_stage_owned(self, state, action, deadline):
        with self.active["guard"]:
            self.active["count"] += 1
            self.active["maximum"] = max(self.active["maximum"], self.active["count"])
        self.entered.set()
        self.release.wait(1.0)
        with self.active["guard"]:
            self.active["count"] -= 1
        return ProjectActionResult(state, LifecycleEvent(ProjectEvent.STAGE_EXECUTED))


class ProjectRoleExecutorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.registry = ProjectRegistry(Path(self.temp.name) / "projects.json")
        self.state = _state()
        self.registry.put(ProjectRecord(self.state, "build a Houdini asset"))

    def tearDown(self) -> None:
        self.temp.cleanup()

    def executor(self, client: _Client, *, lock=None) -> ProjectRoleExecutor:
        return ProjectRoleExecutor(
            client=client,
            registry=self.registry,
            scene_write_lock=lock or threading.Lock(),
            allowed_evidence_roots=(Path(self.temp.name),),
            total_timeout_seconds=2.0,
        )

    def test_non_houdini_supervisor_answer_is_natural_and_completes(self) -> None:
        client = _Client(({"schema": "hia-project-start/1", "route": "answered", "reply": "Hello!"},))
        result = self.executor(client).execute(self.state, ProjectAction("start_supervisor", {}))
        self.assertEqual(ProjectEvent.PROJECT_ANSWERED, result.event.kind)
        self.assertEqual(1, len([call for call in client.calls if call[0] == "turn/start"]))

    def test_structured_output_gets_at_most_one_correction(self) -> None:
        valid = {"schema": "hia-project-start/1", "route": "answered", "reply": "done"}
        client = _Client(({"schema": "wrong"}, valid))
        result = self.executor(client).execute(self.state, ProjectAction("start_supervisor", {}))
        self.assertEqual(ProjectEvent.PROJECT_ANSWERED, result.event.kind)
        starts = [params for method, params in client.calls if method == "turn/start"]
        self.assertEqual(2, len(starts))
        correction = json.loads(starts[1]["input"][0]["text"])["schema_correction"]
        self.assertEqual(1, correction["attempt"])

        client = _Client(({"schema": "wrong"}, {"schema": "still-wrong"}, valid))
        with self.assertRaises(ProjectRoleError) as raised:
            self.executor(client).execute(self.state, ProjectAction("start_supervisor", {}))
        self.assertEqual("INVALID_STRUCTURED_OUTPUT", raised.exception.code)
        self.assertEqual(2, len([call for call in client.calls if call[0] == "turn/start"]))

    def test_guidance_is_read_from_supervisor_native_history(self) -> None:
        envelope = {
            "schema": "hia-project-guidance/1",
            "project_id": "p1",
            "revision": 1,
            "target_role": "execution",
            "text": "make the next stage narrower",
        }
        client = _Client(
            histories={
                "thread-supervisor": _turns(
                    {"type": "userMessage", "content": [{"type": "text", "text": json.dumps(envelope)}]}
                )["thread"]
            }
        )
        # Histories are keyed by the whole response shape.
        client.histories["thread-supervisor"] = {
            "thread": {"id": "thread-supervisor", "turns": [{"items": [
                {"type": "userMessage", "content": [{"type": "text", "text": json.dumps(envelope)}]}
            ]}]}
        }
        state = replace(self.state, guidance_revision=1)
        guidance = self.executor(client)._native_guidance(state, Role.EXECUTION)
        self.assertEqual("make the next stage narrower", guidance[0]["text"])

    def test_missing_or_conflicting_guidance_history_fails_explicitly(self) -> None:
        state = replace(self.state, guidance_revision=1)
        with self.assertRaises(ProjectRoleError) as missing:
            self.executor(_Client())._native_guidance(state, Role.EXECUTION)
        self.assertEqual("MISSING_GUIDANCE_HISTORY", missing.exception.code)

        first = {"schema": "hia-project-guidance/1", "project_id": "p1", "revision": 1, "target_role": None, "text": "a"}
        second = {**first, "text": "b"}
        history = {"thread": {"id": "thread-supervisor", "turns": [{"items": [
            {"type": "userMessage", "content": [
                {"type": "text", "text": json.dumps(first)},
                {"type": "text", "text": json.dumps(second)},
            ]}
        ]}]}}
        with self.assertRaises(ProjectRoleError) as conflict:
            self.executor(_Client(histories={"thread-supervisor": history}))._native_guidance(
                state, Role.PLANNING
            )
        self.assertEqual("INVALID_GUIDANCE_HISTORY", conflict.exception.code)

    def test_direct_and_focused_do_not_inherit_full_dual_evidence_requirement(self) -> None:
        self.assertEqual((False, False), _stage_evidence_needs({"depth": "direct"}))
        self.assertEqual((False, False), _stage_evidence_needs({"depth": "focused"}))
        self.assertEqual(
            (True, True),
            _stage_evidence_needs(
                {
                    "depth": "full",
                    "evidence_contract": {"capture": True, "technical": True},
                }
            ),
        )

    def test_stage_review_coverage_requires_both_independent_reviewers(self) -> None:
        stage = {"requirement_ids": ["REQ-1", "REQ-2"]}
        complete = [
            {
                "reviewer": role.value,
                "claims": [{"claim_id": "REQ-1"}, {"claim_id": "REQ-2"}],
            }
            for role in (Role.VISUAL_REVIEW, Role.TECHNICAL_REVIEW)
        ]
        proof = _review_coverage_proof(stage, complete)
        self.assertEqual(
            {Role.VISUAL_REVIEW.value, Role.TECHNICAL_REVIEW.value},
            set(proof["reviewers"]),
        )
        with self.assertRaises(ProjectRoleError) as missing:
            _review_coverage_proof(stage, complete[:1])
        self.assertEqual("MISSING_STAGE_REVIEW", missing.exception.code)
        self.assertEqual(
            (False, True),
            _stage_evidence_needs(
                {
                    "depth": "full",
                    "evidence_contract": {"capture": False, "technical": True},
                }
            ),
        )

    def test_two_projects_cannot_overlap_execution_scene_write(self) -> None:
        lock = threading.Lock()
        active = {"guard": threading.Lock(), "count": 0, "maximum": 0}
        first_entered = threading.Event()
        second_entered = threading.Event()
        first_release = threading.Event()
        second_release = threading.Event()
        first = _OwnedExecutionProbe(
            client=_Client(), registry=self.registry, scene_write_lock=lock,
            allowed_evidence_roots=(Path(self.temp.name),), total_timeout_seconds=2,
            entered=first_entered, release=first_release, active=active,
        )
        second = _OwnedExecutionProbe(
            client=_Client(), registry=self.registry, scene_write_lock=lock,
            allowed_evidence_roots=(Path(self.temp.name),), total_timeout_seconds=2,
            entered=second_entered, release=second_release, active=active,
        )
        state = replace(self.state, status=ProjectStatus.EXECUTING)
        threads = [
            threading.Thread(target=first.execute, args=(state, ProjectAction("start_execution", {}))),
            threading.Thread(target=second.execute, args=(state, ProjectAction("start_execution", {}))),
        ]
        threads[0].start()
        self.assertTrue(first_entered.wait(0.5))
        threads[1].start()
        self.assertFalse(second_entered.wait(0.05))
        first_release.set()
        self.assertTrue(second_entered.wait(0.5))
        second_release.set()
        for thread in threads:
            thread.join(1.0)
        self.assertEqual(1, active["maximum"])

    def test_all_read_only_role_turns_are_not_serialized_by_scene_write_lock(self) -> None:
        lock = threading.Lock()
        lock.acquire()
        try:
            for role in (
                Role.SUPERVISOR,
                Role.PLANNING,
                Role.VISUAL_REVIEW,
                Role.TECHNICAL_REVIEW,
            ):
                with self.subTest(role=role):
                    client = _Client((
                        {"schema": "hia-project-start/1", "route": "answered", "reply": "ok"},
                    ))
                    started = time.monotonic()
                    self.executor(client, lock=lock)._run_structured(
                        self.state,
                        role,
                        {"schema": "hia-project-role-request/1", "action": "probe"},
                        "hia-project-start/1",
                        time.monotonic() + 1.0,
                    )
                    self.assertLess(time.monotonic() - started, 0.5)
        finally:
            lock.release()


if __name__ == "__main__":
    unittest.main()
