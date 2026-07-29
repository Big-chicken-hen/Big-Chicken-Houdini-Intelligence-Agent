# FEM solid, constraint, collision, and render-deformation workflow

Canonical ID: `fem-solids-constraints`

Houdini version: 21 (documented/static; not live-verified)

Pack version: 2.0.0

## Use for

Use this workflow for deformable solids or hybrid solid-and-shell objects whose
elastic response, stress, and local volume preservation need the continuum
model of Houdini's finite element solver. It covers construction and validation
of a simulation mesh, selection of FEM objects and solve method, animation
constraints, collision strategy, caching, and transfer back to render geometry.
It is intentionally separate from Vellum's position-based constraints and from
MPM's particle/grid treatment of large plastic deformation and mixed materials.

## Context and core data

FEM Solid Objects use tetrahedra. FEM Hybrid Objects may combine tetrahedra,
surface polygons, curves, and isolated points. The simulation cage should be
lower resolution and better conditioned than the render mesh; embedding
transfers its deformation to detailed geometry. Start from an airtight,
non-self-intersecting source at real scale. Tet Embed is the usual first choice,
while Remesh followed by Tet Conform gives more direct meshing control. FEM
Validate reports poor and inverted elements before they reach the solver.

Animation influence is represented by first-class target and region
constraints rather than arbitrary point edits. `targetP` provides target
positions, Target Strength and Target Damping control soft following, and
`pintoanimation` identifies hard following where exact attachment is truly
required. Geometry state, material multipliers, constraints, and collision
configuration together define the solve.

## Recommended data flow

Use:

`clean render or proxy source -> Tet Embed, or Remesh + Tet Conform -> FEM
Validate -> FEM Solid/Hybrid Object -> target/attach/slide/region constraints ->
FEM Solver -> tetrahedral cache -> embedded render deformation or Convert Tets
surface -> render handoff`

Keep the simulation cage, constraint selections, collision representations,
and render mesh separate. Cache the tetrahedral state before surfacing because
fracture or conversion may change surface topology and point numbers.

## Step-by-step workflow

1. Set the scene Unit Length and verify real dimensions, watertightness,
   normals, self-intersections, zero-area faces, and disconnected components.
2. Build a coarse, regular tetrahedral cage with Tet Embed. If the shape needs
   tighter boundary control, prepare a simplified surface with VDB, Remesh, or
   PolyReduce before Tet Conform rather than tetrahedralizing the render mesh.
3. Run FEM Validate, inspect slices, and repair inverted, tiny, or badly shaped
   tetrahedra before creating a DOP object.
4. Choose FEM Solid Object for an all-tetrahedral solid or FEM Hybrid Object
   when the solve intentionally combines solid, shell, curve, or point
   elements. Set material density and elastic behavior at physical scale.
5. Choose GNL or GSL from the required feature set. Prefer the nonlinear method
   for supported highly nonlinear response, but verify documented limitations
   such as stress fracture or collision combinations before committing.
6. Add the least restrictive constraint that expresses the goal: distributed
   target deformation for matching animation of identical topology, point
   targets for local control, or attach, slide, fuse, and region constraints
   for relationships between objects.
7. Select surface collision for deforming or fast contacts that need continuous
   detection. Use volume/SDF collision only where its faster but less accurate
   static behavior is appropriate. Test self-collision only when required.
8. Run a short low-resolution test, then cache tetrahedra and constraints.
   Apply embedding or Convert Tets downstream and validate the render mesh
   independently from the solve cage.

## Critical parameters and attributes

Unit Length and mass density establish the physical scale of gravity,
stiffness, damping, and forces. Tet size and quality influence cost and
conditioning, but FEM material response is designed to remain comparatively
consistent across useful mesh resolutions. Material-model parameters on the
Solid or Hybrid Object define shape and volume stiffness; attributes such as
`solidstiffness` may provide local multipliers when the task needs variation.

FEM Solver Substeps improve temporal accuracy and often help nonlinear
convergence. Collision Passes control repeated collision detection and
resolution. Repulsion controls the soft collision response. GNL and GSL have
different supported features and should not be swapped solely to hide a bad
mesh.

For animated targets, use Target Deformation or `targetP`, then tune Target
Strength and Target Damping. Start with the lowest soft strength that follows
the animation adequately. `targetstrength` can vary influence locally.
`pintoanimation` is a hard constraint and should be limited to points that must
match exactly. Attach and slide constraints need stable object and point-region
selection; region constraints can connect interior regions without identical
topology.

## Cache, version, and performance

Do not solve render-resolution tetrahedra when a coarse cage and embedding
preserve the required deformation. Compare element counts and solve time before
adding resolution. Surface self-collision and multiple collision passes can be
expensive, so enable them from observed need rather than as blanket insurance.
Constant topology and material data permit solver optimizations; changing rest
shape, constraints, or material coefficients during the solve requires
deliberate support.

Write tetrahedral simulation geometry to cache and derive the render surface
afterward. Preserve the cage-to-render embedding contract and version it when
either topology changes. Reuse the repository's general cache and packaging
rules rather than inventing a FEM-specific storage policy.

## Validation

Before solving, use FEM Validate to inspect element quality, inversions, and
slices through the cage. Record scene scale, tet count, minimum element quality,
and the chosen object and solve method. Run simple gravity, compression, or
bending tests that expose the intended material identity before introducing
complex animation.

During the solve, inspect penetration, self-collision, target error, volume
retention, recovery, energy or force visualization, and any solver warnings.
Compare the same frames at two reasonable cage resolutions to catch a bad
meshing dependency. After caching, reload from disk, convert or embed the render
mesh, and verify positions, velocities, normals, silhouette, and attachment
regions. This card is based on SideFX documentation and was not reproduced in a
live Houdini 21 FEM scene.

## Common failures and troubleshooting

Explosions and linear-solve failures commonly come from inverted or near-zero
tetrahedra, impossible initial overlap, non-positive mass, extreme stiffness,
or too few substeps. Repair and revalidate the mesh before increasing every
solver parameter. Visible penetration may require the correct collision type,
greater Repulsion, or more Collision Passes; a deforming or fast collider
should not be represented by a coarse static SDF.

Targets that either lag or destabilize the solve often use excessive strength
or an unnecessary hard pin. Lower soft Target Strength, add appropriate
damping, and restrict the affected region. Do not use a SOP Solver to directly
rewrite simulated `P` or `v`; use target, attach, slide, or region constraints.
Likewise, legacy `force` and `fexternal` attributes lack the information FEM
needs for robust high-quality control.

If the render surface tears, swims, or loses textures while the cage is valid,
inspect the embedding and post-solve conversion rather than retuning material
stiffness. Cache the tets, not a topology-changing surfaced result, when stable
downstream correspondence matters.

## When not to use

Do not use FEM for a rigid object that should remain an RBD. Prefer Vellum for
thin cloth, hair, or deliberately art-directed constraint behavior when a full
continuum solve is unnecessary. Prefer MPM for large plastic deformation,
granular/chunky breakup, or multi-material interactions that naturally use a
particle/grid model. Do not introduce FEM merely to add a static squash that a
procedural deformation can produce more cheaply.

## Houdini 21 notes

The SideFX finite-element chapter and FEM Solver 2.0 help describe the H21
workflow. GNL supports nonlinear material response but retains documented
feature limits; GSL remains relevant when a required operation is unsupported
by GNL. Confirm the actual H21 object version, material model, and limitation
list from installed help before a production setup.

This card classifies the workflow as documented/static. No live H21 tet
generation, DOP solve, collision test, cache reload, or render deformation was
performed. Recommendations about naming, versioning, and cache organization are
workflow guidance, not claims of a SideFX-mandated facility policy.

## Official sources

- SideFX, [About finite elements](https://www.sidefx.com/docs/houdini/finiteelements/about.html), accessed 2026-07-27.
- SideFX, [Creating simulation meshes for use with FEM](https://www.sidefx.com/docs/houdini/finiteelements/geometry.html), accessed 2026-07-27.
- SideFX, [FEM constraints](https://www.sidefx.com/docs/houdini/finiteelements/constraints.html), accessed 2026-07-27.
- SideFX, [Finite element collisions](https://www.sidefx.com/docs/houdini/finiteelements/collisions.html), accessed 2026-07-27.
- SideFX, [Rendering finite elements](https://www.sidefx.com/docs/houdini/finiteelements/rendering.html), accessed 2026-07-27.
- SideFX, [FEM Solver 2.0 DOP](https://www.sidefx.com/docs/houdini/nodes/dop/femsolver.html), accessed 2026-07-27.
