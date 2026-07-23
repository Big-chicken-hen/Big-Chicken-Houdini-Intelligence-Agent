---
name: houdini-procedural-modeling
description: Procedural construction, substantial restructuring, and structural repair of complete, recognizable, editable Houdini assets. Use for nontrivial products, buildings, machines, props, environment modules, organic structures, parameterized asset families, converting a tangled model into a maintainable procedural network, or repairing substantial construction, host/dependency, sweep, geometry-integrity, or UV/coordinate failures in such assets. Pair with visual research for reference-driven construction. Do not use for one primitive, one parameter or connection, routine inspection, ordinary HOM errors, material-only or animation-only work, read-only review, or an explicitly requested blockout, proxy, placeholder, or technical test.
---

# Houdini Procedural Modeling

Build complete assets as readable procedural systems. More nodes do not make a model procedural; stable rules, meaningful controls, local editability, and recognizable structure do.

## Route the request

1. Interpret a request to generate a nontrivial named asset as a complete, structurally resolved, editable deliverable unless the user explicitly asks for a blockout, proxy, placeholder, or technical test. A stylized or low-poly final asset can still be complete.
2. Keep a single primitive, one parameter or connection change, routine inspection, and an ordinary HOM error direct. Do not load this workflow for material-only, animation-only, or read-only review work.
3. When running as a modeling subtask, return the construction plan, HOM draft, and validation advice only. Keep the main task as the sole writer of the current HIP.

## Load only the needed guidance

- Read [modeling-judgment.md](references/modeling-judgment.md) to define identity, structural hierarchy, and the difference between a blockout and a complete asset.
- Read [procedural-architecture.md](references/procedural-architecture.md) when choosing subsystems, representations, continuous paths, host dependencies, controls, repetition, performance strategy, or graph layout.
- Read [modeling-validation.md](references/modeling-validation.md) before a substantial handoff or when deciding whether the model is genuinely complete.
- Read [geometry-integrity.md](references/geometry-integrity.md) only when a scoped assembly, repeated placement, thin surface, self-intersection, animation, or simulation needs penetration diagnosis. Do not load it for a simple primitive, one parameter edit, or an ordinary API error.
- Read [uv-and-surface-coordinates.md](references/uv-and-surface-coordinates.md) when image textures, decals, directional patterns, baking, or external delivery require validated UVs or an explicit alternative coordinate system.

Keep references one level deep and do not load guidance unrelated to the current asset.

## Define before authoring

Make a short construction model: asset identity; world scale and axes; recognition features; primary, secondary, and tertiary forms; real structural subsystems and their relationships; meaningful editable controls; output purpose; and completion evidence. Treat this as working reasoning, not a checklist, gate, or approval step.

## Build the asset

1. Inspect only the current scene context and affected network needed for safe construction. Preserve unrelated nodes and scene state.
2. Decompose the target into coherent subsystems with clear inputs, outputs, responsibility, and limited dependencies. Build primary silhouette and major negative spaces before secondary assemblies, then add tertiary details only when they reinforce the design.
3. Track each attached component's host, anchor, support, clearance, and update rule using the asset's existing stable semantics. After an upstream change, rebuild only affected dependents and revalidate their relationships.
4. Choose geometry representations from the construction problem. Use primitives, curves, booleans, instances, loops, VEX, VDBs, and other methods as components, never as completion claims by themselves.
5. Expose controls that map to user decisions such as dimensions, proportions, counts, spacing, variation, quality, and output purpose. Keep changes local; hide internal parameters with negligible user value.
6. Use shared sources and instance- or compile-friendly patterns for substantial repetition. Balance editability, cook cost, geometry size, and visible benefit instead of adding complexity to appear procedural.
7. Plan semantic stages, nodes, connections, and positions before creating a substantial graph. For a new network, stack the main flow top to bottom; expand same-stage inputs and parallel subsystems left to right, then merge downward. Avoid upward connections, wires through nodes, needless long diagonals, and unclear outputs. Preserve an existing network's direction and style, reorganizing only the affected region.
8. Name important nodes by responsibility and make outputs explicit. Use `layoutChildren()` only for an initial rough arrangement; use boxes, comments, dots, and subnets only when they express a meaningful subsystem or reusable module.

## Use the live Houdini route

Default current-scene work to HIA MCP V2 and HOM. Obtain context with `hia_context` or `hia_inspect` only as needed, then prefer one or a small number of serial, cohesive `hia_execute_hom` batches. Call `hia_search_node_types` followed by `hia_node_help` narrowly and serially only when installed node behavior is uncertain. Avoid tool forests and parallel live-scene writes.

Use native `hython` only when the user explicitly requests offline work, an independent HIP, batch processing, or background execution. Use FXHoudiniMCP only when the launcher was explicitly placed in that compatibility mode; never make it the default.

## Validate and hand off

Require both relevant technical evidence and task-specific visual evidence before claiming a complete asset. Validate selectively; do not impose fixed tool calls, captures, iterations, scores, or gates. Do not permanently change the user's camera, viewport, pane layout, or display state. If representative visual evidence is unavailable, state that the model is technically checked but visually unverified.

When geometry integrity is relevant, bound the check as described in [geometry-integrity.md](references/geometry-integrity.md) and recheck the same scope after any repair before claiming completion.

When the delivery requires texture-space information, require validated UVs or a documented alternative coordinate system and stable semantic groups or paths for LookDev handoff. Explain any intentional omission.

Report the asset root and outputs, subsystems, principal controls, construction choices, validation evidence, performance limitations, and any incomplete or unverified area.

## Work with the other skills

- Use `$houdini-visual-research` for reference decomposition, external sources, technique or style research, and visual comparison. Reference-driven complex modeling may use both skills.
- Use `$houdini-material-lookdev` for material, shader, texture, and lighting LookDev without changing model scope.
- Use `$houdini-artifact-review` for read-only milestone or delivery review; let the main task decide and apply any fixes serially.

## Guardrails

- Do not finish a complex asset as a generic primitive assembly unless the user asked for a blockout.
- Do not add MCP tools, an Agent backend, planner, database, service, scoring system, fixed gate, approval chain, node allowlist, or one-shot apply mechanism.
- Do not prescribe asset-specific recipes, fixed node counts, tool counts, capture counts, stage counts, iteration counts, or coordinate tables.
