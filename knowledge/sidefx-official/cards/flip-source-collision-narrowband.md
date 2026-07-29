# FLIP sourcing, collision, and narrow-band workflow

Pack version: 2.0.0

## Use for

Use this workflow to establish a SOP FLIP simulation in Houdini 21, including its container, initial or continuous source, domain boundaries, moving collision objects, and narrow-band behavior. It applies to pouring, splashes, water tanks, rivers, guided ocean regions, and other liquid effects where particle motion and a temporary pressure grid are required.

## Context and core data

FLIP stores persistent fluid state on particles while rebuilding velocity and
pressure grids during the solve. Particle Separation determines particle
spacing and contributes to collision and pressure resolution. FLIP Container
defines the domain and physical attributes. FLIP Boundary converts source or
sink geometry into the particle/field streams passed along the solver's first
three inputs. FLIP Collide adds surface and velocity collision fields to that
same three-stream chain. Solver input 4 is a separate Boundary Flow connection
for external pressure or velocity such as up-res or ocean guidance.

## Recommended data flow

Use `FLIP Container three outputs → optional FLIP Boundary → optional FLIP
Collide → FLIP Solver inputs 1-3`, preserving the three parallel streams through
each inline node. On the solver, input 1 **Sources** carries fluid particles,
input 2 **Container** carries the container surface field, input 3
**Collisions** carries collision surface and velocity fields, and input 4
**Boundary Flow** independently transfers external pressure or velocity.

Connect source geometry to FLIP Boundary's fourth input and collision geometry
to FLIP Collide's fourth input; those are not the solver's Boundary Flow input.
Use solver input 4 for up-res or ocean-style boundary transfer. When it is used,
turn off Waterline because the connected boundary flow creates the surface.

## Step-by-step workflow

1. Set meter-based scene scale and define the smallest liquid or collision feature that must resolve.
2. Create FLIP Container and choose provisional Particle Separation and Grid
   Scale. Connect its first three outputs to the next FLIP node's first three
   inputs, or directly to FLIP Solver Sources, Container, and Collisions.
3. For emitted or removed fluid, insert FLIP Boundary inline on those three
   streams and connect closed source/sink geometry to the Boundary node's fourth
   input. Choose None, Velocity, or Pressure sourcing deliberately.
4. Insert FLIP Collide after the Boundary when collisions are required. Preserve
   the first three streams and connect collider geometry to FLIP Collide input
   4. Verify Volume/Surface Collide and computed subframe velocity, then feed
   the three Collide outputs to solver inputs 1-3.
5. Use FLIP Solver input 4 only for Boundary Flow pressure/velocity transfer.
   Disable Waterline for this route. Otherwise choose Waterline and domain
   boundary behavior only where fluid reaches domain limits.
6. Run a coarse simulation and inspect particles, `pscale`, velocity, collision
   SDF, reseeding, and domain exits. Container and Solver Particle Separation
   must match before interpreting compression or leakage.
7. Enable Narrow Band only where the surface-layer representation is useful.
   Inspect Bandwidth and any Attribute-Field Pairs needed to preserve state when
   deep particles are deleted and recreated.
8. Before a long solve, enable **Save Checkpoints** and set unique **Base
   Folder**, **Base Name**, and **Version**. Choose **Checkpoint Interval** and
   **Checkpoint Trail Length** from restart cost and disk capacity, then test a
   bounded resume before relying on it.

## Critical parameters and attributes

Particle Separation on Container and Solver must agree. Inspect particle
`pscale` in Geometry Spreadsheet; it is the represented particle scale, and
particles that remain closer than `pscale` can compress because projection
removes separating forces. Grid Scale controls temporary pressure-grid spacing.
A no-force retention test is the documented way to judge the combined
Particle Separation/packing setting.

Solver ports are fixed by role: Sources = particles, Container = surface field,
Collisions = surface plus velocity fields, Boundary Flow = external pressure or
velocity. Source methods use velocity or pressure and require closed solid 3D
or volume geometry. Surface Collide is a mesh representation; Volume Collide
builds an SDF. Move Outside Collision supports volumes; Particle collision mode
is required for open surface collisions. Narrow Band keeps particles within a
voxel Bandwidth of the surface, deletes deeper particles, requires reseeding,
and needs Attribute-Field Pairs for state that must survive regeneration.

## Cache, version, and performance

Large water bodies benefit from Narrow Band because only a surface layer retains
particles. Small or viscous simulations with modest particle counts may be
cheaper without it. Lowering Particle Separation increases particles, pressure
voxels, collision detail, memory, and solve time together.

For long simulations, **Save Checkpoints** writes managed `.sim` states under
**Base Folder** using **Base Name** and **Version**. An Interval of `1` saves
every frame; a larger Interval trades disk use for more frames to recook after a
failure. Trail Length `0` never removes old checkpoint files. A positive Trail
Length removes older checkpoints created by the current Houdini session; after
a restart, pre-existing files are retained so the crashed location remains
available.

Recovery loads the latest valid checkpoint at or before the requested frame and
solves only subsequent frames. Checkpoints are not automatically deleted when
invalidated, and Reset Simulation does not guarantee they are ignored. If
Particle Separation, container, source, collision, boundary flow, forces, or
other upstream solve inputs change, increment **Version** or choose a new Base
Folder/Name before recooking. Never mix an old checkpoint branch into evidence
for a changed simulation.

## Validation

Trace the network and inspect solver inputs: Sources must contain particles,
Container a surface field, Collisions surface/velocity fields, and Boundary Flow
only the intended external pressure/velocity data. Display collision volumes
and velocities, especially on the fastest moving collider. Confirm source
closure, initial fill, Waterline versus input-4 exclusivity, and open-boundary
response.

Match Container and Solver Particle Separation, inspect `pscale`, and run a
no-force retention test; particle count and occupied liquid volume should not
show unexplained compression from mismatched scale. For narrow band, verify
particles occupy the configured surface Bandwidth rather than the deep volume,
reseeding remains enabled, and every required regenerated attribute survives
through its field pair.

For checkpoints, simulate a short range, record a checkpoint frame, reset RAM
state, and request a later frame. Confirm the solver loads the expected Version
and resumes after the recorded frame. Then change Version for a controlled
upstream variation and confirm the old checkpoint is not reused. These are
documented validation steps; this pack did not execute them.

## Common failures and troubleshooting

Thin container walls can be visible to particles but absent from the pressure
grid, causing compression or leakage; thicken collision geometry or improve
resolution. An empty or miswired solver port can look like a sourcing,
collision, or boundary failure: inspect the four port roles before changing
physics. Moving colliders with missing `v` create weak splashes or leaks. Source
geometry that is open or two-dimensional fails for volume emission.

Narrow-band `id` values are not inherently persistent because particles are
regenerated; use supported Attribute-Field Pairs or reconsider the requirement.
A changed upstream network that appears unchanged after Reset Simulation may be
loading an existing checkpoint. Do not retry blindly; switch Version/path and
compare checkpoint metadata and resumed frame.

## When not to use

Use Vellum Fluid for very small cohesive or highly viscous droplets when unified interaction with soft bodies matters. Use an Ocean Spectrum without FLIP for a non-interacting distant sea. Use Pyro for gases. A static liquid surface may need only modeled geometry and a material. Avoid pressure-driven open boundaries for a closed glass where object collisions, not domain limits, define the fluid.

## Houdini 21 notes

The SOP FLIP workflow introduced in Houdini 19.5 is the high-level basis in H21.
H21 change notes add neural point surfacing and partitioned OpenCL surface
processing, while Container, Boundary, Collide, Solver ports, and checkpoint
controls remain the workflow summarized here. The port semantics, `pscale`,
narrow-band, and checkpoint behavior above are **documented/static** from
SideFX help. No H21 solve, checkpoint recovery, collision, or narrow-band
inspection was run for this pack, so none is live-verified.

## Official sources

- SideFX, [FLIP Solver SOP](https://www.sidefx.com/docs/houdini/nodes/sop/flipsolver.html), accessed 2026-07-26.
- SideFX, [Minimal SOP FLIP setups](https://www.sidefx.com/docs/houdini/fluid/sopminimalsetup.html), accessed 2026-07-26.
- SideFX, [FLIP Container SOP](https://www.sidefx.com/docs/houdini/nodes/sop/flipcontainer.html), accessed 2026-07-26.
- SideFX, [FLIP Boundary SOP](https://www.sidefx.com/docs/houdini/nodes/sop/flipboundary.html), accessed 2026-07-26.
- SideFX, [Fluid-object collisions](https://www.sidefx.com/docs/houdini/fluid/sopcollisions.html), accessed 2026-07-26.
- SideFX, [Narrow-band simulations](https://www.sidefx.com/docs/houdini/fluid/sopnarrowband.html), accessed 2026-07-26.
- SideFX, [Caching simulations and checkpoint invalidation](https://www.sidefx.com/docs/houdini/dyno/cache), accessed 2026-07-26.
