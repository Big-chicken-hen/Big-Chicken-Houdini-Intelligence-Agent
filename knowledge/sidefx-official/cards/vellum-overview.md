# Vellum cloth and hair workflow

Pack version: 2.0.0

## Use for

Use this workflow for cloth, fabric panels, capes, flags, drapes, hair guides, wires, and other thin flexible structures whose behavior can be described by particles plus stretch, bend, pin, attach, stitch, or hair constraints. Vellum's position-based method is especially effective when controllability and stable iteration matter more than a fully continuum-based material solve.

## Context and core data

Vellum carries moving points and collision polygons in Geometry data, while a separate Constraint Geometry stream stores the rules that bind those points. The two streams have one-to-one point correspondence. `pscale` represents thickness or collision radius, `mass` controls inertia, and point groups identify pins or material regions. Cloth commonly uses distance and bend constraints. Hair uses distance constraints along edges plus bend-between-edges constraints with bend/twist behavior; point `orient` is input state for stable rotation, not a parallel constraint type.

For hair, that last distinction is precise: the Hair constraint type creates
distance constraints along edges plus bend-between-edges constraints whose
bend/twist behavior supplies torsion. The lighter String type keeps distance
and angular bend but omits twist, so edges may spin freely. `orient` is not a
parallel general-purpose constraint type. It is a point attribute that supplies
the stable rotation basis required when deforming/animated hair guides enter the
solver; the generated hair constraint geometry carries the distance and
bend/twist rules.

## Recommended data flow

Use `uniform simulation mesh or resampled curves → for animated guides, stable
orient from Guide Deform/rest-and-animated skin or Compute Missing Orientation
at the chosen rest frame → Vellum Constraints Hair (distance + bend/twist) or
String (distance + bend, no twist) → pins, stitches, or attach constraints →
Vellum Solver SOP → Vellum I/O → Vellum Post-Process → optional transfer to
render mesh`. Keep high-resolution render geometry out of the primary solve.
For garments, establish panel topology, seams, and rest shape before tuning
wind or wrinkles.

## Step-by-step workflow

1. Normalize scene scale and build an evenly spaced triangulated cloth mesh or
   resampled hair curves with deterministic root-to-tip order.
2. For hair, choose Hair when the guide must preserve edge length, bending, and
   torsion; choose String only when free axial spin is acceptable. Do not add an
   imaginary generic “orient constraint” alongside them.
3. When hair input is deformed by skin or another animation source, create a
   stable point `orient` using Guide Deform with rest and animated skin, or
   enable Compute Missing Orientation and set the correct Rest Orientation
   Frame. Treat this as input-state preparation, not constraint generation.
4. Define thickness, mass, stretch/distance, bend/twist, and pin regions with a
   Vellum Configure or Constraints SOP. Inspect both geometry and constraint
   outputs to confirm expected curve connectivity and constraint types.
5. Add stitch, weld, attach-to-geometry, or pin-to-target constraints only where
   the task requires them. For rooted hair, decide whether position-only or
   position-plus-orientation pinning is needed.
6. Supply clean collision geometry with accurate transforms, velocities, and
   an appropriate collision representation.
7. Solve at low resolution, adjusting Substeps for fast motion and Constraint
   Iterations for stiffness convergence. Tune bend stiffness/damping for shape
   retention and torsional response without confusing it with `orient`.
8. Inspect collisions, distance change, bend/twist response, point orientations,
   and thickness before increasing resolution or adding wind detail.
9. Cache geometry and constraints with Vellum I/O, then post-process or deform
   the render mesh.

## Critical parameters and attributes

`pscale` that is too large slows collision detection and can force visible
separation. Stretch stiffness sets resistance to elongation; bend stiffness and
rest angle control fold scale. Hair adds twist to its bend behavior, while
String omits twist. The point `orient` attribute, plus corresponding angular
velocity state, gives hair a stable rotation basis over time; it does not
replace distance or bend/twist constraints. Compute Missing Orientation derives
it relative to Rest Orientation Frame when no suitable input orientation
exists. Substeps divide motion in time, while Constraint Iterations repeat
projection within each substep. Collision Passes interleave collision tests
with constraint projection. Post Collision Passes perform cleanup after
constraints. Hair angular damping can suppress excessive rotational energy.
The `layer` point attribute improves ordered cloth-layer collisions.

## Cache, version, and performance

Use Remesh or PolyReduce with edge equalization to avoid long skinny triangles. For cloth, solve a uniform low-resolution cage for broad motion, then target a higher-resolution Vellum pass or transfer deformation for fine wrinkles. Soft fabrics may move expensive bend constraints into a lower-frequency Secondary Constraint Pass, but stiff materials need frequent bend solves. Vellum I/O can remove unnecessary attributes and stores both geometry and constraints.

## Validation

Visualize thickness, self-collision failures, external-collision failures, pins,
stitches, and constraint groups. For hair, confirm distance and bend/twist
constraints exist along the expected root-to-tip connectivity, then inspect
`orient` continuity on the input and solved points at the rest frame and first
deforming frame. A guide that rotates without a position change should retain a
stable frame rather than flip or lose torsional response. Use false color for
stretch and bend stress. Check the first moving frame, maximum acceleration,
closest collider contact, and final rest. Confirm that cached constraints
accompany cached geometry and that a disk reload reproduces the same
deformation. Compare silhouette and wrinkle or guide curvature with the intended
material. These checks are documentation-backed but were not reproduced in a
live Houdini 21 solve for this corpus rebuild.

## Common failures and troubleshooting

Stretchiness usually means insufficient substeps or convergence, not automatically weak material stiffness. Snagging and tent-poling can require more collision interleaving, cleaner colliders, or smaller timesteps. Explosive tearing can follow newly broken welds colliding immediately. Nonuniform topology produces directional folds and unstable thin triangles. If a cloth layer penetrates another, inspect `pscale`, `layer`, initial overlap attributes, and collider normals before increasing all solver passes.

Hair that stretches lacks sufficient distance-constraint convergence or has
incorrect curve connectivity. Hair that bends but spins freely may be using
String rather than Hair, or may lack the intended bend/twist stiffness. Flips
or unstable driven rotations at the first animated frame point to a missing or
discontinuous `orient`, a wrong Rest Orientation Frame, or a rest/animated-skin
mismatch. Adding more generic constraints does not repair an invalid input
rotation basis.

## When not to use

Do not use cloth constraints for large free-surface water, smoke, or rigid-body fracture. Use FLIP, Pyro, or Bullet respectively. For high-accuracy volumetric elasticity, FEM or MPM may be more suitable. A static drape that never changes can be solved once with Vellum Brush or a short settle instead of carrying a full animated simulation.

## Houdini 21 notes

The core SOP Vellum workflow in Houdini 21 follows the modern Configure–Solver–I/O pattern. SideFX does not publish a separate H21 Vellum news page, so current help must be read conservatively. Features documented before H21, including wind shadow, default thickness, coupled tetrahedral volume solves, and 64-bit Vellum Fluid support, remain relevant, but Houdini 22-only nodes must not be assumed.

The Hair-versus-String distinction and the role of `orient` are grounded in
current SideFX node and attribute references. Confirm the exact H21 parameter
surface in installed help before implementation; this card does not claim a
live H21 solver verification.

## Official sources

- SideFX, [Vellum overview](https://www.sidefx.com/docs/houdini/vellum/overview.html), accessed 2026-07-26.
- SideFX, [Vellum Constraints SOP](https://www.sidefx.com/docs/houdini/nodes/sop/vellumconstraints), accessed 2026-07-26.
- SideFX, [Vellum attributes](https://www.sidefx.com/docs/houdini/vellum/vellumattributes.html), accessed 2026-07-26.
- SideFX, [Vellum Hair workflow](https://www.sidefx.com/docs/houdini/shelf/vellumhair.html), accessed 2026-07-26.
- SideFX, [Vellum Solver DOP](https://www.sidefx.com/docs/houdini/nodes/dop/vellumsolver.html), accessed 2026-07-26.
- SideFX, [Working with low- and high-resolution cloth](https://www.sidefx.com/docs/houdini/vellum/lowreshighres.html), accessed 2026-07-26.
