from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from services.bridge.hia_bridge.project_contracts import (
    ProjectState,
    ProjectStatus,
    Requirement,
    Role,
    RoleThread,
    StageState,
    authoritative_task_identity,
)
from services.bridge.hia_bridge.project_guidance import RequirementDelta, publish_guidance
from services.bridge.hia_bridge.project_registry import (
    REGISTRY_SCHEMA,
    ProjectRecord,
    ProjectRegistry,
)


def _record(
    task: str = "build a Houdini cabin",
    *,
    status: ProjectStatus = ProjectStatus.PLANNING,
) -> ProjectRecord:
    task_id, digest = authoritative_task_identity(task)
    roles = {role: RoleThread(role, f"thread-{role.value}") for role in Role}
    return ProjectRecord(
        ProjectState(
            project_id="project-1",
            authoritative_task_id=task_id,
            authoritative_task_sha256=digest,
            status=status,
            roles=roles,
            requirements=(Requirement("REQ-structure", "structure", source_ref="task"),),
            stage=StageState(stage_id="stage-2", ordinal=2),
        ),
        task,
    )


class ProjectRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "projects.json"
        self.registry = ProjectRegistry(self.path)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_disk_entry_contains_exactly_the_seven_authoritative_fields(self) -> None:
        self.registry.put(_record())
        document = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(REGISTRY_SCHEMA, document["schema"])
        self.assertEqual(
            {
                "project_id",
                "authoritative_task",
                "current_status",
                "current_stage_id",
                "role_thread_ids",
                "requirements",
                "guidance_revision",
            },
            set(document["projects"][0]),
        )
        self.assertEqual({role.value for role in Role}, set(document["projects"][0]["role_thread_ids"]))
        serialized = json.dumps(document, ensure_ascii=False)
        for forbidden in (
            "plan_history",
            "repair_progress",
            "pending_effects",
            "host_errors",
            "goal_thread_id",
            "execution_receipt",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_active_projects_restart_stopped_without_replay(self) -> None:
        active = (
            ProjectStatus.PLANNING,
            ProjectStatus.EXECUTING,
            ProjectStatus.REVIEWING,
            ProjectStatus.WAITING_USER,
        )
        for status in active:
            with self.subTest(status=status):
                self.registry = ProjectRegistry(self.path)
                self.registry.put(_record(status=status))
                loaded = ProjectRegistry(self.path).require("project-1")
                self.assertEqual(ProjectStatus.STOPPED, loaded.state.status)
                self.assertEqual("stage-2", loaded.state.stage.stage_id)

    def test_terminal_statuses_round_trip_and_runtime_bodies_do_not(self) -> None:
        for status in (ProjectStatus.COMPLETED, ProjectStatus.FAILED, ProjectStatus.STOPPED):
            with self.subTest(status=status):
                self.registry = ProjectRegistry(self.path)
                self.registry.put(_record(status=status))
                loaded = ProjectRegistry(self.path).require("project-1")
                self.assertEqual(status, loaded.state.status)
                self.assertEqual(0, loaded.state.revision)
                self.assertEqual({}, dict(loaded.state.turns))
                self.assertEqual((), loaded.state.stage.latest_evidence_ids)

    def test_guidance_persists_only_revision_and_requirement_delta(self) -> None:
        record = _record()
        revised = publish_guidance(
            record.state,
            "add a roof",
            requirement_delta=RequirementDelta(
                add=(Requirement("REQ-roof", "structure", source_ref="guidance"),)
            ),
        )
        self.registry.put(ProjectRecord(revised, record.authoritative_task_text))
        loaded = ProjectRegistry(self.path).require("project-1")
        self.assertEqual(1, loaded.state.guidance_revision)
        self.assertEqual({"REQ-structure", "REQ-roof"}, {item.requirement_id for item in loaded.state.requirements})
        self.assertNotIn("add a roof", self.path.read_text(encoding="utf-8"))

    def test_optimistic_revision_prevents_lost_updates(self) -> None:
        record = _record()
        self.registry.put(record)
        changed = ProjectRecord(replace(record.state, revision=1), record.authoritative_task_text)
        with self.assertRaisesRegex(ValueError, "revision mismatch"):
            self.registry.put(changed, expected_revision=9)
        self.registry.put(changed, expected_revision=0)

    def test_corrupt_or_legacy_schema_fails_closed_on_load(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text('{"schema":"wrong","projects":[]}', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "legacy schema"):
            ProjectRegistry(self.path)

    def test_unknown_entry_field_fails_closed(self) -> None:
        self.registry.put(_record())
        document = json.loads(self.path.read_text(encoding="utf-8"))
        document["projects"][0]["shadow_plan"] = {"stage": "copied"}
        self.path.write_text(json.dumps(document), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "unsupported fields"):
            ProjectRegistry(self.path)


if __name__ == "__main__":
    unittest.main()
