# Project Knowledge and Memory

Use the existing HIA knowledge and memory tools to recover relevant context without replacing Codex judgment or external research. Load this guidance only for a complex, unfamiliar, version-sensitive, or project-specific task where prior knowledge can change the decision. A simple primitive, known parameter edit, or simple read does not trigger local retrieval or a memory write.

## Retrieve before deciding

1. Before implementation or web research, formulate the few related questions that matter now and make one batched `hia_local_help_search` over the live schema's relevant sources. Reuse returned Houdini, Skill, project, and user-tutorial passages and provenance instead of repeating equivalent searches. If project memory is not exposed by local help search, use `hia_project_memory` `search` separately only when durable prior context can affect the decision.
2. Use more local queries only when the first result exposes a materially different question. Do not turn retrieval into a fixed gate or a tool forest.
3. Treat hybrid ranking as retrieval assistance, not authority. Verify version-sensitive claims against the active build and distinguish confirmed project facts from indexed prose, old memory, or hypotheses.
4. If the user-selected local Qwen embedding encoder is unavailable, keep using the FTS5 lexical results; encoder availability or choice must not disable local help search or change memory policy. The encoder only supplies retrieval vectors. Codex remains responsible for interpretation, research, planning, scene decisions, visual review, and memory wording.
5. Local retrieval never replaces deep external research. When evidence is insufficient, conflicting, unfamiliar, reference-driven, or version-sensitive, continue multi-round web research using current SideFX documentation and original sources as described in `research-and-sources.md`.

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
- Keep the main task responsible for `record`, `supersede`, and `delete`. Research, modeling-plan, material-plan, and review subtasks may return concise evidence or a memory candidate but do not write competing entries.

Do not create another Agent, planner, summarizer, chat database, memory layer, vector store, resident service, or scheduler around these tools.
