# Curve, NURBS, and subdivision modeling

Canonical ID: `curve-nurbs-subdivision`

Houdini version: `21` (current online node pages may identify themselves as Houdini 22)

Pack version: 2.0.0

## Use for

Use this workflow when an editable form is driven by polygon, Bézier, or NURBS curves; when a NURBS surface must remain parametric long enough for shaping or trimming; or when a low-resolution polygon cage must produce a controlled subdivision surface. The goal is to choose the primitive representation deliberately, preserve curve direction and semantic identity, and defer conversion or dense subdivision until a downstream consumer actually needs it. This card supplements the general SOP procedural-modeling card; it does not replace its rules for stable names, attributes, stages, and explicit outputs.

## Context and core data

A polygon curve is explicit line-segment topology. NURBS and Bézier curves are smooth parametric primitives defined by order, control vertices, knots, weights, open or closed state, and direction. Curve SOP edit points are not interchangeable with every underlying control vertex: cubic Bézier curves expose tangent handles, while NURBS editing is interpreted through its control structure. Changing primitive type or order can therefore change the meaning of existing points.

Resample can consume all three curve types. For NURBS and Bézier input it estimates length from a polygonal approximation controlled by Level of Detail, while final samples still lie on the original curve. Sweep accepts polygon, NURBS, or Bézier spines and cross-sections; by default its output primitive type follows those inputs. A subdivision surface instead starts from a polygon control cage. Shared points define shared edges, `creaseweight` controls edge sharpness, `cornerweight` controls OpenSubdiv corners, and `subdivision_hole` identifies faces that contribute to refinement but disappear from the result.

## Recommended data flow

For curve-driven construction:

`Curve -> normalize direction/open state -> stable name or path ID -> optional Resample -> tangent/orientation attributes -> Sweep or another curve-surface operation -> Convert only when required -> normals/UV handoff -> OUT_*`

For subdivision construction:

`clean polygon control cage -> semantic edge/face groups -> Crease or OpenSubdiv attributes -> Subdivide preview/evaluation -> topology and face-varying checks -> OUT_*`

Keep the original curve or cage on a named branch so controls remain editable. Do not repeatedly convert NURBS to polygons and back. Treat a viewport subdivision preview, a baked Subdivide SOP result, and renderer-side subdivision as different outputs with different topology and performance consequences.

## Step-by-step workflow

1. Define the downstream requirement first: editable curve, NURBS surface, polygon mesh, subdivision cage, render-time subdivision, or an interchange format with specific primitive support.
2. Create or inspect curves with explicit primitive type, order, open or closed state, direction, and stable primitive names. Avoid reinterpreting existing curves when only new curves should adopt a changed type or order.
3. Test endpoints, branch connections, and curve direction before generating a surface. Reverse only the affected curves, and preserve an original control branch.
4. Resample only when a consumer needs even spacing, points, tangents, or a normalized curve parameter. Choose Along Arc versus Along Chord intentionally and expose the generated tangent or Curve U attribute when downstream orientation depends on it.
5. For a Sweep, keep modeled cross-sections at the origin in the XY plane with positive Y as their up direction. Drive width and frame changes through the documented spine attributes or Sweep controls rather than hand-transforming copied sections.
6. Inspect closed seams, caps, twist, primitive type, and surface direction before converting the result. For a NURBS trim, create valid profile curves in the surface's parametric space and verify whether trimming keeps the intended inside or outside region.
7. When polygon delivery is required, convert once at a named handoff and choose resolution from silhouette, deformation, UV, and render needs rather than an arbitrary high density.
8. For subdivision, prepare a connected, predictable control cage; create semantic edge selections; then author `creaseweight`, `cornerweight`, holes, and OpenSubdiv scheme overrides only where required.
9. Compare the unsubdivided cage and evaluated result at a modest depth, validate UV seams and boundaries, and publish explicit cage and evaluated outputs when both are deliverables.

## Critical parameters and attributes

- Curve SOP **Primitive Type** and **Order** define how stored points are interpreted. Cubic Bézier drawing is optimized around order 4; higher orders are not a generic quality slider.
- Curve open/closed state and direction affect Sweep seams, skinning, carving, and parameter-space correspondence.
- Resample **Level of Detail** affects NURBS/Bézier length estimation. **Maximum Segment Length**, **Along Arc/Along Chord**, **Tangent Attribute**, and **Curve U Attribute** should match the downstream operation.
- Sweep assumes cross-sections are centered at the origin in the XY plane. **Primitive Type**, caps, roll, yaw, twist, scale ramps, `pscale`, `scale`, and orientation attributes control the generated surface.
- Subdivide **Algorithm**, **Depth**, **Group**, and **Creases** determine refinement scope and method. OpenSubdiv Loop requires triangular input.
- `creaseweight` may be a vertex or primitive attribute; shared-edge conflicts resolve to the maximum weight. With OpenSubdiv, `cornerweight`, `subdivision_hole`, `osd_scheme`, and face-varying interpolation settings can override node defaults.

## Cache, version, and performance

Keep analytical curves and low-resolution cages live as long as possible. Resampling too early creates more points for every later operation and can erase meaningful corner placement. Dense NURBS-to-polygon conversion and high subdivision depth multiply memory, cook, UV, and export cost; each additional subdivision level can multiply polygon count dramatically. Profile the actual consumer rather than assuming a smooth primitive is always faster or slower.

Cache after a stable conversion or evaluated subdivision boundary, not between every curve edit. Preserve the procedural source and record the primitive type, conversion tolerance, subdivision algorithm, depth, and crease policy beside a delivered cache. Renderer-side subdivision may avoid storing dense SOP geometry, but its support and interpolation rules must be validated in the target Karma/USD pipeline rather than inferred from legacy Mantra-oriented pages.

## Validation

Inspect primitive type, order, open/closed state, point and primitive counts, bounds, names, and curve direction before and after each representation change. Visualize tangents and Curve U; confirm that U is monotonic along each intended path and that resampled points stay on the source curve. For Sweep, examine several high-curvature regions and the closed seam for frame flips, flattened sections, unexpected stretch, and inverted normals.

For NURBS surfaces, check U/V direction, closure, profile-curve visibility, and trim boundaries before conversion. For subdivision, compare cage and evaluated silhouettes, inspect extraordinary vertices and boundary behavior, and verify that only intended edges or corners remain sharp. Check that UV and other vertex attributes behave correctly at seams. A useful static reproduction is a named NURBS spine plus a centered NURBS cross-section through Sweep, followed on a separate branch by polygon conversion; a second branch applies one semantic crease group to a low-resolution shared-point cage and compares Subdivide depth 0 and 1. This corpus did not cook either network.

## Common failures and troubleshooting

- A surface twists or flips: curve direction, tangent continuity, or frame/up-vector data is inconsistent; fix the smallest offending span rather than adding arbitrary roll keys everywhere.
- A Sweep scales or rotates unexpectedly: the cross-section was modeled away from the XY origin or the spine already carries conflicting orientation attributes.
- Corners disappear after Resample: preserve polygon edges independently or enable the method that treats polygon edges separately before increasing density.
- NURBS/Bézier samples appear uneven: raise only the length-estimation LOD necessary for the requested tolerance and compare arc-length evidence.
- A trimmed NURBS region vanishes: inspect profile numbers, surface UV placement, and inside/outside selection; a clipped profile may still exist even when it is not visible on the surface.
- Partial subdivision opens cracks: the selected faces do not share points with their neighbors, or boundary closing settings are inappropriate.
- Creases do not match: the crease topology does not correspond to the cage, attribute ownership is wrong, or a higher competing crease value wins on a shared edge.
- Subdivision produces an unexpected surface: check the algorithm and `osd_*` overrides before changing the cage.
- The viewport is smooth but exported geometry is coarse: a display preview was mistaken for baked or renderer-recognized subdivision.

## When not to use

Do not use NURBS merely as a smoothing switch for a polygon workflow whose consumers require polygons. Do not build a dense curve network for one straight segment or use Sweep when a simple native primitive already expresses the form. Avoid subdivision when the target is a deliberately faceted mesh, a volume-based surface, or geometry whose topology cannot support the chosen scheme. Do not claim CAD-style tolerances or continuity from visual smoothness alone. For groom guide generation, UV-specific transfer, road frameworks, or renderer-specific subdivision authoring, load the dedicated workflow for that domain instead of expanding this card.

## Houdini 21 notes

The core Curve, Resample, Sweep, Crease, and Subdivide concepts predate Houdini 21. H21 adds branched-curve creation, fusing, and splitting improvements to the Curve state, and introduces Unsubdivide for compatible Catmull-Clark-derived topology. The H21 modeling change notes are direct evidence for those additions. The current online node pages opened for this audit may display a Houdini 22 header, so parameters not explicitly supported by the H21 change notes must be checked in installed H21 help before being treated as version-specific guarantees. The workflow here is documented/static and source-reviewed; it has not been live-verified in Houdini 21.

## Official sources

- SideFX, [Curve SOP](https://www.sidefx.com/docs/houdini/nodes/sop/curve.html), accessed 2026-07-27.
- SideFX, [Resample SOP](https://www.sidefx.com/docs/houdini/nodes/sop/resample.html), accessed 2026-07-27.
- SideFX, [Sweep SOP](https://www.sidefx.com/docs/houdini/nodes/sop/sweep.html), accessed 2026-07-27.
- SideFX, [Trim NURBS surfaces](https://www.sidefx.com/docs/houdini/model/trim_nurbs.html), accessed 2026-07-27.
- SideFX, [Subdivide SOP](https://www.sidefx.com/docs/houdini/nodes/sop/subdivide.html), accessed 2026-07-27.
- SideFX, [Crease SOP](https://www.sidefx.com/docs/houdini/nodes/sop/crease.html), accessed 2026-07-27.
- SideFX, [What's new: Modeling, geometry, and terrains in Houdini 21](https://www.sidefx.com/docs/houdini/news/21/model.html), accessed 2026-07-26.
