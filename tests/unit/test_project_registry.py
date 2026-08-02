from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from services.bridge.hia_bridge.project_contracts import (
    ProjectState,
    ProjectStatus,
    Requirement,
    Role,
    RoleThread,
    authoritative_task_identity,
)
from services.bridge.hia_bridge.project_guidance import RequirementDelta, publish_guidance
from services.bridge.hia_bridge.project_registry import ProjectRecord, ProjectRegistry


def _record(task: str = "在 Houdini 中建造木屋") -> ProjectRecord:
    task_id, digest = authoritative_task_identity(task)
    return ProjectRecord(
        ProjectState(
            project_id="project-1",
            goal_thread_id="supervisor-thread",
            authoritative_task_id=task_id,
            authoritative_task_sha256=digest,
            roles={
                Role.SUPERVISOR: RoleThread(
                    Role.SUPERVISOR, "supervisor-thread", model="gpt-test"
                )
            },
        ),
        task,
    )


class ProjectRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.registry = ProjectRegistry(Path(self.temp.name) / "projects.json")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_round_trip_preserves_explicit_identity_and_original_text(self) -> None:
        record = _record()
        self.registry.put(record)
        loaded = self.registry.require("project-1")
        self.assertEqual(record, loaded)
        self.assertEqual("supervisor-thread", loaded.state.goal_thread_id)
        self.assertEqual(
            "supervisor-thread",
            loaded.state.roles[Role.SUPERVISOR].thread_id,
        )

    def test_original_text_hash_mismatch_is_rejected(self) -> None:
        record = _record()
        with self.assertRaisesRegex(ValueError, "hash|ID"):
            ProjectRecord(record.state, "different task")

    def test_material_blueprint_revision_and_delta_survive_restart(self) -> None:
        record = _record()
        planned = replace(
            record.state,
            blueprint_revision=2,
            authorized_blueprint_revision=2,
        )
        revised = publish_guidance(
            planned,
            "add a roof",
            requirement_delta=RequirementDelta(
                add=(Requirement("REQ-roof", "structure", source_ref="task"),)
            ),
        )
        self.registry.put(ProjectRecord(revised, record.authoritative_task_text))
        loaded = ProjectRegistry(self.registry.path).require("project-1")
        self.assertTrue(loaded.state.plan_stale)
        self.assertEqual(2, loaded.state.blueprint_revision)
        self.assertEqual(2, loaded.state.authorized_blueprint_revision)
        self.assertEqual(
            "REQ-roof",
            loaded.state.guidance[-1].requirement_delta["add"][0]["requirement_id"],
        )

    def test_optimistic_revision_prevents_lost_updates(self) -> None:
        record = _record()
        self.registry.put(record)
        changed = ProjectRecord(
            replace(record.state, status=ProjectStatus.INTAKE, revision=1),
            record.authoritative_task_text,
        )
        with self.assertRaisesRegex(ValueError, "revision mismatch"):
            self.registry.put(changed, expected_revision=9)
        self.registry.put(changed, expected_revision=0)
        self.assertEqual(ProjectStatus.INTAKE, self.registry.require("project-1").state.status)

    def test_corrupt_schema_fails_closed(self) -> None:
        self.registry.path.parent.mkdir(parents=True, exist_ok=True)
        self.registry.path.write_text('{"schema":"wrong","projects":[]}', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "schema"):
            self.registry.list()


if __name__ == "__main__":
    unittest.main()
