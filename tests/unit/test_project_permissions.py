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
from services.bridge.hia_bridge.project_thread_factory import (
    ROLE_INSTRUCTIONS,
    ProjectThreadFactory,
)
from tests.unit.project_test_support import observable_thread_response, server_transports


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def request(self, method: str, params: dict):
        self.calls.append((method, params))
        role = params["threadSource"].rsplit("/", 1)[-1]
        return observable_thread_response(params, f"thread-{role}")


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
    def test_every_role_prioritizes_generic_project_envelopes_over_goal_context(self) -> None:
        for role, instruction in ROLE_INSTRUCTIONS.items():
            with self.subTest(role=role):
                self.assertIn("hia-project-role-request/1", instruction)
                self.assertIn("response_contract", instruction)
                self.assertIn("do not call update_goal", instruction)
                self.assertIn("semantically", instruction)
                self.assertIn("natural reply", instruction)
                self.assertNotIn("box", instruction.lower())
                self.assertNotIn("house", instruction.lower())
        planning = ROLE_INSTRUCTIONS[Role.PLANNING]
        self.assertIn("direct", planning)
        self.assertIn("focused", planning)
        self.assertIn("full", planning.lower())

    def test_only_execution_receives_hia_and_workspace_write(self) -> None:
        for role in Role:
            with self.subTest(role=role):
                profile = permission_profile(role, "hia_mcp_v2", server_transports())
                expected = role is Role.EXECUTION
                self.assertEqual(expected, profile.scene_write)
                self.assertEqual("workspace-write" if expected else "read-only", profile.sandbox)
                self.assertEqual(expected, profile.config[HIA_SERVER_KEYS[0]])
                self.assertFalse(profile.config[HIA_SERVER_KEYS[1]])
                self.assertNotIn("multi_agent_mode", profile.config)

    def test_execution_enables_only_selected_backend(self) -> None:
        profile = permission_profile(
            Role.EXECUTION,
            "houdini_intelligence",
            server_transports(),
        )
        self.assertFalse(profile.config[HIA_SERVER_KEYS[0]])
        self.assertTrue(profile.config[HIA_SERVER_KEYS[1]])

    def test_complete_transports_are_required_for_both_inventories(self) -> None:
        transports = server_transports()
        del transports["houdini_intelligence"]
        with self.assertRaisesRegex(ValueError, "both exact HIA MCP"):
            permission_profile(Role.SUPERVISOR, "hia_mcp_v2", transports)

    def test_transport_descriptor_requires_a_nonempty_command(self) -> None:
        transports = server_transports()
        transports["hia_mcp_v2"]["command"] = ""
        with self.assertRaisesRegex(ValueError, "transport command is invalid"):
            permission_profile(Role.EXECUTION, "hia_mcp_v2", transports)

    def test_permission_validation_rejects_read_only_escalation(self) -> None:
        transports = server_transports()
        profile = permission_profile(Role.VISUAL_REVIEW, "hia_mcp_v2", transports)
        descriptor = {
            "sandbox": profile.sandbox,
            "approvalPolicy": profile.approval_policy,
            "config": dict(profile.config),
        }
        validate_role_permissions(
            Role.VISUAL_REVIEW,
            descriptor,
            "hia_mcp_v2",
            transports,
        )
        descriptor["config"][HIA_SERVER_KEYS[0]] = True
        with self.assertRaisesRegex(ValueError, "permission drift"):
            validate_role_permissions(
                Role.VISUAL_REVIEW,
                descriptor,
                "hia_mcp_v2",
                transports,
            )

    def test_permission_validation_rejects_unexpected_mcp_alias(self) -> None:
        transports = server_transports()
        profile = permission_profile(Role.PLANNING, "hia_mcp_v2", transports)
        descriptor = {
            "sandbox": profile.sandbox,
            "approvalPolicy": profile.approval_policy,
            "config": {
                **dict(profile.config),
                "mcp_servers.unexpected.enabled": True,
            },
        }
        with self.assertRaisesRegex(ValueError, "unexpected permission keys"):
            validate_role_permissions(
                Role.PLANNING,
                descriptor,
                "hia_mcp_v2",
                transports,
            )

    def test_lazy_provisioning_starts_only_supervisor_before_intake(self) -> None:
        client = FakeClient()
        factory = ProjectThreadFactory(
            client, Path.cwd(), "hia_mcp_v2", server_transports()
        )
        state = factory.start_supervisor(_state(), model="gpt-test")
        self.assertEqual({Role.SUPERVISOR}, set(state.roles))
        self.assertEqual("thread-supervisor", state.goal_thread_id)
        self.assertEqual(1, len(client.calls))
        with self.assertRaisesRegex(ValueError, "eligible intake"):
            factory.start_role(state, Role.EXECUTION)

    def test_eligible_intake_provisions_exactly_five_native_threads(self) -> None:
        client = FakeClient()
        transports = server_transports()
        factory = ProjectThreadFactory(
            client, Path.cwd(), "hia_mcp_v2", transports
        )
        state = factory.start_supervisor(_state(), model="gpt-test")
        state = ProjectState(**{**state.__dict__, "status": ProjectStatus.PROVISIONING_ROLES})
        state = factory.provision_workers(state)
        require_complete_project_roles(state.roles)
        self.assertEqual(set(Role), set(state.roles))
        self.assertEqual(5, len(client.calls))
        for _, params in client.calls:
            role = Role(params["threadSource"].rsplit("/", 1)[-1])
            validate_role_permissions(role, params, "hia_mcp_v2", transports)

    def test_scene_write_readiness_rejects_partial_role_set(self) -> None:
        client = FakeClient()
        factory = ProjectThreadFactory(
            client, Path.cwd(), "hia_mcp_v2", server_transports()
        )
        state = factory.start_supervisor(_state())
        with self.assertRaisesRegex(ValueError, "exactly five"):
            require_complete_project_roles(state.roles)

    def test_partial_worker_creation_is_precisely_rolled_back(self) -> None:
        client = FailingClient()
        factory = ProjectThreadFactory(
            client, Path.cwd(), "hia_mcp_v2", server_transports()
        )
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
