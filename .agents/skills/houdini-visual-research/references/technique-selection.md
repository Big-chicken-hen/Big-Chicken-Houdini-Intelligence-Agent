# Technique Selection

Choose the simplest Houdini system that preserves the requested quality, editability, scale, and renderer compatibility. Combine contexts when each has a clear responsibility.

## Selection matrix

| Visual need | Primary system | Useful companions | Avoid as the final answer |
| --- | --- | --- | --- |
| Editable hard-surface, organic, or procedural asset | SOP network; HOM for network construction; VEX for repeated topology/attribute logic | Curves, booleans, VDBs, UV and normal tools | A loose pile of primitives without resolved silhouette, topology, bevels, or material regions |
| Reference-image product model | SOPs with explicit dimensions, named controls, construction layers, and camera alignment | VEX for repeated details; COPs for masks; Solaris for presentation | Matching only one outline while ignoring scale, assembly logic, secondary forms, and editability |
| Particle, crowd-like, or event-driven motion | POP/DOP or SOP solvers when state and interactions matter | VEX forces, instancing, caches | A solver when independent time functions or keyframes are sufficient |
| Pyro, fluids, destruction, or evolving volumes | Relevant DOP/SOP-level solver and volumes/VDBs | SOP sourcing/cleanup, caching, Karma volumes | Geometry noise presented as a simulated effect when dynamics are required |
| Image-space procedural texture or compositing effect | Copernicus/COPs | SOP masks, MaterialX texture consumption | Forcing iterative screen-space math into a MaterialX graph that cannot express it reliably |
| Portable surface/volume look development | MaterialX in a Solaris Material Library | SOP attributes promoted to USD primvars; COP-authored textures | Traditional VEX shaders when Karma XPU or portability is required |
| Scene assembly, variants, instancing, lighting, and render configuration | Solaris/LOPs and USD composition | SOP imports, MaterialX, Karma | Flattening a reusable scene into destructive object-level edits |
| Final physically based rendering | Karma CPU or XPU chosen from required feature support | Solaris lighting/camera/AOV setup | Assuming CPU and XPU support the same shading features |
| Rigged or art-directed animation | Keyframes, constraints, KineFX, or parameterized SOP deformation | CHOPs where signal processing helps; solvers only for history | Simulation merely to create motion that must remain directly art-directable |

## Decision heuristics

1. Start from the required final evidence: editable asset, animation, close-up render, texture set, USD asset, or simulation cache.
2. Choose a data representation before nodes: polygon/subdivision surface, curve, points/instances, VDB field, image layers, material graph, or USD prim hierarchy.
3. Decide whether the result needs temporal state. Use direct time functions or keyframes for stateless motion; use a solver only for feedback, collisions, accumulation, or history.
4. Decide the renderer target early. For Karma XPU, plan around MaterialX/USD-supported shading rather than legacy VEX shading.
5. Expose controls that correspond to the user's art direction: primary dimensions, counts, seed, timing, shape language, material values, and quality tiers.
6. Inspect the installed Houdini build and live node categories before authoring; names and capabilities can change by version.

## Reference-driven asset hierarchy

Analyze the reference before construction:

- Lock scale and major proportions from trustworthy views or user dimensions.
- Separate primary silhouette, secondary assemblies/panel breaks, and tertiary manufacturing or wear detail.
- Infer hidden construction cautiously and label assumptions.
- Establish a comparison camera; account for perspective and lens differences instead of deforming the model to one photograph.
- Resolve bevel width, normals, UVs/primvars, material boundaries, and repeated-detail logic before final validation.

## Construction strategy

For complex work, use one or a small number of `hia_execute_hom` calls for a coherent subsystem that creates nodes, sets parameters, wires the graph, lays it out, and returns paths or validation data. Obtain only necessary context with `hia_context` or `hia_inspect`; query `hia_search_node_types` or `hia_node_help` narrowly when node knowledge is missing. Use `hia_validate` or `hia_scene_diff` when verification needs them, and reserve `hia_capture_viewport` for actual visual comparison. These are choices, not a mandatory checklist. Prefer several named, inspectable stages over an opaque script or a forest of tiny calls.

When node knowledge is missing, call `hia_search_node_types` narrowly and serially first, then reuse its returned `category`, `name`, or `resolved_name`. Call `hia_node_help` with `node_path`, with `category` plus a bare `node_type`, or with the supported qualified form `node_type="Category/name"`. Never fan out repeated search/help calls for the same target: this can cause `QUEUE_FULL`. If either tool fails, preserve the exact error and continue from already retrieved catalog results or local help when possible instead of retrying blindly.

Work in the live scene through HIA MCP V2/HOM by default. Keep current-scene `hia_*` I/O few and serial, and never parallelize repeated node-type/help searches. Use FXHoudiniMCP only when the launcher explicitly selects that compatibility fallback. Use `hython` only for an explicitly requested offline, batch, independent-HIP, or background-render workflow.

Primary references: [HOM](https://www.sidefx.com/docs/houdini/hom/), [Attribute Wrangle](https://www.sidefx.com/docs/houdini/nodes/sop/attribwrangle), [Solaris/USD basics](https://www.sidefx.com/docs/houdini/solaris/usd.html), and [Karma](https://www.sidefx.com/docs/houdini/solaris/karma.html).
