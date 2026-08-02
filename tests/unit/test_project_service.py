from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from services.bridge.hia_bridge.project_contracts import Role
from services.bridge.hia_bridge.project_registry import ProjectRegistry
from services.bridge.hia_bridge.project_service import ProjectTeamService, ProjectTeamSettings


class FakeClient:
    def __init__(self) -> None:
        self.calls = []

    def request(self, method, params):
        self.calls.append((method, dict(params)))
        if method == "thread/start":
            role = params["threadSource"].rsplit("/", 1)[-1]
            return {"thread": {"id": f"thread-{role}"}, "model": params.get("model")}
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
        service = ProjectTeamService(
            client=GoalAndCleanupFailingClient(),
            project_root=root,
            registry=ProjectRegistry(root / "failed-projects.json"),
            settings=self.settings,
        )
        with self.assertRaisesRegex(
            RuntimeError,
            "goal rpc failed.*delete rpc failed",
        ):
            service.start_team_project(task_text="build a scene")


if __name__ == "__main__":
    unittest.main()
