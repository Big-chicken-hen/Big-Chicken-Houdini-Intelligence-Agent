---
name: houdini-visual-research
description: Reference decomposition, external technical and visual research, technique translation, and visual iteration for complex Houdini modeling, FX, materials, animation, reference-image, ShaderToy/GLSL, Solaris, and Karma tasks. Use when the user cites an image, artist, paper, tutorial, website, product, or specific effect; needs reference matching, unfamiliar or version-sensitive techniques, external sources, or visual diagnosis; or requires ShaderToy/GLSL translation. Pair with procedural modeling only for complete or substantial reference-driven construction. An explicitly requested blockout, proxy, placeholder, or technical test does not trigger procedural modeling, though this skill may still research its references. Do not use for an ordinary asset construction request with no research or reference need, routine repository/plugin development, a simple successful Box or one-parameter read, or an ordinary HOM error without unfamiliar technical judgment.
---

# Houdini Visual Research

Apply professional visual judgment without making small requests heavyweight. Keep Codex as the only intelligent decision-maker.

## Route the request

1. Treat a simple, deterministic scene operation such as creating one Box, reading a parameter, or making one known edit as a direct HIA MCP V2 operation. Do not research or expand its scope when it succeeds.
2. When complete or substantial reference-driven construction is requested, define the observable target and completion evidence; use `$houdini-procedural-modeling` to own model structure, procedural construction, and editability. An explicitly requested blockout, proxy, placeholder, or technical test may still use this skill for reference research, but does not trigger `$houdini-procedural-modeling` or get promoted to full construction.
3. Treat reference matching, unfamiliar modeling or FX methods, look development, animation, lighting, or final-image work with a meaningful visual result as complex research or visual judgment. Choose semantic research depth (`none`, `light`, or `deep`) from [research-and-sources.md](references/research-and-sources.md), without fixed call counts or gates.
4. Use deep research when the user cites ShaderToy, GLSL, a paper, or an unfamiliar technique, or when Houdini/version behavior is uncertain. Prefer original sources and current SideFX material.
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
2. Obtain only the context needed for the decision with `hia_context` or `hia_inspect`. When node knowledge is genuinely missing, use `hia_search_node_types` or `hia_node_help` narrowly. Do not impose a fixed node-type allowlist.
3. Research required references, record authorship and licensing, then choose the Houdini context from the actual visual and technical requirements.
4. For substantial modeling, provide reference decomposition, technique choices, and visual iteration guidance to `$houdini-procedural-modeling`; let it own model structure, controls, and network editability. For other visual work, build a parameterized, inspectable network in coherent stages.
5. Prefer one or a small number of cohesive HOM Python batches through `hia_execute_hom` for complex creation or modification. Use narrower HIA tools only for necessary inspection, isolated edits, help, and verification; avoid a tool forest.
6. After construction, use `hia_validate` or `hia_scene_diff` only when their evidence is useful. Use `hia_capture_viewport` only when the task actually needs visual confirmation, then analyze the evidence and refine the highest-impact mismatch. Repeat with a bounded iteration count; do not confuse successful node creation with visual completion.
7. Report the chosen technique, material limitations, source ledger, validation evidence, and remaining gaps. Never present an unverified hypothesis as a confirmed result.

For the active scene, default to HIA MCP V2 and HOM. Keep the main task as the sole writer of the current HIP and keep live `hia_*` scene I/O to a few serial calls. When useful, subtasks may perform source research, propose modeling or material plans, or conduct read-only review with `$houdini-artifact-review`; they return evidence and recommendations for the main task to decide and apply serially. This is a working method within Codex, not a new Agent system or backend. Never fan out parallel, repetitive node-type or help searches. Use FXHoudiniMCP only as a compatibility path when the launcher was explicitly set to that fallback, and describe fallback operations without binding the workflow to legacy tool names. Never switch the current scene to native `hython` automatically; use `hython` only when the user explicitly requests offline work, an independent HIP, batch processing, or background rendering. Never take over the screen.

## Guardrails

- Understand an external algorithm before translating it. Never claim arbitrary GLSL can be pasted unchanged into Karma or MaterialX.
- Prefer original authors, SideFX documentation/tutorials, original papers, and original projects. Do not copy substantial code when licensing is absent or unclear.
- Preserve the current scene and report partial changes after failures. Avoid unbounded retries; produce the diagnostic report described in visual-validation.md when its trigger is met.
- Leave Fast/serviceTier selection, conversation history, and disconnect/reconnect handling to the Panel and Bridge; do not simulate or implement them in this skill.
- Do not create another agent, planner, semantic memory, database, monitoring service, or network service.
