---
name: houdini-material-lookdev
description: Material and LookDev planning, authoring, and validation for substantive Houdini shader, MaterialX, Karma, texture, UV, glass, liquid, wet-surface, metal, plastic, SSS, transmission, emission, displacement, and material-lighting tasks. Use for complex material work and for a native-team material or lighting subtask. Do not use for a simple direct assignment such as changing one color to red, reading one parameter, or routine plugin development.
---

# Houdini Material LookDev

Create editable, traceable materials that serve the requested image without making simple assignments heavyweight.

## Route the task

1. Treat a deterministic operation such as changing an existing material color to red as a direct edit. Do not start research, rebuild the shader, or require a render loop when the requested change is already clear.
2. Treat a material identity, multi-channel surface, renderer-dependent shader, texture/UV problem, or material-lighting handoff as substantive LookDev and follow the workflow below.
3. Keep the main task as the sole writer of the current HIP. When running as a material or lighting subtask, return a material plan, parameter and node choices, a script draft, and validation advice; never write the live scene in parallel.

## Model the material first

Before building MaterialX for a substantive task, capture a compact reasoning model:

- Identify the substrate and its base optical identity, any surface layers, and their condition.
- Name the recognition cues, their macro/meso/micro scale, and the spatial logic that places them.
- State the Karma CPU/XPU target and only the renderer constraints relevant to the look.
- Decide what visual evidence is needed to judge the material rather than merely prove that the graph cooks.

This is a reasoning sketch, not a form or gate. Skip it for a direct edit.

## Develop the look

1. Read only the necessary current state: render context and target, relevant geometry, UVs and primvars, existing materials and bindings, available texture paths, and the observable visual goal.
2. Choose the representation and input sources from the deliverable. Textures are one source; use UV or position, SOP attributes, USD primvars, material IDs, curvature, cavity, height, proximity, or painted masks only when they explain a required visual cue. Use MaterialX/Karma for portable rendered shading and procedural maps when external textures are unnecessary. Do not hard-code version-specific node names. When node behavior is uncertain, call `hia_search_node_types` and then `hia_node_help` narrowly and serially.
3. Establish the substrate's optical identity before adding wear, grunge, stains, or wetness. Place secondary effects by physical logic such as contact, exposed edges, gravity, cavities, and drainage, not by generic breakup alone.
4. Treat noise only as a variation source, never as the material concept. Let one signal drive multiple channels only when a physical cause links them; otherwise keep their patterns and scales appropriately independent.
5. Check only channels the task needs: colorspace and texture channel interpretation; UVs and required primvars; normal or bump; roughness and metalness; IOR, transmission, opacity, or SSS; displacement; emission; and material assignment or binding. Keep units, scales, and renderer support explicit.
6. For a substantive network, let the main task use one or a small number of cohesive `hia_execute_hom` batches to create or edit nodes, parameters, bindings, and layout. Avoid a forest of tiny calls.
7. Build a procedural material when maps are unavailable and the look can be derived. When external visual or technical references materially affect the result, use `$houdini-visual-research`; prefer original or SideFX sources, record licensing, and never download unknown-license textures or code blindly.
8. Validate selectively with existing tools such as `hia_validate`, `hia_material_render_summary`, and `hia_scene_diff`. Check relevant node errors, bindings, texture paths, and primvars. When visual ambiguity warrants it, use suitable diagnostic lighting to expose highlight shape, roughness, transmission, normal or displacement scale, and layer mixing before judging the target camera.
9. For substantive LookDev, follow the low-resolution stage-preview contract in `$houdini-visual-research` when the material or lighting response becomes meaningfully complete and before handoff if that is a distinct visible stage. Supply the preview to read-only `$houdini-artifact-review`; let the main task fix only the highest-impact visible material, exposure, transparency, lighting, or reference mismatch within a small task-specific iteration budget. Skip capture for direct assignments or visually unchanged stages, and stop as soon as the requested bar is met.
10. When the look misses the target, classify the likely source before editing: material, geometry or bevels, UV or scale, lighting or reflection environment, camera, Karma support, or post-processing. Do not respond by blindly adding noise or grunge.
11. Hand off an editable network with named controls, source and texture provenance, bindings, assumptions, validation evidence, and remaining limitations. Valid nodes, connected textures, and an error-free cook are implementation evidence, not proof of visual completion; a default gray material or single-color placeholder is not completion for a substantive task.

## Keep the network editable

1. Before executing a substantive build, plan only the semantic regions it needs: inputs/coordinates, semantic masks, substrate/layers, optical channels, dedicated normal/displacement lanes, and assembly/output.
2. For a new graph, run the main semantic flow from top to bottom. Expand same-stage inputs, masks, channels, and parallel MaterialX branches left to right, then merge them downward into the trunk. Avoid upward back-connections, wires through nodes, and unnecessary long diagonals. When editing an existing graph, inherit its prevailing direction and style instead of forcing a re-layout.
3. Name nodes by responsibility and route shared masks through meaningful named hubs or dots. Use `layoutChildren()` only for a rough first pass; manually position a complex graph by semantic flow before handoff. Use a Network Box or subnet only for a complete layer, mask generator, or clearly reusable module. Never box nodes merely by type or use a subnet to hide tangled wiring.
4. For every substantial branch, state its visual purpose, placement reason, scale, affected channels, and validation method. Delete or bypass redundant Noise/Multiply/Ramp branches that make no visible contribution.

## Guardrails

- Do not add MCP tools, an Agent backend, a scoring platform, a scheduler, or a fixed approval or iteration gate.
- Do not inspect every possible channel by ritual; follow the actual material and renderer requirements.
- Do not claim visual completion from successful node creation alone. If representative visual evidence is unavailable, report that the look remains visually unverified.
