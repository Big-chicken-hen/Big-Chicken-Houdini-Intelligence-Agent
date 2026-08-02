from __future__ import annotations

from pathlib import Path
import unittest

from services.bridge.hia_bridge.project_contracts import (
    ProjectState,
    ProjectStatus,
    Role,
    authoritative_task_identity,
)
from services.bridge.hia_bridge.project_permissions import (
    HIA_SERVER_KEYS,
    permission_profile,
    require_complete_project_roles,
    validate_role_permissions,
)
from services.bridge.hia_bridge.project_thread_factory import ProjectThreadFactory


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def request(self, method: str, params: dict):
        self.calls.append((method, params))
        role = params["threadSource"].rsplit("/", 1)[-1]
        return {
            "thread": {"id": f"thread-{role}"},
            "model": params.get("model", "gpt-test"),
            "serviceTier": params.get("serviceTier"),
        }


class FailingClient(FakeClient):
    def request(self, method: str, params: dict):
        if method == "thread/start" and params["threadSource"].endswith("/execution"):
            raise RuntimeError("injected start failure")
        if method == "thread/delete":
            self.calls.append((method, params))
            return {"deleted": True}
        return super().request(method, params)


def _state(status: ProjectStatus = ProjectStatus.PROVISIONING) -> ProjectState:
    task_id, digest = authoritative_task_identity("build a Houdini scene")
    return ProjectState(
        project_id="project-1",
        goal_thread_id="thread-supervisor",
        authoritative_task_id=task_id,
        authoritative_task_sha256=digest,
        status=status,
    )


class ProjectPermissionTests(unittest.TestCase):
    def test_only_execution_receives_hia_and_workspace_write(self) -> None:
        for role in Role:
            with self.subTest(role=role):
                profile = permission_profile(role)
                expected = role is Role.EXECUTION
                self.assertEqual(expected, profile.scene_write)
                self.assertEqual("workspace-write" if expected else "read-only", profile.sandbox)
                for key in HIA_SERVER_KEYS:
                    self.assertEqual(expected, profile.config[key])

    def test_permission_validation_rejects_read_only_escalation(self) -> None:
        profile = permission_profile(Role.VISUAL_REVIEW)
        descriptor = {
            "sandbox": profile.sandbox,
            "approvalPolicy": profile.approval_policy,
            "config": dict(profile.config),
        }
        validate_role_permissions(Role.VISUAL_REVIEW, descriptor)
        descriptor["config"][HIA_SERVER_KEYS[0]] = True
        with self.assertRaisesRegex(ValueError, "permission drift"):
            validate_role_permissions(Role.VISUAL_REVIEW, descriptor)

    def test_lazy_provisioning_starts_only_supervisor_before_intake(self) -> None:
        client = FakeClient()
        factory = ProjectThreadFactory(client, Path.cwd())
        state = factory.start_supervisor(_state(), model="gpt-test")
        self.assertEqual({Role.SUPERVISOR}, set(state.roles))
        self.assertEqual("thread-supervisor", state.goal_thread_id)
        self.assertEqual(1, len(client.calls))
        with self.assertRaisesRegex(ValueError, "eligible intake"):
            factory.start_role(state, Role.EXECUTION)

    def test_eligible_intake_provisions_exactly_five_native_threads(self) -> None:
        client = FakeClient()
        factory = ProjectThreadFactory(client, Path.cwd())
        state = factory.start_supervisor(_state(), model="gpt-test")
        state = ProjectState(**{**state.__dict__, "status": ProjectStatus.PROVISIONING_ROLES})
        state = factory.provision_workers(state)
        require_complete_project_roles(state.roles)
        self.assertEqual(set(Role), set(state.roles))
        self.assertEqual(5, len(client.calls))
        for _, params in client.calls:
            role = Role(params["threadSource"].rsplit("/", 1)[-1])
            validate_role_permissions(role, params)

    def test_scene_write_readiness_rejects_partial_role_set(self) -> None:
        client = FakeClient()
        factory = ProjectThreadFactory(client, Path.cwd())
        state = factory.start_supervisor(_state())
        with self.assertRaisesRegex(ValueError, "exactly five"):
            require_complete_project_roles(state.roles)

    def test_partial_worker_creation_is_precisely_rolled_back(self) -> None:
        client = FailingClient()
        factory = ProjectThreadFactory(client, Path.cwd())
        state = factory.start_supervisor(_state())
        state = ProjectState(
            **{**state.__dict__, "status": ProjectStatus.PROVISIONING_ROLES}
        )
        with self.assertRaisesRegex(RuntimeError, "injected start failure"):
            factory.provision_workers(state)
        deletes = [params["threadId"] for method, params in client.calls if method == "thread/delete"]
        self.assertEqual(["thread-planning"], deletes)


if __name__ == "__main__":
    unittest.main()
