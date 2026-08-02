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
PROCEDURAL_ARCHITECTURE_CONTRACT = (
    SKILLS_ROOT
    / "houdini-procedural-modeling"
    / "references"
    / "procedural-architecture.md"
)
MODELING_VALIDATION_CONTRACT = (
    SKILLS_ROOT
    / "houdini-procedural-modeling"
    / "references"
    / "modeling-validation.md"
)
GEOMETRY_INTEGRITY_CONTRACT = (
    SKILLS_ROOT
    / "houdini-procedural-modeling"
    / "references"
    / "geometry-integrity.md"
)
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
FULL_GOAL_BLUEPRINT_CONTRACT = (
    SKILLS_ROOT
    / "houdini-visual-research"
    / "references"
    / "full-goal-blueprint.md"
)
HIA_TOOLS_SCHEMA = (
    REPOSITORY_ROOT / "services" / "hia_mcp_v2" / "hia_mcp_v2" / "tools.py"
)
BRIDGE_SESSION = (
    REPOSITORY_ROOT / "services" / "bridge" / "hia_bridge" / "session.py"
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
        cls.procedural_architecture = read_contract(
            PROCEDURAL_ARCHITECTURE_CONTRACT
        )
        cls.modeling_validation = read_contract(MODELING_VALIDATION_CONTRACT)
        cls.geometry_integrity = read_contract(GEOMETRY_INTEGRITY_CONTRACT)
        cls.material = read_contract(MATERIAL_SKILL)
        cls.knowledge_memory = read_contract(KNOWLEDGE_MEMORY_CONTRACT)
        cls.build_review = read_contract(BUILD_REVIEW_CONTRACT)
        cls.build_review_flat = " ".join(cls.build_review.split())
        cls.full_goal_blueprint = read_contract(FULL_GOAL_BLUEPRINT_CONTRACT)
        cls.full_goal_blueprint_flat = " ".join(cls.full_goal_blueprint.split())
        cls.hia_tools_schema = read_contract(HIA_TOOLS_SCHEMA)
        cls.bridge_session = read_contract(BRIDGE_SESSION)
        cls.hia_runtime_executor = read_contract(HIA_RUNTIME_EXECUTOR)
        cls.repository_agents = read_contract(REPOSITORY_AGENTS)
        cls.repository_agents_flat = " ".join(cls.repository_agents.split())
        cls.hia_mcp_doc = read_contract(HIA_MCP_DOC)
        cls.diagnostics = read_contract(DIAGNOSTICS_DOC)

    def test_deep_research_is_triggered_by_decision_relevant_uncertainty(self) -> None:
        for marker in (
            "do not search merely because work is Full, complex, reference-driven",
            "only for an unsupplied ShaderToy/GLSL implementation",
            "materially affects the decision",
            "A supplied image by itself requires visual decomposition, not a web search",
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
        self.assertIn(
            "Do not retrieve merely because material work is substantive",
            self.material,
        )
        self.assertIn(
            "uncertain or version-sensitive complex material/simulation/Solaris "
            "workflow",
            self.build_review,
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
            "A known route, supplied reference that can be directly decomposed, "
            "deterministic edit, and read-only inspection require no external research",
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

    def test_external_research_is_proportional_not_a_full_task_gate(self) -> None:
        for marker in (
            "run exactly one relevant batched `hia_local_help_search`",
            "can change the decision",
            "use proportionate external research",
            "Otherwise continue with installed help, live inspection, and real-scene validation",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.knowledge_memory)
        self.assertIn(
            "do not search merely because work is Full",
            self.research,
        )

    def test_uncertain_complex_tasks_retrieve_official_workflows_before_planning(
        self,
    ) -> None:
        self.assertIn(
            "When that uncertainty rule triggers for complex, multi-domain, "
            "unfamiliar, or version-sensitive work, retrieve only the relevant "
            "bundled SideFX workflow and version "
            "evidence before planning",
            self.visual,
        )
        self.assertIn(
            "using available retrieval evidence, any necessary external research",
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

    def test_all_professional_skills_route_to_full_coordination_contract(self) -> None:
        for contract in (
            self.visual,
            self.procedural,
            self.material,
            self.review,
        ):
            self.assertIn("full-goal-blueprint.md", contract)

    def test_four_skills_and_agents_share_full_blueprint_authorization_boundary(
        self,
    ) -> None:
        markers_by_contract = (
            (
                self.visual,
                "Planning sends the complete blueprint to Supervisor for initial "
                "authorization and every material revision",
            ),
            (
                self.procedural,
                "Planning sends the complete blueprint to Supervisor for initial and "
                "material-revision authorization",
            ),
            (
                self.material,
                "reach Supervisor in full for initial and material-revision "
                "authorization",
            ),
            (
                self.review,
                "Supervisor receives the complete blueprint for initial and "
                "material-revision authorization",
            ),
            (
                self.build_review,
                "Supervisor must receive the complete synthesized blueprint, all stage "
                "cards",
            ),
            (
                self.repository_agents_flat,
                "Planning sends Supervisor the complete blueprint for initial "
                "authorization and again after every material revision",
            ),
        )
        for contract, marker in markers_by_contract:
            with self.subTest(marker=marker):
                self.assertIn(marker, contract)
        combined = "\n".join(
            (
                self.repository_agents,
                self.visual,
                self.procedural,
                self.material,
                self.review,
                self.build_review,
                self.full_goal_blueprint,
            )
        )
        for retired_absolute in (
            "Keep the complete blueprint out of the Supervisor context",
            "Do not paste the full blueprint",
            "Do not copy the full blueprint back into the Supervisor context",
        ):
            with self.subTest(retired_absolute=retired_absolute):
                self.assertNotIn(retired_absolute, combined)

    def test_full_information_floors_are_specific_not_a_general_gate(self) -> None:
        for contract, marker in (
            (
                self.procedural,
                "10,000-overall, 2,500-per-stage, and 350-per-step task-specific "
                "information floors",
            ),
            (
                self.material,
                "10,000-overall, 2,500-per-stage, and 350-per-step task-specific "
                "information floors",
            ),
            (
                self.build_review_flat,
                "at least 10,000 task-specific information units overall, 2,500 per "
                "complete stage card, and 350 per ordered step",
            ),
            (
                self.repository_agents_flat,
                "at least 10,000 task-specific information units overall, 2,500 in "
                "every complete stage card, and 350 in every ordered step",
            ),
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, contract)
        self.assertIn(
            "not a generic Planner, reusable Gate, quality score, or workflow state "
            "machine",
            self.repository_agents_flat,
        )

    def test_supervisor_authorizes_full_blueprint_then_loops_use_bounded_payloads(
        self,
    ) -> None:
        self.assertIn(
            "detailed enough to remove material ambiguity",
            self.full_goal_blueprint_flat,
        )
        section_contract = self.full_goal_blueprint_flat.split(
            "## Use these stable user-visible blueprint sections", 1
        )[1].split(
            "## Authorize the full blueprint, then use bounded loop payloads", 1
        )[0]
        for heading in (
            "### Goal and observable completion",
            "### User facts and hard constraints",
            "### Complete stage acceptance cards",
            "### Evidence and review ledger",
        ):
            with self.subTest(heading=heading):
                self.assertIn(heading, section_contract)
        for payload in (
            "**Global hard-constraint capsule:**",
            "**Complete current stage card:**",
            "**Latest evidence delta:**",
        ):
            with self.subTest(payload=payload):
                self.assertIn(payload, self.full_goal_blueprint_flat)
        for marker in (
            "Planning must send Supervisor the complete synthesized blueprint for "
            "initial authorization",
            "Supervisor must see the actual artifact",
            "Supervisor reviews the complete artifact against User facts",
            "After Supervisor authorizes that full version",
            "each repeated execution and review loop uses exactly these bounded "
            "semantic parts",
            "Do not re-paste the already authorized full blueprint",
            "send the complete revised blueprint back to Supervisor for renewed strict "
            "authorization",
            "Execution and both review Threads never need the whole blueprint merely "
            "because Supervisor received it for authorization",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.full_goal_blueprint_flat)
        for retired_absolute in (
            "Keep the complete blueprint out of the Supervisor context",
            "Do not paste the full blueprint",
            "Do not copy the full blueprint back into the Supervisor context",
        ):
            with self.subTest(retired_absolute=retired_absolute):
                self.assertNotIn(retired_absolute, self.full_goal_blueprint_flat)

    def test_build_stages_follow_semantic_order_and_loop_on_evidence(self) -> None:
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
        self.assertIn(
            "Continue without a fixed iteration count until both applicable evidence "
            "sets are verified",
            self.build_review,
        )
        self.assertIn("Merge or skip inapplicable stages", self.build_review)
        self.assertIn("primary form as the first bounded authoring batch", self.procedural)
        self.assertIn("Do not remove or weaken User facts", self.procedural)
        self.assertIn("continue modeling that stage", self.procedural)
        self.assertIn("instead of handing off to LookDev", self.procedural)
        self.assertLess(
            self.procedural.index("primary form as the first bounded authoring batch"),
            self.procedural.index("before adding broad tagging, animation, detail, or materials"),
        )
        self.assertIn(
            "Missing either review Thread or any internal subagent must never collapse",
            self.build_review,
        )
        self.assertIn("one all-system batch", self.build_review)
        self.assertIn("Never author all stages of a complex asset", self.procedural)
        self.assertIn("one semantic stage or one coherent subsystem", self.procedural)
        self.assertIn(
            "after every stage, return routed evidence to the parallel Technical "
            "Review and actual-image Visual Review Threads",
            self.procedural,
        )

    def test_complex_execution_orders_search_before_blueprint_stages_and_review(
        self,
    ) -> None:
        positions = [
            self.visual.index("Decide whether uncertainty warrants the shared lookup"),
            self.visual.index("Complete an uncertainty-triggered local lookup"),
            self.visual.index(
                "Resolve the user's per-submission choice before complexity depth"
            ),
            self.visual.index("Advance the applicable complete stage cards"),
            self.visual.index("After every project-team stage"),
        ]
        self.assertEqual(positions, sorted(positions))

    def test_canonical_project_roles_keep_one_live_hip_writer(self) -> None:
        for role in (
            "**监督（Supervisor）**",
            "**方案（Planning）**",
            "**执行（Execution）**",
            "**视觉审查（Visual Review）**",
            "**技术审查（Technical Review）**",
        ):
            with self.subTest(role=role):
                self.assertIn(role, self.full_goal_blueprint_flat)
        self.assertIn("Execution） is the only live-HIP writer", self.build_review)
        self.assertIn("not persistent backend Agents", self.build_review)
        self.assertIn(
            "Planning and both review Threads provide plans or evidence only",
            self.review,
        )
        for retired_role in (
            "Blueprint Steward",
            "Research and Architecture",
            "Acceptance Review",
            "Build & " + "Blueprint",
        ):
            with self.subTest(retired_role=retired_role):
                self.assertNotIn(retired_role, self.full_goal_blueprint_flat)

    def test_professional_skills_keep_domain_and_write_boundaries(self) -> None:
        self.assertIn(
            "When serving the Planning responsibility, return target-specific "
            "research, architecture, construction steps",
            self.procedural,
        )
        self.assertIn(
            "Keep Execution as the sole writer of the current HIP",
            self.procedural,
        )
        self.assertIn(
            "When serving Planning for a material or lighting stage",
            self.material,
        )
        self.assertIn("never mutate the live scene", self.material)
        self.assertIn(
            "Act inside either the independent read-only Visual Review Thread or the "
            "independent read-only Technical Review Thread",
            self.review,
        )
        self.assertIn(
            "Bridge enforces the role boundary by exposing empty `hia_mcp_v2` and "
            "`houdini_intelligence` inventories",
            self.visual,
        )

    def test_task_routing_exposes_only_single_ai_or_project_team(self) -> None:
        for marker in (
            "User-facing team settings must name these results in ordinary language",
            "Machine storage values are implementation details and must not be the "
            "primary labels",
            "The Project Team settings surface uses intuitive outcome wording",
            "saved default and the per-submission selector expose only **单个 AI / "
            "项目团队**",
            "return the Panel selector to the saved default immediately after send",
            "one-shot choice never writes back to that default",
            "Resolve the per-submission selector first",
            "Choosing **单个 AI** keeps that newly submitted task in the original "
            "Panel Thread and creates no team project",
            "Choosing **项目团队** automatically creates one Panel project, one native "
            "Goal, and all five real project Threads",
            "Direct, Focused, and Full are depth descriptions, not hidden routing "
            "decisions",
            "must not override, downgrade, or upgrade the user's explicit single/team "
            "choice",
            "Never split, merge, or rebuild an already running project",
            "they are not a Planner, Gate, Goal transition, or quality level",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.full_goal_blueprint_flat)
        for retired_choice in (
            "`o" + "ff`",
            "`sug" + "gest`",
            "`au" + "to`",
            "沿用" + "默认",
            "先展示" + "分工",
            "单个 AI " + "完成",
            "项目团队" + "协作",
        ):
            with self.subTest(retired_choice=retired_choice):
                self.assertNotIn(retired_choice, self.full_goal_blueprint_flat)
        self.assertIn("automatically creates one Panel project", self.full_goal_blueprint_flat)
        self.assertIn("giant HOM script", self.full_goal_blueprint_flat)
        current_routing = "\n".join((self.visual, self.full_goal_blueprint_flat))
        for retired_override in (
            "Direct and Focused tasks remain in their original single Thread regardless "
            "of the team preference",
            "Direct, Focused, or **单个 AI** stays in the original Panel Thread",
        ):
            with self.subTest(retired_override=retired_override):
                self.assertNotIn(retired_override, current_routing)
        for marker in (
            "Resolve the user's per-submission choice before complexity depth",
            "**项目团队** creates one Panel project, one native Goal, and exactly "
            "Supervisor, Planning, Execution, Visual Review, and Technical Review",
            "must never silently override the explicit choice",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.visual)

    def test_full_team_is_a_thin_overlay_over_existing_contracts(self) -> None:
        for owner in (
            "build-brief-and-review.md",
            "knowledge-and-memory.md",
            "visual-validation.md",
        ):
            with self.subTest(owner=owner):
                self.assertIn(owner, self.full_goal_blueprint_flat)
        for prohibited in (
            "second roster schema",
            "status machine",
            "evidence ledger",
        ):
            with self.subTest(prohibited=prohibited):
                self.assertIn(prohibited, self.full_goal_blueprint_flat)
        self.assertIn("optional evidence", self.knowledge_memory)
        self.assertIn("transient", self.validation)

    def test_contracts_remove_retired_external_host_and_serial_fallback_routes(
        self,
    ) -> None:
        combined = "\n".join(
            (
                self.repository_agents,
                self.visual,
                self.procedural,
                self.material,
                self.review,
                self.build_review,
                self.validation,
                self.knowledge_memory,
                self.research,
                self.full_goal_blueprint,
            )
        ).casefold()
        retired_routes = (
            "codex " + "desktop",
            "host-" + "native",
            "local/" + "shared",
            "git work" + "tree",
            "isolated app-" + "server cannot",
            "sequential " + "route",
            "build & " + "blueprint",
            "fixed four-" + "thread roster",
        )
        for retired_route in retired_routes:
            with self.subTest(retired_route=retired_route):
                self.assertNotIn(retired_route, combined)

    def test_repository_routes_user_choice_before_complexity_depth(self) -> None:
        for marker in (
            "keep the user in HIA Panel",
            "resolve the per-submission **单个 AI / 项目团队** choice before Direct, "
            "Focused, or Full depth",
            "**单个 AI** keeps that new task in its original Thread and creates no "
            "team",
            "**项目团队** creates one native Goal and one Panel project containing "
            "exactly five real Codex app-server Threads",
            "Complexity scales blueprint and review depth only and must never override "
            "the user's choice",
            "stable `thread/start` and `turn/start` method families",
            "**监督（Supervisor）**, **方案（Planning）**, **执行（Execution）**, "
            "**视觉审查（Visual Review）**, and **技术审查（Technical Review）**",
            "empty `hia_mcp_v2` and `houdini_intelligence` inventories for every "
            "non-Execution Thread",
            "Execution alone writes the HIP and receives only the current complete "
            "card",
            "every stage and repair requires usable actual image content plus technical "
            "evidence",
            "parallel Visual/Technical reviews",
            "without a fixed iteration count",
            "Keep the native Goal active until every stage really passes both reviews",
            "The five Threads are the baseline for every model",
            "when one of the four non-Execution read-only roles actually exposes "
            "native subagent tools",
            "Execution never proactively spawns or delegates to native subagents",
            "they inherit its scene-write capability",
            "The saved default and one-shot choice affect only the new submission",
            "return the selector to the saved default after send",
            "allow the user to change a Thread's supported model or append guidance "
            "from Panel",
            "changing the default never rebuilds a running project",
            "Keep existing Goal, Focus, stage, review, Context Pack, EffectSpec",
            "third real Codex app-server automatic context compaction",
            "native `thread/fork` migration",
            "any failure preserves the old Thread",
            "sole automatic deletion exception",
            "never create a local summary, request manual compaction, or persist chat "
            "bodies",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.repository_agents_flat)

    def test_full_project_uses_bridge_managed_app_server_threads(self) -> None:
        for marker in (
            "## Coordinate through the Codex app-server",
            "stable Codex app-server `thread/start` method family",
            "starts each bounded role turn with `turn/start`",
            "freezes only those method families, not a payload schema",
            "associates each app-server Thread with the Panel project, Goal, role key",
            "ensure all five members exist before the first scene write",
            "Send Execution only the approved hard-constraint capsule, one complete "
            "current stage card",
            "never send all future stage cards or ask it to execute the whole asset",
            "applies the flat overrides in `thread/start.config`",
            "`hia_mcp_v2` and `houdini_intelligence` tool inventories are empty",
            "Bridge-enforced capability boundary",
            "consume real scene facts, diffs, errors, captures, and render evidence "
            "routed from Execution",
            "A user-selected model change applies to subsequent turns",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.full_goal_blueprint_flat)

    def test_goal_reuses_one_project_and_five_real_threads(self) -> None:
        roles = (
            "**监督（Supervisor）**",
            "**方案（Planning）**",
            "**执行（Execution）**",
            "**视觉审查（Visual Review）**",
            "**技术审查（Technical Review）**",
        )
        for role in roles:
            self.assertIn(role, self.full_goal_blueprint_flat)
        for marker in (
            "keep one Panel project for the native Goal",
            "reuse exactly five real project Threads for every stage and correction",
            "Every project-team Full Goal project has exactly these five stable",
            "`supervisor`, `planning`, `execution`, `visual_review`, and "
            "`technical_review`",
            "Resume these same five Threads for every stage and correction",
            "Keep all five Threads associated with the same Panel project and Goal",
            "never create another project for a later stage or correction",
            "Keep worker Threads inside the project container rather than adding them to "
            "the top-level task list",
            "change its model for later turns, and add guidance",
            "This is the baseline for every model",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.full_goal_blueprint_flat)

    def test_sparse_full_blueprint_separates_facts_observations_and_assumptions(
        self,
    ) -> None:
        for marker in (
            "Label every item **User fact**",
            "Label every item **Reference observation**",
            "Label every item **Codex assumption**",
            "Label live observations **Verified scene fact**",
            "detailed enough to remove material ambiguity",
            "Never hard-code a domain or asset-family recipe",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.full_goal_blueprint_flat)
        precedence = self.full_goal_blueprint_flat.split(
            "## Keep authority and provenance explicit", 1
        )[1].split("## Expand sparse prompts", 1)[0]
        for source in (
            "the user's latest explicit instruction for this Goal",
            "verified current-scene facts",
            "reference observations supported by cited evidence",
            "Codex assumptions",
        ):
            with self.subTest(source=source):
                self.assertIn(source, precedence)

    def test_sparse_full_prompt_expands_into_task_specific_advanced_blueprint(
        self,
    ) -> None:
        for marker in (
            "A sparse prompt for a complete asset still requires a genuinely "
            "several-thousand-to-tens-of-thousands-scale advanced construction "
            "blueprint",
            "at least **10,000 task-specific information units** across the complete "
            "blueprint",
            "at least **2,500 task-specific information units** in every complete "
            "stage card",
            "at least **350 task-specific information units** in every ordered "
            "construction step",
            "These information floors are necessary but never sufficient",
            "**task anchors:**",
            "**required structure:**",
            "**anti-repetition and anti-filler:**",
            "**stage and step semantic completeness:**",
            "Length is an auxiliary depth signal, not a quality score",
            "a semantically strong artifact below any required information floor is "
            "still incomplete",
            "do not create a general Planner, reusable Gate, or workflow state machine",
            "A title-only sequence of broad phases is a routing outline, not a "
            "construction blueprint",
            "Detail means resolving the requested deliverable at professional "
            "construction depth",
            "does not authorize speculative features",
            "Keep User facts in their own highest-authority section or table column",
            "keep Codex assumptions in a separate labeled section or column",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.full_goal_blueprint_flat)
        for marker in (
            "Planning expands even a sparse complete-deliverable prompt into a "
            "task-specific advanced blueprint",
            "Require that Full artifact to pass the reference's production floors of "
            "10,000 task-specific information units overall",
            "Planning sends the complete blueprint to Supervisor for initial "
            "authorization and every material revision",
            "Supervisor returns any generic, under-floor, padded, or semantically "
            "incomplete artifact",
            "one fully expanded current stage card",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.visual)

    def test_each_construction_step_is_complete_and_generic_plans_are_returned(
        self,
    ) -> None:
        ordered_steps = self.full_goal_blueprint_flat.split(
            "### Ordered construction steps", 1
        )[1].split("### Native-node strategy", 1)[0]
        for field in (
            "**Network region and responsibility:**",
            "**Native operation or node strategy:**",
            "**Inputs and connections:**",
            "**Key parameter dependencies:**",
            "**Expected result:**",
            "**Evidence:**",
            "**Failure minimum repair:**",
        ):
            with self.subTest(field=field):
                self.assertIn(field, ordered_steps)
        for marker in (
            "A generic plan, phase-only list, undefined instruction to add detail",
            "is not authorizable",
            "Supervisor must either expand the authorization request",
            "return the plan or card to Planning for task-specific expansion",
            "Planning remains the single authoritative blueprint owner",
            "before the fully expanded current card reaches Execution",
            "Send Execution only that fully expanded current stage card",
            "A returned generic card never reaches Execution",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.full_goal_blueprint_flat)

    def test_complete_current_stage_card_has_all_required_contract_fields(self) -> None:
        headings = (
            "### Stage objective",
            "### Prerequisites",
            "### Inputs",
            "### Ordered construction steps",
            "### Native-node strategy",
            "### Authoring batches",
            "### Parameter dependencies",
            "### Outputs",
            "### Visible characteristics",
            "### Structural relationships",
            "### Prohibitions",
            "### Technical evidence",
            "### Visual evidence",
            "### Reviewer",
            "### Failure minimum repair",
            "### Downstream contract",
            "### Card evidence disposition",
        )
        card_contract = self.full_goal_blueprint_flat.split(
            "## Complete current stage card contract", 1
        )[1].split("## Run the stage build and acceptance loop", 1)[0]
        positions = [card_contract.index(heading) for heading in headings]
        self.assertEqual(positions, sorted(positions))
        for marker in (
            "Use a natural-language stage title, not a short number",
            "at least 2,500 task-specific information units under the production "
            "measurement",
            "Every ordered step must contain at least 350 production-measured "
            "task-specific information units",
            "One card may require several bounded writes",
            "Never put an entire complex asset",
            "Report technical evidence and visual evidence separately",
            "not mandatory transitions and not a state machine",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.full_goal_blueprint_flat)

    def test_execution_gets_one_stage_then_both_reviews_run_in_parallel(self) -> None:
        for marker in (
            "Each turn receives only the approved current stage card",
            "must never receive or execute the whole asset plan as one batch",
            "Never send future cards or ask it to author the whole asset in one turn",
            "Start Technical Review and Visual Review in parallel after the stage",
            "Wait for both review returns",
            "Supervisor combines their real evidence without merging the claims",
            "Continue the Supervisor-driven repair and parallel review loop without "
            "a fixed iteration count",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.full_goal_blueprint_flat)
        loop_contract = self.full_goal_blueprint_flat.split(
            "## Run the stage build and acceptance loop", 1
        )[1].split(
            "## Use available native subagents as a read-only internal layer", 1
        )[0]
        positions = [
            loop_contract.index("Have Planning issue the complete current stage card"),
            loop_contract.index(
                "Send Execution only that fully expanded current stage card"
            ),
            loop_contract.index("Start Technical Review and Visual Review in parallel"),
            loop_contract.index("Wait for both review returns"),
            loop_contract.index("Continue the Supervisor-driven repair"),
            loop_contract.index("After a pass, have Planning record the evidence delta"),
        ]
        self.assertEqual(positions, sorted(positions))

    def test_native_goal_stays_active_until_every_stage_really_passes(self) -> None:
        for marker in (
            "Keep the existing native Goal `active` throughout this entire loop",
            "one reviewer pass, or one stage pass never completes the Goal",
            "Only after every applicable stage has passed both review lanes with real "
            "evidence",
            "mark the existing native Goal complete through its established Goal "
            "surface",
            "retain the active Goal and expose the limitation through the existing "
            "surface",
            "Keep Goal state, stage cards, Technical Review evidence, and Visual Review "
            "evidence on their established surfaces",
            "never copies their schema into a second Goal record",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.full_goal_blueprint_flat)
        lifecycle = self.full_goal_blueprint_flat.split(
            "## Run the stage build and acceptance loop", 1
        )[1].split(
            "## Use available native subagents as a read-only internal layer", 1
        )[0]
        self.assertLess(
            lifecycle.index("native Goal `active`"),
            lifecycle.index("mark the existing native Goal complete"),
        )

    def test_every_team_review_cycle_requires_image_and_technical_evidence(
        self,
    ) -> None:
        for marker in (
            "usable actual image content is required for every stage review and every "
            "repair review",
            "claim-specific technical evidence from the real scene plus usable actual "
            "image content for every stage and every repair",
            "visibly substandard result, interpenetration, unsupported or floating "
            "construction",
            "incorrect support or contact, insufficient clearance",
            "a Box-heavy stand-in for requested finished construction",
            "Supervisor issues the smallest directed repair",
            "Reacquire both usable actual image content and claim-specific technical "
            "evidence",
            "Do not turn review into a numeric score, fixed iteration ritual",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.full_goal_blueprint_flat)

    def test_project_reviewers_are_real_threads_but_single_ai_separates_passes(
        self,
    ) -> None:
        for marker in (
            "In a project-team Full Goal, use inside the independent Visual Review or "
            "Technical Review project Thread",
            "in a single-AI route, the same Panel Thread may perform the visual and "
            "technical reviews only as separated read-only passes",
            "both review Threads are required and run in parallel after every stage",
            "never collapse either into a Supervisor pass",
            "Only the explicit single-AI route may keep both reviews in the original "
            "Thread",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.review)
        retired_fallback = (
            "may be an independent Panel project Thread or a separated "
            + "read-only Supervisor pass"
        )
        self.assertNotIn(retired_fallback, self.review)

    def test_available_native_subagents_stay_inside_read_only_roles(self) -> None:
        for marker in (
            "The five real project Threads are the baseline for every model",
            "At each Supervisor, Planning, Visual Review, or Technical Review turn",
            "When a native subagent tool is available and that read-only role has "
            "genuinely parallel, non-overlapping work",
            "it must dispatch bounded internal subagents",
            "appropriate research, structural analysis, visual review, or technical "
            "review",
            "Sol Ultra is an important capability-bearing case, not a version or "
            "model-ID dependency",
            "Every internal subagent assignment is read-only",
            "Execution deliberately does not proactively spawn or delegate to native "
            "subagents",
            "they inherit its HIA scene-write capability",
            "Execution instead remains a serialized mainline writer",
            "do not become project roles, app-server project Threads, top-level tasks",
            "When the native tool is unavailable or no suitable parallel read-only "
            "work exists, do not claim or invent subagent activity",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.full_goal_blueprint_flat)
        self.assertNotIn(
            "When Sol Ultra provides internal subagents, use them only",
            self.full_goal_blueprint_flat,
        )
        for marker in (
            "these five Threads are the baseline for every model",
            "it must dispatch read-only internal subagents",
            "Execution never proactively spawns or delegates to native subagents",
            "they inherit its HIA scene-write capability",
            "When those tools are unavailable, do not invent subagent activity",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.visual)
        for marker in (
            "When this review role actually exposes native subagent tools",
            "genuinely parallel, non-overlapping read-only claims",
            "They never replace the independent Visual Review or Technical Review "
            "project Threads",
            "never touch the HIP",
            "must not be invented when the tools or suitable work are unavailable",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.review)

    def test_third_real_compaction_uses_verified_thread_fork_migration(self) -> None:
        for marker in (
            "applies independently to an ordinary Panel task Thread and to each of the "
            "five real project-role Threads",
            "Count only real automatic context-compaction events reported by the Codex "
            "app-server for that exact Thread",
            "Immediately after that Thread's third reported automatic compaction",
            "use the native `thread/fork` method to create one replacement",
            "Do not create a local summary, invoke a manual compact operation, persist "
            "chat bodies",
            "required conversation and task context is present and usable",
            "ordinary-task identity or exact project role is unchanged",
            "the selected model is unchanged unless the user already requested a "
            "change",
            "the capability boundary is unchanged",
            "the native Goal identity and, when applicable, Panel project membership "
            "are exact",
            "Only after every check succeeds",
            "precisely delete the one superseded old Thread",
            "If fork, validation, reassociation, or deletion preconditions fail, retain "
            "the old Thread unchanged",
            "This verified third-compaction migration is the only automatic deletion "
            "exception",
            "Do not migrate Ultra or other internal subagents",
            "Never delete a Goal project or role Thread as cleanup",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.full_goal_blueprint_flat)
        self.assertNotIn(
            "Never delete a Goal project or its Threads automatically",
            self.full_goal_blueprint_flat,
        )
        for marker in (
            "third real app-server automatic compaction as the only authorized "
            "automatic replacement trigger",
            "verified native `thread/fork` migration",
            "delete only the superseded Thread after context, role, model, permissions, "
            "and Goal/project ownership all pass",
            "Never create a local summary, request manual compaction, persist chat "
            "bodies, batch-delete Threads, or migrate internal native subagents",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.visual)

    def test_panel_project_stays_visible_steerable_and_single_owner(self) -> None:
        for marker in (
            "## Keep the project visible and steerable in Panel",
            "HIA Panel is the user-facing owner of project presentation and guidance",
            "Show one project container for the Goal",
            "its five project Threads",
            "current supported model and state",
            "change its model for later turns",
            "add guidance to that role or to the project",
            "routes the instruction to the same app-server Thread with `turn/start`",
            "saved default and the per-submission selector expose only **单个 AI / "
            "项目团队**",
            "Goal and Focus remain on their established surfaces",
            "Context Pack remains optional evidence",
            "EffectSpec remains transient",
            "project memory changes remain explicit",
            "Bridge owns app-server transport, Thread/project association",
            "Panel owns display and user guidance",
            "Do not create a second roster schema",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.full_goal_blueprint_flat)
        team_page_contract = self.full_goal_blueprint_flat.split(
            "## Keep the project visible and steerable in Panel", 1
        )[1].split("## Reject false complexity", 1)[0]
        for duplicate_heading in (
            "### Goal summary",
            "### Full blueprint",
            "### Current stage",
            "### Visual review evidence",
            "### Technical review evidence",
            "### Goal archive",
        ):
            with self.subTest(duplicate_heading=duplicate_heading):
                self.assertNotIn(duplicate_heading, team_page_contract)

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
        self.assertIn(
            "Select only the domains needed by the current stage card",
            self.build_review,
        )
        self.assertIn("never invented estimates", self.build_review)
        self.assertIn("Do not turn review into a score or exhaustive checklist", self.build_review)

    def test_internal_research_stays_compact_but_blueprint_stays_detailed(self) -> None:
        for marker in (
            "Keep internal subtask returns compact",
            "Synthesize them into the full blueprint or review ledger",
            "Do not forward full working transcripts to Supervisor",
            "always forward the complete synthesized blueprint when initial or "
            "material-revision authorization is due",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.build_review)
        self.assertIn(
            "Return a compact internal synthesis to Planning",
            self.research,
        )
        self.assertIn(
            "Synthesize it into the detailed user-visible blueprint",
            self.research,
        )

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
            "Read-only inspection does not trigger construction, a Full Blueprint",
            self.build_review,
        )
        self.assertIn("Do not start external research, build a full Brief", self.material)

    def test_negative_constraints_cannot_be_evaded_by_equivalent_geometry(self) -> None:
        for marker, contract in (
            (
                "explicit negative constraint as a hard acceptance requirement on "
                "the resulting geometry and semantic role",
                self.procedural,
            ),
            (
                "changing the node, script, or construction method must not recreate "
                "a forbidden form as an equivalent stand-in",
                self.procedural,
            ),
            (
                "Judge an explicit negative constraint by the resulting geometry and "
                "component role",
                self.procedural_architecture,
            ),
            (
                "semantically equivalent substitute for a forbidden form fails "
                "acceptance",
                self.modeling_validation,
            ),
            (
                "negative constraint is hard on the observed result and its semantic "
                "role",
                self.build_review,
            ),
            (
                "different construction method does not make an equivalent forbidden "
                "substitute acceptable",
                self.review,
            ),
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, contract)
        self.assertIn("用户负约束必须原样保留并进入验收", self.bridge_session)
        self.assertIn("禁止用语义等价或换皮替代绕过", self.bridge_session)

    def test_small_relational_assemblies_get_validation_without_research_overhead(
        self,
    ) -> None:
        for marker in (
            "small multi-part assembly whose endpoints, support, contact, spacing, "
            "clearance, or nonintersection must be correct",
            "also needs relation-aware validation even when its construction is simple",
            "does not by itself require knowledge retrieval or external research",
            "including a small one",
            "Do not load it for an isolated primitive",
        ):
            with self.subTest(procedural_marker=marker):
                self.assertIn(marker, self.procedural)
        for marker in (
            "small multi-part assembly's completion depends on endpoints, support, "
            "contact, spacing, clearance, or nonintersection",
            "Do not use for an isolated simple primitive",
        ):
            with self.subTest(review_marker=marker):
                self.assertIn(marker, self.review)
        self.assertIn("Keep a single primitive", self.procedural)
        self.assertIn(
            "execute without knowledge retrieval and run one necessary targeted "
            "validation",
            self.procedural,
        )

    def test_spatial_completion_requires_numeric_relationship_evidence(self) -> None:
        for marker in (
            "both endpoints resolve to their intended hosts",
            "intended span or bounds",
            "scale-relative tolerance",
            "clearance has a measured minimum",
            "forbidden intersection has an appropriate precise result",
            "AABB or packed bounds as a broad phase",
            "overlapping bounds identify a candidate rather than prove penetration",
            "Neither a viewport image, an error-free cook, nor generic validation "
            "silently proves precise nonintersection",
            "keep that narrow completion claim unverified",
        ):
            with self.subTest(validation_marker=marker):
                self.assertIn(marker, self.modeling_validation)
        for marker in (
            "measure both endpoint-to-host relationships",
            "full segment or envelope",
            "Endpoint contact alone does not prove support or clearance",
        ):
            with self.subTest(integrity_marker=marker):
                self.assertIn(marker, self.geometry_integrity)
        self.assertIn(
            "Bounds are only a broad phase; a viewport image, node existence, and a "
            "clean cook do not prove nonintersection",
            self.review,
        )
        self.assertIn(
            "Spatial intersection is an extension boundary, not a hidden heavy scan",
            self.hia_tools_schema,
        )

    def test_skill_workflows_resolve_version_sensitive_types_from_live_houdini(self) -> None:
        combined = "\n".join(
            (
                self.visual,
                self.material,
                self.technique_selection,
                self.full_goal_blueprint,
            )
        )
        for marker in (
            "version-sensitive",
            "active Houdini build",
            "relevant live node categories",
            "hia_search_node_types",
            "hia_node_help",
            "do not assume a fixed release name or versioned internal node type",
            "Record installed-type uncertainty rather than guessing a versioned type",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, combined)

    def test_risk_scaled_workflow_keeps_simple_edits_direct(self) -> None:
        for marker in (
            "**Direct:**",
            "**Focused:**",
            "**Full:**",
            "execute a known deterministic edit immediately and perform one necessary "
            "targeted validation",
            "Do not require local retrieval, a Full Blueprint, external research, capture, "
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
            "follow the `rollback.status` and `automatic_retry_safe` rule",
            "do not assume either a complete rollback or a partial scene change",
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
        self.assertIn(
            "Prefer modifying suitable existing nodes, then installed native Houdini "
            "nodes and parameter networks",
            self.hia_tools_schema,
        )
        self.assertIn("优先现有节点和标准原生节点网络", self.bridge_session)
        self.assertIn("场景内 Python SOP 或直接几何须说明必要性", self.bridge_session)

    def test_retry_and_runtime_identity_contracts_match_mcp_behavior(self) -> None:
        for marker in (
            "only verified `rolled_back` plus `automatic_retry_safe=true`",
            "`unknown` or `partial` validation and `NO_OBSERVED_EFFECT` do not "
            "trigger Undo",
            "`not_proven`, timeout, or possible external side effects require a "
            "targeted inspect/diff first",
            "consult relevant local help only when the cause is uncertain",
            "evidence gaps, not terminal Goal states or restart reasons",
            "continue with a corrected in-scope action when safe",
        ):
            with self.subTest(recovery_marker=marker):
                self.assertIn(marker, self.validation)
        for marker in (
            "`HOUDINI_SESSION_CHANGED`",
            "`HOUDINI_RUNTIME_SOURCE_CHANGED`",
            "`STALE_HOUDINI_RUNTIME`",
            "different HIA session, Houdini process, or executor module path",
            "disk-mtime or source-newer notice",
            "is advisory",
            "continue using the executor already loaded in that Houdini process",
            "Do not hot-reload the executor or switch routes",
        ):
            with self.subTest(identity_marker=marker):
                self.assertIn(marker, self.visual)
        for marker in (
            "Scene writes remain bound to the launcher session, Houdini process, "
            "and executor module path",
            "A newer executor source file on disk is only advisory",
            "the write continues against the version already loaded",
        ):
            with self.subTest(tool_identity_marker=marker):
                self.assertIn(marker, self.hia_tools_schema)
        for marker in (
            "Stop 后已发 HOM 仍可能收尾",
            "真实 HOM 异常仅失败本次",
            "dirty、automatic_retry_safe=false、unknown/partial/NO_OBSERVED_EFFECT",
            "只报告，不Undo、不终止Goal",
            "hom_may_still_execute=true",
            "必须保持串行 barrier",
            "源码更新提示，用加载版本；验新源码再重启",
        ):
            with self.subTest(bridge_marker=marker):
                self.assertIn(marker, self.bridge_session)
        for marker in (
            "`expected_outputs` 只隐式补目标存在性和节点错误检查",
            "`unknown`、`partial` 与 `NO_OBSERVED_EFFECT`",
            "`rollback.status`",
            "`automatic_retry_safe=true`",
            "运行时身份绑定 launcher session、Houdini PID",
            "读写工具都在 dispatch 前硬拒绝",
        ):
            with self.subTest(doc_marker=marker):
                self.assertIn(marker, self.hia_mcp_doc)
        self.assertNotIn("health 和只读工具仍可用于确认实际连接", self.hia_mcp_doc)
        self.assertNotIn("不会回滚", self.hia_mcp_doc)

    def test_context_and_knowledge_absence_do_not_gate_or_restart_goal(self) -> None:
        for marker in (
            "A Context Pack is optional evidence and Direct tasks skip it",
            "not a terminal Goal state or restart reason",
            "Do not repeat the same lookup",
            "mark only the unresolved claim unverified",
        ):
            with self.subTest(skill_marker=marker):
                self.assertIn(marker, self.knowledge_memory)
        self.assertIn(
            "Explicit include_context_pack=false suppresses Context Pack construction "
            "and knowledge retrieval",
            self.hia_tools_schema,
        )
        self.assertIn(
            "Local knowledge remains an optional single batched lookup and is not an "
            "execution gate",
            self.hia_tools_schema,
        )
        self.assertIn("hia_context 设 include_context_pack=false", self.bridge_session)
        self.assertIn("仅非平凡或不确定改动", self.bridge_session)

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

    def test_capture_quality_and_display_match_gate_visual_evidence(self) -> None:
        for marker in (
            "quality_status",
            "quality_reasons",
            "quality_metrics",
            "display_match",
            "hdr_display_mismatch_risk",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.validation)
        for marker in (
            "actual image content",
            "without image content is not visual observation",
            "capture-integrity and basic pixel-sanity evidence only",
            "For a project-team Full complex visual milestone",
            "start the independent read-only Visual Review and Technical Review project "
            "Threads in parallel",
            "Supervisor waits for both returns",
            "Ultra internal subagents may add non-overlapping review",
            "never replace the five project Threads",
            "Execution remains the sole writer",
            "model or internal-subagent availability never changes the evidence bar",
            "start both review Threads again in parallel with the same acceptance claims",
            "Supervisor-controlled evidence/rework loop without a fixed iteration count",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.validation)
        self.assertIn("capture-quality and display-match interpretation", self.visual)
        self.assertIn("selective preview and display-match contract", self.material)
        self.assertIn("capture-quality and display-match rule", self.review)
        self.assertNotIn("capture_quality", self.validation)
        for runtime_marker in (
            '"quality_status"',
            '"quality_scope": "capture_integrity_only"',
            '"quality_reasons"',
            '"quality_metrics"',
            '"display_match": "unverified"',
            '"hdr_display_mismatch_risk": "unverified"',
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
            "one representative frame for an ordinary static claim",
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
            "frame coverage or restoration is unproven",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.validation)
        self.assertIn(
            "risk-appropriate static or temporal preview evidence",
            self.procedural,
        )
        self.assertNotIn("low-resolution same-frame preview", self.procedural)

    def test_subjective_effect_experiment_is_goal_scoped_and_direct_tasks_are_exempt(
        self,
    ) -> None:
        for marker in (
            "temporary, bounded EffectSpec experiment contract",
            "current task context associated with the Goal",
            "do not add fields to the native Goal",
            "Direct deterministic work never enters this loop",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.visual)
        for marker in (
            "A simple deterministic edit does not enter the experiment loop",
            "goal, target and mutable scope",
            "a few visual or temporal objectives",
            "only relevant metrics and controls",
            "fixed frame samples",
            "one locked view for each experiment call",
            "observable success conditions",
            "do not automatically write them into Skill files or project memory",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.validation)

    def test_effect_experiment_combines_preview_data_ranking_and_bounded_iteration(
        self,
    ) -> None:
        for marker in (
            "one low-cost baseline and two or three bounded candidates",
            "actual previews for every specified frame in the call's locked view together with the "
            "relevant returned Houdini data",
            "one call supports several cameras",
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

    def test_effect_experiment_separates_codex_runtime_and_knowledge_responsibilities(
        self,
    ) -> None:
        for marker in (
            "`hia_run_effect_experiment` live tool contract",
            "runtime executes candidates and returns observed previews and Houdini "
            "facts only",
            "it does not rank them or create the EffectSpec or Evaluation",
            "Skill defines workflow and completion evidence only",
            "does not execute candidates, clear caches, assemble contact sheets",
            "copy the tool's input schema",
            "knowledge may inform control direction, version evidence, failure causes, "
            "and comparable cases",
            "does not prewrite this task's scoring criteria",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.validation)
        self.assertIn('"hia_run_effect_experiment"', self.hia_tools_schema)
        self.assertIn(
            "It never scores candidates, builds EffectSpec",
            self.hia_tools_schema,
        )

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

    def test_full_blueprint_rejects_false_complexity(self) -> None:
        for marker in (
            "Do not build or add another Agent, LLM, planner, autonomous RAG system, "
            "state machine",
            "Do not encode asset-specific construction recipes in this reference",
            "Do not measure quality by lines, nodes, Boxes, network boxes, calls, "
            "screenshots",
            "Do not turn the required 10,000/2,500/350 information floors into a "
            "quality score or a reusable generic Gate",
            "They are Houdini Full authorization minimums and remain insufficient "
            "without task-anchor, structure, anti-repetition/filler, and semantic "
            "completeness checks",
            "Do not confuse completeness with padding, repeated prose, speculative "
            "overdesign",
            "Do not add a second Planner to repair weak plans",
            "do not turn revision into a workflow state machine",
            "Do not fan out project Threads or live-scene calls",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.full_goal_blueprint_flat)


if __name__ == "__main__":
    unittest.main()
