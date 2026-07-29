# MPM material, collision, cache, and surfacing workflow

Canonical ID: `mpm-material-simulation`

Houdini version: 21 (documented/static; not live-verified)

Pack version: 2.0.0

## Use for

Use this workflow for Material Point Method simulations of elastic, plastic,
chunky, granular, viscous, or liquid materials, especially when several such
materials interact through one particle/background-grid solve. It covers the
SOP-level Container, Source, Collider, and Solver contract introduced in
Houdini 20.5 and the H21 caching, collision, sleeping, post-processing, and
surfacing path. It is not a generic particle recipe and should not replace RBD,
Vellum, FEM, FLIP, or procedural deformation without a material-behavior reason.

## Context and core data

MPM Source converts a mesh or volume into material points and assigns
constitutive-model attributes. MPM Collider converts collision geometry to VDB
fields. MPM Container defines the start frame, global particle separation,
background-grid scale, and optional domain boundaries. Every Source and
Collider, as well as the Solver, must use the same Container contract. Merge
multiple source streams and collider streams separately; the Solver receives
sources, colliders, and container on their dedicated inputs.

The Solver transfers point mass and velocity to a sparse background grid,
solves interactions, and transfers the result back to points through OpenCL.
Output attributes can include velocity, `id`, `pscale`, deformation gradient
`F`, elastic and plastic deformation measures, and simulation metadata such as
particle separation and grid scale. H21 MPM Surface can turn solved points into
a VDB or polygon surface and transfer selected rest-source attributes,
including UVs with island-aware handling.

## Recommended data flow

Use:

`global MPM Container -> MPM Source branches + MPM Collider branches -> merge
sources and merge colliders -> MPM Solver inputs (sources, colliders, container)
-> point cache and optional checkpoints -> MPM Surface or
Post-Fracture/Deform Pieces -> render or secondary-effect handoff`

Keep the source mesh, material points, collision VDBs, solved points, and final
render geometry as separate named stages. The Container must be shared by
reference or connection rather than recreated independently for each branch.

## Step-by-step workflow

1. Establish real scale, start frame, expected bounds, material categories, and
   the smallest feature that must survive. Create one MPM Container and choose
   a provisional Particle Separation and Grid Scale.
2. Create each MPM Source from a clean mesh or volume. Choose Once or Continuous
   emission, segment independent pieces when needed, and assign a material
   Behavior and preset only as a measured starting point.
3. Inspect and, where justified, vary source attributes such as density,
   stiffness, volume preservation, critical compression/stretch, hardening,
   viscosity, friction angle, cohesion, and initial velocity.
4. Convert each collider with MPM Collider. Use Static for fixed geometry,
   Animated (Rigid) for transform-only motion, and Animated (Deforming) only
   when shape changes. Merge all collider outputs separately from sources.
5. Connect merged sources, merged colliders, and the shared Container to the
   corresponding MPM Solver inputs. Run a short low-resolution material and
   collision test before adding secondary forces or final resolution.
6. Tune substeps and material stiffness from observed stability. Disable
   Assume Unchanging Material Properties when properties actually change over
   time, accepting the additional work required to recompute conditions.
7. Enable only required outputs. Preserve `F` for a destruction/deformation
   handoff, and save solver checkpoints for a long simulation using an explicit
   folder, version, trail length, and interval.
8. Cache solved points, then choose H21 MPM Surface for VDB or polygon output,
   or the H21 post-fracture/deform-pieces route for a textured source that must
   preserve piece detail. Validate transferred attributes before rendering.

## Critical parameters and attributes

Container Particle Separation drives source, collider, and solver resolution.
Grid Scale converts separation to background-grid voxel width; a smaller grid
can reduce unwanted property mixing between nearby materials but increases
cost. Optional boundaries may remain open, bounce material, or delete points.
Source point-generation mode, oversampling, relaxation, emission, and
segmentation affect initial coverage and identity.

Material stiffness `E` is Young's modulus and has a strong timestep cost.
`nu` controls volume-preserving transverse response. `c_compress`,
`c_stretch`, and `hardening` control plastic behavior for chunky materials;
viscosity, cohesion, friction angle, incompressibility, and surface tension
apply only to relevant material models. Material presets are starting values,
not acceptance evidence.

`pintoanimation` and `targetp` support animated targets. If pins may activate
later, `pintoanimation` must exist from the start even when initialized to
zero. Collider Voxel Size and velocity coverage matter for moving surfaces.
Thin colliders may need finer VDB resolution plus Solver
Particle-Level Collisions. `F` is required for the documented destruction
workflow; `Jp` and `Je` help distinguish plastic and elastic deformation.
Viewport `pscale` affects display and meshing, not the material point's physical
volume, which derives from particle separation.

## Cache, version, and performance

The solver is implemented in OpenCL. Prefer a supported device with sufficient
memory, but do not describe MPM as GPU-only: the node help exposes both CPU and
GPU OpenCL build flags. Record the device and driver when comparing results.
Reducing Particle Separation increases point and grid cost rapidly. Very high
stiffness can demand small timesteps, so reduce it to the lowest value that
preserves the intended behavior before spending resolution.

Use checkpoints to recover long solves, not as the final render cache. Set Base
Folder and Version deliberately, retain enough trail for recovery, and choose
an interval that balances disk use against lost work. Cache solved points
before expensive surfacing. If topology, separation, source material, or
Container settings change, invalidate the dependent cache explicitly.

## Validation

Begin with one material in a bounded drop, compression, shear, or flow test and
compare recovery, permanent deformation, settling, and volume against the
intended identity. Inspect particle count, `particlesep`, `dx`, actual substep
count, active/passive distribution, and solver warnings. Visualize colliders,
background grids, pinned points, and material attributes. Check output `F`,
`Jp`, `Je`, velocity, and positions for non-finite values.

Test the fastest contact and thinnest collider at representative subframes.
For multiple materials, inspect interface behavior and grid-scale bleeding.
Reload a point cache, resume a checkpoint in a controlled test, and compare a
repeat solve on the same device and settings when deterministic transfer is
enabled. For MPM Surface, inspect silhouette, holes, loose-particle treatment,
velocity, and UV or color transfer from the rest source. These are
documentation-backed checks; no H21 OpenCL solve, checkpoint recovery, or
surface generation was run for this card.

## Common failures and troubleshooting

Exploding, `inf`, or `nan` motion usually indicates excessive stiffness,
insufficient substeps, extreme initial overlap, or incompatible changing
material properties. Fix the physical setup before relying on clamps.
Unwanted mixing between materials can result from a background grid that is
too coarse; reduce Grid Scale cautiously and remeasure cost. A sparse active
region that cannot keep up with fast points may require a larger voxel-dilation
margin.

Particles crossing a thin collider point to inadequate collider VDB
resolution, missing velocity coverage, or the absence of particle-level
collisions. Use Animated (Rigid) instead of deforming collision when only the
transform changes; it is both cheaper and more accurately interpolated.
Animation pins that appear too late often lack `pintoanimation` at the initial
state. A destruction handoff that cannot reconstruct rotation or deformation
may have omitted `F`.

If surfacing loses textures, verify the rest-source input, UV ownership and
islands, and attribute-transfer list rather than smoothing the final mesh
blindly. If an online parameter is absent in H21, check the H21 release notes
and installed help before assuming the feature exists.

## When not to use

Use RBD for rigid pieces that do not need material deformation, Vellum for
thin sheets or art-directed constraints, FEM for continuum elastic tissue and
controlled solid deformation, and FLIP for a conventional free-surface liquid
when MPM multiphysics is not required. Do not run MPM for a static shape or a
single procedural squash. MPM is unavailable in Houdini Core because its
SOP-level interface still requires DOP-level permissions.

## Houdini 21 notes

Houdini 20.5 introduced the SOP MPM Source, Collider, Container, and Solver
workflow. H21 added documented sleeping controls, deterministic
particle-to-grid transfer, surface-tension options, improved collider handling,
MPM Surface, debris and post-fracture tools, and related post-processing. Limit
an H21 card to features supported by the 20.5 baseline and H21 change notes;
current node pages may also contain later parameters.

This workflow is documented/static and not live-verified. Exact OpenCL device
support, memory limits, parameter availability, and checkpoint recovery must be
tested in the target H21 build. No solver, cache, surface, or license-tier
check was run while authoring this card.

## Official sources

- SideFX, [What's new: MPM in Houdini 20.5](https://www.sidefx.com/docs/houdini/news/20_5/mpm.html), accessed 2026-07-27.
- SideFX, [What's new: MPM in Houdini 21](https://www.sidefx.com/docs/houdini/news/21/mpm.html), accessed 2026-07-27.
- SideFX, [MPM Container SOP](https://www.sidefx.com/docs/houdini/nodes/sop/mpmcontainer.html), accessed 2026-07-27.
- SideFX, [MPM Source SOP](https://www.sidefx.com/docs/houdini/nodes/sop/mpmsource.html), accessed 2026-07-27.
- SideFX, [MPM Collider SOP](https://www.sidefx.com/docs/houdini/nodes/sop/mpmcollider.html), accessed 2026-07-27.
- SideFX, [MPM Solver SOP](https://www.sidefx.com/docs/houdini/nodes/sop/mpmsolver.html), accessed 2026-07-27.
- SideFX, [MPM Surface SOP](https://www.sidefx.com/docs/houdini/nodes/sop/mpmsurface.html), accessed 2026-07-27.
