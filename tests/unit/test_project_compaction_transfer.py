from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from services.bridge.hia_bridge.events import EventBuffer
from services.bridge.hia_bridge.main import ProjectRuntime
from services.bridge.hia_bridge.project_contracts import (
    ProjectState,
    Role,
    RoleThread,
    authoritative_task_identity,
)
from services.bridge.hia_bridge.project_permissions import validate_observable_role_response
from services.bridge.hia_bridge.project_registry import ProjectRecord, ProjectRegistry
from services.bridge.hia_bridge.project_transfer import PreparedProjectTransfer, TransferResult


FIXTURES = ROOT / "tests" / "fixtures" / "codex_app_server_0_144_3"
SCHEMAS = ROOT / "schemas" / "codex-app-server" / "0.144.3" / "v2"


class _Service:
    def __init__(self, registry: ProjectRegistry) -> None:
        self.registry = registry

    def role_identity_for_thread(self, thread_id: str):
        for record in self.registry.list():
            for role, binding in record.state.roles.items():
                if binding.thread_id == thread_id:
                    return record.state.project_id, role
        return None

    def snapshot(self):
        return {"schema": "fixture"}


class _Transfer:
    def prepare(self, state: ProjectState, role: Role) -> PreparedProjectTransfer:
        binding = state.roles[role]
        return PreparedProjectTransfer(
            project_id=state.project_id,
            role=role,
            old_thread_id=binding.thread_id,
            new_thread_id=f"new-{binding.thread_id}",
            thread_source=f"hia-project/{state.project_id}/{role.value}",
            model=binding.model,
            effort=binding.effort,
            service_tier=binding.service_tier,
            selected_backend="hia_mcp_v2",
        )

    def commit(self, state, prepared, persist):
        roles = dict(state.roles)
        roles[prepared.role] = RoleThread(
            role=prepared.role,
            thread_id=prepared.new_thread_id,
            model=prepared.model,
            effort=prepared.effort,
            service_tier=prepared.service_tier,
        )
        updated = replace(state, roles=roles, revision=state.revision + 1)
        persist(updated)
        return TransferResult(updated, True)


class ProjectCompactionTransferTests(unittest.TestCase):
    def test_real_response_fixtures_match_pinned_observable_contract(self) -> None:
        start = json.loads((FIXTURES / "thread_start_read_only.json").read_text())
        fork = json.loads((FIXTURES / "thread_fork_execution_ceiling.json").read_text())
        start_schema = json.loads((SCHEMAS / "ThreadStartResponse.json").read_text())
        fork_schema = json.loads((SCHEMAS / "ThreadForkResponse.json").read_text())
        self.assertNotIn("config", start_schema["properties"])
        self.assertNotIn("config", fork_schema["properties"])
        self.assertEqual({"type": "readOnly", "networkAccess": False}, start["sandbox"])
        self.assertEqual({"type": "readOnly", "networkAccess": False}, fork["sandbox"])
        validate_observable_role_response(
            Role.PLANNING,
            start,
            expected_source="hia-project/project-fixture/planning",
            expected_model="gpt-5.6-sol",
        )
        validate_observable_role_response(
            Role.EXECUTION,
            fork,
            expected_source="hia-project/project-fixture/execution",
            expected_model="gpt-5.6-sol",
        )

    def test_third_unique_real_compaction_migrates_and_resets_counter(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            registry = ProjectRegistry(Path(temporary) / "registry.json")
            roles = {
                role: RoleThread(role, f"thread-{role.value}", model="gpt-5.6-sol")
                for role in Role
            }
            task_id, task_hash = authoritative_task_identity("task")
            state = ProjectState(
                project_id="project-1",
                goal_thread_id="thread-supervisor",
                authoritative_task_id=task_id,
                authoritative_task_sha256=task_hash,
                roles=roles,
            )
            registry.put(ProjectRecord(state, "task"))
            events = EventBuffer()
            runtime = ProjectRuntime(
                service=_Service(registry),
                workflow=None,  # type: ignore[arg-type]
                registry=registry,
                runner=None,  # type: ignore[arg-type]
                effect_client=None,  # type: ignore[arg-type]
                thread_factory=None,  # type: ignore[arg-type]
                thread_transfer=_Transfer(),  # type: ignore[arg-type]
                artifacts=None,  # type: ignore[arg-type]
                events=events,
            )

            def compact(turn_id: str) -> None:
                runtime.observe_codex_event(
                    {
                        "type": "codex_notification",
                        "method": "thread/compacted",
                        "params": {
                            "threadId": "thread-planning",
                            "turnId": turn_id,
                        },
                    }
                )

            def compact_item(turn_id: str, item_id: str) -> None:
                runtime.observe_codex_event(
                    {
                        "type": "codex_notification",
                        "method": "item/completed",
                        "params": {
                            "threadId": "thread-planning",
                            "turnId": turn_id,
                            "item": {"id": item_id, "type": "contextCompaction"},
                        },
                    }
                )

            compact_item("turn-1", "compact-a")
            compact("turn-1")
            compact_item("turn-1", "compact-b")
            compact_item("turn-2", "compact-c")
            runtime.observe_state(registry.require("project-1").state)
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                binding = registry.require("project-1").state.roles[Role.PLANNING]
                if binding.thread_id == "new-thread-planning":
                    break
                time.sleep(0.01)
            self.assertEqual("new-thread-planning", binding.thread_id)
            self.assertEqual(0, binding.compaction_count)
            emitted = events.poll(0)["events"]
            self.assertTrue(any(item["type"] == "thread_transferred" for item in emitted))


if __name__ == "__main__":
    unittest.main()
