# Pack, Copy to Points, and instances

Pack version: 2.0.0

## Use for

Use this workflow to distribute repeated or variant source geometry across template points while controlling transform, material-related attributes, memory, and downstream editability. It covers Copy to Points, packed geometry, packed disk geometry, and render-time instancing as a deliberate continuum. Choose real copied geometry when every result needs later topology edits, packed copies when geometry is shared but transforms vary, and render-time or USD point instancing when very large counts must remain lightweight.

## Context and core data

Copy to Points reads source geometry from its first input and template points from its second. Template-point attributes define each result. Packed primitives are lightweight references: their internal geometry is not directly editable, but each primitive can carry a transform and attributes. Packed Disk primitives defer loading from a file. Render-time instancing keeps one source and emits occurrences during rendering. Source variants can be labeled by a string or integer piece attribute and selected by matching values on template points.

## Recommended data flow

Use:

`source variants -> stable source piece attribute -> optional pack per variant`

`distribution surface -> scatter or authored points -> P/orient/scale/variant/material attributes`

`both streams -> Copy to Points with Piece Attribute -> packed or editable output -> optional cache/instance export -> OUT_*`

Author template attributes before Copy to Points. Keep source pivots and local axes consistent. Delay Unpack until a downstream operation truly requires internal edits.

## Step-by-step workflow

1. Normalize every source variant to a documented local origin, pivot, forward axis, and unit scale.
2. Assign a stable string or integer piece attribute to source primitives and the matching selector attribute to template points.
3. Create template-point `P`, orientation, scale, and optional material or custom attributes, using stable `id` values for random variation.
4. Configure Copy to Points `Piece Attribute`, source/target groups, target-attribute transfer, and transform behavior.
5. Enable `Pack and Instance` when copies share source geometry and do not need immediate internal topology edits.
6. Inspect transform precedence and source matching on a small point set before increasing instance count.
7. Choose regular copies, packed geometry, packed disk geometry, render-time instances, or USD point instances for the delivery stage.
8. Validate bounds, pivots, attributes, memory, motion blur, and source-version references.

## Critical parameters and attributes

- `P` supplies translation.
- `pivot` establishes the local pivot; `trans` adds translation.
- `orient` is a quaternion and overrides `N/up`.
- Without `orient`, `N` aligns source +Z and `up` supplies +Y; `v` may supply direction when `N` is absent.
- `rot` is an additional quaternion rotation after base orientation.
- `pscale` multiplies uniformly and `scale` multiplies per axis.
- `transform` overrides orientation and scale attributes, but not the translation contribution.
- `Piece Attribute` matches source variants to target points.
- `Attributes from Target` controls attribute copying, multiplication, addition, or subtraction.
- `Display As` chooses full, point, bounds, centroid, or hidden packed display.

## Cache, version, and performance

Pack source geometry when copy counts are high and per-copy topology does not change. Packed disk references reduce memory further and form a useful delivery boundary, but their file path and source version become dependencies. Prefer point or render-time instancing for very high counts. Cache the source library independently from the distribution points so either can change without rebuilding the other. Profile viewport drawing separately from SOP cooking. Avoid repeated Unpack/Pack cycles because they defeat sharing and add cook cost.

## Validation

Display template axes before copying. Test one point with known values for every transform attribute, then test several variants and scales. Confirm source-piece matching, missing variants, target attribute transfer, final bounds, primitive counts, and packed intrinsic transforms. Compare MMB memory information for packed and unpacked alternatives. Verify velocity or deformation motion blur at subframes. For disk or USD delivery, reload in a clean scene to ensure every referenced file and prototype resolves.

## Common failures and troubleshooting

- Copies face an unexpected direction: check `transform`, then `orient`, then `N/up`, then `v`.
- Scale is doubled: both `pscale` and `scale` exist and multiply.
- Variants do not match: source and target piece attributes differ in owner, type, or value.
- Every copy consumes full memory: `Pack and Instance` is off or downstream nodes unpacked the result.
- Pivot offsets vary: source variants were not normalized to a common local pivot.
- Materials disappear in the viewport: some per-instance material overrides are render-only.
- Motion blur points in the wrong direction: `v` is independent of orientation and should be authored in world units per second.
- A packed result cannot be deformed: unpack the required geometry or perform deformation before packing.

## When not to use

Do not use packing when each copy requires different internal topology or deformation after copying. Do not create a For-Each loop merely to vary transform or choose source pieces. Avoid render-time instancing when the copied surfaces must be visible to downstream SOP modeling. Do not use point numbers as persistent variation seeds if point order may change. Avoid absolute packed-disk paths when the package must be relocatable.

## Houdini 21 notes

Copy to Points 2.0 predates H21 and its transform conventions remain applicable. H21's broader viewport and Vulkan threading improvements can help scenes with many objects or primitives, but instance percentage and display representation still matter. When exporting to Solaris, SOP `N`, `up`, `orient`, `scale`, and `pscale` map naturally into Copy to Points LOP orientation and scale controls.

## Official sources

- SideFX, [Copy to Points SOP](https://www.sidefx.com/docs/houdini/nodes/sop/copytopoints), accessed 2026-07-26.
- SideFX, [Copying and instancing point attributes](https://www.sidefx.com/docs/houdini/copy/instanceattrs), accessed 2026-07-26.
- SideFX, [Packed primitives and Pack SOP](https://www.sidefx.com/docs/houdini/nodes/sop/pack), accessed 2026-07-26.
- SideFX, [Copying and instancing](https://www.sidefx.com/docs/houdini/copy/index.html), accessed 2026-07-26.
- SideFX, [Copy to Points LOP](https://www.sidefx.com/docs/houdini/nodes/lop/copytopoints.html), accessed 2026-07-26.
