# UV workflows

Pack version: 2.0.0

## Use for

Use this workflow to generate editable, non-overlapping texture coordinates for polygonal assets, to maintain multiple UV sets, to distribute islands across UDIM tiles, or to derive surface-distance coordinates from source points. The workflow supports both interactive seam and pin authoring and fully procedural regeneration. Choose the method according to the required preservation of angles, areas, island orientation, and downstream texture resolution.

## Context and core data

Texture coordinates are normally stored in a vector-like attribute named `uv`. Vertex ownership is preferred because two vertices sharing one point can carry different values across a seam. UV islands are connected regions in texture space; seams split islands, pins constrain positions, and layout packs the flattened islands into targets. UV Flatten offers Spectral and Angle Based methods. UV Layout packs islands into rectangles, UDIM tiles, or profiles supplied through a second input. H21's UV Flatten from Points computes geodesic distance and direction from source points.

## Recommended data flow

Use:

`topology cleanup -> seam generation or seam group -> UV Flatten/Unwrap -> pin, align, straighten, or rectify constraints -> UV Layout -> distortion and overlap checks -> final UV output`

For geodesic coordinates:

`manifold mesh + source points -> UV Flatten from Points -> distorted/unprocessed and seam outputs -> UV Layout -> conventional fallback for unprocessed regions`

Keep flattening and packing as separate stages when tight control or efficient packing is required.

## Step-by-step workflow

1. Inspect topology for non-manifold edges, disconnected regions, degenerates, and existing UV ownership.
2. Choose the UV attribute name and decide whether existing seams or layout must be preserved.
3. Create or select seams that reduce overlap and distortion without fragmenting the surface unnecessarily.
4. Flatten with Spectral for speed and robustness or Angle Based when lower area distortion justifies slower solving.
5. Add only necessary pins, axis alignment, loop straightening, or rectangular-grid constraints, then repack.
6. Send islands to UV Layout and set target rectangle or UDIM policy, rotation step, padding, search resolution, and iterations.
7. Inspect distortion, overlap, flipped orientation, failed islands, and texel spacing at the intended texture resolution.
8. Preserve seam, island, distorted, and unprocessed groups needed by downstream tools.

## Critical parameters and attributes

- `UV Attribute` selects `uv` or another UV set.
- UV Flatten `Seams`, pins, Rectify, and alignment constraints define solvability and shape.
- Spectral is faster and more robust; Angle Based is slower and may fail but can reduce area distortion.
- UV Layout `Island Padding` is measured relative to its search image.
- `Search Resolution` should not exceed the final texture resolution.
- `Iterations` trades cook time for potentially tighter packing.
- `Island Rotation Step` controls packing flexibility and axis preservation.
- A primitive target attribute can assign islands to specific UDIMs.
- UV Flatten from Points can output seam, island-ID, distorted, and unprocessed groups.

## Cache, version, and performance

Cache UVs after topology is stable; any upstream topology change can invalidate vertex correspondence and seams. Use procedural seams for assets expected to change often. Higher layout iterations and finer search resolution increase cost, so tune them for finalization rather than every interactive cook. Preserve existing layout when adding UVs to only part of an asset. Version an HDA when its packing policy, target UDIM scheme, or UV attribute contract changes.

## Validation

Use the UV viewport and a directional checker pattern. Inspect distortion visualization for stretching and compression, verify no unintended overlaps, and confirm island orientation. Check that padding remains sufficient after texture filtering and mip generation. Verify every renderable face has the intended UV set and that seam groups align with vertex discontinuities. For UDIMs, confirm tile numbers and downstream filename conventions. For geodesic UVs, inspect high-distortion and unreachable output groups.

## Common failures and troubleshooting

- The flattened surface overlaps itself: add meaningful seams or use an automatic seam stage.
- A seam cannot separate values: confirm `uv` is a vertex attribute.
- Layout shifts during interactive edits: commit changes with Repack or use the procedural mode consistently.
- Pins and straightening fight each other: remove or simplify conflicting constraints.
- Small islands share texture pixels: lower search resolution to the actual texture size and increase padding.
- UV Flatten from Points leaves regions unchanged: sources cannot reach disconnected or non-manifold regions; process them separately.
- Distant regions distort strongly: add source points, use distortion pruning, or switch to conventional flattening.
- Fixed-scale islands appear outside targets: inspect the failed-island output group and reduce scale or add targets.

## When not to use

Do not force geodesic UVs onto non-manifold geometry or assume they are conformal far from source points. Do not retain point-owned UVs when visible seams require per-face-corner values. Avoid expensive final-quality packing while topology and seams are still changing. Do not use UV Unwrap as a final answer when its planar decomposition needs artist-controlled seams or more efficient packing.

## Houdini 21 notes

H21 introduces UV Flatten from Points, including Fast, Accurate, and Only Seams methods, automatic source selection, surface Voronoi information, and distortion outputs. UV Layout can automatically pack islands into a contiguous range of UDIM tiles. UV Fuse is compilable, and Poly Bridge and Poly Extrude can generate side UVs. These changes expand procedural options while the standard vertex-UV, seam, flatten, and layout pipeline remains valid.

## Official sources

- SideFX, [UV Flatten SOP](https://www.sidefx.com/docs/houdini/nodes/sop/uvflatten.html), accessed 2026-07-26.
- SideFX, [UV Layout SOP](https://www.sidefx.com/docs/houdini/nodes/sop/uvlayout.html), accessed 2026-07-26.
- SideFX, [UV Unwrap SOP](https://www.sidefx.com/docs/houdini/nodes/sop/uvunwrap.html), accessed 2026-07-26.
- SideFX, [UV Flatten from Points SOP](https://www.sidefx.com/docs/houdini/nodes/sop/uvflattenfrompoints.html), accessed 2026-07-26.
- SideFX, [What's new: Modeling, geometry, and terrains in Houdini 21](https://www.sidefx.com/docs/houdini/news/21/model.html), accessed 2026-07-26.
