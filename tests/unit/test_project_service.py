from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from services.bridge.hia_bridge.project_contracts import ProjectStatus, Requirement, Role
from services.bridge.hia_bridge.project_guidance import RequirementDelta
from services.bridge.hia_bridge.project_registry import ProjectRegistry
from services.bridge.hia_bridge.project_service import (
    ProjectGuidanceUnavailable,
    ProjectRuntimeSelectionError,
    ProjectTeamService,
    ProjectTeamSettings,
)
from services.bridge.hia_bridge.project_thread_factory import ProjectThreadFactory
from tests.unit.project_test_support import observable_thread_response, server_transports


def _factory(client, root: Path) -> ProjectThreadFactory:
    return ProjectThreadFactory(
        client,
        root,
        "hia_mcp_v2",
        server_transports(),
    )


def _model_catalog():
    return {
        "models": [
            {
                "model": "gpt-next",
                "isDefault": True,
                "inputModalities": ["text", "image"],
                "supportedReasoningEfforts": [
                    {"reasoningEffort": "low", "description": "fast"},
                    {"reasoningEffort": "high", "description": "deep"},
                ],
                "defaultReasoningEffort": "high",
                "serviceTiers": [
                    {"id": "priority", "name": "Priority", "description": "fast"}
                ],
                "defaultServiceTier": "priority",
            },
            {
                "model": "audio-only",
                "isDefault": False,
                "inputModalities": ["audio"],
                "supportedReasoningEfforts": [],
                "serviceTiers": [],
            },
        ]
    }


class FakeClient:
    def __init__(self) -> None:
        self.calls = []

    def request(self, method, params):
        self.calls.append((method, dict(params)))
        if method == "thread/start":
            role = params["threadSource"].rsplit("/", 1)[-1]
            return observable_thread_response(params, f"thread-{role}")
        if method == "thread/goal/set":
            return {
                "goal": {
                    "threadId": params["threadId"],
                    "status": params["status"],
                    "objective": params["objective"],
                    "tokenBudget": params["tokenBudget"],
                }
            }
        if method == "thread/delete":
            return {"deleted": True}
        raise AssertionError(method)


class GoalAndCleanupFailingClient(FakeClient):
    def request(self, method, params):
        if method == "thread/goal/set":
            raise RuntimeError("goal rpc failed")
        if method == "thread/delete":
            raise RuntimeError("delete rpc failed")
        return super().request(method, params)


class FakeWorkflow:
    def __init__(self) -> None:
        self.started = []
        self.stopped = []
        self.resumed = []

    def start(self, project_id):
        self.started.append(project_id)
        return True

    def stop(self, project_id):
        self.stopped.append(project_id)
        return False

    def resume(self, project_id):
        self.resumed.append(project_id)
        return True


class ProjectServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.client = FakeClient()
        self.registry = ProjectRegistry(root / "projects.json")
        self.settings = ProjectTeamSettings(root / "settings.json")
        self.service = ProjectTeamService(
            client=self.client,
            project_root=root,
            registry=self.registry,
            settings=self.settings,
            thread_factory=_factory(self.client, root),
            model_catalog=_model_catalog,
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_mode_is_only_single_or_team(self) -> None:
        self.assertEqual("single", self.service.route(None))
        self.service.set_mode("team")
        self.assertEqual("team", self.service.route(None))
        self.assertEqual("single", self.service.route("single"))
        with self.assertRaises(ValueError):
            self.service.set_mode("default")

    def test_start_returns_after_supervisor_and_goal_ack_only(self) -> None:
        result = self.service.start_team_project(task_text="建造木屋", model="gpt-test")
        self.assertEqual("team", result["routing"])
        self.assertEqual("start_intake", result["pending_effect"])
        project = self.registry.require(result["project_id"])
        self.assertEqual({Role.SUPERVISOR}, set(project.state.roles))
        self.assertEqual(2, len(self.client.calls))
        self.assertEqual("thread/start", self.client.calls[0][0])
        self.assertEqual("thread/goal/set", self.client.calls[1][0])

    def test_role_identity_is_resolved_only_from_persisted_registry_membership(self) -> None:
        result = self.service.start_team_project(task_text="建造木屋")
        expected = (result["project_id"], Role.SUPERVISOR)
        self.assertEqual(
            expected,
            self.service.role_identity_for_thread(result["root_thread_id"]),
        )
        self.assertIsNone(self.service.role_identity_for_thread("thread-unrelated"))

        # Reconstructing the service proves that the decision is not based on
        # an in-memory title, cwd, model, or previously observed request.
        reconstructed = ProjectTeamService(
            client=self.client,
            project_root=Path(self.temp.name),
            registry=self.registry,
            settings=self.settings,
            thread_factory=_factory(self.client, Path(self.temp.name)),
        )
        self.assertEqual(
            expected,
            reconstructed.role_identity_for_thread(result["root_thread_id"]),
        )

    def test_role_guidance_and_runtime_use_explicit_membership(self) -> None:
        result = self.service.start_team_project(task_text="建造木屋")
        project_id = result["project_id"]
        thread_id = result["root_thread_id"]
        calls_before_guidance = list(self.client.calls)
        snapshot = self.service.append_guidance(
            project_id=project_id, thread_id=thread_id, text="保留屋顶"
        )
        record = self.registry.require(project_id)
        self.assertEqual(1, len(record.state.guidance))
        self.assertEqual(calls_before_guidance, self.client.calls)
        snapshot = self.service.set_role_runtime(
            project_id=project_id,
            thread_id=thread_id,
            model="gpt-next",
            effort="high",
            service_tier="priority",
        )
        role = snapshot["projects"][0]["threads"][0]
        self.assertEqual("gpt-next", role["model"])
        with self.assertRaisesRegex(ValueError, "explicit member"):
            self.service.append_guidance(
                project_id=project_id, thread_id="guessed", text="x"
            )

    def test_force_replan_keeps_original_text_and_invalidates_existing_plan(self) -> None:
        result = self.service.start_team_project(
            task_text="建造一栋克制写实的近未来住宅\n后续正文不能进入 Goal 标题"
        )
        project_id = result["project_id"]
        record = self.registry.require(project_id)
        self.registry.put(
            replace(
                record,
                state=replace(
                    record.state,
                    blueprint_revision=1,
                    authorized_blueprint_revision=1,
                    revision=record.state.revision + 1,
                ),
            ),
            expected_revision=record.state.revision,
        )
        text = "改成三层钢结构并重新安排所有阶段"
        self.service.append_guidance(
            project_id=project_id,
            thread_id=result["root_thread_id"],
            text=text,
            force_replan=True,
        )
        state = self.registry.require(project_id).state
        self.assertTrue(state.plan_stale)
        self.assertEqual(text, state.guidance[-1].text)
        self.assertTrue(state.guidance[-1].force_replan)
        self.assertIsNone(state.guidance[-1].target_role)

        goal_call = next(
            params
            for method, params in self.client.calls
            if method == "thread/goal/set"
        )
        self.assertTrue(goal_call["objective"].startswith("建造一栋克制写实的近未来住宅 · task-"))
        self.assertNotIn("后续正文", goal_call["objective"])

    def test_role_runtime_rejects_unknown_or_incompatible_catalog_combinations(self) -> None:
        result = self.service.start_team_project(task_text="build")
        arguments = {
            "project_id": result["project_id"],
            "thread_id": result["root_thread_id"],
            "model": "missing",
            "effort": "high",
            "service_tier": "priority",
        }
        with self.assertRaises(ProjectRuntimeSelectionError) as unknown:
            self.service.set_role_runtime(**arguments)
        self.assertEqual("model", unknown.exception.field)
        arguments["model"] = "gpt-next"
        arguments["effort"] = "ultra"
        with self.assertRaises(ProjectRuntimeSelectionError) as effort:
            self.service.set_role_runtime(**arguments)
        self.assertEqual(["low", "high"], effort.exception.allowed)
        arguments["effort"] = "high"
        arguments["service_tier"] = "flex"
        with self.assertRaises(ProjectRuntimeSelectionError) as tier:
            self.service.set_role_runtime(**arguments)
        self.assertEqual("service_tier", tier.exception.field)
        arguments.update(model="audio-only", effort=None, service_tier=None)
        with self.assertRaises(ProjectRuntimeSelectionError) as modality:
            self.service.set_role_runtime(**arguments)
        self.assertEqual("model", modality.exception.field)

    def test_project_snapshot_does_not_duplicate_task_body_into_roles(self) -> None:
        result = self.service.start_team_project(task_text="line one\nlong private body")
        project = result["project_team"]["projects"][0]
        self.assertEqual("line one", project["title"])
        self.assertNotIn("long private body", str(project["threads"]))

    def test_project_attachments_keep_path_hash_and_size_identity(self) -> None:
        attachment = Path(self.temp.name) / "reference.png"
        attachment.write_bytes(b"reference-bytes")
        result = self.service.start_team_project(
            task_text="build from reference",
            local_image_paths=[str(attachment)],
        )
        record = self.registry.require(result["project_id"])
        self.assertEqual(1, len(record.attachments))
        self.assertEqual(str(attachment.resolve()), record.attachments[0].path)
        self.assertEqual(len(b"reference-bytes"), record.attachments[0].size_bytes)
        self.assertEqual(64, len(record.attachments[0].sha256))

    def test_goal_failure_reports_incomplete_precise_cleanup(self) -> None:
        root = Path(self.temp.name)
        client = GoalAndCleanupFailingClient()
        service = ProjectTeamService(
            client=client,
            project_root=root,
            registry=ProjectRegistry(root / "failed-projects.json"),
            settings=self.settings,
            thread_factory=_factory(client, root),
        )
        with self.assertRaisesRegex(
            RuntimeError,
            "goal rpc failed.*delete rpc failed",
        ):
            service.start_team_project(task_text="build a scene")

    def test_workflow_is_started_and_stop_is_routed_by_project_identity(self) -> None:
        root = Path(self.temp.name)
        workflow = FakeWorkflow()
        service = ProjectTeamService(
            client=self.client,
            project_root=root,
            registry=ProjectRegistry(root / "workflow-projects.json"),
            settings=self.settings,
            thread_factory=_factory(self.client, root),
            workflow=workflow,
        )
        result = service.start_team_project(task_text="build a scene")
        project_id = result["project_id"]
        self.assertEqual([project_id], workflow.started)
        service.stop_project(project_id=project_id)
        self.assertEqual([project_id], workflow.stopped)

        record = service._registry.require(project_id)
        attention = replace(
            record.state,
            status=ProjectStatus.NEEDS_ATTENTION,
            resume_status=ProjectStatus.PLANNING,
            pending_effects=(),
            revision=record.state.revision + 1,
        )
        service._registry.put(
            type(record)(attention, record.authoritative_task_text, record.attachments),
            expected_revision=record.state.revision,
        )
        service.continue_project(project_id=project_id)
        self.assertEqual([project_id], workflow.resumed)

    def test_material_guidance_wakes_workflow_but_plain_guidance_does_not(self) -> None:
        root = Path(self.temp.name)
        workflow = FakeWorkflow()
        registry = ProjectRegistry(root / "material-guidance-projects.json")
        service = ProjectTeamService(
            client=self.client,
            project_root=root,
            registry=registry,
            settings=self.settings,
            thread_factory=_factory(self.client, root),
            workflow=workflow,
        )
        result = service.start_team_project(task_text="build a scene")
        project_id = result["project_id"]
        record = registry.require(project_id)
        active = replace(
            record.state,
            status=ProjectStatus.EXECUTING_STAGE,
            pending_effects=(),
            blueprint_revision=1,
            authorized_blueprint_revision=1,
            revision=record.state.revision + 1,
        )
        registry.put(
            type(record)(active, record.authoritative_task_text, record.attachments),
            expected_revision=record.state.revision,
        )
        service.append_guidance(project_id=project_id, text="explain the next step")
        self.assertEqual([project_id], workflow.started)
        service.append_guidance(
            project_id=project_id,
            text="add a roof requirement",
            requirement_delta=RequirementDelta(
                add=(Requirement("REQ-roof", "structure", source_ref="task"),)
            ),
        )
        self.assertEqual([project_id, project_id], workflow.started)
        self.assertTrue(registry.require(project_id).state.plan_stale)

    def test_stop_request_immediately_disables_and_rejects_guidance(self) -> None:
        root = Path(self.temp.name)
        workflow = FakeWorkflow()
        service = ProjectTeamService(
            client=self.client,
            project_root=root,
            registry=ProjectRegistry(root / "stopped-guidance-projects.json"),
            settings=self.settings,
            thread_factory=_factory(self.client, root),
            workflow=workflow,
        )
        result = service.start_team_project(task_text="build a scene")
        project_id = result["project_id"]

        stopped = service.stop_project(project_id=project_id)

        project = stopped["projects"][0]
        self.assertFalse(project["actions"]["append_guidance"])
        self.assertTrue(
            all(
                not role["actions"]["append_guidance"]
                for role in project["threads"]
            )
        )
        with self.assertRaises(ProjectGuidanceUnavailable) as raised:
            service.append_guidance(project_id=project_id, text="too late")
        self.assertFalse(raised.exception.recoverable)


if __name__ == "__main__":
    unittest.main()
