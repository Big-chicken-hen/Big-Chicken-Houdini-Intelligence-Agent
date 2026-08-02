# Research and Sources

Use the uncertainty-triggered local-retrieval rule in `knowledge-and-memory.md`; do not search merely because work is Full, complex, reference-driven, material, rendering, simulation, animation, or because a scene write will occur. Use external research before the affected write only when it can materially change technique, node or parameter choice, version compatibility, licensing, or visual fidelity. Iterate through only as many high-quality sources as that decision genuinely needs. A known route, supplied reference that can be directly decomposed, deterministic edit, and read-only inspection require no external research.

External research is required when the user names or links a specific source whose relevant contents were not supplied, or when uncertain current Houdini/version limitations can change the decision. A supplied image by itself requires visual decomposition, not a web search; search its origin or surrounding technique only when that evidence changes construction or acceptance.

## Research depth

- `none`: Use for a simple, deterministic operation, known direct edit, or known parameter read when external evidence would not change the result.
- `light`: Use focused current sources when a small technique, renderer, licensing, or version check can materially improve the implementation.
- `deep`: Use multiple research rounds and sources only for an unsupplied ShaderToy/GLSL implementation, paper, unfamiliar technique, ambiguous external reference, or uncertain Houdini/version behavior that materially affects the decision.

Treat these as semantic reasoning labels, not fixed search counts, time budgets, or approval gates. Change depth as evidence develops.

## Source order

1. Current SideFX documentation and official SideFX tutorials for Houdini behavior.
2. Original papers, authors, projects, repositories, artwork, and exact referenced pages, including the original ShaderToy page.
3. The upstream standard or project documentation, such as MaterialX or OpenUSD.
4. Reliable technical explanations, extending to community experience only when primary sources leave a practical gap.

Do not manually search this repository's src, services, or contracts as visual research. Approved project Skills and current documentation returned by `hia_local_help_search` are local knowledge, not authorization to inspect ordinary source; read that source only for explicit plugin development or debugging.

## Research procedure

1. Plan the current stage's evidence needs and high-value starting sources before retrieval; do not impose a fixed limit on search rounds or source count.
2. When the uncertainty rule triggered local retrieval, consume that single shared result; do not issue another local-help search merely because external research begins or this Skill also triggered. Reuse its Houdini, Skill, project, and user-tutorial passages. If project memory was not exposed by that search, use `hia_project_memory` `search` separately only when durable prior context can affect the decision. Do not use retrieval for a known deterministic edit or a purely read-only inspection.
3. Treat local matches as leads with provenance, not as a reason to reduce research depth or as automatic proof. When they are insufficient, conflicting, stale, or the task needs external visual or technical evidence, prefer Codex-native web/search tools and continue through as many rounds, pages, and primary sources as the decision needs.
4. Reuse already retrieved content. Reduce duplicate visits and aimless searching, not research depth.
5. Only when native web/search is unavailable, use one or a small number of read-only batch network commands. Combine related URLs instead of requesting PowerShell approval page by page; do not request global Shell auto-approval or create an approval system.
6. Read primary pages rather than relying on search snippets. For version-sensitive behavior, check the active Houdini build and current documentation.
7. Extract only reusable facts: algorithm stages, coordinate assumptions, data flow, constraints, renderer support, and quality cues.
8. Keep a compact source ledger in the task handoff, runtime research memo, or requested artifact. Use one row per source or local reference and preserve all columns:

   | Title/source | Author/owner | URL/path | Access date | License/status | Houdini version/build | How used | Verification status | Verification evidence |
   | --- | --- | --- | --- | --- | --- | --- | --- | --- |

9. Record the active Houdini version/build that a version-sensitive source or result applies to; use `unknown` rather than inferring it.
10. Mark inaccessible pages, ambiguous attribution, and unsupported inferences explicitly.

## Research memo and knowledge promotion

Write findings as an original short memo that captures the decision, reusable facts, Houdini translation, constraints, sources, and remaining uncertainty. Summarize and re-derive; do not copy a tutorial, paper, repository, or ShaderToy implementation into the memo.

Return a compact internal synthesis to Planning: decision, evidence, risks, necessary source links, and unverified items. Synthesize it into the detailed user-visible blueprint; keep the full ledger or memo at its referenced path and do not paste source documents, every search result, or a long research-subtask transcript into the Supervisor context.

Keep every draft, partial synthesis, rejected approach, and hypothesis only under:

    <project-root>/.runtime/cache/research/<thread-or-turn-id>/

Treat that directory as untracked runtime state. A draft may use `source-only`, `hypothesis`, `rejected`, or `unverified` in the ledger's **Verification status** column. Source authority, a successful web lookup, plausible reasoning, an offline fake, and node creation alone are not real Houdini verification.

Mark a result `verified` only after the relevant claim has been reproduced or directly observed in real Houdini on the recorded version/build. Put concrete evidence in **Verification evidence**, such as the live HIP and node paths, frame or time range, parameter or cook result, validation output, and a representative viewport capture or render when the claim is visual. State the narrower verified claim rather than promoting an entire technique from one observation. A reusable procedure also requires the user's acceptance of the observed result.

Only a `verified` original memo with its complete source ledger and verification evidence may be copied or linked into a repository-maintained formal tracked knowledge index. If real Houdini evidence is unavailable, keep the memo in `.runtime` and report it as unverified; do not create an index entry. Use only the existing HIA knowledge layer; this promotion rule does not authorize another Agent, planner, summarizer, chat database, knowledge or memory layer, service, or scheduler.

## Copyright and licensing

- Record the displayed license for code, assets, images, and downloadable projects. Do not infer a license from public visibility.
- If licensing is missing or unclear, study the idea and write an original implementation; do not copy substantial code or reconstruct it line by line.
- Quote only the minimum necessary and attribute it. Prefer an original summary of the technique.
- Do not download entire documentation sites, ShaderToy collections, tutorial archives, or large page dumps.
- Keep user-supplied reference images in their existing authorized locations; do not republish them.

## Maintained primary links

These links were checked on 2026-07-18; re-check them when current behavior matters:

- [SideFX HOM / Python scripting](https://www.sidefx.com/docs/houdini/hom/)
- [SideFX Attribute Wrangle SOP](https://www.sidefx.com/docs/houdini/nodes/sop/attribwrangle)
- [SideFX Copernicus workflows](https://www.sidefx.com/docs/houdini/copernicus/working_with_cops.html)
- [SideFX Solaris and USD basics](https://www.sidefx.com/docs/houdini/solaris/usd.html)
- [SideFX MaterialX in Solaris](https://www.sidefx.com/docs/houdini/solaris/materialx)
- [SideFX Karma](https://www.sidefx.com/docs/houdini/solaris/karma.html)
- [MaterialX current specification](https://materialx.org/Specification.html)
- [Heartfelt by Martijn Steinrucken / BigWings](https://www.shadertoy.com/view/ltffzl)
