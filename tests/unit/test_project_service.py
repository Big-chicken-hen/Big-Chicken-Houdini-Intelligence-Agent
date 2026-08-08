from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import threading
import unittest

from services.bridge.hia_bridge.errors import BridgeError
from services.bridge.hia_bridge.project_contracts import (
    ProjectStatus,
    Requirement,
    Role,
    StageState,
)
from services.bridge.hia_bridge.project_effects import CompletedTurn
from services.bridge.hia_bridge.project_guidance import RequirementDelta
from services.bridge.hia_bridge.project_lifecycle import LifecycleEvent, ProjectEvent
from services.bridge.hia_bridge.project_registry import ProjectRecord, ProjectRegistry
from services.bridge.hia_bridge.project_runner import ProjectActionResult, ProjectRunner
from services.bridge.hia_bridge.project_service import (
    PROJECT_TEAM_SCHEMA,
    ProjectGuidanceRecordError,
    ProjectTeamService,
    ProjectTeamSettings,
)
from services.bridge.hia_bridge.project_thread_factory import ProjectThreadFactory
from tests.unit.project_test_support import observable_thread_response, server_transports


class _Client:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.sources: dict[str, str] = {}
        self.active = False
        self.guidance_payload: dict = {
            "schema": "hia-project-guidance-recorded/1",
            "revision": 1,
        }
        self.guidance_wait_entered: threading.Event | None = None
        self.guidance_wait_release: threading.Event | None = None
        self.histories: dict[str, list[dict]] = {}

    def request(self, method: str, params: dict):
        self.calls.append((method, dict(params)))
        if method == "thread/start":
            role = params["threadSource"].rsplit("/", 1)[-1]
            thread_id = f"thread-{role}"
            self.sources[thread_id] = params["threadSource"]
            return observable_thread_response(params, thread_id)
        if method == "turn/start":
            return {"turn": {"id": "guidance-turn"}}
        if method == "thread/read":
            return {
                "thread": {
                    "id": params["threadId"],
                    "threadSource": self.sources[params["threadId"]],
                    "turns": self.histories.get(params["threadId"], []),
                }
            }
        if method == "thread/delete":
            return {}
        raise AssertionError(f"unexpected RPC: {method}")

    def wait_for_turn(self, thread_id: str, turn_id: str, timeout_seconds: float):
        if self.guidance_wait_entered is not None:
            self.guidance_wait_entered.set()
        if self.guidance_wait_release is not None:
            if not self.guidance_wait_release.wait(timeout_seconds):
                raise TimeoutError("test guidance release timed out")
        return CompletedTurn(thread_id, turn_id, "completed", self.guidance_payload)

    def has_active_thread(self, thread_id: str) -> bool:
        return self.active


class _Workflow:
    def __init__(self) -> None:
        self.started: list[str] = []
        self.stopped: list[str] = []
        self.resumed: list[tuple[str, dict[str, object]]] = []
        self.accept_start = True
        self.inflight = False

    def start(self, project_id: str) -> bool:
        self.started.append(project_id)
        return self.accept_start

    def stop(self, project_id: str) -> bool:
        self.stopped.append(project_id)
        return False

    def resume(self, project_id: str, continuation: dict[str, object]) -> bool:
        self.resumed.append((project_id, continuation))
        return True

    def is_inflight(self, project_id: str) -> bool:
        return self.inflight


def _catalog():
    return {
        "models": [
            {
                "model": "gpt-test",
                "isDefault": True,
                "inputModalities": ["text", "image"],
                "supportedReasoningEfforts": [{"reasoningEffort": "high"}],
                "serviceTiers": [{"id": "priority"}],
            }
        ]
    }


def _turn(turn_id: str, *items: dict) -> dict:
    return {"id": turn_id, "items": list(items)}


def _agent(payload: dict) -> dict:
    return {"type": "agentMessage", "text": json.dumps(payload)}


def _request(guidance_revision: int) -> dict:
    return {
        "type": "userMessage",
        "content": [
            {
                "type": "text",
                "text": json.dumps({"guidance_revision": guidance_revision}),
            }
        ],
    }


class ProjectServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.registry = ProjectRegistry(self.root / "projects.json")
        self.settings = ProjectTeamSettings(self.root / "settings.json")
        self.client = _Client()
        self.runner = ProjectRunner(self.registry)
        self.workflow = _Workflow()
        self.factory = ProjectThreadFactory(
            self.client, self.root, "hia_mcp_v2", server_transports()
        )
        self.service = ProjectTeamService(
            client=self.client,
            project_root=self.root,
            registry=self.registry,
            settings=self.settings,
            thread_factory=self.factory,
            runner=self.runner,
            workflow=self.workflow,
            model_catalog=_catalog,
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def start(self) -> str:
        result = self.service.start_team_project(task_text="build a Houdini asset", model="gpt-test")
        return result["project_id"]

    def test_all_runtime_dependencies_are_required(self) -> None:
        with self.assertRaisesRegex(ValueError, "dependencies"):
            ProjectTeamService(
                client=self.client,
                project_root=self.root,
                registry=self.registry,
                settings=self.settings,
                thread_factory=self.factory,
                runner=self.runner,
                workflow=None,
                model_catalog=_catalog,
            )

    def test_settings_missing_defaults_single_but_corruption_fails_closed(self) -> None:
        self.assertEqual("single", self.settings.get())
        path = self.root / "settings.json"
        path.write_text("not-json", encoding="utf-8")
        with self.assertRaises(BridgeError) as raised:
            self.settings.get()
        self.assertEqual("SETTINGS_CORRUPTED", raised.exception.code)

    def test_start_project_creates_exactly_five_roles_without_goal_or_delete(self) -> None:
        result = self.service.start_team_project(task_text="build a Houdini asset", model="gpt-test")
        project_id = result["project_id"]
        record = self.registry.require(project_id)
        self.assertEqual(set(Role), set(record.state.roles))
        self.assertEqual([project_id], self.workflow.started)
        self.assertEqual("start_supervisor", result["next_action"])
        methods = [method for method, _ in self.client.calls]
        self.assertEqual(5, methods.count("thread/start"))
        self.assertNotIn("goal/create", methods)
        self.assertNotIn("goal/update", methods)
        self.assertNotIn("thread/delete", methods)

    def test_rotation_rebind_changes_only_one_project_role_identity(self) -> None:
        project_id = self.start()
        before = self.registry.require(project_id)
        binding = before.state.roles[Role.PLANNING]
        profile = self.service.rotation_profile(binding.thread_id, "turn-3")
        self.assertIsNotNone(profile)
        self.assertEqual("gpt-test", profile.model)
        self.assertTrue(self.service.rotation_is_idle(binding.thread_id))

        self.service.rotation_rebind(binding.thread_id, "thread-planning-rotated")

        after = self.registry.require(project_id)
        self.assertTrue(
            self.service.rotation_readback(
                binding.thread_id,
                "thread-planning-rotated",
            )
        )
        self.assertEqual(
            "thread-planning-rotated",
            after.state.roles[Role.PLANNING].thread_id,
        )
        for role in set(Role) - {Role.PLANNING}:
            self.assertEqual(
                before.state.roles[role],
                after.state.roles[role],
            )
        self.assertEqual(before.authoritative_task_text, after.authoritative_task_text)
        self.assertEqual(before.state.stage, after.state.stage)

    def test_guidance_is_confirmed_in_native_supervisor_history_before_revision_changes(self) -> None:
        project_id = self.start()
        snapshot = self.service.append_guidance(project_id=project_id, text="narrow the next stage")
        self.assertEqual(1, self.registry.require(project_id).state.guidance_revision)
        turn = next(params for method, params in self.client.calls if method == "turn/start")
        envelope = json.loads(turn["input"][0]["text"])
        self.assertEqual("hia-project-guidance/1", envelope["schema"])
        self.assertEqual("narrow the next stage", envelope["text"])
        self.assertEqual("never", turn["approvalPolicy"])
        self.assertEqual("readOnly", turn["sandboxPolicy"]["type"])
        self.assertEqual(PROJECT_TEAM_SCHEMA, snapshot["schema"])
        self.assertNotIn("narrow the next stage", self.registry.path.read_text(encoding="utf-8"))

    def test_busy_or_bad_guidance_confirmation_leaves_registry_unchanged(self) -> None:
        project_id = self.start()
        self.client.active = True
        with self.assertRaises(ProjectGuidanceRecordError) as busy:
            self.service.append_guidance(project_id=project_id, text="first")
        self.assertEqual("PROJECT_GUIDANCE_BUSY", busy.exception.code)
        self.assertEqual(0, self.registry.require(project_id).state.guidance_revision)

        self.client.active = False
        self.client.guidance_payload = {"schema": "wrong", "revision": 1}
        with self.assertRaises(ProjectGuidanceRecordError) as failed:
            self.service.append_guidance(project_id=project_id, text="second")
        self.assertEqual("PROJECT_GUIDANCE_RECORD_FAILED", failed.exception.code)
        self.assertEqual(0, self.registry.require(project_id).state.guidance_revision)

    def test_workflow_commit_during_native_guidance_merges_latest_state_once(self) -> None:
        project_id = self.start()
        entered = threading.Event()
        release = threading.Event()
        self.client.guidance_wait_entered = entered
        self.client.guidance_wait_release = release
        failures: list[BaseException] = []

        def append() -> None:
            try:
                self.service.append_guidance(
                    project_id=project_id,
                    text="apply this to the next explicit stage",
                )
            except BaseException as exc:  # pragma: no cover - asserted below
                failures.append(exc)

        guidance_thread = threading.Thread(target=append)
        guidance_thread.start()
        self.assertTrue(entered.wait(2.0))

        class _StageExecutor:
            def execute(self, state, action):
                return ProjectActionResult(
                    replace(state, stage=StageState(stage_id="stage-after-supervisor")),
                    LifecycleEvent(ProjectEvent.PROJECT_ACCEPTED),
                )

        self.runner.execute_next(project_id, _StageExecutor())
        release.set()
        guidance_thread.join(2.0)
        self.assertFalse(guidance_thread.is_alive())
        self.assertEqual([], failures)
        final = self.registry.require(project_id).state
        self.assertEqual(1, final.guidance_revision)
        self.assertEqual("stage-after-supervisor", final.stage.stage_id)
        self.assertEqual(ProjectStatus.PLANNING, final.status)

    def test_continue_without_current_stage_history_waits_for_user_without_replay(self) -> None:
        project_id = self.start()
        record = self.registry.require(project_id)
        stopped = replace(record.state, status=ProjectStatus.STOPPED, revision=record.state.revision + 1)
        self.registry.put(ProjectRecord(stopped, record.authoritative_task_text), expected_revision=record.state.revision)
        self.service.continue_project(project_id=project_id)
        self.assertEqual([(project_id, {})], self.workflow.resumed)
        self.assertNotIn("turn/start", [method for method, _ in self.client.calls])

    def test_continue_rejects_stale_plan_authorization_and_stage_decision(self) -> None:
        project_id = self.start()
        record = self.registry.require(project_id)
        stopped = replace(
            record.state,
            status=ProjectStatus.STOPPED,
            stage=StageState(stage_id="stage-1"),
            revision=record.state.revision + 1,
        )
        self.registry.put(
            ProjectRecord(stopped, record.authoritative_task_text),
            expected_revision=record.state.revision,
        )
        plan = {"schema": "hia-project-plan/1", "stages": [{"stage_id": "stage-1"}]}
        self.client.histories["thread-planning"] = [
            _turn("plan-turn", _request(0), _agent(plan))
        ]
        project_start = {
            "schema": "hia-project-start/1",
            "route": "planning",
            "reply": "accepted",
        }
        stale_authorization = {
            "schema": "hia-project-authorization/1",
            "authorized": True,
            "planning_thread_id": "thread-planning",
            "planning_turn_id": "older-plan-turn",
            "guidance_revision": 0,
        }
        self.client.histories["thread-supervisor"] = [
            _turn("start-turn", _agent(project_start)),
            _turn("authorization-turn", _agent(stale_authorization)),
        ]
        self.service.continue_project(project_id=project_id)
        self.assertEqual(
            {"command": "request_authorization"},
            self.workflow.resumed[-1][1],
        )

        matching_authorization = {
            **stale_authorization,
            "planning_turn_id": "plan-turn",
        }
        stale_decision = {
            "schema": "hia-project-stage-decision/1",
            "stage_id": "stage-1",
            "execution_thread_id": "thread-execution",
            "execution_turn_id": "older-execution-turn",
            "decision": "repair",
        }
        self.client.histories["thread-supervisor"] = [
            _turn("start-turn", _agent(project_start)),
            _turn("authorization-turn", _agent(matching_authorization)),
            _turn("decision-turn", _agent(stale_decision)),
        ]
        self.client.histories["thread-execution"] = [
            _turn(
                "execution-turn",
                _agent(
                    {
                        "schema": "hia-project-execution/1",
                        "stage_id": "stage-1",
                    }
                ),
            )
        ]
        self.service.continue_project(project_id=project_id)
        self.assertEqual(
            {"command": "start_reviews"},
            self.workflow.resumed[-1][1],
        )

    def test_material_requirement_change_requires_stop_and_invalidates_plan(self) -> None:
        project_id = self.start()
        delta = RequirementDelta(
            add=(Requirement("REQ-new", "structure", source_ref="guidance"),)
        )
        starts_before = len(
            [method for method, _ in self.client.calls if method == "turn/start"]
        )
        with self.assertRaises(BridgeError) as raised:
            self.service.append_guidance(
                project_id=project_id,
                text="add the new requirement",
                requirement_delta=delta,
            )
        self.assertEqual(
            "PROJECT_REQUIREMENT_CHANGE_REQUIRES_STOP", raised.exception.code
        )
        self.assertEqual(
            starts_before,
            len([method for method, _ in self.client.calls if method == "turn/start"]),
        )
        record = self.registry.require(project_id)
        self.assertEqual(0, record.state.guidance_revision)

        stopped = replace(
            record.state,
            status=ProjectStatus.STOPPED,
            revision=record.state.revision + 1,
        )
        self.registry.put(
            ProjectRecord(stopped, record.authoritative_task_text),
            expected_revision=record.state.revision,
        )
        self.service.append_guidance(
            project_id=project_id,
            text="add the new requirement",
            requirement_delta=delta,
        )
        project_start = {
            "schema": "hia-project-start/1",
            "route": "planning",
            "reply": "accepted",
        }
        self.client.histories["thread-supervisor"] = [
            _turn("start-turn", _agent(project_start))
        ]
        self.client.histories["thread-planning"] = [
            _turn(
                "old-plan-turn",
                _request(0),
                _agent(
                    {
                        "schema": "hia-project-plan/1",
                        "stages": [{"stage_id": "stage-1"}],
                    }
                ),
            )
        ]
        self.service.continue_project(project_id=project_id)
        self.assertEqual(
            {"command": "request_plan"},
            self.workflow.resumed[-1][1],
        )

    def test_rejected_workflow_start_removes_only_new_project_resources(self) -> None:
        draft_id = "failed-project"
        draft = (
            self.root
            / ".runtime"
            / "project-attachments"
            / "drafts"
            / draft_id
        )
        draft.mkdir(parents=True)
        source = draft / "reference.png"
        source.write_bytes(b"project-reference")
        self.workflow.accept_start = False
        with self.assertRaises(BridgeError) as raised:
            self.service.start_team_project(
                task_text="build a Houdini asset",
                model="gpt-test",
                local_image_paths=[str(source)],
                attachment_draft_id=draft_id,
            )
        self.assertEqual("PROJECT_WORKFLOW_NOT_STARTED", raised.exception.code)
        project_id = self.workflow.started[-1]
        self.assertEqual((), self.registry.list())
        self.assertFalse(self.runner.has_pending(project_id))
        self.assertFalse(
            (self.root / ".runtime" / "project-attachments" / project_id).exists()
        )
        self.assertEqual(
            5,
            len([method for method, _ in self.client.calls if method == "thread/delete"]),
        )

    def test_image_only_project_owns_its_attachment_without_an_ordinary_thread(self) -> None:
        draft_id = "draft-image-only"
        draft = (
            self.root
            / ".runtime"
            / "project-attachments"
            / "drafts"
            / draft_id
        )
        draft.mkdir(parents=True)
        source = draft / "reference.png"
        source.write_bytes(b"project-reference")
        result = self.service.start_team_project(
            task_text="",
            model="gpt-test",
            local_image_paths=[str(source)],
            attachment_draft_id=draft_id,
        )
        record = self.registry.require(result["project_id"])
        self.assertEqual("", record.authoritative_task_text)
        self.assertEqual(1, len(record.attachments))
        resolved = self.registry.attachment_paths(record, self.root)
        self.assertEqual(1, len(resolved))
        self.assertIn("project-attachments", Path(resolved[0]).parts)
        self.assertNotIn("attachments", Path(resolved[0]).parts)

    def test_snapshot_has_no_project_delete_action(self) -> None:
        project_id = self.start()
        project = next(item for item in self.service.snapshot()["projects"] if item["project_id"] == project_id)
        self.assertNotIn("delete", project["actions"])
        self.assertEqual(5, len(project["threads"]))

    def test_role_runtime_uses_live_catalog_and_changes_only_selected_role(self) -> None:
        project_id = self.start()
        thread_id = self.registry.require(project_id).state.roles[Role.VISUAL_REVIEW].thread_id
        self.service.set_role_runtime(
            project_id=project_id,
            thread_id=thread_id,
            model="gpt-test",
            effort="high",
            service_tier="priority",
        )
        binding = self.registry.require(project_id).state.roles[Role.VISUAL_REVIEW]
        self.assertEqual(("gpt-test", "high", "priority"), (binding.model, binding.effort, binding.service_tier))


if __name__ == "__main__":
    unittest.main()
