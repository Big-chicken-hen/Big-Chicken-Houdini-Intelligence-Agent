---
name: houdini-visual-research
description: Research-led planning, procedural construction, and visual validation for complex Houdini modeling, FX, materials, animation, reference-image, ShaderToy/GLSL, Solaris, and Karma tasks. Use when a Houdini visual task has a quality bar beyond a primitive blockout, cites an image, artist, paper, tutorial, website, or specific effect, needs external technical research, or needs diagnosis after a Houdini tool/render/validation failure or explicit user dissatisfaction. Do not use for routine repository/plugin development or simple successful operations such as creating a Box or reading one parameter.
---

# Houdini Visual Research

Apply professional visual judgment without making small requests heavyweight. Keep Codex as the only intelligent decision-maker.

## Route the request

1. Treat a simple, deterministic scene operation such as creating one Box, reading a parameter, or making one known edit as a direct HIA MCP V2 operation. Do not research or expand its scope when it succeeds.
2. Treat modeling, FX, look development, animation, lighting, reference matching, or final-image work with a meaningful quality bar as a complex visual task. Follow the workflow below.
3. Research first when the user cites ShaderToy, GLSL, a paper, tutorial, website, artist, named effect, or reference image, or when the technique depends on current external information.
4. When a Houdini tool, node, validation, or render fails, or when the user explicitly says a technically successful result is visually unsatisfactory, read [visual-validation.md](references/visual-validation.md) and follow its diagnostic-report closure. Do not infer subjective dissatisfaction without the user or visible evidence.

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
4. Build a parameterized, inspectable network in coherent stages. Primitive nodes, spheres, boxes, and Copy operations may be internal construction components, but are not a finished complex result unless the user explicitly requests a blockout.
5. Prefer one or a small number of cohesive HOM Python batches through `hia_execute_hom` for complex creation or modification. Use narrower HIA tools only for necessary inspection, isolated edits, help, and verification; avoid a tool forest.
6. After construction, use `hia_validate` or `hia_scene_diff` only when their evidence is useful. Use `hia_capture_viewport` only when the task actually needs visual confirmation, then analyze the evidence and refine the highest-impact mismatch. Repeat with a bounded iteration count; do not confuse successful node creation with visual completion.
7. Report the chosen technique, material limitations, source ledger, validation evidence, and remaining gaps. Never present an unverified hypothesis as a confirmed result.

For the active scene, default to HIA MCP V2 and HOM. Keep live `hia_*` scene I/O to a few serial calls made by the main agent. Never fan out parallel, repetitive node-type or help searches; research subagents may investigate sources and propose plans, but must not operate the current Houdini session. Use FXHoudiniMCP only as a compatibility path when the launcher was explicitly set to that fallback, and describe fallback operations without binding the workflow to legacy tool names. Never switch the current scene to native `hython` automatically; use `hython` only when the user explicitly requests offline work, an independent HIP, batch processing, or background rendering. Never take over the screen.

## Guardrails

- Understand an external algorithm before translating it. Never claim arbitrary GLSL can be pasted unchanged into Karma or MaterialX.
- Prefer original authors, SideFX documentation/tutorials, original papers, and original projects. Do not copy substantial code when licensing is absent or unclear.
- Preserve the current scene and report partial changes after failures. Avoid unbounded retries; produce the diagnostic report described in visual-validation.md when its trigger is met.
- Do not create another agent, planner, semantic memory, database, monitoring service, or network service.
