from __future__ import annotations

import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).parents[2]
SKILLS_ROOT = REPOSITORY_ROOT / ".agents" / "skills"
VISUAL_SKILL = SKILLS_ROOT / "houdini-visual-research" / "SKILL.md"
VISUAL_METADATA = (
    SKILLS_ROOT / "houdini-visual-research" / "agents" / "openai.yaml"
)
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
TECHNIQUE_SELECTION_CONTRACT = (
    SKILLS_ROOT
    / "houdini-visual-research"
    / "references"
    / "technique-selection.md"
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
BUILD_REVIEW_CONTRACT = (
    SKILLS_ROOT
    / "houdini-visual-research"
    / "references"
    / "build-brief-and-review.md"
)
HIA_TOOLS_SCHEMA = (
    REPOSITORY_ROOT / "services" / "hia_mcp_v2" / "hia_mcp_v2" / "tools.py"
)
HIA_RUNTIME_EXECUTOR = (
    REPOSITORY_ROOT
    / "houdini_package"
    / "python_libs"
    / "hia_mcp_runtime"
    / "executor.py"
)
REPOSITORY_AGENTS = REPOSITORY_ROOT / "AGENTS.md"
HIA_MCP_DOC = REPOSITORY_ROOT / "docs" / "HIA-MCP-V2.md"
DIAGNOSTICS_DOC = REPOSITORY_ROOT / "docs" / "DIAGNOSTICS.md"


def read_contract(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class HoudiniSkillContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.visual = read_contract(VISUAL_SKILL)
        cls.visual_metadata = read_contract(VISUAL_METADATA)
        cls.research = read_contract(RESEARCH_CONTRACT)
        cls.validation = read_contract(VALIDATION_CONTRACT)
        cls.technique_selection = read_contract(TECHNIQUE_SELECTION_CONTRACT)
        cls.review = read_contract(REVIEW_SKILL)
        cls.procedural = read_contract(PROCEDURAL_SKILL)
        cls.material = read_contract(MATERIAL_SKILL)
        cls.knowledge_memory = read_contract(KNOWLEDGE_MEMORY_CONTRACT)
        cls.build_review = read_contract(BUILD_REVIEW_CONTRACT)
        cls.hia_tools_schema = read_contract(HIA_TOOLS_SCHEMA)
        cls.hia_runtime_executor = read_contract(HIA_RUNTIME_EXECUTOR)
        cls.repository_agents = read_contract(REPOSITORY_AGENTS)
        cls.hia_mcp_doc = read_contract(HIA_MCP_DOC)
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

    def test_deterministic_writes_skip_lookup_while_uncertain_work_reuses_one_batch(
        self,
    ) -> None:
        canonical = (
            "run exactly one relevant batched `hia_local_help_search` before "
            "the affected write"
        )
        combined = "\n".join(
            (
                self.visual,
                self.procedural,
                self.material,
                self.review,
                self.knowledge_memory,
                self.research,
                self.build_review,
                self.validation,
            )
        )
        self.assertEqual(combined.count(canonical), 1)
        self.assertIn(
            "retrieval is not a gate for every scene write",
            self.knowledge_memory,
        )
        self.assertIn(
            "Treat it as the single shared lookup across active Houdini skills",
            self.knowledge_memory,
        )
        self.assertIn(
            "Do not issue another local-help search",
            self.knowledge_memory,
        )
        self.assertIn(
            "do not issue another local-help search",
            self.research,
        )
        for marker in (
            "known parameter change",
            "connection between known nodes",
            "delete",
            "rename",
            "reposition",
            "known color assignment",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.knowledge_memory)
        self.assertIn(
            "execute without knowledge retrieval",
            self.procedural,
        )
        self.assertIn(
            "execute without knowledge retrieval",
            self.material,
        )
        for obsolete in (
            "Every creation, modification, or repair that will write",
            "Before the first scene write for any creation",
            "Every Houdini request that will write the scene starts",
        ):
            with self.subTest(obsolete=obsolete):
                self.assertNotIn(obsolete, combined)
                self.assertNotIn(obsolete, self.repository_agents)
        self.assertIn(
            "A known deterministic edit and a read-only inspection require no research",
            self.research,
        )

    def test_uncertainty_lookup_matches_repository_and_mcp_contract(self) -> None:
        self.assertIn(
            "Knowledge retrieval is not a mandatory gate for every scene write",
            self.repository_agents,
        )
        self.assertIn(
            "When node, parameter, version, complex material/FX/simulation/Solaris/render "
            "workflow, historical project convention, or failure cause is uncertain, "
            "run one relevant batched local-knowledge search before the affected write "
            "and reuse its results",
            self.repository_agents,
        )
        self.assertIn(
            "run exactly one relevant batched `hia_local_help_search` before the "
            "affected write",
            self.knowledge_memory,
        )
        self.assertIn('"hia_local_help_search"', self.hia_tools_schema)
        self.assertIn("The single local-knowledge search entry", self.hia_tools_schema)
        self.assertIn(
            "Use one queries batch instead of parallel duplicate searches",
            self.hia_tools_schema,
        )
        self.assertIn("does not gate hia_execute_hom", self.hia_tools_schema)
        self.assertIn("`queries` 批量形状", self.hia_mcp_doc)

    def test_repository_and_visual_skill_share_risk_bounded_retrieval(self) -> None:
        agents_direct = next(
            line
            for line in self.repository_agents.splitlines()
            if "Knowledge retrieval is not a mandatory gate" in line
        ).casefold()
        agents_uncertain = next(
            line
            for line in self.repository_agents.splitlines()
            if line.startswith("- When node, parameter, version")
        ).casefold()
        visual_route = next(
            line
            for line in self.visual.splitlines()
            if line.startswith("1. Decide whether uncertainty")
        ).casefold()

        for marker in ("parameter", "connect", "delet", "renam", "position", "color"):
            with self.subTest(direct_marker=marker):
                self.assertIn(marker, agents_direct)
                self.assertIn(marker, visual_route)
        for marker in (
            "node",
            "parameter",
            "version",
            "material",
            "simulation",
            "solaris",
            "historical project convention",
            "failure cause",
            "uncertain",
        ):
            with self.subTest(uncertainty_marker=marker):
                self.assertIn(marker, agents_uncertain)
                self.assertIn(marker, visual_route)

        obsolete_gate = (
            "Before the first scene write for any creation, modification, or repair "
            "request"
        )
        self.assertNotIn(obsolete_gate, self.repository_agents)
        self.assertNotIn(obsolete_gate, self.visual)
        self.assertIn("validate the actual scene result", agents_direct)
        self.assertIn("ends with targeted validation", visual_route)

    def test_source_ledger_has_complete_provenance_and_verification_columns(self) -> None:
        self.assertIn(
            "| Title/source | Author/owner | URL/path | Access date | "
            "License/status | Houdini version/build | How used | "
            "Verification status | Verification evidence |",
            self.research,
        )

    def test_complex_tasks_are_local_first_without_weakening_web_research(self) -> None:
        for marker in (
            "run exactly one relevant batched `hia_local_help_search`",
            "Local retrieval never replaces deep external research",
            "continue multi-round web research",
            "SideFX documentation and original sources",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.knowledge_memory)
        self.assertIn(
            "not as a reason to reduce research depth",
            self.research,
        )

    def test_complex_tasks_retrieve_official_workflows_before_planning(self) -> None:
        self.assertIn(
            "retrieve only the relevant bundled SideFX workflow and version "
            "evidence before planning",
            self.visual,
        )
        for marker in (
            "include the official task workflow",
            "retrieve only the domains the task needs",
            "planning summary, not proof of current node behavior",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.knowledge_memory)

    def test_complex_local_lookup_uses_small_purposeful_query_batch(self) -> None:
        for marker in (
            "one batch containing two to four purposeful subqueries",
            "node or capability location",
            "parameters and inputs/outputs",
            "operating principle",
            "practical configuration",
            "Do not fan out ten or more synonymous or repeated queries",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.knowledge_memory)

    def test_retrieval_routes_to_the_relevant_authoritative_source(self) -> None:
        for marker in (
            "exact HOM or Python API question",
            "official HOM or full-help source",
            "For an exact node type, use `hia_node_help`",
            "narrow `hia_search_node_types` call first",
            "project implementation, HDA, or authorized tutorial questions",
            "prefer `project` and `user`",
            "Query `memory` only for a durable project decision",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.knowledge_memory)

    def test_capability_zero_result_is_not_treated_as_unsupported(self) -> None:
        for marker in (
            "zero-result capability search is not proof",
            "`empty_reason`",
            "`catalog_health`",
            "list the capability matrix",
            "unfiltered `hia_search_capabilities`",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.knowledge_memory)

    def test_skill_does_not_stack_another_and_or_fallback(self) -> None:
        self.assertIn(
            "strict-AND to single-OR relaxation",
            self.knowledge_memory,
        )
        self.assertIn(
            "Do not add query-rewrite loops or another fallback cascade",
            self.knowledge_memory,
        )

    def test_retrieval_and_memory_do_not_depend_on_desktop_start_path(self) -> None:
        contract_paths = sorted(SKILLS_ROOT.rglob("*.md")) + sorted(
            SKILLS_ROOT.rglob("*.yaml")
        )
        combined = "\n".join(read_contract(path) for path in contract_paths).casefold()
        self.assertNotIn("launcher", combined)
        self.assertNotIn("wpf", combined)
        self.assertIn("hia mcp or the stable hia cli surface", self.knowledge_memory.casefold())

    def test_simple_writes_skip_retrieval_and_memory(self) -> None:
        for marker in (
            "retrieval is not a gate for every scene write",
            "Execute a known parameter change",
            "directly and validate the live result",
            "Neither a skipped lookup nor a performed lookup writes project memory "
            "automatically",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.knowledge_memory)

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

    def test_qwen_is_only_an_encoder_and_retrieval_mode_is_explicit(self) -> None:
        for marker in (
            "Retrieval mode is explicit",
            "`lexical` uses FTS5 without requiring Qwen",
            "selected `hybrid` or `vector`",
            "explicit failure",
            "never substitute lexical results",
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

    def test_complex_creation_uses_one_compact_build_brief(self) -> None:
        for marker in (
            "**Goal:**",
            "**Reference and design language:**",
            "**Subsystems:**",
            "**Editable controls:**",
            "**Outputs:**",
            "**Risks:**",
            "**Stages:**",
            "**Acceptance:**",
            "not an intermediate representation, node list, approval gate",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.build_review)

    def test_build_stages_follow_semantic_order_and_stop_on_evidence(self) -> None:
        positions = [
            self.build_review.index("proportions and primary form"),
            self.build_review.index("structure, supports, contacts"),
            self.build_review.index("mid-level assemblies"),
            self.build_review.index("task-relevant detail"),
            self.build_review.index("materials and surface response"),
            self.build_review.index("lighting, animation, simulation, or FX"),
            self.build_review.index("output and delivery validation"),
        ]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("name the largest consequential deviation", self.build_review)
        self.assertIn("Stop when that stage's acceptance claim is supported", self.build_review)
        self.assertIn("Merge or skip inapplicable stages", self.build_review)

    def test_complex_execution_orders_search_before_brief_stages_and_review(self) -> None:
        positions = [
            self.visual.index("Decide whether uncertainty warrants the shared lookup"),
            self.visual.index("Complete any uncertainty-triggered local lookup"),
            self.visual.index("create the compact Build Brief"),
            self.visual.index("Advance the applicable Build Brief stages"),
            self.visual.index("Give the preview plus relevant"),
        ]
        self.assertEqual(positions, sorted(positions))

    def test_native_codex_roles_keep_one_live_hip_writer(self) -> None:
        for role in (
            "Director",
            "Researcher",
            "Architect",
            "Lookdev Reviewer",
            "Artifact Reviewer",
            "Performance Reviewer",
        ):
            with self.subTest(role=role):
                self.assertIn(role, self.build_review)
        self.assertIn("sole writer of the live HIP", self.build_review)
        self.assertIn("not persistent Agents", self.build_review)
        self.assertIn("Research and planning roles do not write the live HIP", self.build_review)

    def test_professional_review_routes_by_claim_and_real_evidence(self) -> None:
        for domain in (
            "| Modeling |",
            "| Material |",
            "| Lighting and render |",
            "| Animation |",
            "| Simulation |",
            "| Network organization |",
            "| Performance |",
        ):
            with self.subTest(domain=domain):
                self.assertIn(domain, self.build_review)
        self.assertIn("Select only the domains needed by the Brief", self.build_review)
        self.assertIn("never invented estimates", self.build_review)
        self.assertIn("Do not turn review into a score or exhaustive checklist", self.build_review)

    def test_brief_and_research_handoffs_stay_compact(self) -> None:
        for marker in (
            "Build Brief plus a compact research synthesis",
            "reference full documents, ledgers, and subtask artifacts by path or URL",
            "instead of pasting whole documents, long subtask replies, or source bodies",
            "Do not forward the full working transcript",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.build_review)
        self.assertIn("Return a compact synthesis to the Director", self.research)

    def test_verified_procedure_requires_real_houdini_and_user_acceptance(self) -> None:
        for marker in (
            "succeeded in real Houdini on a recorded build",
            "user accepted the result",
            "promotion is still explicit, never automatic",
            "A failed or merely plausible attempt is not a Verified Procedure",
            "do not automatically write them to project memory",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.build_review)
        self.assertIn("including user acceptance", self.knowledge_memory)

    def test_simple_and_read_only_tasks_do_not_enter_full_build_workflow(self) -> None:
        self.assertIn(
            "Do not load the full workflow for one primitive, one known parameter "
            "or connection change, routine read-only inspection",
            self.build_review,
        )
        self.assertIn(
            "Read-only inspection does not trigger construction, a Build Brief",
            self.build_review,
        )
        self.assertIn("Do not start external research, build a full Brief", self.material)

    def test_risk_scaled_workflow_keeps_simple_edits_direct(self) -> None:
        for marker in (
            "**Direct:**",
            "**Focused:**",
            "**Full:**",
            "execute a known deterministic edit immediately and perform one necessary "
            "targeted validation",
            "Do not require local retrieval, a Build Brief, external research, capture, "
            "or artifact review",
            "These are reasoning tiers, not a Gate, approval layer, state machine",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.build_review)

        for contract in (self.visual, self.procedural, self.material):
            self.assertIn("Direct", contract)
            self.assertIn("Focused", contract)
            self.assertIn("Full", contract)
        self.assertIn(
            "does not require retrieval, external research, a Brief, capture, or "
            "artifact review",
            self.visual,
        )
        self.assertIn(
            "Do not require a Brief, external research, capture, or artifact review",
            self.procedural,
        )
        self.assertIn(
            "Do not start external research, build a full Brief",
            self.material,
        )
        self.assertNotIn("automatically capture a low-resolution preview", self.visual)
        self.assertIn("do not capture every edit", self.visual)
        self.assertIn(
            "Do not trigger research for a known deterministic parameter, connection, "
            "delete, rename, position, or color edit unless uncertainty appears",
            self.visual,
        )
        self.assertIn(
            "Risk-scaled Houdini research and validation",
            self.visual_metadata,
        )
        for marker in (
            "**Direct:** execute a known deterministic edit immediately",
            "**Focused:** for a bounded multi-node edit",
            "**Full:** use one shared batched local lookup when",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.build_review)

    def test_scene_authoring_prefers_native_nodes_and_bounds_hom_writes(self) -> None:
        for marker in (
            "Prefer this authoring order",
            "edit suitable existing nodes and parameters",
            "add standard native Houdini nodes and networks",
            "use a short cohesive HOM batch",
            "create geometry directly or use an in-scene Python/script node only when",
            "Record the reason for an exception",
            "inspect `errors[*].partial_scene_changes_possible` and `scene_change_status`",
            "requires targeted scene inspection or `hia_scene_diff` before another write",
            "HIA does not automatically undo a failed batch",
            "A successful Python return is not completion",
            "verify the actual live-scene nodes or geometry",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.visual)

        skill_contract = "\n".join(
            read_contract(path)
            for path in sorted(SKILLS_ROOT.glob("houdini-*/**/*.md"))
        )
        self.assertEqual(
            1,
            skill_contract.count(
                "A successful Python return is not completion"
            ),
        )

    def test_retry_and_runtime_identity_contracts_match_mcp_behavior(self) -> None:
        for marker in (
            "read `errors[*].partial_scene_changes_possible` and `scene_change_status`",
            "A syntax or undo-group setup failure reported as unchanged",
            "A possible, changed, or unknown scene state requires targeted inspection",
            "HIA does not automatically undo a failed batch",
            "`unknown`, `partial`, or `NO_OBSERVED_EFFECT` validation does not prove completion",
        ):
            with self.subTest(recovery_marker=marker):
                self.assertIn(marker, self.validation)
        for marker in (
            "`HOUDINI_SESSION_CHANGED`",
            "`HOUDINI_RUNTIME_SOURCE_CHANGED`",
            "`STALE_HOUDINI_RUNTIME`",
            "use health or read-only inspection only as needed",
            "do not hot-reload the executor or switch routes",
        ):
            with self.subTest(identity_marker=marker):
                self.assertIn(marker, self.visual)
        for marker in (
            "`partial_scene_changes_possible`",
            "`scene_change_status`",
            "HIA 自身不请求 Undo",
            "运行时身份绑定 launcher session、Houdini PID",
            "health 和只读工具仍可用于确认实际连接",
        ):
            with self.subTest(doc_marker=marker):
                self.assertIn(marker, self.hia_mcp_doc)
        self.assertNotIn("不会回滚", self.hia_mcp_doc)

    def test_retrieved_knowledge_becomes_explicit_semantic_expectations(self) -> None:
        for marker in (
            "Knowledge retrieval supplies implementation evidence; it does not "
            "prove the resulting network is correct",
            "source `flame` mapping to destination `flame`",
            "required fields having a nonzero active range",
            "velocity remaining finite with magnitude plausible",
            "live tool contract at call time",
            "do not restate MCP payload fields",
            "An error-free cook does not satisfy a semantic expectation",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.build_review)

        for contract in (
            self.visual,
            self.procedural,
            self.material,
            self.review,
            self.knowledge_memory,
        ):
            self.assertIn("semantic expectation", contract)
            self.assertIn("hia_validate", contract)
        self.assertIn(
            "A retrieved rule is an input to execution, not evidence",
            self.knowledge_memory,
        )

    def test_semantic_validation_uses_live_contract_without_copying_schema(self) -> None:
        for schema_marker in (
            '"semantic_expectations"',
            '"semantic_checks": SEMANTIC_CHECKS',
            'enum": ["presence", "sample", "mapping"]',
            '"data_kind"',
            '"finite"',
            '"nonzero"',
            '"min_magnitude"',
            '"max_magnitude"',
            '"source": SEMANTIC_DATA_REF',
            '"target": SEMANTIC_DATA_REF',
            '"forbidden_targets"',
        ):
            with self.subTest(schema_marker=schema_marker):
                self.assertIn(schema_marker, self.hia_tools_schema)

        for skill_marker in (
            "semantic-expectation capability",
            "live tool contract",
            "intent-and-evidence level rather than copying the MCP payload schema",
        ):
            with self.subTest(skill_marker=skill_marker):
                self.assertIn(
                    skill_marker,
                    "\n".join((self.build_review, self.knowledge_memory)),
                )

        skill_contract = "\n".join(
            (
                self.visual,
                self.procedural,
                self.material,
                self.review,
                self.knowledge_memory,
                self.build_review,
            )
        )
        for copied_schema_marker in (
            'checks=["semantic_expectations"]',
            "`semantic_checks`",
            '`type="presence"`',
            '`type="sample"`',
            '`type="mapping"`',
            "`data_kind`",
            "`forbidden_targets`",
            "`min_magnitude`",
            "`max_magnitude`",
            "`sample_limit`",
        ):
            with self.subTest(copied_schema_marker=copied_schema_marker):
                self.assertNotIn(copied_schema_marker, skill_contract)

    def test_simulation_and_cache_claims_require_real_execution_evidence(self) -> None:
        for marker in (
            "evidence that the state was reset",
            "cooking began from the configured start",
            "the cache is current and covers the claimed output",
            "required upstream dependencies participated",
            "live runtime contract instead of freezing its output schema",
            "Any unproven item keeps the claim unverified",
            "report the missing proof once",
            "Observed bounded recomputation does not by itself prove reset",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.build_review)
        for marker in (
            "reset, configured-start, range, cache, and dependency evidence rule",
            "surface missing proof once as a real risk",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.visual)
        for runtime_marker in (
            '"cook_cache_evidence"',
            '"cook_started"',
            '"cook_completed"',
            '"reset": "not_observed"',
            '"dependency_invalidation"',
            '"recompute_verified"',
            '"recompute_not_proven"',
            '"stale_cache_risk"',
        ):
            with self.subTest(runtime_marker=runtime_marker):
                self.assertIn(runtime_marker, self.hia_runtime_executor)
        self.assertIn("Missing proof prevents verification", self.review)
        self.assertIn(
            "reset, configured-start, range, cache, and dependency evidence rule",
            self.review,
        )

    def test_capture_returns_objective_facts_for_codex_visual_judgment(self) -> None:
        for marker in (
            "actual returned image content",
            "objective `source_state`",
            "exact frame lock",
            "capture success",
            "cook evidence",
            "temporal coverage",
            "runtime does not score them",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.validation)
        self.assertIn("objective source-state, frame-lock, capture, cook, and temporal facts", self.visual)
        self.assertIn("selective preview and display-match contract", self.material)
        self.assertIn("capture-quality and display-match rule", self.review)
        for runtime_marker in (
            '"source_state"',
            '"frame_lock"',
            '"capture_ok"',
            '"cook_cache_evidence"',
        ):
            with self.subTest(runtime_marker=runtime_marker):
                self.assertIn(runtime_marker, self.hia_runtime_executor)
        for marker in (
            "HDR",
            "OCIO",
            "not final color evidence",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, "\n".join((self.validation, self.material, self.review)))

    def test_dynamic_visual_evidence_is_temporal_but_bounded(self) -> None:
        for marker in (
            "one representative frame for a static claim",
            "animation, simulation, or a time-varying material or effect",
            "risk-proportionate short sequence or representative frame set",
            "meaningful change, contacts or transitions, continuity",
            "suspect or failed frame",
            "Do not substitute one still for a dynamic completion claim",
            "force a long flipbook merely for process",
            "intended camera and aspect",
            "actual returned frame or bounded sequence covers what was requested",
            "original frame and view state were restored",
            "low-resolution preview as bounded review evidence",
            "frame, view, cook, or restoration evidence is unproven",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.validation)
        self.assertIn(
            "risk-appropriate static or temporal preview evidence",
            self.procedural,
        )
        self.assertNotIn("low-resolution same-frame preview", self.procedural)

    def test_subjective_comparison_is_task_scoped_and_direct_tasks_are_exempt(
        self,
    ) -> None:
        for marker in (
            "temporary, bounded comparison contract",
            "only in the current task context",
            "Direct deterministic work never enters this loop",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.visual)
        for marker in (
            "A simple deterministic edit does not enter the experiment loop",
            "goal, target and mutable scope",
            "a few visual or temporal objectives",
            "only relevant metrics and controls",
            "fixed frame and view samples",
            "observable success conditions",
            "do not automatically write them into Skill files or project memory",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.validation)

    def test_effect_experiment_combines_preview_data_ranking_and_bounded_iteration(
        self,
    ) -> None:
        for marker in (
            "one low-cost baseline",
            "two or three bounded candidates one at a time",
            "actual previews for every specified frame and view together with the "
            "relevant returned Houdini data",
            "explicit ranking and best candidate",
            "each candidate's main issue",
            "single largest remaining issue",
            "small set of controls allowed to change next",
            "Without usable preview evidence and relevant Houdini data",
            "Keep the best candidate as the next baseline",
            "change only the few controls addressing that single largest issue",
            "two or three rounds chosen by risk",
            "two rounds show no improvement",
            "further cost is disproportionate",
            "final write-back of the evidence-supported best candidate",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.validation)

    def test_bounded_comparison_separates_codex_runtime_and_knowledge_responsibilities(
        self,
    ) -> None:
        for marker in (
            "capture it under fixed comparison conditions",
            "evaluate two or three bounded candidates one at a time",
            "one explicit `hia_execute_hom` call",
            "followed by explicit capture and claim-specific validation",
            "return to or adjust the baseline only through another deliberate bounded HOM call",
            "does not automatically rank candidates, restore a baseline, clear caches",
            "does not prewrite this task's scoring criteria",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.validation)

        experiment = self.validation.split(
            "## Bounded subjective effect experiments", 1
        )[1].split("## Bounded recovery", 1)[0]
        for copied_schema_marker in (
            "```json",
            '"type": "object"',
            '"properties"',
            '"required"',
        ):
            with self.subTest(copied_schema_marker=copied_schema_marker):
                self.assertNotIn(copied_schema_marker, experiment)
        self.assertNotIn("| --- |", experiment)
        for domain_recipe in (
            "Pyro",
            "FLIP",
            "viscosity",
            "temperature",
            "voxel size",
            "roughness =",
        ):
            with self.subTest(domain_recipe=domain_recipe):
                self.assertNotIn(domain_recipe, experiment)

    def test_all_creation_and_review_skills_route_to_build_contract(self) -> None:
        for contract in (
            self.visual,
            self.procedural,
            self.material,
            self.review,
        ):
            self.assertIn("build-brief-and-review.md", contract)

    def test_new_karma_materials_default_to_modern_solaris_materialx(self) -> None:
        for marker in (
            "default to modern Solaris/USD with a Material Library LOP and MaterialX",
            "`mtlxstandard_surface`",
            "Do not habitually route new Karma work through `/mat`, Principled Shader, "
            "legacy VOP materials, or Mantra",
            "user explicitly requests it",
            "existing project requires compatibility",
            "target renderer is not Karma",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.material)
        self.assertIn("Karma material default", self.technique_selection)

    def test_karma_node_types_are_confirmed_without_version_name_lock_in(self) -> None:
        for marker in (
            "uncertainty-triggered local-help result",
            "`hia_search_node_types`",
            "`hia_node_help`",
            "not permission to hard-code a versioned internal type name",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.material)

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
        contract_paths = sorted(SKILLS_ROOT.rglob("*.md")) + sorted(
            SKILLS_ROOT.rglob("*.yaml")
        )
        combined = "\n".join(read_contract(path) for path in contract_paths).casefold()
        for asset_recipe in (
            "vending machine",
            "wood cabin",
            "wooden table",
            "staircase",
            "售货机",
            "木屋",
            "桌子",
            "楼梯",
        ):
            with self.subTest(asset_recipe=asset_recipe):
                self.assertNotIn(asset_recipe, combined)


if __name__ == "__main__":
    unittest.main()
