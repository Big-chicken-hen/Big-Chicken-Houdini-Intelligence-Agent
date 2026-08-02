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
    validate_observable_role_response,
    validate_role_permissions,
)
from services.bridge.hia_bridge.project_thread_factory import ROLE_INSTRUCTIONS, ProjectThreadFactory
from tests.unit.project_test_support import observable_thread_response, server_transports


class _Client:
    def __init__(self, *, fail_role: Role | None = None) -> None:
        self.fail_role = fail_role
        self.calls: list[tuple[str, dict]] = []

    def request(self, method: str, params: dict):
        self.calls.append((method, dict(params)))
        if method != "thread/start":
            raise AssertionError(f"unexpected RPC: {method}")
        role = Role(params["threadSource"].rsplit("/", 1)[-1])
        if role is self.fail_role:
            raise RuntimeError("injected start failure")
        return observable_thread_response(params, f"thread-{role.value}")


def _state() -> ProjectState:
    task_id, digest = authoritative_task_identity("build a Houdini scene")
    return ProjectState(
        project_id="project-1",
        authoritative_task_id=task_id,
        authoritative_task_sha256=digest,
        status=ProjectStatus.PLANNING,
    )


class ProjectPermissionTests(unittest.TestCase):
    def test_role_instructions_forbid_internal_project_subagents(self) -> None:
        for role, instruction in ROLE_INSTRUCTIONS.items():
            with self.subTest(role=role):
                self.assertIn("hia-project-role-request/1", instruction)
                self.assertIn("five native project-role Threads", instruction)
                self.assertIn("do not call update_goal", instruction)
                self.assertNotIn("spawn_agent", instruction)
                self.assertNotIn("collabAgentToolCall", instruction)

    def test_non_execution_is_read_only_and_execution_is_workspace_write(self) -> None:
        transports = server_transports()
        for role in Role:
            with self.subTest(role=role):
                profile = permission_profile(role, "hia_mcp_v2", transports)
                writer = role is Role.EXECUTION
                self.assertEqual(writer, profile.scene_write)
                self.assertEqual("workspace-write" if writer else "read-only", profile.sandbox)
                self.assertEqual("on-request" if writer else "never", profile.approval_policy)
                self.assertEqual(writer, profile.config[HIA_SERVER_KEYS[0]])
                self.assertFalse(profile.config[HIA_SERVER_KEYS[1]])

    def test_only_selected_backend_transport_is_required(self) -> None:
        transports = server_transports()
        transports.pop("houdini_intelligence")
        profile = permission_profile(Role.EXECUTION, "hia_mcp_v2", transports)
        self.assertTrue(profile.config["mcp_servers.hia_mcp_v2.required"])
        self.assertFalse(profile.config["mcp_servers.houdini_intelligence.required"])

        with self.assertRaisesRegex(ValueError, "transport is missing"):
            permission_profile(Role.EXECUTION, "houdini_intelligence", transports)

    def test_observable_permissions_fail_closed_on_role_escalation(self) -> None:
        source = "hia-project/p1/visual_review"
        params = {
            "threadSource": source,
            "sandbox": "read-only",
            "approvalPolicy": "never",
            "model": "gpt-test",
        }
        descriptor = observable_thread_response(params, "thread-visual")
        validate_observable_role_response(
            Role.VISUAL_REVIEW,
            descriptor,
            expected_source=source,
            expected_model="gpt-test",
        )
        descriptor["sandbox"] = {"type": "workspaceWrite"}
        with self.assertRaisesRegex(ValueError, "sandbox permission drift"):
            validate_observable_role_response(
                Role.VISUAL_REVIEW,
                descriptor,
                expected_source=source,
                expected_model="gpt-test",
            )

    def test_config_validation_rejects_unexpected_or_enabled_alias(self) -> None:
        transports = server_transports()
        profile = permission_profile(Role.PLANNING, "hia_mcp_v2", transports)
        descriptor = {
            "sandbox": profile.sandbox,
            "approvalPolicy": profile.approval_policy,
            "config": dict(profile.config),
        }
        validate_role_permissions(Role.PLANNING, descriptor, "hia_mcp_v2", transports)
        descriptor["config"]["mcp_servers.hia_mcp_v2.enabled"] = True
        with self.assertRaisesRegex(ValueError, "permission drift"):
            validate_role_permissions(Role.PLANNING, descriptor, "hia_mcp_v2", transports)

    def test_five_roles_are_created_directly_without_eligibility_phase(self) -> None:
        client = _Client()
        factory = ProjectThreadFactory(client, Path.cwd(), "hia_mcp_v2", server_transports())
        state = factory.start_supervisor(_state(), model="gpt-test")
        state = factory.provision_workers(state)
        require_complete_project_roles(state.roles)
        self.assertEqual(set(Role), set(state.roles))
        self.assertEqual(5, len(client.calls))
        self.assertEqual(
            [
                Role.SUPERVISOR,
                Role.PLANNING,
                Role.EXECUTION,
                Role.VISUAL_REVIEW,
                Role.TECHNICAL_REVIEW,
            ],
            [Role(params["threadSource"].rsplit("/", 1)[-1]) for _, params in client.calls],
        )

    def test_partial_creation_failure_never_deletes_visible_threads(self) -> None:
        client = _Client(fail_role=Role.EXECUTION)
        factory = ProjectThreadFactory(client, Path.cwd(), "hia_mcp_v2", server_transports())
        state = factory.start_supervisor(_state())
        with self.assertRaisesRegex(RuntimeError, "injected start failure"):
            factory.provision_workers(state)
        self.assertFalse(any(method == "thread/delete" for method, _ in client.calls))


if __name__ == "__main__":
    unittest.main()
