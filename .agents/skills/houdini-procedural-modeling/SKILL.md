---
name: houdini-procedural-modeling
description: Procedural construction, substantial restructuring, and structural repair of complete, recognizable, editable Houdini assets. Use for nontrivial products, buildings, machines, props, environment modules, organic structures, parameterized asset families, converting a tangled model into a maintainable procedural network, repairing substantial construction, host/dependency, sweep, geometry-integrity, or UV/coordinate failures, and a small multi-part assembly whose endpoints, support, contact, spacing, clearance, or nonintersection must be correct. Pair with visual research for reference-driven construction. Do not use for one primitive, one parameter or connection, routine inspection, ordinary HOM errors, material-only or animation-only work, read-only review, or an explicitly requested blockout, proxy, placeholder, or technical test.
---

# Houdini Procedural Modeling

Build complete assets as readable procedural systems. More nodes do not make a model procedural; stable rules, meaningful controls, local editability, and recognizable structure do.

## Route the request

1. Interpret a request to generate a nontrivial named asset as a complete, structurally resolved, editable deliverable unless the user explicitly asks for a blockout, proxy, placeholder, or technical test. A stylized or low-poly final asset can still be complete. Treat every explicit negative constraint as a hard acceptance requirement on the resulting geometry and semantic role: changing the node, script, or construction method must not recreate a forbidden form as an equivalent stand-in, and only the user may relax that constraint or permit an approximation.
2. Keep a single primitive, known parameter or connection change, routine inspection, and an ordinary HOM error Direct: execute without knowledge retrieval and run one necessary targeted validation. Do not require a Brief, external research, capture, or artifact review, and do not load this workflow for material-only, animation-only, or read-only review work.
3. Treat a bounded familiar repair or restructuring as Focused and load only the one or two references or evidence steps that address its real uncertainty. A small multi-part assembly whose completion depends on endpoints, hosts, support, contact, spacing, clearance, or nonintersection also needs relation-aware validation even when its construction is simple; this does not by itself require knowledge retrieval or external research.
4. Treat a complete multi-subsystem asset, substantial dependency/UV/integrity rebuild, version-sensitive technique, high-cost construction, or simulation/cache handoff as Full and use the shared full blueprint, complete current-stage card, and technical/visual acceptance evidence. Resolve **单个 AI / 项目团队** before this complexity depth: project team uses all five Panel project Threads even when the work is Direct or Focused, while single AI creates no team.
5. When serving the Planning responsibility, return target-specific research, architecture, construction steps, bounded HOM drafts, risks, parameter dependencies, downstream contracts, and validation advice only. Keep Execution as the sole writer of the current HIP.

## Load only the needed guidance

- Read [knowledge-and-memory.md](../houdini-visual-research/references/knowledge-and-memory.md) only when node/parameter/version behavior, a complex workflow, historical project context, or failure cause is uncertain. Skip retrieval for a known deterministic edit.
- Read [build-brief-and-review.md](../houdini-visual-research/references/build-brief-and-review.md) for semantic stage order and domain-specific acceptance evidence for complex construction.
- For every Full Goal, read [full-goal-blueprint.md](../houdini-visual-research/references/full-goal-blueprint.md) completely for the single-AI/project-team routing, five Supervisor/Planning/Execution/Visual Review/Technical Review Threads, production information-depth and semantic checks, Supervisor full-blueprint authorization, bounded post-authorization payloads, provenance, complete stage-card fields, app-server coordination, model/guidance behavior, parallel reviews, native-subagent boundary, and project lifecycle.
- Read [modeling-judgment.md](references/modeling-judgment.md) to define identity, structural hierarchy, and the difference between a blockout and a complete asset.
- Read [procedural-architecture.md](references/procedural-architecture.md) when choosing subsystems, representations, continuous paths, host dependencies, controls, repetition, performance strategy, or graph layout.
- Read [modeling-validation.md](references/modeling-validation.md) before a substantial handoff or when deciding whether the model is genuinely complete.
- Read [geometry-integrity.md](references/geometry-integrity.md) when a scoped assembly, including a small one, repeated placement, thin surface, self-intersection, animation, or simulation needs contact, clearance, penetration, or endpoint-envelope diagnosis. Do not load it for an isolated primitive, one parameter edit, or an ordinary API error.
- Read [uv-and-surface-coordinates.md](references/uv-and-surface-coordinates.md) when image textures, decals, directional patterns, baking, or external delivery require validated UVs or an explicit alternative coordinate system.

Keep references one level deep and do not load guidance unrelated to the current asset.

## Define before authoring

For the Full route, contribute target-specific modeling decisions to the authoritative user-visible blueprint: identity, scale and axes, recognition features, primary/secondary/tertiary hierarchy, real subsystems and dependencies, editable controls, ordered native-node construction, parameter dependencies, outputs, construction risks, technical and visual evidence, minimum repair, and downstream contracts. A sparse complete-asset prompt still receives this full expansion and must satisfy the shared 10,000-overall, 2,500-per-stage, and 350-per-step task-specific information floors plus task-anchor, structure, anti-repetition/filler, and semantic checks. In a project-team Goal, Planning sends the complete blueprint to Supervisor for initial and material-revision authorization. After authorization, give Execution only the global hard constraints plus one complete current modeling-stage card and latest evidence delta. This is human-readable construction guidance, not an IR, hard-coded asset recipe, general Planner, reusable Gate, or state machine. A Focused task records only the affected semantic expectations and evidence.

## Build the asset

1. Inspect only the current scene context and affected network needed for safe construction. Preserve unrelated nodes and scene state.
2. Decompose the target into coherent subsystems with clear inputs, outputs, responsibility, and limited dependencies. For a complete multi-subsystem asset, keep primary form as the first bounded authoring batch and inspect that visible milestone before adding broad tagging, animation, detail, or materials. If actual image evidence does not support the current stage card's primary-form acceptance, correct and review that stage again before advancing. Advance from proportions and primary form through structure, mid-level assemblies, and task-relevant detail before LookDev handoff; do not use detail to conceal an unresolved earlier stage. Do not remove or weaken User facts, recognition features, or completion promises to make the result pass; report an unmet promise as incomplete or unverified.
3. Track each attached component's host, anchor, support, clearance, and update rule using the asset's existing stable semantics. After an upstream change, rebuild only affected dependents and revalidate their relationships.
4. Choose geometry representations from the construction problem. Use primitives, curves, booleans, instances, loops, VEX, VDBs, and other methods as components, never as completion claims by themselves.
5. Expose controls that map to user decisions such as dimensions, proportions, counts, spacing, variation, quality, and output purpose. Keep changes local; hide internal parameters with negligible user value.
6. Use shared sources and instance- or compile-friendly patterns for substantial repetition. Balance editability, cook cost, geometry size, and visible benefit instead of adding complexity to appear procedural.
7. Plan semantic stages, nodes, connections, and positions before creating a substantial graph. For a new network, stack the main flow top to bottom; expand same-stage inputs and parallel subsystems left to right, then merge downward. Avoid upward connections, wires through nodes, needless long diagonals, and unclear outputs. Preserve an existing network's direction and style, reorganizing only the affected region.
8. Name important nodes by responsibility and make outputs explicit. Use `layoutChildren()` only for an initial rough arrangement; use boxes, comments, dots, and subnets only when they express a meaningful subsystem or reusable module.

## Use the live Houdini route

Default current-scene work to HIA MCP V2 and HOM. In a project-team Goal, keep Execution as the only current-HIP writer; Planning may draft but never execute a mutating batch, and Supervisor authorizes the complete blueprint and accepts evidence without mutating the scene. When uncertainty triggers the shared `knowledge-and-memory.md` contract, reuse any result already supplied by the project or visual-research Skill and do not run a modeling-specific duplicate. For complex, unfamiliar, reference-driven, material-dependent, or version-sensitive construction, complete only the external research that can change authoring; search durable project memory only when it can affect the decision. Obtain context with `hia_context` or `hia_inspect` only as needed, then have Execution use serialized, cohesive `hia_execute_hom` batches bounded to one semantic stage or one coherent subsystem. Never author all stages of a complex asset in one HOM script; after every stage, return routed evidence to the parallel Technical Review and actual-image Visual Review Threads before the next batch. When a non-Execution read-only role exposes native subagent tools, use them for genuinely parallel, non-overlapping read-only research or review. Execution never proactively spawns or delegates to native subagents because they inherit its scene-write capability; its mainline stays serialized. A missing required review Thread or unavailable internal subagent never justifies a giant HOM script. When several installed-node questions are genuinely needed, use one batched `hia_search_node_types` request, reuse its results, and batch any follow-up `hia_node_help` targets; do not fan out parallel queries. Avoid tool forests and parallel live-scene writes.

Use native `hython` only when the user explicitly requests offline work, an independent HIP, batch processing, or background execution. Use FXHoudiniMCP only when the active HIA configuration explicitly selects that compatibility mode; never make it the default.

## Validate and hand off

Require both relevant technical evidence and task-specific visual evidence before claiming a complete asset. Validate selectively; outside the shared Full-blueprint information floors, do not impose fixed tool calls, captures, iterations, scores, or generic gates. Do not permanently change the user's camera, viewport, pane layout, or display state. If representative visual evidence is unavailable, state that the model is technically checked but visually unverified.

Turn retrieved topology, attribute, dependency, placement, range, and version rules into explicit semantic expectations before a Focused or Full write. After execution, ask the current `hia_validate` semantic-expectation capability to test them through its live contract, then use only the other evidence needed by the claim. Node existence and an error-free cook do not prove the expected host relationship, group/attribute contract, placement range, or dependency update; report an unknown or not-proven result once as an unverified risk.

When an assembly claim depends on attachment or fit, turn both endpoints, intended hosts, span or bounds, contact tolerance, minimum clearance, and forbidden intersection into task-specific numeric expectations. Use bounds only to narrow candidates, then a bounded native Houdini or demonstrably non-mutating HOM check for any precise spatial claim that the current validator does not observe. A clean cook or viewport image cannot prove nonintersection. Keep tolerances relative to the asset and feature scale, and mark the claim unverified when the required measurement is unavailable.

For a complex visual asset, follow the stage-preview contract in `$houdini-visual-research`: use its risk-appropriate static or temporal preview evidence at meaningful milestones rather than capturing after every small edit. A returned path or capture metadata without actual image content is not visual observation. If the primary-form preview still reads as a generic primitive assembly, continue modeling that stage instead of handing off to LookDev or claiming completion. Route modeling, network, dependency, geometry-integrity, and performance evidence to the read-only Technical Review Thread and `$houdini-artifact-review`; route actual image content and visible recognition claims to the read-only Visual Review Thread. Start both in parallel after each stage. Let only Execution apply the Supervisor-approved highest-impact fix, then repeat both reviews without a fixed iteration count until verified or genuinely blocked.

When geometry integrity is relevant, bound the check as described in [geometry-integrity.md](references/geometry-integrity.md) and recheck the same scope after any repair before claiming completion.

When the delivery requires texture-space information, require validated UVs or a documented alternative coordinate system and stable semantic groups or paths for LookDev handoff. Explain any intentional omission.

Report the asset root and outputs, subsystems, principal controls, construction choices, validation evidence, performance limitations, and any incomplete or unverified area.

## Work with the other skills

- Use `$houdini-visual-research` for reference decomposition, external sources, technique or style research, and visual comparison. Reference-driven complex modeling may use both skills.
- Use `$houdini-material-lookdev` for material, shader, texture, and lighting LookDev without changing model scope.
- Use `$houdini-artifact-review` inside the Visual Review or Technical Review Thread for read-only milestone or delivery evidence; let Supervisor decide the repair and only Execution apply it.

## Guardrails

- Do not finish any requested final construction as a forbidden or generic primitive equivalent merely by changing its node type or authoring method; use a primitive blockout only when the user requested one.
- Do not claim a multi-part assembly complete from appearance alone when endpoints, support, contact, clearance, or intersection are part of acceptance.
- Do not add MCP tools, an Agent backend, planner, summarizer, chat database, second knowledge or memory system, service, scoring system, fixed gate, approval chain, node allowlist, or one-shot apply mechanism.
- Do not prescribe asset-specific recipes, fixed node counts, tool counts, capture counts, stage counts, iteration counts, or coordinate tables.
