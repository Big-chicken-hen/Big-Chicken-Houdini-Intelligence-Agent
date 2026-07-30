# VEX topology and spatial query patterns

Source kind: `community_tutorial`
Verification: `community_unverified`
Version: one source is marked H18; recheck VEX signatures and node behavior in the active build.

## Use, prerequisites, and target

Use this when VEX must walk connected geometry, find nearby elements, build relationships, or edit topology. Begin with stable input geometry and a clear decision: is the relationship defined by topology, spatial distance, or both? The target is a network that validates all returned indices, bounds search work, and separates relationship discovery from geometry mutation.

## Semantic network stages

`IN_TOPOLOGY` → optional `Facet/Fuse SOP` cleanup → relationship wrangle → optional mutation wrangle → diagnostics → `OUT_TOPOLOGY`. Put a spatial-index branch beside, not inside, a topology traversal when their results need comparison.

## Ordered workflow

1. Inspect point/vertex/primitive counts and shared-point structure. A visible edge does not prove two primitives share points. Use Geometry Spreadsheet and viewport point/primitive numbers.
2. Select the query domain. For connected neighbors use topology functions such as `pointprims`, `primpoints`, `pointvertices`, `vertexpoint`, `vertexprim`, and half-edge functions. For proximity use `nearpoint`, `nearpoints`, `pcopen`/`pciterate`, or `xyzdist` plus `primuv`.
3. In an `Attribute Wrangle SOP`, Run Over the smallest natural owner. Store discovered relationships first, for example an integer array of neighbor point numbers or a primitive/UVW hit. Limit spatial searches with a meaningful radius and maximum count; do not scan the whole input per point when local bounds suffice.
4. Guard every lookup. Treat `-1`, an empty array, or an invalid primitive as “no result.” Check array length before indexing. When querying another input, pass the correct geometry handle/input number and document that connection.
5. If geometry must be added or removed, separate mutation into a second wrangle. Use the discovery attributes from step 3 so that changing topology does not invalidate indices during the same conceptual pass. When adding primitives, create points first, then vertices/primitive, and preserve required attributes deliberately.
6. For projected data, use `xyzdist` to obtain primitive and parametric coordinates, then sample with `primuv`. Retain the hit primitive and UVW as diagnostic attributes until validation is complete.
7. Add diagnostics: color elements with zero/multiple hits, record neighbor-count min/max, and isolate invalid indices with a group. End at `OUT_TOPOLOGY`.

## Key responsibilities and fields

- Cleanup SOPs establish whether coincident points are fused and whether winding/normals are stable.
- Discovery wrangle owns arrays such as `i[]@neighbors`, hit fields such as `i@hitprim`, and vector coordinates such as `v@hituvw`.
- Mutation wrangle owns `addpoint`, `addprim`, `addvertex`, or removals; it must not silently discard `name`, `id`, or groups.
- Diagnostic nodes prove cardinality and mapping, not just appearance.

## Executable parameter and connection contract

1. Connect the topology to input 1 of `Attribute Wrangle SOP` named `DISCOVER_RELATIONSHIPS`; connect a reference/search surface to input 2 when required. Set **Run Over** to Points for point neighbors, Primitives for primitive adjacency, or Detail for a deliberately serial graph-wide operation.
2. For spatial neighbors expose `radius > 0` and integer `maxpoints >= 1`. A point-domain pattern is `i[]@neighbors = nearpoints(1, @P, chf("radius"), chi("maxpoints"));`. If the search uses the same geometry input, remove `@ptnum` deliberately; never remove that integer from a different input where it has another identity.
3. For surface projection use `xyzdist(1, @P, i@hitprim, v@hituvw)` and retain the returned non-negative distance. Guard `i@hitprim >= 0` before `primuv`; sample only named attributes that exist on input 2. Put misses in `projection_miss` and do not fabricate a zero-position hit.
4. For topological relationships use `pointprims`, `primpoints`, or half-edge functions on input 0. Store results first as bounded arrays and validate every index against `npoints(0)`/`nprimitives(0)`. A proximity match is not proof of shared topology.
5. Connect discovery to a second `Attribute Wrangle SOP` named `APPLY_TOPOLOGY_EDIT`. This node alone may call `addpoint`, `addprim`, `addvertex`, or removals. Carry stable `id`/`name` fields across the edit; do not consume stored element numbers after an intervening topology-changing node.
6. Parameter ranges are contract data: radius is in scene units, `maxpoints` is capped, distances are finite and non-negative, `hituvw` is finite, and required neighbor counts have declared minima/maxima. Use a group filter when only part of the geometry is eligible.

## Data flow, caching, version, and performance

Topology indices are only stable until a topology-changing node. Cache either before discovery with a stable upstream hash or after mutation with the relationship attributes needed downstream. `nearpoints` with large radius/count per point trends toward costly all-to-all work; use spatial locality, groups, or a second input. Native SOPs may outperform VEX for common connectivity tasks. Function signatures are established Houdini concepts, but this exact staged implementation is the project's synthesis and is not live-verified.

## Common failures and repairs

- **Unexpected disconnected islands:** fuse tolerance or primitive winding differs; inspect shared point numbers before increasing search radius.
- **Invalid point/primitive reads:** a query returned `-1` or an empty array; add guards and a failure group.
- **Results change after deletion:** stored indices referenced pre-mutation topology. Split discovery and mutation, or use stable `id`/`name`.
- **Slow cook:** cap proximity results, avoid nested unbounded loops, and profile native alternatives.
- **Projection jumps across surfaces:** restrict candidate primitives/groups or validate normal/distance in addition to nearest distance.

## Checkpoints and observable evidence

- **Q0 — input:** record point/primitive counts, shared-point structure, and the connected input number for every lookup.
- **Q1 — discovery:** required-hit miss count is zero; optional misses are isolated. Neighbor-count min/max never exceed `maxpoints`, and every stored index resolves on the unchanged discovery geometry.
- **Q2 — projection:** `hitprim`, `hituvw`, and distance reproduce a sampled position on the intended primitive within a documented tolerance.
- **Q3 — mutation:** observed point/primitive count deltas equal the declared additions/removals, with zero accidental isolated points or degenerate primitives.
- **Q4 — performance:** Performance Monitor shows bounded growth when input count doubles; an unbounded quadratic jump triggers a group/radius/native-node redesign.

## When not to use this workflow

Do not write VEX traversal for connectivity, transfer, or proximity that a clear native SOP already performs and profiles faster. Do not use spatial proximity when identity/topology is the actual relation, and do not store point or primitive numbers across a topology-changing boundary when stable `id`/`name` mapping is available.

## Provenance boundary

Matt Estela's topology explanation and Moeen Sayed's VEX teaching project inspired the paired topology/spatial framing. The two-pass graph, field names, and validation loop are original synthesis. No tutorial code is reproduced.

## Semantic expectations and verification checklist

- Presence: hit and neighbor attributes exist on the declared owner.
- Mapping: every stored index resolves on the geometry version where it is consumed.
- Range: neighbor counts respect the cap; distances are non-negative and within radius; hit UVW is finite.
- Topology evidence: intended point/primitive count deltas match mutation operations; no accidental isolated points or degenerate primitives.
- Cook evidence: invalid-result group is empty for required hits and intentionally populated for optional misses.
- Visual evidence: debug colors/lines connect only expected neighbors and projected samples remain on the intended surface.

## Sources

- Matt Estela, [Points and Verts and Prims](https://www.tokeru.com/cgwiki/Points_and_Verts_and_Prims.html).
- Moeen Sayed, [VEX Isn't Scary Project](https://www.sidefx.com/tutorials/vex-isnt-scary-project/).
