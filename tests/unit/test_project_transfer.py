from __future__ import annotations

from dataclasses import replace
import hashlib
import unittest

from services.bridge.hia_bridge.project_contracts import (
    ProjectState,
    Role,
    RoleThread,
    TurnState,
)
from services.bridge.hia_bridge.project_permissions import permission_profile
from services.bridge.hia_bridge.project_transfer import (
    PreparedProjectTransfer,
    ProjectThreadTransfer,
    ProjectTransferError,
)


def state() -> ProjectState:
    roles = {
        role: RoleThread(
            role=role,
            thread_id=f"old-{role.value}",
            model="gpt-5.6-sol",
            effort="high",
            service_tier="priority",
        )
        for role in Role
    }
    return ProjectState(
        project_id="project-1",
        goal_thread_id="old-supervisor",
        authoritative_task_id="task-1",
        authoritative_task_sha256=hashlib.sha256(b"task").hexdigest(),
        roles=roles,
    )


class FakeClient:
    def __init__(self, project: ProjectState) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.threads: dict[str, dict] = {}
        self.goals: dict[str, dict | None] = {}
        self.delete_failures: set[str] = set()
        for role, binding in project.roles.items():
            source = f"hia-project/{project.project_id}/{role.value}"
            self.threads[binding.thread_id] = {
                "id": binding.thread_id,
                "threadSource": source,
                "source": "appServer",
                "status": {"type": "idle"},
                "turns": [{"id": f"turn-{role.value}", "status": "completed"}],
            }
            self.goals[binding.thread_id] = (
                {"status": "active", "objective": "build", "tokenBudget": None}
                if role is Role.SUPERVISOR
                else None
            )

    def request(self, method: str, params: dict):
        self.calls.append((method, dict(params)))
        if method == "thread/read":
            return {"thread": dict(self.threads[params["threadId"]])}
        if method == "thread/fork":
            old = self.threads[params["threadId"]]
            new_id = f"new-{params['threadId']}"
            self.threads[new_id] = {
                **old,
                "id": new_id,
                "threadSource": params["threadSource"],
                "forkedFromId": params["threadId"],
            }
            self.goals[new_id] = self.goals[params["threadId"]]
            return self._fork_response(new_id, params)
        if method == "thread/goal/get":
            return {"goal": self.goals[params["threadId"]]}
        if method == "thread/delete":
            thread_id = params["threadId"]
            if thread_id in self.delete_failures:
                raise RuntimeError(f"delete failed: {thread_id}")
            self.threads.pop(thread_id, None)
            self.goals.pop(thread_id, None)
            return {"deleted": True}
        raise AssertionError(method)

    @staticmethod
    def _fork_response(thread_id: str, params: dict) -> dict:
        return {
            "thread": {"id": thread_id},
            "sandbox": params["sandbox"],
            "approvalPolicy": params["approvalPolicy"],
            "config": dict(params["config"]),
            "model": params.get("model"),
            "reasoningEffort": "high",
            "serviceTier": params.get("serviceTier"),
        }


class ProjectTransferTests(unittest.TestCase):
    def test_disabled_never_forks_or_deletes(self) -> None:
        project = state()
        client = FakeClient(project)
        transfer = ProjectThreadTransfer(client, enabled=False)

        with self.assertRaisesRegex(ProjectTransferError, "disabled"):
            transfer.prepare(project, Role.PLANNING)

        self.assertEqual([], client.calls)

    def test_prepare_verifies_context_source_model_permissions_and_goal(self) -> None:
        project = state()
        client = FakeClient(project)
        transfer = ProjectThreadTransfer(client, enabled=True)

        prepared = transfer.prepare(project, Role.EXECUTION)

        self.assertEqual("old-execution", prepared.old_thread_id)
        self.assertEqual("new-old-execution", prepared.new_thread_id)
        fork = next(params for method, params in client.calls if method == "thread/fork")
        profile = permission_profile(Role.EXECUTION)
        self.assertEqual(profile.sandbox, fork["sandbox"])
        self.assertEqual(dict(profile.config), fork["config"])
        self.assertEqual("hia-project/project-1/execution", fork["threadSource"])
        self.assertNotIn(
            ("thread/delete", {"threadId": "old-execution"}), client.calls
        )

    def test_verification_failure_deletes_only_replacement_and_keeps_old(self) -> None:
        project = state()
        client = FakeClient(project)
        original = client.request

        def bad_context(method: str, params: dict):
            result = original(method, params)
            if method == "thread/fork":
                client.threads[result["thread"]["id"]]["turns"] = []
            return result

        client.request = bad_context  # type: ignore[method-assign]
        transfer = ProjectThreadTransfer(client, enabled=True)

        with self.assertRaisesRegex(ProjectTransferError, "context"):
            transfer.prepare(project, Role.VISUAL_REVIEW)

        self.assertIn("old-visual_review", client.threads)
        self.assertNotIn("new-old-visual_review", client.threads)

    def test_permission_or_model_drift_rejects_and_cleans_replacement(self) -> None:
        project = state()
        client = FakeClient(project)
        original = client.request

        def bad_profile(method: str, params: dict):
            result = original(method, params)
            if method == "thread/fork":
                result["sandbox"] = "read-only"
            return result

        client.request = bad_profile  # type: ignore[method-assign]
        transfer = ProjectThreadTransfer(client, enabled=True)

        with self.assertRaisesRegex(ProjectTransferError, "permission drift"):
            transfer.prepare(project, Role.EXECUTION)

        self.assertIn("old-execution", client.threads)
        self.assertNotIn("new-old-execution", client.threads)

    def test_model_drift_rejects_and_cleans_replacement(self) -> None:
        project = state()
        client = FakeClient(project)
        original = client.request

        def bad_model(method: str, params: dict):
            result = original(method, params)
            if method == "thread/fork":
                result["model"] = "different-model"
            return result

        client.request = bad_model  # type: ignore[method-assign]
        transfer = ProjectThreadTransfer(client, enabled=True)

        with self.assertRaisesRegex(ProjectTransferError, "model changed"):
            transfer.prepare(project, Role.PLANNING)

        self.assertIn("old-planning", client.threads)
        self.assertNotIn("new-old-planning", client.threads)

    def test_rejects_native_subagent_without_forking(self) -> None:
        project = state()
        client = FakeClient(project)
        client.threads["old-planning"]["nativeSubagent"] = True
        transfer = ProjectThreadTransfer(client, enabled=True)

        with self.assertRaisesRegex(ProjectTransferError, "subagent"):
            transfer.prepare(project, Role.PLANNING)

        self.assertFalse(any(method == "thread/fork" for method, _ in client.calls))

    def test_rejects_wrong_project_role_source_without_forking(self) -> None:
        project = state()
        client = FakeClient(project)
        client.threads["old-planning"]["threadSource"] = (
            "hia-project/project-2/planning"
        )
        transfer = ProjectThreadTransfer(client, enabled=True)

        with self.assertRaisesRegex(ProjectTransferError, "exact native"):
            transfer.prepare(project, Role.PLANNING)

        self.assertFalse(any(method == "thread/fork" for method, _ in client.calls))

    def test_commit_persists_new_identity_before_precise_old_delete(self) -> None:
        project = state()
        client = FakeClient(project)
        transfer = ProjectThreadTransfer(client, enabled=True)
        prepared = transfer.prepare(project, Role.TECHNICAL_REVIEW)
        order: list[str] = []

        def persist(updated: ProjectState) -> None:
            order.append("persist")
            self.assertEqual(
                prepared.new_thread_id,
                updated.roles[Role.TECHNICAL_REVIEW].thread_id,
            )

        original = client.request

        def observe_delete(method: str, params: dict):
            if method == "thread/delete":
                order.append(f"delete:{params['threadId']}")
            return original(method, params)

        client.request = observe_delete  # type: ignore[method-assign]
        result = transfer.commit(project, prepared, persist)

        self.assertEqual(
            ["persist", "delete:old-technical_review"], order
        )
        self.assertTrue(result.old_thread_deleted)
        self.assertIsNone(result.cleanup_error)

    def test_persist_failure_rolls_back_new_and_keeps_old_identity(self) -> None:
        project = state()
        client = FakeClient(project)
        transfer = ProjectThreadTransfer(client, enabled=True)
        prepared = transfer.prepare(project, Role.PLANNING)

        def fail(_updated: ProjectState) -> None:
            raise RuntimeError("registry unavailable")

        with self.assertRaisesRegex(ProjectTransferError, "old Thread retained"):
            transfer.commit(project, prepared, fail)

        self.assertIn("old-planning", client.threads)
        self.assertNotIn(prepared.new_thread_id, client.threads)

    def test_old_delete_failure_reports_cleanup_without_corrupting_new_state(self) -> None:
        project = state()
        client = FakeClient(project)
        transfer = ProjectThreadTransfer(client, enabled=True)
        prepared = transfer.prepare(project, Role.EXECUTION)
        client.delete_failures.add(prepared.old_thread_id)
        persisted: list[ProjectState] = []

        result = transfer.commit(project, prepared, persisted.append)

        self.assertEqual(prepared.new_thread_id, result.state.roles[Role.EXECUTION].thread_id)
        self.assertEqual(result.state, persisted[0])
        self.assertFalse(result.old_thread_deleted)
        self.assertIn("delete failed", result.cleanup_error or "")
        self.assertIn(prepared.old_thread_id, client.threads)
        self.assertIn(prepared.new_thread_id, client.threads)

    def test_explicit_rollback_never_deletes_old_thread(self) -> None:
        project = state()
        client = FakeClient(project)
        transfer = ProjectThreadTransfer(client, enabled=True)
        prepared = transfer.prepare(project, Role.PLANNING)

        error = transfer.rollback(prepared)

        self.assertIsNone(error)
        self.assertIn(prepared.old_thread_id, client.threads)
        self.assertNotIn(prepared.new_thread_id, client.threads)

    def test_restart_descriptor_is_reverified_without_another_fork(self) -> None:
        project = state()
        client = FakeClient(project)
        transfer = ProjectThreadTransfer(client, enabled=True)
        prepared = transfer.prepare(project, Role.SUPERVISOR)
        descriptor = prepared.restart_descriptor()
        calls_before = sum(method == "thread/fork" for method, _ in client.calls)

        restored = transfer.restore(project, descriptor)

        self.assertEqual(prepared, restored)
        self.assertEqual(
            calls_before,
            sum(method == "thread/fork" for method, _ in client.calls),
        )

    def test_restart_descriptor_rejects_changed_context(self) -> None:
        project = state()
        client = FakeClient(project)
        transfer = ProjectThreadTransfer(client, enabled=True)
        prepared = transfer.prepare(project, Role.PLANNING)
        client.threads[prepared.new_thread_id]["turns"] = []

        with self.assertRaisesRegex(ProjectTransferError, "context"):
            transfer.restore(project, prepared.restart_descriptor())

    def test_active_role_turn_is_not_migrated(self) -> None:
        project = state()
        project = replace(
            project,
            turns={
                Role.EXECUTION: TurnState(
                    role=Role.EXECUTION,
                    thread_id="old-execution",
                    turn_id="turn-active",
                    active=True,
                )
            },
        )
        client = FakeClient(project)
        transfer = ProjectThreadTransfer(client, enabled=True)

        with self.assertRaisesRegex(ProjectTransferError, "active"):
            transfer.prepare(project, Role.EXECUTION)

        self.assertEqual([], client.calls)


if __name__ == "__main__":
    unittest.main()
