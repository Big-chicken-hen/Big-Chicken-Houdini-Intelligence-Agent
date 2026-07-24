from __future__ import annotations

import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).parents[2]
SKILLS_ROOT = REPOSITORY_ROOT / ".agents" / "skills"
VISUAL_SKILL = SKILLS_ROOT / "houdini-visual-research" / "SKILL.md"
RESEARCH_CONTRACT = (
    SKILLS_ROOT
    / "houdini-visual-research"
    / "references"
    / "research-and-sources.md"
)
VALIDATION_CONTRACT = (
    SKILLS_ROOT
    / "houdini-visual-research"
    / "references"
    / "visual-validation.md"
)
REVIEW_SKILL = SKILLS_ROOT / "houdini-artifact-review" / "SKILL.md"
DIAGNOSTICS_DOC = REPOSITORY_ROOT / "docs" / "DIAGNOSTICS.md"


def read_contract(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class HoudiniSkillContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.visual = read_contract(VISUAL_SKILL)
        cls.research = read_contract(RESEARCH_CONTRACT)
        cls.validation = read_contract(VALIDATION_CONTRACT)
        cls.review = read_contract(REVIEW_SKILL)
        cls.diagnostics = read_contract(DIAGNOSTICS_DOC)

    def test_deep_research_is_iterative_multi_source_and_not_count_limited(self) -> None:
        for marker in (
            "complex, unfamiliar, reference-driven, material, rendering, "
            "simulation, animation, version-sensitive, or ShaderToy work",
            "multiple research rounds and sources",
            "do not impose a fixed limit on search rounds or source count",
            "Current SideFX documentation",
            "Original papers, authors, projects",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.research)

    def test_known_direct_operations_do_not_trigger_research(self) -> None:
        self.assertIn(
            "simple successful Box, known direct operation, or single-parameter read",
            self.research,
        )
        self.assertIn(
            "simple, deterministic scene operation such as creating one Box, "
            "reading a parameter, or making one known edit",
            self.visual,
        )

    def test_source_ledger_has_complete_provenance_and_verification_columns(self) -> None:
        self.assertIn(
            "| Title/source | Author/owner | URL/path | Access date | "
            "License/status | Houdini version/build | How used | "
            "Verification status | Verification evidence |",
            self.research,
        )

    def test_only_real_houdini_evidence_can_promote_original_memos(self) -> None:
        for marker in (
            "original short memo",
            ".runtime/cache/research/<thread-or-turn-id>/",
            "reproduced or directly observed in real Houdini",
            "Only a `verified` original memo",
            "formal tracked knowledge index",
            "do not create an index entry",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.research)
        self.assertIn("Verification status", self.review)
        self.assertIn("Verification evidence", self.review)
        self.assertIn("real Houdini build", self.review)

    def test_diagnostics_use_bounded_recovery_and_one_final_report_per_turn(self) -> None:
        for marker in (
            "Intermediate failures are not report triggers by themselves",
            "bounded in-scope",
            "Create exactly one human-readable Markdown report",
            "final meaningful failure",
            "user explicitly reports",
            "rather than creating another file for the Turn",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.validation)
        self.assertIn("attempt only bounded in-scope recovery", self.diagnostics)
        self.assertIn("same single report for that Turn", self.diagnostics)

    def test_contracts_remain_asset_agnostic(self) -> None:
        combined = "\n".join(
            (self.visual, self.research, self.validation, self.review)
        ).casefold()
        for asset_recipe in ("vending machine", "wood cabin", "售货机", "木屋"):
            with self.subTest(asset_recipe=asset_recipe):
                self.assertNotIn(asset_recipe, combined)


if __name__ == "__main__":
    unittest.main()
