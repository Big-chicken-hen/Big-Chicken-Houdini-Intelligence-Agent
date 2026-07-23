# Procedural Architecture

Use these rules to turn the construction model into a maintainable Houdini system.

## Define subsystem boundaries

Give each subsystem one clear responsibility, explicit inputs and outputs, meaningful controls, and limited dependencies. Separate master dimensions, primary shell or frame, openings, structural assemblies, repeated modules, secondary components, detail, cleanup, and outputs only when the target actually needs them.

Pass a subsystem only the values it consumes. Avoid global parameters that make unrelated branches recook or change unexpectedly. Keep replacement and variants local to the affected subsystem.

## Choose representations by behavior

Choose generators from component semantics. Do not disguise a pile of unrelated primitives as a procedural design by emitting the same construction from Python, VEX, copied meshes, or custom point arrays.

- Use polygonal or subdivision construction for controlled manufactured surfaces.
- Use curves and profiles for directional, swept, rail, trim, cable, or path-driven forms.
- Use points, packed geometry, or instances for repeated independent elements.
- Use booleans for meaningful cuts, cavities, and assembly relationships when their topology and cost remain acceptable.
- Use VDB or field methods for volumetric, blended, or broad organic construction, retaining guides or anchors needed for art direction.
- Use height fields for terrain-scale surfaces and VEX for scalable repeated geometry or attribute logic.
- Use loops only where iteration is structurally required; do not use a solver for a static result.

Use HOM to author the network. Keep the asset's modeling logic in inspectable Houdini nodes rather than hiding it inside one opaque Python node or script.

## Keep paths and sweeps stable

Generate a genuinely continuous linear component from a continuous path and a stable profile. Keep path position and direction separate from profile shape; do not approximate a bend with intersecting straight pieces or conceal it with oversized connectors.

Before sweeping, remove duplicate and near-zero segments and establish stable direction, tangent, normal, reference frame, and seam behavior. Treat necking, bulging, profile flipping, unintended scale change, or uncontrolled twist as path, frame, or profile failures. Repair that upstream system instead of smoothing or decorating the symptom.

Keep independent parts separate when the intended result is a joint, bracket, clamp, or other assembly rather than one continuous form.

## Track hosts and dependency updates

Represent significant attachment semantics through existing groups, attributes, metadata, hierarchy, or USD paths. Record the component role, valid host, anchor, support, clearance, local relationship, and update behavior without forcing one universal schema.

When a host changes, distinguish fixed anchors from moving boundaries. Rebind endpoint and corner components, recalculate path- or boundary-distributed systems, regenerate opening- or surface-derived parts, and resolve both endpoints of multi-host components. Rebuild only affected dependents.

Do not preserve obsolete world-space positions, apply one translation to every dependent, uniformly scale linear components, or patch a downstream mesh when the upstream relationship is wrong.

## Design useful controls

Expose decisions the user is likely to change: primary dimensions, proportions, thickness, opening placement, module count and spacing, bounded variation, quality, and output purpose. Group related controls and use units that match the asset.

Do not expose every internal parameter or a control with negligible visible effect. A control should change a comprehensible part of the design without destabilizing unrelated systems.

## Handle repetition and performance

Define the source, placement domain, orientation, spacing, scale, allowed variation, overlap constraints, seed, and quality separately when relevant. Preserve identity and structural plausibility; do not randomize every property.

Prefer shared sources, packed instances, bounded loops, staged quality, local recomputation, and named checkpoints when they materially reduce cost. Consider geometry size, iteration and boolean cost, VDB resolution, time dependence, and viewport versus final quality before choosing an expensive route.

Do not bake an editable construction prematurely or create complexity with no visible or reuse benefit.

## Author a readable graph

Plan semantic stages, node roles, connections, outputs, and approximate positions before execution.

- Stack the main data flow and major stages top to bottom in a new network.
- Expand inputs and parallel branches left to right within a stage, then merge them downward.
- Avoid upward wires, wires through nodes, unnecessary long diagonals, obvious crossings, overlaps, disconnected experiments, and ambiguous outputs.
- Name important nodes by responsibility and use clear `OUT_*` or material/render outputs.
- Use `layoutChildren()` only for rough placement, then position complex graphs by semantic flow.
- Use boxes, comments, dots, and subnets only for meaningful nontrivial subsystems, shared routing, or reusable modules.
- Preserve the direction, spacing, naming, and grouping of an existing network; reorganize only the affected region unless the user requests broader cleanup.

Before finishing, bypass or remove exploratory branches that make no useful technical or visible contribution.

## Batch live authoring coherently

Use one or a small number of serial `hia_execute_hom` batches for substantial construction. Let each batch create or revise a coherent subsystem, set its controls, connect and position nodes, and return important paths. Inspect installed node types narrowly when compatibility is uncertain; do not impose a node allowlist or fan out repeated searches.

Never let multiple tasks write the current HIP in parallel. A modeling subtask supplies a plan and HOM draft; the main task performs scene mutations serially.
