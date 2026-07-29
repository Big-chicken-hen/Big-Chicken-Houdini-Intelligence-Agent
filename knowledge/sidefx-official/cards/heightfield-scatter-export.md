# Heightfield scattering and export

Pack version: 2.0.0

## Use for

Use this workflow after terrain form and masks are approved, when the heightfield must drive point distributions, instance variants, shading zones, polygon conversion, image or engine export, or tiled delivery. It separates downstream distribution and packaging from the mask-and-erosion authoring workflow. The goal is to preserve useful layer semantics while producing only the geometry, points, maps, and metadata required by the destination.

## Context and core data

Heightfields are volume primitives, typically `height`, `mask`, and named material or environmental layers. HeightField Scatter converts selected terrain regions into points that can carry `P`, `N`, `orient`, `pscale`, `scale`, variant, color, and custom environmental attributes. Masks modulate density or exclusion. HeightField Convert creates polygons for renderers or formats that need meshes. HeightField Output can write terrain layers. Game-engine targets may impose heightmap resolution, tile, layer-name, range, or axis requirements.

## Recommended data flow

Use:

`approved frozen terrain -> cleanup and normalize named masks -> slope/height/flow distribution masks -> HeightField Scatter -> point attributes and variant selection -> packed or USD instances`

and, separately:

`approved frozen terrain -> crop/tile/resample for target -> convert to polygons or write height/layer maps -> validate bounds, seams, ranges, and metadata -> versioned export`

Keep scatter points and terrain export as separate branches so a distribution change does not rebuild terrain maps.

## Step-by-step workflow

1. Freeze or cache the approved terrain and inventory every layer required for distribution, shading, and export.
2. Normalize masks to documented ranges and combine slope, height, flow, or painted zones into explicit inclusion and exclusion layers.
3. Scatter a low density first, then set seed, separation, scale classes, and hierarchical rules before increasing counts.
4. Author stable point IDs and the standard orientation, scale, color, and variant attributes consumed by copying or instancing.
5. Build an independent export branch that crops, tiles, or resamples to the destination's exact size and resolution.
6. Convert only if required, preserving selected layer values as attributes or maps and documenting axis and unit conversions.
7. Write versioned files, then reload them in a clean scene or target host and compare bounds, layer ranges, seam continuity, and point counts.
8. Record output paths, tile scheme, Houdini build, source cache version, and expected downstream attribute names.

## Critical parameters and attributes

- Scatter density and separation should be controlled through named masks.
- Stable `id` prevents random variants from changing when point order changes.
- `orient`, `N`, and `up` control alignment to terrain; `pscale` and `scale` control size.
- A string or integer variant attribute should match the source library contract.
- HeightField size and grid spacing determine output extent and sampling.
- Resample filters affect both height fidelity and mask edges.
- HeightField Convert polygon resolution should match the actual delivery need.
- Layer ranges may need normalization for image formats.
- Target engines may require a power-of-two-plus-one heightmap resolution or specific axis conventions.

## Cache, version, and performance

Cache the terrain before scatter and export branches. Keep low-density preview controls separate from final density. Prefer packed or point instancing to copied polygons for large distributions. Avoid converting a full-resolution terrain merely to derive points that native heightfield tools can produce. Version terrain maps, scatter points, and instance libraries independently. A changed tile extent, layer name, normalization range, or coordinate convention is a package-contract change and should create a new version.

## Validation

Visualize each scatter mask and color points by source layer, variant, or scale. Check orientation on steep slopes and exclude unstable regions deliberately. Verify no unexpected overlap across hierarchical classes. For tiled exports, compare edge samples between adjacent tiles and inspect bounds. Reload image layers to confirm value range and bit-depth behavior. Reload polygon output to confirm axes, units, normals, UVs if present, and triangle or quad counts. Test the delivered instance attributes with the actual downstream copying or Solaris workflow.

## Common failures and troubleshooting

- Distribution ignores a mask: the layer name or mask input binding is wrong.
- Random variants change after density edits: use stable IDs rather than point numbers.
- Instances lean or flip: rebuild `N/up` or `orient` consistently after terrain conversion.
- Tile seams appear: crop and resample with a consistent shared grid and validate boundary samples.
- Exported terrain is vertically compressed: the target expects normalized heights and a separate height scale.
- A mesh is unexpectedly huge: conversion resolution followed the authoring heightfield rather than delivery requirements.
- Layers disappear in export: the selected file format does not preserve arbitrary volume primitives; write explicit maps or attributes.
- Engine import is offset: reconcile terrain center, axis orientation, and world-unit scale.

## When not to use

Do not scatter final counts while erosion and masks are still changing. Do not convert the whole heightfield to dense polygons solely to query slope or height. Avoid assuming a general geometry file preserves the semantics expected by a terrain engine. Do not bake random variation from transient point numbers. Do not merge scatter, map export, and mesh conversion into one cache if they require different update frequencies.

## Houdini 21 notes

H21's rewritten HeightField Erode outputs `flow`, `flowdir`, `sediment`, and `debris`, which can become useful scatter or shading masks. H21 terrain materials and Copernicus workflows can consume named layers, but export contracts remain destination-specific. Record whether a layer comes from H21 Erode 3.0 because older networks may produce differently named or scaled erosion data.

## Official sources

- SideFX, [Heightfield scattering attributes](https://www.sidefx.com/docs/houdini/heightfields/scatterattribs.html), accessed 2026-07-26.
- SideFX, [Heightfields and terrains](https://www.sidefx.com/docs/houdini/heightfields/index.html), accessed 2026-07-26.
- SideFX, [Various heightfield features and output](https://www.sidefx.com/docs/houdini/heightfields/workflows.html), accessed 2026-07-26.
- SideFX, [HeightField SOP](https://www.sidefx.com/docs/houdini/nodes/sop/heightfield.html), accessed 2026-07-26.
- SideFX, [Terrain heightfields for Unity](https://www.sidefx.com/docs/houdini/unity/terrain/basics.html), accessed 2026-07-26.
