# Project Knowledge and Memory

Use the existing HIA knowledge and memory tools only when they can resolve real uncertainty; retrieval is not a gate for every scene write. Execute a known parameter change, connection between known nodes, delete, rename, reposition, or known color assignment directly and validate the live result. When node/parameter behavior, Houdini version support, a complex material/FX/simulation/Solaris/render workflow, historical project convention, or failure cause is uncertain, run exactly one relevant batched `hia_local_help_search` before the affected write. Treat it as the single shared lookup across active Houdini skills, put useful matches and provenance into compact task context, and reuse them in planning, execution, and validation. Neither a skipped lookup nor a performed lookup writes project memory automatically.

## Retrieve before deciding

1. When retrieval is triggered, batch only the few related questions that matter now. For substantial work, include the official task workflow, its critical data or parameters, and version cautions among the purposeful queries; retrieve only the domains the task needs. Reuse returned Houdini, Skill, project, and user-tutorial passages and provenance instead of repeating equivalent searches. If project memory is not exposed by local help search, use `hia_project_memory` `search` separately only when durable prior context can affect the decision.
2. Do not issue another local-help search when another skill activates, planning begins, or execution starts. Resolve a remaining exact node or API uncertainty with the appropriate installed help, and continue necessary SideFX/original-source web research for complex work. A materially changed user scope is a new request, not a reason to repeat the same lookup silently.
3. Treat hybrid ranking as retrieval assistance, not authority. Verify version-sensitive claims against the active build and distinguish confirmed project facts from indexed prose, old memory, or hypotheses. A retrieved rule is an input to execution, not evidence that the resulting Houdini network satisfies it.
4. If the user-selected local Qwen embedding encoder is unavailable, keep using the FTS5 lexical results; encoder availability or choice must not disable local help search or change memory policy. The encoder only supplies retrieval vectors. Codex remains responsible for interpretation, research, planning, scene decisions, visual review, and memory wording.
5. A bundled workflow card is a planning summary, not proof of current node behavior. Local retrieval never replaces deep external research. When its evidence is incomplete, conflicting, unfamiliar, reference-driven, or version-sensitive, continue multi-round web research through the linked/current SideFX documentation and original sources as described in `research-and-sources.md`.

When retrieval informs a Focused or Full write, convert relevant constraints into the compact semantic expectations defined in `build-brief-and-review.md`. After the write, ask the current `hia_validate` semantic-expectation capability to test them according to its live tool contract. Keep the Skill at the intent-and-evidence level rather than copying the MCP payload schema. Do not claim success merely because nodes exist, Python returned successfully, or cooking produced no error; the expected live-scene mapping, field state, range, or relationship must be proven or reported once as unknown or not proven.

## Shape the lookup

- For a complex question with several concepts, use one batch containing two to four purposeful subqueries. Separate only decisions that need different evidence, such as node or capability location, parameters and inputs/outputs, operating principle, and practical configuration. Do not fan out ten or more synonymous or repeated queries.
- For an exact HOM or Python API question, prefer the installed Houdini and official HOM or full-help source. For an exact node type, use `hia_node_help`; resolve an uncertain type with a narrow `hia_search_node_types` call first.
- For project implementation, HDA, or authorized tutorial questions, prefer `project` and `user`. Query `memory` only for a durable project decision, preference, asset entry point, verified lesson, or reusable workflow that can affect the task.
- A zero-result capability search is not proof that HIA or Houdini lacks the capability. Inspect `empty_reason` and `catalog_health` when returned; otherwise list the capability matrix or use an unfiltered `hia_search_capabilities` call before concluding that support is absent.
- The service already performs its bounded strict-AND to single-OR relaxation. Do not add query-rewrite loops or another fallback cascade in the Skill.

Depend only on tool availability through HIA MCP or the stable HIA CLI surface. Do not condition retrieval or memory on a particular desktop start path.

Use the live MCP schema for both tools. The stable `hia_project_memory` actions are `record`, `search`, `list`, `delete`, and `supersede`; do not invent unconfirmed argument names or payload shapes.

## Keep only durable project memory

- At task start, use `search` only when durable prior context can affect the work. Use `list` for an explicit inventory or memory-management need, not routine startup.
- At task end, use `record` only when the task established genuinely new, long-lived, reusable information:
  - an explicit user preference;
  - a confirmed project decision;
  - a stable asset structure or entry point;
  - a verified failure lesson or version-compatibility fact;
  - a reusable workflow that has evidence beyond a one-off attempt.
- When the same fact changes, use `supersede` rather than adding a duplicate. When the user asks to forget a fact, use `delete`.
- Do not store temporary progress, plans, unchecked assumptions, speculative causes, transient scene state, one-off error noise, full conversations, or unrelated source/code dumps.
- Never store secrets, access tokens, authorization headers, cookies, credentials, private keys, or sensitive content unrelated to the reusable fact.
- A memory hit is context, not current proof. Recheck live scene state, software version, paths, and user intent when they can drift.
- Do not record a procedure merely because nodes cooked or a tool returned success. Only a real-Houdini workflow that meets the Verified Procedure contract, including user acceptance, may become reusable workflow memory. Failed attempts remain diagnostics or unverified experience and never trigger an automatic memory write.
- Keep the main task responsible for `record`, `supersede`, and `delete`. Research, modeling-plan, material-plan, and review subtasks may return concise evidence or a memory candidate but do not write competing entries.

Do not create another Agent, planner, summarizer, chat database, memory layer, vector store, resident service, or scheduler around these tools.
