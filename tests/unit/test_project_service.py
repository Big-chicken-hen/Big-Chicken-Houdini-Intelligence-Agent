from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from services.bridge.hia_bridge.project_contracts import ProjectStatus, Role
from services.bridge.hia_bridge.project_registry import ProjectRegistry
from services.bridge.hia_bridge.project_service import (
    ProjectGuidanceUnavailable,
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
        snapshot = self.service.append_guidance(
            project_id=project_id, thread_id=thread_id, text="保留屋顶"
        )
        self.assertEqual(1, len(self.registry.require(project_id).state.guidance))
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
