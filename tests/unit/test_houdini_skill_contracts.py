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
PROCEDURAL_SKILL = SKILLS_ROOT / "houdini-procedural-modeling" / "SKILL.md"
MATERIAL_SKILL = SKILLS_ROOT / "houdini-material-lookdev" / "SKILL.md"
KNOWLEDGE_MEMORY_CONTRACT = (
    SKILLS_ROOT
    / "houdini-visual-research"
    / "references"
    / "knowledge-and-memory.md"
)
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
        cls.procedural = read_contract(PROCEDURAL_SKILL)
        cls.material = read_contract(MATERIAL_SKILL)
        cls.knowledge_memory = read_contract(KNOWLEDGE_MEMORY_CONTRACT)
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

    def test_complex_tasks_are_local_first_without_weakening_web_research(self) -> None:
        for marker in (
            "one batched `hia_local_help_search`",
            "Local retrieval never replaces deep external research",
            "continue multi-round web research",
            "current SideFX documentation and original sources",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.knowledge_memory)
        self.assertIn(
            "not as a reason to reduce research depth",
            self.research,
        )

    def test_simple_tasks_do_not_retrieve_or_write_memory(self) -> None:
        self.assertIn(
            "A simple primitive, known parameter edit, or simple read does not "
            "trigger local retrieval or a memory write",
            self.knowledge_memory,
        )

    def test_project_memory_keeps_only_durable_reusable_information(self) -> None:
        for marker in (
            "an explicit user preference",
            "a confirmed project decision",
            "a stable asset structure or entry point",
            "a verified failure lesson or version-compatibility fact",
            "a reusable workflow that has evidence",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.knowledge_memory)

    def test_project_memory_rejects_secrets_chat_and_transient_noise(self) -> None:
        for marker in (
            "temporary progress",
            "one-off error noise",
            "full conversations",
            "access tokens",
            "authorization headers",
            "cookies",
            "credentials",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.knowledge_memory)

    def test_project_memory_updates_and_forgetting_are_explicit(self) -> None:
        self.assertIn(
            "actions are `record`, `search`, `list`, `delete`, and `supersede`",
            self.knowledge_memory,
        )
        self.assertIn(
            "use `supersede` rather than adding a duplicate",
            self.knowledge_memory,
        )
        self.assertIn(
            "user asks to forget a fact, use `delete`",
            self.knowledge_memory,
        )
        self.assertIn(
            "do not invent unconfirmed argument names or payload shapes",
            self.knowledge_memory,
        )

    def test_qwen_is_only_an_encoder_and_fts5_remains_available(self) -> None:
        for marker in (
            "user-selected local Qwen embedding encoder",
            "keep using the FTS5 lexical results",
            "encoder availability or choice must not disable local help search "
            "or change memory policy",
            "encoder only supplies retrieval vectors",
            "Codex remains responsible",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.knowledge_memory)
        combined = "\n".join((self.visual, self.knowledge_memory)).casefold()
        self.assertNotIn("0.6b", combined)
        self.assertNotIn("8b", combined)

    def test_all_professional_skills_route_to_one_shared_contract(self) -> None:
        for contract in (
            self.visual,
            self.procedural,
            self.material,
            self.review,
        ):
            self.assertIn("knowledge-and-memory.md", contract)

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
            (
                self.visual,
                self.research,
                self.validation,
                self.review,
                self.procedural,
                self.material,
                self.knowledge_memory,
            )
        ).casefold()
        for asset_recipe in ("vending machine", "wood cabin", "售货机", "木屋"):
            with self.subTest(asset_recipe=asset_recipe):
                self.assertNotIn(asset_recipe, combined)


if __name__ == "__main__":
    unittest.main()
