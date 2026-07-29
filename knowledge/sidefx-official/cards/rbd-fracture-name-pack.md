# RBD fracture, naming, and packing workflow

Pack version: 2.0.0

## Use for

Use this workflow to prepare breakable rigid geometry for Bullet when the asset must remain editable through fracture, simulation, caching, and render reconstruction. It is appropriate for concrete, glass, wood, masonry, and custom cutter workflows in Houdini 21. The goal is not merely to create many pieces: it is to produce uniquely named packed pieces, efficient collision proxies, and an optional constraint stream that downstream RBD SOPs can interpret without ambiguity.

## Context and core data

The standard RBD SOP contract has three synchronized geometry streams: render geometry, constraint geometry, and proxy geometry. Fractured pieces are identified by the string primitive attribute `name`. Packed primitives preserve each piece as a lightweight transformable object. Constraint primitives, when present, refer to the same names at their endpoints. Proxy geometry supplies Bullet with convex or compound collision shapes while the original geometry remains available for rendering and deformation transfer.

RBD Pack is a transport wrapper, not the Bullet Solver's three input contract.
It stores the render, constraint, and proxy streams as three packed primitives
in one wire and marks them with `rbd_type`. Before solving that single stream,
RBD Unpack must restore its three outputs: render geometry to RBD Bullet Solver
input 1, constraint geometry to input 2, and proxy geometry to input 3. If the
setup was never RBD-packed, connect the original three streams directly to
those same inputs.

## Recommended data flow

For an unpacked setup, use `source geometry → cleanup/material groups → RBD
Material Fracture/RBD Configure → optional constraint editing → render output
to Bullet input 1 + constraints to input 2 + proxy to input 3`.

For a one-wire transport or cache setup, use `the same three outputs → RBD
Pack/RBD I/O → switches, merges, or cache → RBD Unpack → outputs 1/2/3 to
Bullet inputs 1/2/3`. Preserve `rbd_type` while the setup is packed. Insert RBD
Exploded View only as an inspection branch. Do not repeatedly unpack piece
geometry for polygon edits and repack it without a specific need.

## Step-by-step workflow

1. Confirm scene scale, watertightness, normals, thickness, and material regions before fracturing.
2. Create stable primitive groups for regions that need different fracture methods or physical properties.
3. Fracture one material region at a time, using impact points, density attributes, or custom cutters to direct detail.
4. Assign or verify a unique `name` value for every final piece; add a namespace when separate fractured assets may be merged.
5. Inspect the generated proxy output and choose convex hull or convex decomposition according to piece concavity.
6. Pack the render, constraint, and proxy streams together only when a
   one-wire switch, merge, or cache boundary is useful, and preserve the
   wrapper's `rbd_type` metadata.
7. Before RBD Bullet Solver, restore an RBD-packed stream with RBD Unpack.
   Connect unpack output 1 to solver input 1, output 2 to input 2, and output 3
   to input 3. For an unpacked setup, connect the three fracture outputs
   directly to those inputs.
8. Test the correctly wired triplet in a low-cost Bullet solve before adding
   secondary fracture detail.

## Critical parameters and attributes

`name` is the primary piece identity and must be unique and consistent between
render geometry, proxy geometry, and constraint endpoints. RBD Pack adds
`rbd_type` to distinguish its render, constraint, and proxy payloads; deleting
or rewriting that attribute prevents reliable RBD Unpack routing. Optional
`rbd_name` identifies packed setups when several are merged. `density` can
guide fracture point distribution before the fracture node, while fracture
namespaces prevent collisions between names from different assets. Proxy
generation parameters determine collision fidelity. Excessively detailed
proxies defeat packing performance; proxies that ignore major concavities
produce floating or premature contacts.

## Cache, version, and performance

Bullet prefers packed convex shapes. Use convex decomposition only where a
single convex hull changes the physical collision silhouette materially.
During expensive fracture tuning, switch Houdini to manual update and force a
cook only after a coherent parameter change. Cache the static fractured setup
separately from the time-dependent simulation so changes to lighting or
materials do not recook fracture. RBD I/O can store the synchronized triplet in
one sequence, but a packed single-wire result still needs RBD Unpack before the
solver's first three inputs.

## Validation

Use RBD Exploded View to inspect every piece, internal surface, and gap.
Middle-click the three outputs to compare piece counts and attributes. Verify
unique names with the geometry spreadsheet. Display proxy geometry over the
render mesh and check that collision shapes are neither oversized nor missing.
At the solver, inspect the actual wires: render must arrive on input 1,
constraints on input 2, and proxy on input 3. If a packed carrier was used,
verify RBD Unpack produced all three streams and retained their names. Run a
short drop test and confirm that simulated proxy transforms reconstruct the
render pieces without changing their internal topology. This is a documented
static contract until checked in a target Houdini build.

## Common failures and troubleshooting

Duplicate names cause constraints or transforms to affect the wrong pieces. Missing interior faces usually indicate unsuitable input topology or cutter settings. Convex hulls around strongly concave shards create invisible collision gaps; use decomposition selectively. Very small chips raise solve and cache cost disproportionately. If pieces appear welded unexpectedly, inspect incoming constraints rather than increasing impact force. If the render mesh separates from the simulation, verify that both streams share the same `name` values.

A single RBD Pack output wired only to solver input 1 does not satisfy the
three-stream contract: insert RBD Unpack and route outputs 1/2/3 to solver
inputs 1/2/3. Empty constraint or proxy inputs after unpacking often mean
`rbd_type` was lost or the wrong data was packed.

## When not to use

Do not fracture a rigid hero object that never breaks. Use a single packed RBD instead. Do not represent continuously bending cloth, rubber, or thin sheet deformation with hundreds of rigid fragments when Vellum, FEM, or MPM is the intended physical model. For a purely visual crack that never changes silhouette, a shader or modeled displacement can be cheaper.

## Houdini 21 notes

Houdini 21 includes the established three-input/three-output RBD SOP workflow
and improved Bullet handling of initially overlapping objects. The routing
above follows SideFX's documented RBD Pack, RBD Unpack, and RBD Bullet Solver
contracts; it was not live-solved for this pack. Current help may show RBD
Material Fracture controls introduced later, so confirm the installed H21 node
version.

## Official sources

- SideFX, [Destruction overview](https://www.sidefx.com/docs/houdini/destruction/overview.html), accessed 2026-07-26.
- SideFX, [RBD Pack SOP](https://www.sidefx.com/docs/houdini/nodes/sop/rbdpack.html), accessed 2026-07-26.
- SideFX, [RBD Unpack SOP](https://www.sidefx.com/docs/houdini/nodes/sop/rbdunpack.html), accessed 2026-07-26.
- SideFX, [RBD Bullet Solver SOP](https://www.sidefx.com/docs/houdini/nodes/sop/rbdbulletsolver.html), accessed 2026-07-26.
- SideFX, [Houdini 21 Smashing Wine Glass](https://media.sidefx.com/files/tutorials/h21-foundations-smashing-wineglass/h21_foundations_smashing_wineglass.pdf), accessed 2026-07-26.
