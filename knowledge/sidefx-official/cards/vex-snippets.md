# VEX and Wrangle workflow

Source: https://www.sidefx.com/docs/houdini/vex/snippets

Use VEX when an operation is naturally expressed per point, primitive, vertex,
voxel, sample, or element and a long chain of micro-nodes would obscure the
algorithm. Prefer ordinary SOPs when they already express the operation clearly.

Before writing a Wrangle:

- Confirm its run-over class and input geometry.
- List every attribute read or written, including type and ownership.
- Decide whether the code changes values, creates geometry, removes elements, or
  only builds groups.
- Keep parameters promoted or named when an artist should control them.

Read input attributes explicitly and avoid accidental type creation. Use stable
IDs for animation and simulation logic instead of point numbers when topology
can change. When creating geometry, make the ownership and order of new points,
vertices, and primitives deliberate.

Validation should check the attribute class, tuple size, representative values,
element counts before and after, and warnings or errors. Typical failures are
running over the wrong class, reading from the wrong input, unstable point-number
IDs, and changing topology while iterating without a clear plan.
