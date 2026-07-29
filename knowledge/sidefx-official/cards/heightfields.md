# Heightfield terrain workflows

Pack version: 2.0.0

## Use for

Use heightfields for surfaces represented as a two-dimensional grid with one height value per cell. This card concentrates on mask construction, staged detail, and H21 hydraulic and thermal erosion. Use the companion scatter/export card for vegetation points, conversion, tiling, and deliverables. Convert to other geometry only when overhangs, caves, vertical detail, or a downstream format requires it.

## Context and core data

A HeightField SOP creates two volume primitives: `height` and `mask`. Height stores elevation relative to the ground plane; mask normally ranges from zero for no effect to one for full effect. Additional named layers can represent material zones, flow, sediment, debris, or custom controls. Size and grid spacing are expressed in Houdini units interpreted as meters by terrain and dynamics tools. Grid spacing determines voxel resolution and therefore the smallest useful feature. Most terrain SOPs accept a mask input or layer binding.

## Recommended data flow

Use:

`low-resolution HeightField -> massing by paint, projection, or maps -> seeding with noise and distortion -> coarse erosion for lobing -> elevation remap -> staged resampling -> terraces, clipping, and masks -> high-resolution reseeding -> multi-scale H21 erosion -> Freeze -> visualization, scattering, conversion, and cache`

Develop large-scale shape first. Add detail again after each resolution increase rather than expecting upsampling to invent meaningful structure.

## Step-by-step workflow

1. Set terrain world `Size`, orientation, center, and a coarse `Grid Spacing` appropriate for primary forms.
2. Build massing with smooth height edits, projected geometry, or imported maps, keeping the overall elevation range intentional.
3. Seed the surface with restrained noise and distortion so erosion has obstacles and directional variation.
4. Run a coarse erosion pass to establish lobes and drainage, then remap elevation if multiple height bands are needed.
5. Resample by a controlled factor, inspect voxel size, and reapply noise or distortion at the new physical scale.
6. Shape selected regions with named masks, terraces, clips, and supplementary erodability controls.
7. Apply one or more HeightField Erode 3.0 nodes at decreasing `Erosion Feature Size` values and freeze each accepted result.
8. Validate output layers, scatter points through masks, convert only required deliverables, and cache the stable result.

## Critical parameters and attributes

- HeightField `Size` establishes world extent.
- `Grid Spacing` or `Grid Samples` establishes resolution and memory use.
- `height` is the mandatory terrain layer; `mask` is the conventional effect layer.
- Named mask layers allow independent regional control.
- HeightField Erode `Erosion Feature Size` defines channel, slope, and deposit width in meters.
- Feature size cannot be smaller than three times the input voxel size.
- `Spread Iterations` and feature size control transport distance per frame.
- `Erodability` and its supplementary mask restrict erosion strength spatially.
- H21 erosion outputs include `height`, `sediment`, `debris`, `flow`, and `flowdir`.
- `Freeze at Frame` turns an iterative erosion state into a deterministic cooked output.

## Cache, version, and performance

Begin coarse, because high-resolution erosion and repeated visualization are expensive. Increase resolution in stages and reserve fine erosion for approved massing. Freeze accepted erosion nodes; chaining several unfrozen simulations can increase cook time and produce unexpected evaluation order. Cache after meaningful terrain milestones rather than every node. Preserve layer names and physical scale in the cache contract. When a digital asset changes grid, erosion, or layer semantics, version it because existing masks and downstream shading may no longer match.

## Validation

Use HeightField Visualize to inspect height, masks, flow, sediment, and debris independently. Verify bounds, orientation, size, voxel spacing, elevation range, and layer names after every resample or import. Compare erosion at two resolutions while holding physical feature size constant. Inspect drainage continuity and whether deposits respect masks. Test scattering density and orientation against slopes and masks. Before conversion, check the required polygon resolution and confirm that the converted bounds match the source terrain.

## Common failures and troubleshooting

- Fine channels never appear: feature size is clamped by voxel spacing; resample before requesting smaller physical detail.
- A mask has no effect: bind the correct primitive name on the second input and enable the associated mask control.
- Several erosion nodes cook unpredictably: freeze earlier accepted passes.
- The terrain looks uniformly noisy: separate massing, seeding, shaping, and erosion scales.
- Vertical faces stretch: a heightfield is a single-valued surface and cannot represent true vertical or overhanging detail.
- Resolution increases without added detail: reseed and erode after resampling.
- Imported maps produce wrong scale: reconcile source elevation units, HeightField size, and center.
- Memory spikes after conversion: retain volumes for authoring and convert only the required output resolution.

## When not to use

Do not use a heightfield as the final representation for caves, arches, overhangs, multilayer surfaces, or detailed vertical cliffs. Do not start at maximum resolution merely to avoid staged development. Avoid embedding every terrain layer in downstream geometry when only selected masks are needed. Do not assume viewport visualization is identical to render or exported polygon resolution; validate the actual deliverable representation.

## Houdini 21 notes

Houdini 21 introduces the rewritten HeightField Erode 3.0 SOP, documented as Since 21.0. It uses a physical erosion feature size, produces similar features across input resolutions when not clamped, supports frozen range evaluation or iterative playbar evaluation, and outputs reusable erosion layers. The H21 modeling change list describes it as a new, faster hydraulic and thermal erosion workflow with fewer, more intuitive controls.

## Official sources

- SideFX, [Heightfields and terrains](https://www.sidefx.com/docs/houdini/heightfields/index.html), accessed 2026-07-26.
- SideFX, [Terrain creation](https://www.sidefx.com/docs/houdini/heightfields/creation.html), accessed 2026-07-26.
- SideFX, [HeightField SOP](https://www.sidefx.com/docs/houdini/nodes/sop/heightfield.html), accessed 2026-07-26.
- SideFX, [HeightField Erode 3.0 SOP](https://www.sidefx.com/docs/houdini/nodes/sop/heightfield_erode.html), accessed 2026-07-26.
- SideFX, [What's new: Modeling, geometry, and terrains in Houdini 21](https://www.sidefx.com/docs/houdini/news/21/model.html), accessed 2026-07-26.
