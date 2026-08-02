from __future__ import annotations

from dataclasses import replace
import unittest

from services.bridge.hia_bridge.project_contracts import (
    ProjectState,
    Requirement,
    RequirementStatus,
    Role,
    authoritative_task_identity,
)
from services.bridge.hia_bridge.project_guidance import (
    RequirementDelta,
    mark_guidance_consumed,
    pending_guidance,
    publish_guidance,
    validate_requirement_coverage,
)


def _state() -> ProjectState:
    task_id, digest = authoritative_task_identity("build a detailed cabin")
    return ProjectState(
        project_id="project-1",
        goal_thread_id="supervisor",
        authoritative_task_id=task_id,
        authoritative_task_sha256=digest,
        requirements=(
            Requirement("REQ-structure", "structure"),
            Requirement("REQ-material", "material"),
            Requirement("REQ-animation", "output"),
        ),
    )


class ProjectGuidanceTests(unittest.TestCase):
    def test_project_and_role_guidance_route_without_title_guessing(self) -> None:
        state = publish_guidance(_state(), "把屋顶改为金属")
        state = publish_guidance(
            state, "先核对屋檐悬挑", target_role=Role.TECHNICAL_REVIEW
        )
        self.assertEqual(2, len(pending_guidance(state, Role.TECHNICAL_REVIEW)))
        self.assertEqual(1, len(pending_guidance(state, Role.EXECUTION)))

    def test_late_guidance_must_be_consumed_before_role_continues(self) -> None:
        state = publish_guidance(_state(), "第一条")
        first = pending_guidance(state, Role.PLANNING)
        state = mark_guidance_consumed(
            state, Role.PLANNING, [item.guidance_id for item in first]
        )
        state = publish_guidance(state, "后来取消动画")
        with self.assertRaisesRegex(ValueError, "missing"):
            mark_guidance_consumed(state, Role.PLANNING, [])
        latest = pending_guidance(state, Role.PLANNING)
        self.assertEqual([2], [item.revision for item in latest])

    def test_user_can_remove_stage_requirement_and_shorten_blueprint(self) -> None:
        state = publish_guidance(
            replace(_state(), blueprint_revision=1, authorized_blueprint_revision=1),
            "取消动画阶段",
            requirement_delta=RequirementDelta(remove=("REQ-animation",)),
        )
        animation = next(
            item for item in state.requirements if item.requirement_id == "REQ-animation"
        )
        self.assertEqual(RequirementStatus.REMOVED_BY_USER, animation.status)
        self.assertTrue(state.plan_stale)
        self.assertEqual(["REQ-animation"], state.guidance[-1].requirement_delta["remove"])
        validate_requirement_coverage(
            state.requirements, ("REQ-structure", "REQ-material")
        )

    def test_user_can_supersede_old_requirement_with_smaller_scope(self) -> None:
        replacement = Requirement("REQ-simple-material", "material")
        state = publish_guidance(
            _state(),
            "不做复杂风化，只做基础木材",
            requirement_delta=RequirementDelta(
                add=(replacement,),
                supersede={"REQ-material": "REQ-simple-material"},
            ),
        )
        validate_requirement_coverage(
            state.requirements,
            ("REQ-structure", "REQ-animation", "REQ-simple-material"),
        )
        old = next(item for item in state.requirements if item.requirement_id == "REQ-material")
        self.assertEqual(RequirementStatus.SUPERSEDED_BY_USER, old.status)

    def test_coverage_checks_ids_not_text_length(self) -> None:
        validate_requirement_coverage(
            _state().requirements, ("REQ-structure", "REQ-material", "REQ-animation")
        )
        with self.assertRaisesRegex(ValueError, "missing"):
            validate_requirement_coverage(_state().requirements, ("REQ-structure",))

    def test_plain_guidance_never_invalidates_the_blueprint(self) -> None:
        state = replace(
            _state(), blueprint_revision=1, authorized_blueprint_revision=1
        )
        updated = publish_guidance(state, "make the next explanation shorter")
        self.assertFalse(updated.plan_stale)
        self.assertIsNone(updated.guidance[-1].requirement_delta)

    def test_explicit_replan_invalidates_blueprint_without_fake_requirement(self) -> None:
        state = replace(
            _state(), blueprint_revision=1, authorized_blueprint_revision=1
        )
        text = "改成三层钢结构并重新安排所有阶段"
        updated = publish_guidance(state, text, force_replan=True)
        self.assertTrue(updated.plan_stale)
        self.assertEqual(text, updated.guidance[-1].text)
        self.assertTrue(updated.guidance[-1].force_replan)
        self.assertIsNone(updated.guidance[-1].requirement_delta)


if __name__ == "__main__":
    unittest.main()
