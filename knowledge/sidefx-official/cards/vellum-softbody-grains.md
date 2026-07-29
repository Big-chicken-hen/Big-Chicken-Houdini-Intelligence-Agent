# Vellum soft body and grains workflow

Pack version: 2.0.0

## Use for

Use this workflow for deformable solids, balloons, cushions, contracting tissue-like forms, packed grains, sand, snow-like aggregates, and sticky granular effects that benefit from Vellum's unified particle and constraint framework. It is useful when soft bodies, grains, cloth, or fluids must interact in one solver and when art direction, stable collisions, and controllable plasticity are more important than strict continuum mechanics.

## Context and core data

Soft bodies can be represented by tetrahedral stretch plus volume preservation, shape matching, struts, pressure, or layered surface constraints. Grains are points with `pscale`, `mass`, friction, attraction, and optional glue constraints. Geometry stores motion and collision data; Constraint Geometry stores topology and material rules. For tetrahedra, point order and a valid closed volume matter. For grains, particle spacing and radius establish both packing and apparent scale.

## Recommended data flow

Use `clean volume or source points → tetrahedralize or Vellum Configure Softbody/Grain → constraint property overrides → optional glue/plasticity → Vellum Solver → Vellum I/O → surface or deformation reconstruction`. Keep separate constraint groups for volume, stretch, shape match, glue, and attachment so each class can be tuned or visualized independently.

## Step-by-step workflow

1. Confirm physical scale and create a clean closed volume for soft bodies or evenly distributed source points for grains.
2. Generate tetrahedra, struts, shape-match regions, pressure constraints, or grain particles at a provisional resolution.
3. Set thickness, mass or density, stiffness, volume preservation, friction, attraction, and plasticity deliberately.
4. Add attachments, pins, or dynamic glue constraints only after the base material remains stable.
5. Run a short gravity and collision test with visualization enabled.
6. Tune Substeps, Constraint Iterations, collision passes, and solver precision according to the observed failure.
7. Cache both geometry and constraints, then reconstruct a render surface or transfer deformation.

## Critical parameters and attributes

Tetrahedral stretch stiffness controls deformation, while volume preservation resists collapse. Shape matching defines how strongly regions return to a rest configuration. Plasticity onset and rate determine permanent deformation. Grain `pscale` governs size and contacts; friction and attraction control sliding and cohesion. `mass`, `stopped`, `pintoanimation`, `weld`, and collision-disable attributes change per-point behavior. Dynamic glue creation has a Maximum Constraints Per Point limit and a creation frequency that can dominate cost.

## Cache, version, and performance

Begin with coarse tetrahedra or larger grains. Halving particle spacing can multiply the particle and collision count dramatically. Use 64-bit solving only when numerical range or very large domains justify the extra memory. A Secondary Constraint Pass can reduce the frequency of expensive constraints, but volume or high-stiffness rules generally require adequate solve frequency. Cache constraints with geometry because plasticity, breakage, and dynamic glue alter the constraint state over time.

## Validation

Check volume retention, center-of-mass motion, contact depth, rest recovery, and energy after impacts. Visualize constraint stress and identify whether errors are local or spread across the object. For grains, inspect packing voids, particle overlap, avalanching angle, and whether cohesion survives the intended forces. For soft bodies, compare the coarse cage, render surface, and collider contacts at the same frames. Reload caches and verify permanent deformation remains.

## Common failures and troubleshooting

Soft bodies that explode often contain inverted tetrahedra, impossible initial overlap, extreme stiffness with too few iterations, or contradictory attachments. Volume loss calls for better tetrahedral quality, stronger volume constraints, or more convergence. Grain jitter can come from oversized `pscale`, excessive attraction, insufficient substeps, or initial overlaps. A grain pile that behaves like a rigid block may have too much glue or friction. Dynamic constraints created every substep can cause large performance drops and topology jitter.

## When not to use

Do not use grains for a body of water that requires a smooth free surface; FLIP is usually better. Do not use a coarse shape-matching soft body when accurate stress, fracture, or continuum material response is required; consider FEM or MPM. A rigid object should remain an RBD unless visible deformation is part of the shot.

## Houdini 21 notes

The workflow above is documented/static. Coupled tetrahedral stretch and volume-preservation improvements introduced before H21, plus 64-bit Vellum Fluid support, are change-note-supported rather than live-verified here. Because current node help may include later additions, confirm individual SOP versions in the H21 installation. The solver principles—clean topology, explicit thickness, separated constraint groups, and staged resolution—are stable across these versions. This corpus build did not run an H21 soft-body, grain, or fluid solve.

## Official sources

- SideFX, [Vellum soft bodies](https://www.sidefx.com/docs/houdini/vellum/softbody.html), accessed 2026-07-26.
- SideFX, [Vellum dynamic constraints](https://www.sidefx.com/docs/houdini/vellum/dynamicconstraints.html), accessed 2026-07-26.
- SideFX, [Vellum Solver](https://www.sidefx.com/docs/houdini/nodes/dop/vellumsolver.html), accessed 2026-07-26.
