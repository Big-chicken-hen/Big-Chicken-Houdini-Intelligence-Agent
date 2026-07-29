# RBD constraints, Bullet solving, and cache workflow

Pack version: 2.0.0

## Use for

Use this workflow after rigid pieces have been fractured and named, when the effect requires controlled holding, bending, plastic deformation, staged failure, collision solving, and recoverable caching. It covers glue, soft, and cone-twist relationships in the SOP-level RBD Bullet Solver, including how to diagnose a constraint network before spending time on a full simulation.

## Context and core data

Each SOP constraint is a two-point polyline with a required identity contract.
Both endpoint points need a non-empty string `name` matching the corresponding
packed-piece `name`, and every constraint primitive needs a non-empty string
`constraint_name` matching the relationship data name expected by the solver.
Primitive attributes such as `strength`, stiffness, damping, limits, and
`constraint_tag` then describe or select behavior. `constraint_tag` is an
authoring selector, not a substitute for `constraint_name`. Glue remains rigid
until its strength is exceeded. Soft and cone-twist relationships have their
own supported properties and limits.

## Recommended data flow

Use `named packed pieces + constraint polylines with endpoint point name +
proxy geometry → RBD Constraint Properties authors primitive constraint_name
and behavior → render/constraint/proxy to Bullet inputs 1/2/3 → RBD I/O`.
Create broad structural constraints first, then task-specific overrides. Use
tags or groups to select constraints without replacing their required
relationship name. Keep forces, emitted RBDs, guided motion, and debris
generation downstream of a validated base constraint solve.

## Step-by-step workflow

1. Verify that every constraint primitive is a two-point polyline and that both
   endpoint points carry non-empty `name` values resolving to unique packed
   pieces.
2. Build constraints from proximity, curves, rules, or material fracture
   output, and reject orphan endpoints or self-links not required by the setup.
3. Use RBD Constraint Properties to author a valid primitive
   `constraint_name`, then assign tags or groups for targeted strength,
   stiffness, damping, plasticity, and limit edits.
4. Configure density, bounce, friction, collision padding, and active/animated state on the pieces.
5. Run a low-resolution test with default solver settings and visualize constraint forces or break thresholds.
6. Increase substeps for missed fast motion; increase constraint iterations only when contacts or constraints fail to converge.
7. For a long or interruption-sensitive solve, enable Save Checkpoints and set
   a unique Base Folder, Base Name, and Version. Choose Checkpoint Trail Length
   for the retained recovery window and Checkpoint Interval for the acceptable
   recomputation after interruption. When an upstream solver input changes,
   advance Version or use a new Base Folder/Base Name; explicit checkpoint
   files are not automatically deleted merely because they become invalid.
8. Resume from an existing checkpoint and compare a known later frame with the
   uninterrupted solve. Then change Version for a bounded test and verify the
   old checkpoint is not silently reused before caching approved geometry with
   RBD I/O.

## Critical parameters and attributes

The required primitive string `constraint_name` selects the DOP relationship
data used by the solver; every endpoint point string `name` selects one packed
piece. Both names must have the correct attribute class, type, spelling, and
case. For glue, `strength` determines implicit failure. Soft constraints need
the relevant linear/angular stiffness, damping, rest-length, and plasticity
properties. `constraint_tag` or primitive groups select subsets for authoring
and break rules but do not identify the relationship. Global Substeps address
temporal tunneling. Constraint Iterations address convergence. Breaking rules
may test force, distance, angle, or torque on selected constraints.

## Cache, version, and performance

Use the solver's memory cache only for interactive iteration; RBD I/O is the
durable geometry delivery. Save Checkpoints stores resumable simulation state,
not final geometry. Base Folder, Base Name, and Version identify a checkpoint
sequence. Checkpoint Trail Length controls retained history (`0` keeps all
checkpoints), while Checkpoint Interval trades disk use against restart
recomputation. Bullet's Parallel Gauss-Seidel Islands mode favors many separate
interaction islands, while Graph Coloring favors a few large connected
structures. Avoid transferring unnecessary solver attributes to output
geometry. Explicit checkpoints that are invalidated remain on disk, and files
from an earlier Houdini session are not governed retroactively by the current
session's Trail Length. Treat Base Folder, Base Name, and Version as part of
the solver-input identity.

## Validation

Before solving, count constraint primitives with missing `constraint_name`,
endpoints with missing `name`, endpoints whose names are absent from the piece
stream, and unintended self-links; all must be zero. Display Constraint
Geometry on the RBD Bullet Solver and use the viewport inspector to read point
`name` and primitive `constraint_name` under the cursor. Confirm the constraint
stream is connected to solver input 2 and the render/proxy streams to inputs 1
and 3. Enable task-relevant force or threshold visualizations, test frames
around first impact and progressive failure, then reload the disk cache. This
is a documented/static acceptance contract until a target H21 solve confirms
the relationship data and motion. If checkpoints are enabled, start a fresh
evaluation with the same Base Folder, Base Name, and Version, request a frame
after the newest valid checkpoint, verify earlier frames are not recomputed,
and compare a known later frame with the uninterrupted result. After changing
an upstream solver input, advance Version or checkpoint path and verify the
solver does not load the previous sequence; Reset alone is not proof that old
explicit files cannot be reused.

## Common failures and troubleshooting

Objects that never break usually have excessive glue strength, missing impact energy, or a group rule that excludes the intended constraints. Explosive separation often comes from initial proxy overlap, excessive padding, contradictory constraints, or large timestep error. High-speed tunneling calls for more substeps, not merely more constraint iterations. Persistent wobble suggests poor proxies, mass distribution, or low rotational stiffness. If only some constraints respond to edits, inspect tags and primitive groups for inconsistent spelling.

Constraints that are ignored entirely often have an empty or misspelled
primitive `constraint_name`, missing endpoint point `name` values, endpoint
names that do not exist on the packed pieces, or the constraint stream wired to
the wrong solver input. Do not rely on the solver's best-effort inference of
missing names as an authoring workflow.

A supposedly resumed solve that starts from the beginning usually has a
different Base Folder, Base Name, or Version, no valid checkpoint in the
retained trail. The opposite failure is more dangerous: after an upstream
change, an unchanged checkpoint identity can silently reuse stale explicit
files because invalidated checkpoints are not deleted automatically. Advance
Version or the path and verify the loaded recovery frame. Do not mistake a
checkpoint for the approved RBD I/O cache.

## When not to use

Do not use a dense constraint network to fake a continuous soft material when Vellum, FEM, or MPM offers the correct model. Do not enable checkpoints for a trivial short test. Avoid dynamic constraint rebuilding every frame unless the effect requires it, because topology changes add solve cost and can introduce jitter.

## Houdini 21 notes

Houdini 21 improved storage and tracking of initially overlapping Bullet
objects. The endpoint point `name` plus primitive `constraint_name` contract is
the documented RBD Bullet Solver interface and was not live-solved while
building this pack. Current Houdini 22 constraint-property pages may expose
newer controls; confirm the installed H21 node while preserving these required
attribute classes.

## Official sources

- SideFX, [RBD constraints](https://www.sidefx.com/docs/houdini/destruction/constraints.html), accessed 2026-07-26.
- SideFX, [RBD Bullet Solver SOP](https://www.sidefx.com/docs/houdini/nodes/sop/rbdbulletsolver.html), accessed 2026-07-26.
- SideFX, [RBD Constraint Properties SOP](https://www.sidefx.com/docs/houdini/nodes/sop/rbdconstraintproperties.html), accessed 2026-07-26.
- SideFX, [Caching simulations](https://www.sidefx.com/docs/houdini/dyno/cache), accessed 2026-07-26.
- SideFX, [Houdini 21 Rigid Body Dynamics changes](https://www.sidefx.com/docs/houdini/news/21/rbd.html), accessed 2026-07-26.
