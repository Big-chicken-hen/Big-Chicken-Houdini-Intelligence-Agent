---
name: houdini-material-lookdev
description: Material and LookDev planning, authoring, and validation for substantive Houdini shader, MaterialX, Karma, texture, UV, glass, liquid, wet-surface, metal, plastic, SSS, transmission, emission, displacement, and material-lighting tasks. Use for complex material work and for a native-team material or lighting subtask. Do not use for a simple direct assignment such as changing one color to red, reading one parameter, or routine plugin development.
---

# Houdini Material LookDev

Create editable, traceable materials that serve the requested image without making simple assignments heavyweight.

## Route the task

1. Treat a deterministic operation such as changing a known existing material color to red as a direct edit: execute without knowledge retrieval and validate the actual binding or parameter result. Do not start external research, build a full Brief, rebuild the shader, or require a render loop when the requested change is already clear.
2. Treat a bounded familiar binding, texture, or channel correction as Focused: use only the relevant semantic check and, when the result is visually ambiguous, one suitable view. Do not expand it into a full LookDev loop by default.
3. Treat a material identity, multi-channel surface, renderer-dependent shader, color-management uncertainty, reference match, or material-lighting handoff as Full LookDev and follow only the applicable workflow below.
4. Keep the Director as the sole writer of the current HIP. When running as a material or lighting subtask, return a material plan, parameter and node choices, a script draft, risks, and validation advice; never write the live scene in parallel.

For substantive material work or uncertain node, parameter, renderer, version, color-management, or failure behavior, use the shared [knowledge-and-memory.md](../houdini-visual-research/references/knowledge-and-memory.md) contract before writing. If the main task or visual-research Skill already completed that lookup, reuse it and do not run a LookDev-specific duplicate. Also read [build-brief-and-review.md](../houdini-visual-research/references/build-brief-and-review.md) only for substantive LookDev, contribute the material, lighting, risk, output, and acceptance decisions needed by the compact Brief, and use external research when the visual-research contract requires it.

## Model the material first

Before building MaterialX for a substantive task, capture a compact reasoning model:

- Identify the substrate and its base optical identity, any surface layers, and their condition.
- Name the recognition cues, their macro/meso/micro scale, and the spatial logic that places them.
- State the Karma CPU/XPU target and only the renderer constraints relevant to the look.
- Decide what visual evidence is needed to judge the material rather than merely prove that the graph cooks.

This is a reasoning sketch, not a form or gate. Skip it for a direct edit.

## Develop the look

1. Read only the necessary current state: render context and target, relevant geometry, UVs and primvars, existing materials and bindings, available texture paths, and the observable visual goal.
2. For a new Karma material or render task, default to modern Solaris/USD with a Material Library LOP and MaterialX, normally starting from `mtlxstandard_surface` plus only the MaterialX nodes required by the look. Do not habitually route new Karma work through `/mat`, Principled Shader, legacy VOP materials, or Mantra. Use a legacy route only when the user explicitly requests it, the existing project requires compatibility, or the target renderer is not Karma; state that reason briefly.
3. Confirm current availability before authoring. Reuse any uncertainty-triggered local-help result, and when exact installed types remain uncertain, query `hia_search_node_types` and `hia_node_help` narrowly and serially. Treat `mtlxstandard_surface` as the intended MaterialX standard-surface concept, not permission to hard-code a versioned internal type name.
4. Choose the representation and input sources from the deliverable. Textures are one source; use UV or position, SOP attributes, USD primvars, material IDs, curvature, cavity, height, proximity, or painted masks only when they explain a required visual cue. Use MaterialX/Karma for portable rendered shading and procedural maps when external textures are unnecessary.
5. Establish the substrate's optical identity before adding wear, grunge, stains, or wetness. Place secondary effects by physical logic such as contact, exposed edges, gravity, cavities, and drainage, not by generic breakup alone.
6. Treat noise only as a variation source, never as the material concept. Let one signal drive multiple channels only when a physical cause links them; otherwise keep their patterns and scales appropriately independent.
7. Check only channels the task needs: colorspace and texture channel interpretation; UVs and required primvars; normal or bump; roughness and metalness; IOR, transmission, opacity, or SSS; displacement; emission; and material assignment or binding. Keep units, scales, and renderer support explicit.
8. For a substantive network, let the main task use one or a small number of cohesive `hia_execute_hom` batches to create or edit nodes, parameters, bindings, and layout. Avoid a forest of tiny calls.
9. Build a procedural material when maps are unavailable and the look can be derived. When external visual or technical references materially affect the result, use `$houdini-visual-research`; prefer original or SideFX sources, record licensing, and never download unknown-license textures or code blindly.
10. Before a Focused or Full write, distill retrieved material rules into explicit semantic expectations: binding target, required primvar and ownership, texture channel/colorspace intent, compatible node or renderer route, and any meaningful value/range constraint. After writing, ask the current `hia_validate` semantic-expectation capability to test those claims through its live contract, together with only relevant node, path, or geometry evidence. Do not copy its payload schema into this Skill. A cook without errors does not prove a correct binding, color interpretation, or optical result.
11. Validate selectively with existing tools such as `hia_validate`, `hia_material_render_summary`, and `hia_scene_diff`. Check only relevant node errors, bindings, texture paths, primvars, and semantic expectations. When visual ambiguity warrants it, use suitable diagnostic lighting to expose highlight shape, roughness, transmission, normal or displacement scale, and layer mixing before judging the target camera.
12. For Full LookDev, follow the selective preview and display-match contract in `$houdini-visual-research` when a visual claim needs evidence. If HDR/OCIO or display parity is unproven, do not use the screenshot as final color evidence. Supply usable evidence to a read-only Lookdev Reviewer or `$houdini-artifact-review` only when review can change the handoff. Skip capture for direct assignments or visually unchanged stages.
13. When the look misses the target, classify the likely source before editing: material, geometry or bevels, UV or scale, lighting or reflection environment, camera, Karma support, display transform, or post-processing. Do not respond by blindly adding noise or grunge.
14. Hand off an editable network with named controls, source and texture provenance, bindings, assumptions, validation evidence, and remaining limitations. Valid nodes, connected textures, and an error-free cook are implementation evidence, not proof of visual completion; a default gray material or single-color placeholder is not completion for a substantive task.

## Keep the network editable

1. Before executing a substantive build, plan only the semantic regions it needs: inputs/coordinates, semantic masks, substrate/layers, optical channels, dedicated normal/displacement lanes, and assembly/output.
2. For a new graph, run the main semantic flow from top to bottom. Expand same-stage inputs, masks, channels, and parallel MaterialX branches left to right, then merge them downward into the trunk. Avoid upward back-connections, wires through nodes, and unnecessary long diagonals. When editing an existing graph, inherit its prevailing direction and style instead of forcing a re-layout.
3. Name nodes by responsibility and route shared masks through meaningful named hubs or dots. Use `layoutChildren()` only for a rough first pass; manually position a complex graph by semantic flow before handoff. Use a Network Box or subnet only for a complete layer, mask generator, or clearly reusable module. Never box nodes merely by type or use a subnet to hide tangled wiring.
4. For every substantial branch, state its visual purpose, placement reason, scale, affected channels, and validation method. Delete or bypass redundant Noise/Multiply/Ramp branches that make no visible contribution.

## Guardrails

- Do not add MCP tools, an Agent backend, planner, summarizer, chat database, second knowledge or memory system, scoring platform, scheduler, or fixed approval or iteration gate.
- Do not inspect every possible channel by ritual; follow the actual material and renderer requirements.
- Do not claim visual completion from successful node creation alone. If representative visual evidence is unavailable, report that the look remains visually unverified.
