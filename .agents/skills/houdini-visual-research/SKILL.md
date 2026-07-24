---
name: houdini-visual-research
description: Reference decomposition, external technical and visual research, technique translation, and visual iteration for complex Houdini modeling, FX, materials, animation, reference-image, ShaderToy/GLSL, Solaris, and Karma tasks. Use when the user cites an image, artist, paper, tutorial, website, product, or specific effect; needs reference matching, unfamiliar or version-sensitive techniques, external sources, or visual diagnosis; or requires ShaderToy/GLSL translation. Pair with procedural modeling only for complete or substantial reference-driven construction. An explicitly requested blockout, proxy, placeholder, or technical test does not trigger procedural modeling, though this skill may still research its references. Do not use for an ordinary asset construction request with no research or reference need, routine repository/plugin development, a simple successful Box or one-parameter read, or an ordinary HOM error without unfamiliar technical judgment.
---

# Houdini Visual Research

Apply professional visual judgment without making small requests heavyweight. Keep Codex as the only intelligent decision-maker.

## Route the request

1. Treat a simple, deterministic scene operation such as creating one Box, reading a parameter, or making one known edit as a direct HIA MCP V2 operation. Do not research or expand its scope when it succeeds.
2. When complete or substantial reference-driven construction is requested, define the observable target and completion evidence; use `$houdini-procedural-modeling` to own model structure, procedural construction, and editability. An explicitly requested blockout, proxy, placeholder, or technical test may still use this skill for reference research, but does not trigger `$houdini-procedural-modeling` or get promoted to full construction.
3. Treat reference matching, unfamiliar modeling or FX methods, material and look development, rendering, simulation, animation, lighting, or final-image work with a meaningful visual result as complex research or visual judgment. Choose semantic research depth (`none`, `light`, or `deep`) from [research-and-sources.md](references/research-and-sources.md), without fixed call counts or gates.
4. Use deep research when the user cites ShaderToy, GLSL, a paper, or an unfamiliar technique, or when Houdini/version behavior is uncertain. Continue automatically across as many research rounds and sources as the evidence needs; prefer original sources and current SideFX material.
5. Treat an ordinary HOM/tool syntax or runtime error as execution diagnosis, not visual research by itself. Preserve the exact error and fix it directly; use [visual-validation.md](references/visual-validation.md) for failed complex visual work or explicit dissatisfaction, and research only when the failure exposes an unfamiliar or version-sensitive technical question.

Searching this repository's src, services, docs, or contracts is software development, not visual research. Read project source only when the user explicitly asks to develop or debug the plugin.

## Load only the needed guidance

- Read [technique-selection.md](references/technique-selection.md) for complex tasks, reference-driven assets, or uncertainty between SOP/VEX, COP, MaterialX, DOP/solvers, Solaris/LOPs, and Karma.
- Read [research-and-sources.md](references/research-and-sources.md) whenever external sources or reference works are involved.
- Read [shader-translation.md](references/shader-translation.md) for ShaderToy, GLSL, screen-space effects, ray marching, or shader-inspired work.
- Read [visual-validation.md](references/visual-validation.md) for every complex task before judging completion and for any failure or explicit dissatisfaction report.

Keep references one level deep; do not load unrelated references.

## Execute complex visual work

1. Define the intended image or asset in observable terms: silhouette, proportions, scale, motion, material response, composition, and required editability. Separate user constraints from assumptions.
2. Obtain only the context needed for the decision with `hia_context` or `hia_inspect`. When node knowledge is genuinely missing, combine related keywords or help targets into one batch and reuse the result. Do not impose a fixed node-type allowlist.
3. Research required references iteratively, maintain the complete source ledger from [research-and-sources.md](references/research-and-sources.md), then choose the Houdini context from the actual visual and technical requirements.
4. For substantial modeling, provide reference decomposition, technique choices, and visual iteration guidance to `$houdini-procedural-modeling`; let it own model structure, controls, and network editability. For other visual work, build a parameterized, inspectable network in coherent stages.
5. Prefer one or a small number of cohesive HOM Python batches through `hia_execute_hom` for complex creation or modification. Use narrower HIA tools only for necessary inspection, isolated edits, help, and verification; avoid a tool forest.
6. After construction, use `hia_validate` or `hia_scene_diff` only when their evidence is useful. For a complex visual task, define meaningful visual milestones instead of a fixed stage count. Ordinarily consider the primary structure or silhouette becoming complete, material or lighting response becoming complete when it is in scope, and the last handoff check. Merge adjacent milestones and skip any stage that produces no meaningful visible change.
7. At each applicable milestone, automatically capture a low-resolution preview with `hia_capture_viewport`: use a same-frame `flipbook` at `640 x 360` with `return_image=true`, unless the intended aspect ratio requires a similarly bounded low resolution. For animation or simulation, capture only selected representative or key frames as separate same-frame previews; do not review a long continuous flipbook. Do not capture simple deterministic tasks or every small edit.
8. Give the preview plus relevant `hia_validate` or `hia_scene_diff` evidence to `$houdini-artifact-review` for a read-only review. Compare the largest visible deviation in proportions or silhouette, floating or intersecting parts, support and contact, composition, material response, exposure, transparency, and reference consistency. Set a small task-specific iteration budget; on each pass let the main task fix only the highest-impact area, recapture the same evidence, and stop as soon as the requested bar is met.
9. If live capture is unavailable, mark the milestone visually unverified and do not claim visual completion. Report the chosen technique, material limitations, source ledger, validation evidence, and remaining gaps. Write reusable research as an original short memo under the draft-to-verified contract in [research-and-sources.md](references/research-and-sources.md); never present an unverified hypothesis as a confirmed result or promote it into the formal knowledge index.

For the active scene, default to HIA MCP V2 and HOM. Keep the main task as the sole writer of the current HIP and keep live `hia_*` scene I/O to a few serial calls. When useful, subtasks may perform source research, propose modeling or material plans, or conduct read-only review with `$houdini-artifact-review`; they return evidence and recommendations for the main task to decide and apply serially. This is a working method within Codex, not a new Agent system or backend. Never fan out parallel, repetitive node-type or help searches. Use FXHoudiniMCP only as a compatibility path when the launcher was explicitly set to that fallback, and describe fallback operations without binding the workflow to legacy tool names. Never switch the current scene to native `hython` automatically; use `hython` only when the user explicitly requests offline work, an independent HIP, batch processing, or background rendering. Never take over the screen.

## Guardrails

- Understand an external algorithm before translating it. Never claim arbitrary GLSL can be pasted unchanged into Karma or MaterialX.
- Prefer original authors, SideFX documentation/tutorials, original papers, and original projects. Do not copy substantial code when licensing is absent or unclear.
- Preserve the current scene and report partial changes after failures. Avoid unbounded retries; produce the diagnostic report described in visual-validation.md when its trigger is met.
- Leave Fast/serviceTier selection, conversation history, and disconnect/reconnect handling to the Panel and Bridge; do not simulate or implement them in this skill.
- Do not create another agent, planner, semantic memory, database, monitoring service, or network service.
