# VEX neighbors, volumes, and debugging

Pack version: 2.0.0

## Use for

Use this workflow for VEX code that queries nearby points, traverses connected topology, samples secondary geometry, modifies volume voxels, or needs systematic debugging beyond a compiler message. It complements the core Wrangle card by concentrating on spatial-query assumptions, element correspondence, volume bindings, race-safe accumulation, and reproducible diagnosis. Prefer specialized SOPs when they already provide a tested neighborhood or volume algorithm with appropriate controls.

## Context and core data

Spatial queries operate on an input geometry snapshot. `nearpoints`, `pcopen`, `pcfind`, `xyzdist`, `primuv`, `neighbours`, and topology functions return indices or sampled values whose meaning depends on input number and geometry class. A Volume Wrangle binds named volumes through `@volume_name`; writing an unknown binding does not create a new volume. Voxel coordinates and fields expose bindings such as `@ix`, `@iy`, `@iz`, `@resx`, `@resy`, `@resz`, `@P`, and `@center`. In this context, `@P` is the current voxel center and `@center` is the center of the whole volume in SOP space. Parallel execution means many elements may query the same input safely but must not write shared output without a defined reduction.

## Recommended data flow

Use:

`validated source geometry/volumes -> explicit query radius, count, and input -> neighbor or sampling Wrangle -> diagnostic attributes/groups -> reduction or smoothing stage -> visualization -> final output`

Separate query from mutation when correctness is uncertain. First write diagnostic values such as neighbor count, nearest distance, sampled primitive, or validity flag. After validating them, perform the intended deformation or field update in a downstream Wrangle.

## Step-by-step workflow

1. Confirm which input contains the queried geometry and whether the query expects points, primitives, a point cloud, or named volumes.
2. Expose search radius, maximum count, falloff, and field names as parameters with documented units.
3. Write a diagnostic pass that records hit count, nearest distance, sampled index, and invalid-query groups.
4. Visualize diagnostics and inspect representative boundary, sparse, dense, disconnected, and empty regions.
5. Implement the actual update using input snapshot values and deterministic accumulation rules.
6. For volume code, verify every bound field exists with matching resolution, transform, and scalar/vector type.
7. On a small known `density` volume, a documented diagnostic snippet is `@density = length(@P - @center);`. `@P` must vary by voxel while `@center` stays fixed, so center-adjacent voxels approach zero and values increase outward. This reproduction was specified from the Volume Wrangle contract and was not live-run during this corpus rebuild.
8. MMB the node, inspect the generated attributes or fields, and compare a small case against a native SOP or hand-computed expectation.
9. Profile only after correctness is established, then tune radius, query count, and thread job size if relevant.

## Critical parameters and attributes

- Search radius and maximum points control both result quality and cost.
- `nearpoints` and point-cloud functions return point numbers from a specific input, not persistent IDs.
- `neighbours` follows point-edge connectivity and does not imply spatial proximity.
- `xyzdist` returns a primitive and parametric location; use `primuv` to sample attributes there.
- `@opinputN_name` assumes element correspondence unless a node provides attribute matching.
- Volume bindings must name existing primitives; Volume Wrangle does not create new volumes by writing `@name`.
- Voxel index bindings are integers; `@P` is the current voxel center and `@center` is the whole volume center in SOP space.
- `setpointattrib` modes such as `"add"`, `"min"`, or `"max"` define intentional reductions.

## Cache, version, and performance

Spatial query cost grows with element count, radius, and requested neighbors. Limit maximum results and use an acceleration-friendly query rather than nested all-pairs loops. Cache a stable reference point cloud or volume when reused by several branches. Match volume transforms and resolutions before repeated sampling. Document H21-only functions in reusable code. Profiling must include realistic density because a small test can conceal quadratic or memory-heavy behavior.

## Validation

Visualize neighbor counts as color and nearest vectors as lines. Create groups for zero-hit and capped-hit elements. Test query behavior near boundaries and across disconnected pieces. For interpolation, verify source owner and sampled tuple type. For volumes, inspect slice values, min/max ranges, resolution, voxel size, and field transform. The radial diagnostic above should have one fixed origin at `@center`; a field that behaves as if every voxel were its own origin indicates that `@P` and `@center` were confused. Compare results at two resolutions and across frames. Keep diagnostic attributes until acceptance, then remove or isolate them so they do not pollute the delivery schema.

## Common failures and troubleshooting

- Every point finds itself: exclude the current point where the algorithm requires other neighbors.
- Results jump after topology changes: returned point numbers are transient; transfer stable IDs if correspondence matters.
- An interpolation returns zero: the source attribute owner or primitive location is wrong.
- A field remains absent: create it with a volume SOP before Volume Wrangle.
- Different fields sample misaligned locations: match resolution and transforms.
- Parallel writes are nondeterministic: split query and reduction or use an explicit associative mode.
- A search becomes extremely slow: cap neighbor count, reduce radius, or replace nested loops with spatial queries.
- MMB shows no compiler error but output is wrong: inspect diagnostic hit counts, types, and input indices.

## When not to use

Do not use neighborhood VEX for a standard normal, blur, transfer, or distance operation already implemented efficiently by a native SOP. Avoid writing to arbitrary neighboring elements from every thread without a deterministic reduction. Do not assume volume fields share resolution or transform because they have similar names. Do not optimize away diagnostics before the query assumptions are proven.

## Houdini 21 notes

H21 adds `pointprimuv`, which returns the intrinsic UV location of a point in a primitive, and `osd_limit` for subdivision-surface evaluation. OpenCL gains topology bindings for half-edge and tetrahedral adjacency, but this does not change VEX snapshot or queued-write semantics. H21 volume and geometry Wrangles retain the binding constraints described here.

## Official sources

- SideFX, [Using VEX expressions](https://www.sidefx.com/docs/houdini/vex/snippets), accessed 2026-07-26.
- SideFX, [VEX geometry functions](https://www.sidefx.com/docs/houdini/vex/functions/index.html), accessed 2026-07-26.
- SideFX, [Volume Wrangle SOP](https://www.sidefx.com/docs/houdini/nodes/sop/volumewrangle.html), accessed 2026-07-26.
- SideFX, [Attribute Wrangle SOP](https://www.sidefx.com/docs/houdini/nodes/sop/attribwrangle.html), accessed 2026-07-26.
- SideFX, [What's new: VEX and OpenCL in Houdini 21](https://www.sidefx.com/docs/houdini/news/21/vex.html), accessed 2026-07-26.
