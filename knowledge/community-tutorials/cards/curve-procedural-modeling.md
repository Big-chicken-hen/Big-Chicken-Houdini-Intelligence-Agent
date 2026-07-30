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

## Executable parameter and connection contract

1. Connect `Curve SOP`/imported curves to `Convert SOP` only when a downstream node needs a different primitive type, then to `Resample SOP`. Use `Method = Even Length Segments` with positive `Maximum Segment Length` when geometric spacing matters; use a fixed maximum segment count when one-to-one topology matters. Enable `Curve U Attribute` and name it `curveu`.
2. Verify each primitive's `curveu` is monotonic and approximately `0..1`. If multiple curves exist, compute the range per primitive. Never use global point number divided by total points as a substitute because it crosses primitive boundaries and changes after resampling.
3. Connect resampled curves to `Orientation Along Curve SOP` for the most direct sweep-compatible frame, or to `Polyframe SOP` when custom tangent/normal fields are required. On Orientation Along Curve, choose point output when downstream point attributes are expected, keep `Normalize Scales` on for a frame-only baseline, and enable `Stretch Around Turns` only for a cross-section that must avoid corner squashing. Cap `Max Stretch` rather than allowing a near reversal to create unbounded scale.
4. When building frames manually, normalize tangent and ensure the chosen `up` is not parallel: require `abs(dot(tangent, up)) < 0.999` before constructing `orient`. Store a finite point quaternion `p@orient`; on closed curves compare first/last frames and distribute residual roll across `curveu`.
5. Build the profile around the origin in the XY plane and connect the backbone to input 1 and the profile to input 2 of `Sweep SOP`. Enable `Transform Using Curve Point Attributes` when `orient`, `pscale`, `scale`, or width controls should drive sections. For an automatic profile choose `Surface Shape` explicitly; for a modeled closed profile inspect winding and choose an intentional `End Cap Type`.
6. Define one size contract. If the graph uses `pscale`, require `pscale > 0`; if it uses `width`, bind/convert it explicitly rather than keeping both ambiguous. A useful diagnostic ramp is `0.25 -> 1 -> 0.25` over `curveu`, multiplied by a scene-unit master radius.
7. Put `Normal SOP` and UV construction after final sweep topology. Longitudinal UV comes from `curveu`; the transverse coordinate comes from profile order. Cache the curve before sweep only if reload preserves primitive order, `curveu`, frame, and size fields.

## Data flow, cache, version, and performance

Changing resample density changes point numbers, so downstream correspondence should use `curveu`, primitive identity, or stable `id`, not point number. Cache after expensive deformation and before heavy sweep only if the cached curve retains frame/width fields. More samples improve bends but increase sweep polygons and downstream cost. Prefer adaptive/length-based density and profile simplicity. The node sequence reflects general Houdini practice synthesized from creator examples; current defaults are unverified.

## Common failures and repairs

- **Sweep twists:** frame is underdefined or flips. Normalize tangent, provide a stable up vector, inspect seam, and avoid near-parallel tangent/up.
- **Width pulses:** width lives on wrong owner or is sampled before resampling. Create it after final curve sampling or transfer by `curveu`.
- **Open gaps/duplicate points:** confirm primitive closure and endpoint sharing rather than blindly increasing Fuse tolerance.
- **UV stretches:** derive longitudinal UV from measured arc-length parameter, not point number.
- **Interactive graph is slow:** reduce profile resolution during authoring and switch to final density at the output/cache stage.

## Checkpoints and observable evidence

- **C0 — curve input:** primitive count, open/closed state, length, and endpoint sharing are recorded; every curve has at least two distinct positions.
- **C1 — sampling:** segment lengths fall within the chosen tolerance, `curveu` is finite/monotonic per primitive, and changing curve length changes samples according to the selected policy.
- **C2 — frame:** tangent length is near one, frame vectors are orthogonal within tolerance, quaternion values are finite, and the invalid tangent/up group is empty. Closed seams show no abrupt roll.
- **C3 — sweep:** backbone is input 1 and profile input 2; output section/primitive counts agree with backbone samples and profile resolution. Bounds and width remain positive with no degenerate polygons.
- **C4 — reload/visual:** the disk-loaded curve reproduces the same axis markers and swept silhouette; UV checker size changes continuously without a seam jump.

## When not to use this workflow

Do not use a sweep for a surface that needs independently art-directed cross-sections at arbitrary locations unless that multi-profile construction is explicitly designed. Avoid resampling when original CV/vertex identity must be preserved exactly, and avoid curve frames for a copy operation that only needs point positions with no roll or scale control.

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
