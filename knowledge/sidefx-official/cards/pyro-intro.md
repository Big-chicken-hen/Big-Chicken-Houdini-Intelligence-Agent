# Pyro source and sparse solve workflow

Pack version: 2.0.0

## Use for

Use this workflow to build smoke, fire, explosions, dust, steam, and other gaseous effects with the Houdini 21 SOP Pyro Solver or an equivalent sparse DOP setup. It covers source preparation and the first physically coherent simulation. The intended result is a stable field cache whose motion is driven by meaningful source values and large-scale flow before small shaping noise is introduced.

## Context and core data

Pyro sources are commonly prepared as points with attributes, then rasterized to volumes. Important fields are `density`, `temperature`, `flame`, `vel`, and `divergence`; collisions use `collision` and velocity. The sparse solver allocates active voxels around meaningful fields rather than filling the full bounding box. In the modern flame model, `flame` stores remaining reactant lifetime and can generate smoke, temperature, and expansion as it decays.

## Recommended data flow

Use `source geometry or particles → Pyro Source → attribute shaping/noise → Volume Rasterize Attributes → Pyro Solver SOP → inspection output`. For spreading fire, use Pyro Source Spread before rasterization. Bring collisions through a Collision Source as signed distance and velocity fields. Keep post-processing, look development, and durable caching downstream of the approved solve.

## Step-by-step workflow

1. Establish scene units, effect duration, source motion, maximum bounds, and the intended smoke/fire scale.
2. Convert source geometry to points and author only the required attributes with deliberate ranges.
3. Rasterize the points and inspect each source volume before connecting the solver.
4. Initialize the solver sourcing table and verify field names, target fields, and merge operations.
5. Solve at a coarse voxel size with buoyancy, flame output, and source velocity providing the main motion.
6. Add disturbance, turbulence, shredding, dissipation, or viscosity one scale at a time.
7. Refine bounds, voxel size, CFL behavior, and collisions only after the broad evolution is approved.

## Critical parameters and attributes

Voxel Size is the principal resolution control; halving it can require about eight times as many voxels. `density` controls visible smoke, `temperature` drives buoyancy relative to ambient, `flame` controls reaction lifetime, and `divergence` drives expansion. Source `flame` usually uses Maximum rather than Add to avoid unbounded accumulation. Flame Lifespan, smoke Emission Amount, Temperature Amount, and Expansion Rate shape combustion output. CFL Condition and Max Substeps limit movement through voxels.

## Cache, version, and performance

Sparse domains save work only when active fields occupy a fraction of the bounds. Keep bounds and resize padding large enough for growth but not indefinitely oversized. A Velocity Voxel Scale significantly above one can lower velocity resolution, though small increases may lose time to resampling. Do not lower Voxel Size before the source and motion are correct. Dense OpenCL and minimum-GPU modes have different restrictions and are not automatic upgrades over sparse solving.

## Validation

Inspect source points, rasterized source volumes, solver fields, active bounds, and collision fields separately. Visualize velocity and confirm that the source injects the intended direction and speed. Check the fastest growth and strongest collision frames for clipping or tunneling. Compare density, temperature, flame, and divergence rather than judging only a colored viewport visualization. Record voxel size, frame range, and solver mode with any approved preview.

## Common failures and troubleshooting

No smoke usually means a missing source field, wrong target name, or an unsuitable merge operation. Endless flame often comes from additive source accumulation or excessive lifespan. Clipped plumes require more resize padding or larger maximum bounds. Smoke passing through a collider suggests a coarse collision SDF, missing collider velocity, or an overly permissive CFL step. Excessive noise at every scale indicates shaping was added before broad motion was established.

## When not to use

Use H21 COP Pyro for very fast two-dimensional fire or smoke plates with an infinite timeline. Use particles, sprites, or procedural VDB construction for distant or non-dynamic effects. A static fog bank may need only a modeled volume and shader. Do not choose dense GPU Pyro solely because a GPU is present; sparse CPU/OpenCL behavior may be more appropriate for the source and domain.

## Houdini 21 notes

The workflow above is documented/static. Houdini 21's separated SOP and DOP Pyro shelf workflows, added SOP Pyro presets, Gas Remap, Gas Dissipate 2.0, Gas Burn controls, additional turbulence influence, and COP sparse GPU Pyro are change-note-supported. H21's COP Pyro licensing boundary is also taken from the change notes. This corpus build did not live-verify an H21 GUI, solve, or COP Pyro cook.

## Official sources

- SideFX, [Introduction to Pyro](https://www.sidefx.com/docs/houdini/pyro/intro.html), accessed 2026-07-26.
- SideFX, [Pyro Solver SOP](https://www.sidefx.com/docs/houdini/nodes/sop/pyrosolver.html), accessed 2026-07-26.
- SideFX, [Houdini 21 Pyro and FLIP changes](https://www.sidefx.com/docs/houdini/news/21/pyro.html), accessed 2026-07-26.
