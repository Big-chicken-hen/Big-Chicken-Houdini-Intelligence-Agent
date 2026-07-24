# Research and Sources

Use external research when it materially affects technique or visual fidelity. For complex, unfamiliar, reference-driven, material, rendering, simulation, animation, version-sensitive, or ShaderToy work, research iteratively and cross-check as many high-quality sources as the decision genuinely needs. Codex may continue automatically through multiple research rounds and sources while the investigation remains in scope; do not impose a fixed limit on search rounds or source count. Do not browse for a simple successful Box, known direct operation, or single-parameter read.

Research is required when the user names or links a ShaderToy, GLSL example, paper, tutorial, website, artist, project, named effect, or reference image, and when current Houdini/version limitations could change the decision.

## Research depth

- `none`: Use for a simple, deterministic operation, known direct edit, or known parameter read when external evidence would not change the result.
- `light`: Use focused current sources for a familiar visual task when a small technique, renderer, or version check can improve the implementation.
- `deep`: Search iteratively and cross-check multiple high-quality sources for ShaderToy/GLSL, papers, unfamiliar techniques, complex materials, rendering, simulation, animation, ambiguous references, or uncertain Houdini/version behavior.

Treat these as semantic reasoning labels, not fixed search counts, time budgets, or approval gates. Change depth as evidence develops.

## Source order

1. Current SideFX documentation and official SideFX tutorials for Houdini behavior.
2. Original papers, authors, projects, repositories, artwork, and exact referenced pages, including the original ShaderToy page.
3. The upstream standard or project documentation, such as MaterialX or OpenUSD.
4. Reliable technical explanations, extending to community experience only when primary sources leave a practical gap.

Do not treat searches through this repository's src, services, docs, or contracts as visual research. Read those only when the user explicitly requests plugin development or debugging.

## Research procedure

1. Plan the current stage's evidence needs and high-value starting sources before retrieval; do not impose a fixed limit on search rounds or source count.
2. Prefer Codex-native web/search tools. Run additional searches, open multiple pages, and follow necessary citations as the investigation develops; cross-check important claims before choosing the Houdini implementation.
3. Reuse already retrieved content. Reduce duplicate visits and aimless searching, not research depth.
4. Only when native web/search is unavailable, use one or a small number of read-only batch network commands. Combine related URLs instead of requesting PowerShell approval page by page; do not request global Shell auto-approval or create an approval system.
5. Read primary pages rather than relying on search snippets. For version-sensitive behavior, check the active Houdini build and current documentation.
6. Extract only reusable facts: algorithm stages, coordinate assumptions, data flow, constraints, renderer support, and quality cues.
7. Keep a compact source ledger in the task handoff, runtime research memo, or requested artifact. Use one row per source or local reference and preserve all columns:

   | Title/source | Author/owner | URL/path | Access date | License/status | Houdini version/build | How used | Verification status | Verification evidence |
   | --- | --- | --- | --- | --- | --- | --- | --- | --- |

8. Record the active Houdini version/build that a version-sensitive source or result applies to; use `unknown` rather than inferring it.
9. Mark inaccessible pages, ambiguous attribution, and unsupported inferences explicitly.

## Research memo and knowledge promotion

Write findings as an original short memo that captures the decision, reusable facts, Houdini translation, constraints, sources, and remaining uncertainty. Summarize and re-derive; do not copy a tutorial, paper, repository, or ShaderToy implementation into the memo.

Keep every draft, partial synthesis, rejected approach, and hypothesis only under:

    <project-root>/.runtime/cache/research/<thread-or-turn-id>/

Treat that directory as untracked runtime state. A draft may use `source-only`, `hypothesis`, `rejected`, or `unverified` in the ledger's **Verification status** column. Source authority, a successful web lookup, plausible reasoning, an offline fake, and node creation alone are not real Houdini verification.

Mark a result `verified` only after the relevant claim has been reproduced or directly observed in real Houdini on the recorded version/build. Put concrete evidence in **Verification evidence**, such as the live HIP and node paths, frame or time range, parameter or cook result, validation output, and a representative viewport capture or render when the claim is visual. State the narrower verified claim rather than promoting an entire technique from one observation.

Only a `verified` original memo with its complete source ledger and verification evidence may be copied or linked into a repository-maintained formal tracked knowledge index. If real Houdini evidence is unavailable, keep the memo in `.runtime` and report it as unverified; do not create an index entry. A Markdown memo and index link are searchable records, not a new agent, RAG layer, vector store, database, or service.

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
