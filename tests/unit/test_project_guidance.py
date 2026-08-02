from __future__ import annotations

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
    publish_guidance,
    validate_requirement_coverage,
)


def _state() -> ProjectState:
    task_id, digest = authoritative_task_identity("build a detailed cabin")
    return ProjectState(
        project_id="project-1",
        authoritative_task_id=task_id,
        authoritative_task_sha256=digest,
        requirements=(
            Requirement("REQ-structure", "structure"),
            Requirement("REQ-material", "material"),
            Requirement("REQ-animation", "output"),
        ),
    )


class ProjectGuidanceTests(unittest.TestCase):
    def test_guidance_body_is_not_copied_into_project_state(self) -> None:
        text = "use a lighter roof material"
        updated = publish_guidance(_state(), text, target_role=Role.EXECUTION)
        self.assertEqual(1, updated.guidance_revision)
        serialized = repr(updated)
        self.assertNotIn(text, serialized)
        self.assertFalse(hasattr(updated, "guidance"))
        self.assertFalse(hasattr(updated, "plan_history"))

    def test_each_native_guidance_message_advances_one_revision(self) -> None:
        state = publish_guidance(_state(), "first")
        state = publish_guidance(state, "second", target_role=Role.TECHNICAL_REVIEW)
        self.assertEqual(2, state.guidance_revision)

    def test_user_can_remove_requirement(self) -> None:
        state = publish_guidance(
            _state(),
            "remove animation",
            requirement_delta=RequirementDelta(remove=("REQ-animation",)),
        )
        animation = next(item for item in state.requirements if item.requirement_id == "REQ-animation")
        self.assertEqual(RequirementStatus.REMOVED_BY_USER, animation.status)
        validate_requirement_coverage(state.requirements, ("REQ-structure", "REQ-material"))

    def test_user_can_supersede_requirement(self) -> None:
        state = publish_guidance(
            _state(),
            "replace the material requirement",
            requirement_delta=RequirementDelta(
                add=(Requirement("REQ-simple-material", "material"),),
                supersede={"REQ-material": "REQ-simple-material"},
            ),
        )
        old = next(item for item in state.requirements if item.requirement_id == "REQ-material")
        self.assertEqual(RequirementStatus.SUPERSEDED_BY_USER, old.status)
        self.assertEqual("REQ-simple-material", old.superseded_by)
        validate_requirement_coverage(
            state.requirements,
            ("REQ-structure", "REQ-animation", "REQ-simple-material"),
        )

    def test_coverage_is_exact_not_prose_length(self) -> None:
        validate_requirement_coverage(
            _state().requirements,
            ("REQ-structure", "REQ-material", "REQ-animation"),
        )
        with self.assertRaisesRegex(ValueError, "missing"):
            validate_requirement_coverage(_state().requirements, ("REQ-structure",))
        with self.assertRaisesRegex(ValueError, "unknown"):
            validate_requirement_coverage(
                _state().requirements,
                ("REQ-structure", "REQ-material", "REQ-animation", "REQ-invented"),
            )

    def test_invalid_delta_and_blank_guidance_fail_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "non-empty"):
            publish_guidance(_state(), "   ")
        with self.assertRaisesRegex(ValueError, "does not exist"):
            publish_guidance(
                _state(),
                "remove unknown",
                requirement_delta=RequirementDelta(remove=("REQ-missing",)),
            )


if __name__ == "__main__":
    unittest.main()
