# Procedural curve modeling and sweep frames

Source kind: `community_tutorial`
Verification: `community_unverified`
Version: one source is marked H17.5; Curve/Sweep interfaces changed across releases, so verify current parameters.

## Use, prerequisites, and target

Use for cables, ribbons, trims, quilling, paths, rails, or profiles driven by curves. Inputs are one or more ordered curve primitives and, for a sweep, a clean cross-section. The target is a resampling- and deformation-stable graph with explicit curve parameter, frame, width, seam, and output topology.

## Semantic network stages

1. Curve construction/cleanup.
2. Uniform sampling and parameterization.
3. Shape deformation and masks.
4. Frame/orientation construction.
5. Profile sweep or copy.
6. Surface cleanup, UVs, evidence, and `OUT_CURVE_MODEL`.

The curve trunk runs vertically. Feed the profile from the left into the sweep's profile input.

## Ordered workflow

1. Create or import curves, then use `Convert SOP`/`Polypath SOP` as needed so downstream nodes receive the expected primitive type. Use `Fuse SOP` only when endpoints should truly share a point. Inspect primitive order and closed/open state.
2. Place `Resample SOP`. Choose Maximum Segment Length for scale-aware density or a fixed segment count when topology correspondence matters. Enable a normalized curve parameter such as `curveu` when available; otherwise derive a point/vertex parameter with a measured total length.
3. Shape the curve before frame construction. An `Attribute Wrangle SOP`, `Point VOP`, or `Bend SOP` can offset `@P` using `curveu`, masks, noise, or guide geometry. Keep the unmodified resampled curve on a side branch for comparison.
4. Construct orientation. Use `Polyframe SOP` to generate tangent and an orthogonal normal/bitangent, or create `N` and `up` then convert to `orient`. Choose a style that avoids flips at low curvature. For closed curves, inspect the seam and distribute residual twist rather than accepting one abrupt roll.
5. Prepare the profile on a left branch: centered curve/polygon, known winding, predictable normal, and optional `width`/`pscale`. Connect profile and backbone into `Sweep SOP`. Bind scale, rotation/twist, and frame attributes explicitly.
6. Post-process with `Fuse SOP` only if the sweep should weld, `Normal SOP` for shading normals, and UV generation based on longitudinal `curveu` plus profile coordinate. Avoid recomputing normals before the topology is final.
7. Add an evidence branch that visualizes tangent/normal axes and width, then terminate the production stream at `OUT_CURVE_MODEL`.

## Important fields and responsibilities

- `curveu`: monotonic longitudinal coordinate, normally `[0,1]` per primitive.
- `tangentu` or `N`: normalized direction along the backbone.
- `up`/bitangent or `orient`: roll-stable frame.
- `width`, `pscale`, or a named scale field: profile size; document which consumer reads it.
- Curve construction owns primitive order; Resample owns density; Polyframe owns frames; Sweep owns generated surface topology.

## Data flow, cache, version, and performance

Changing resample density changes point numbers, so downstream correspondence should use `curveu`, primitive identity, or stable `id`, not point number. Cache after expensive deformation and before heavy sweep only if the cached curve retains frame/width fields. More samples improve bends but increase sweep polygons and downstream cost. Prefer adaptive/length-based density and profile simplicity. The node sequence reflects general Houdini practice synthesized from creator examples; current defaults are unverified.

## Common failures and repairs

- **Sweep twists:** frame is underdefined or flips. Normalize tangent, provide a stable up vector, inspect seam, and avoid near-parallel tangent/up.
- **Width pulses:** width lives on wrong owner or is sampled before resampling. Create it after final curve sampling or transfer by `curveu`.
- **Open gaps/duplicate points:** confirm primitive closure and endpoint sharing rather than blindly increasing Fuse tolerance.
- **UV stretches:** derive longitudinal UV from measured arc-length parameter, not point number.
- **Interactive graph is slow:** reduce profile resolution during authoring and switch to final density at the output/cache stage.

## Provenance boundary

Entagma's quilling example and Susie Green's curve tutorial support the procedural-curve focus. The staged graph, field contract, and validation checks are original synthesis; they are not a reconstruction of either author's exact setup.

## Semantic expectations and verification checklist

- Presence: `curveu` and the selected frame/scale fields exist after their authoring stages.
- Range/mapping: `curveu` is finite and monotonic from near 0 to near 1 per curve; width is positive where geometry is expected.
- Topology: output primitive count and cross-section resolution agree with the sweep settings; no unexpected degenerates.
- Cook evidence: changing source curve length changes sample count only according to the chosen resample policy.
- Cache evidence: cached curve reload preserves primitive order, `curveu`, frames, and width.
- Visual evidence: axis markers do not flip, closed seams do not jump, and the swept profile maintains intended orientation.

## Sources

- Moritz, [Procedural Modeling: Quilling](https://entagma.com/procedural-modeling-quilling/).
- Susie Green, [Lines and Curves](https://www.sidefx.com/tutorials/lines-and-curves/?collection=63).
