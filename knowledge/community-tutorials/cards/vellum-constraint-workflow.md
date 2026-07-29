# Vellum constraint and solver evidence

Source kind: `community_tutorial`
Verification: `community_unverified`
Version: sources cover H17 and later Vellum workflows; verify current constraint attributes and SOP inputs.

## Use, prerequisites, and target

Use for cloth, hair/strings, grains, soft bodies, balloons, or mixed Vellum constraints. Inputs need stable topology, appropriate scale, rest shape, and collision geometry. The target is a graph where constraint construction, simulation geometry, collisions, solve quality, and render geometry remain separately inspectable.

## Semantic network stages

Geometry preparation → one or more `Vellum Constraints SOP` branches → constraint merge → `Vellum Solver SOP` with collision side input → simulated geometry/constraint diagnostics → `Vellum Post-Process SOP` or render reconstruction → cache → `OUT_VELLUM`.

## Ordered workflow

1. Prepare simulation geometry. Triangulate/remesh cloth when needed, resample curves, fuse only intentional seams, and preserve a high-resolution render branch for later deformation transfer. Record point count, edge length, and object scale.
2. Create constraint types with dedicated `Vellum Constraints SOP` nodes: cloth/stretch-bend, struts, pressure, hair, attach-to-geometry, weld, or pin. Name constraint groups and keep meaningful types on separate branches until their properties are clear.
3. Inspect the constraint geometry output. Verify endpoints reference valid simulation points and inspect fields such as `constraint_name`, `constraint_type`, `restlength`, `stiffness`, `dampingratio`, and break thresholds when present. Field availability varies by constraint type/version.
4. Merge compatible constraints and connect simulation geometry plus constraints to `Vellum Solver SOP`. Connect collision geometry through its proper input. Configure substeps and constraint iterations from visible stretch/collision error, not as arbitrary quality sliders.
5. Add forces and animation coupling. Pins/attachments need a stable target mapping; animated targets require velocity/transform continuity. For pressure, verify closed topology and rest volume before tuning pressure.
6. Run a short diagnostic range. Display constraint geometry, stress/stretch ratios, broken constraints, collision guides, and point velocities. Change stiffness scale, bend/stretch balance, damping, thickness, and iterations one family at a time.
7. Cache the simulation geometry and, if downstream diagnostics/breakage require it, constraint geometry. Reconstruct/deform the render mesh after cache using stable point/primitive mapping.
8. Finish with post-process thickness/normals only after solve validation. Output `OUT_VELLUM` and a bypassable `OUT_CONSTRAINT_DEBUG`.

## Data flow, cache, version, and performance

Constraint endpoints depend on simulation point numbering; topology edits after constraint creation invalidate them. Cache the low-resolution solve before high-resolution deformation or thickness. More substeps address fast motion/collision timing; more constraint iterations address convergence within a step. Both add cost, so diagnose which error dominates. Mixed constraints can require different stiffness scales; groups preserve control.

## Common failures and repairs

- **Exploding first frame:** overlapping collision, zero/invalid rest length, extreme stiffness at insufficient iterations, or bad scale.
- **Cloth stretches:** inspect mesh resolution, stretch constraint stiffness, iterations, and pin mapping.
- **Pins detach/jump:** target IDs or topology changed; use stable attributes and verify target position each frame.
- **Pressure collapses:** source is not closed or normals/rest volume are wrong.
- **Slow solve:** simplify collision and sim mesh, reduce unnecessary constraint types, then tune substeps/iterations based on evidence.

## Provenance boundary

Matt Estela's Vellum notes and Rohan Dalvi's introduction motivate direct inspection of constraints. The separation of sim/render/cache stages and checks is original synthesis, not copied course material.

## Semantic expectations and verification checklist

- Presence: required constraint types and groups exist; endpoint attributes resolve to valid points.
- Mapping: pinned/attached constraints map to intended target IDs; render deformation maps back to the cached sim mesh.
- Range: rest lengths are positive where applicable; positions/velocities/stiffness values are finite; stretch/error stays within a documented shot tolerance.
- Cook evidence: diagnostic frames solve without invalid-constraint warnings and broken-constraint counts match intent.
- Cache evidence: frame coverage is complete and cached sim topology remains constant where reconstruction requires it.
- Visual evidence: contacts do not tunnel, pins do not pop, pressure volume is plausible, and the final surface has no unexplained stretching or thickness inversion.

## Sources

- Matt Estela, [Vellum](https://www.tokeru.com/cgwiki/HoudiniVellum.html).
- Rohan Dalvi, [Intro to Vellum](https://www.sidefx.com/tutorials/intro-to-vellum/).
