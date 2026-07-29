# Copernicus layer and data-flow workflow

Pack version: 2.0.0

## Use for

Use this workflow to build Houdini 21 Copernicus networks for procedural textures, masks, height data, vector fields, image processing, slap composites, baking inputs, and two- or three-dimensional GPU operations. It focuses on correct layer semantics and predictable interoperability with SOPs, VDBs, Solaris, and MaterialX.

## Context and core data

A Copernicus wire carries one typed value: a layer, geometry, VDB, attribute, or cable. Layer signatures include ID, Mono, UV, RGB, and RGBA. Type Info describes semantic meaning such as Color, Position, Vector, Signed Normal, Offset Normal, Texture Coordinate, Mask, SDF, or Height. A node Signature selects the accepted input and produced output types. The COP Network supplies default resolution, pixel scale, border, precision, and VRAM policy.

## Recommended data flow

Use `File or generated layer/SOP Import/VDB → semantic normalization and type
assignment → masks and main operations → detail and color processing → named
output or cable → ROP Image Output COP or another explicit consumer`. Keep
color, scalar masks, normals, and positions on separate correctly typed wires.
Use Null outputs as stable cross-context references. For file delivery from
inside a COP network, prefer a wired ROP Image Output COP; an Image ROP in
`/out` is the alternative when output control belongs outside the COP network.

## Step-by-step workflow

1. Define final resolution, pixel scale, frame behavior, color space, precision, and consuming context.
2. Create or import the source and assign the correct Signature and Type Info before processing.
3. Normalize data windows, borders, and coordinate space so downstream filters sample predictably.
4. Build masks and broad operations before high-frequency detail or expensive simulation blocks.
5. Preview with a proxy resolution and inspect both numeric data and the default visualizer.
6. Name and expose final outputs explicitly, using cables only when several distinct wires must travel together.
7. For image delivery inside the COP network, connect the final layer or cable
   to a **ROP Image Output COP**. Set **Output File** and, for multiple outputs,
   use **File Layout ▸ Add AOVs from Input**. Inspect every generated **AOV
   Name** and **Port** before **Render to Disk**, then reopen the written file.
   If the ROP Image Output COP is not wired, set its **COP Path** explicitly.

## Critical parameters and attributes

Signature controls channel count and conversion. Automatic signatures may
apply channel extension, such as Mono to RGBA; this can be useful but must be
intentional. Type Info prevents a position or normal from being treated as
display color. Border behavior controls sampling outside the layer and is
essential for convolution and tiling. Mask blends the original value with the
processed result. `size_ref` inputs propagate resolution and precision from an
explicit reference layer.

On ROP Image Output COP, **COP Path** or the direct input selects the source;
**Output File** selects the destination; **Add AOVs from Input** removes the
existing AOV list and rebuilds it from connected outputs. Check **AOV Name**,
**Data Size**, **Raw**, **Explicit Port**, and **Port**. If output order differs
from source port order, enable Explicit Port rather than trusting list order.

## Cache, version, and performance

Use the network's Proxy scale for iteration rather than manually resizing every node. Choose the lowest precision that preserves the required range. Avoid unnecessary RGBA layers where Mono is sufficient. Clear CPU and GPU caches with `copcache -c` when diagnosing memory or stale previews. A Cache COP can accelerate expensive time-dependent branches but consumes RAM and VRAM; recomputation may be faster for cheap nodes.

## Validation

Inspect the same output in Composite View and Scene View. Remember that default
visualizers enhance Height, SDF, and other data, so display appearance is not
the stored value. View channels individually and check minimum, maximum, alpha,
and vector range. Use Tile Visualization to find seams. Move display and 3D
output flags while confirming the COP Network's externally assigned output
remains unchanged. For ROP Image Output, render a bounded frame, reopen the
actual file, and verify dimensions, channel/AOV names, bit depth, raw versus
color-managed handling, and that every Explicit Port maps to the intended wire.

## Common failures and troubleshooting

Wrong colors, normals, or displacement often come from an incorrect Signature
or Type Info. Seams arise from Border settings, coordinate mismatch, or
non-tileable source data. A COP Network may output the display-flag node unless
Use External COP fixes a specific output. A written file with missing or swapped
AOVs usually means Add AOVs from Input was not refreshed after an upstream
change, or Explicit Port points at the wrong output. Automatic channel extension
can hide a missing source channel. Large VRAM usage usually reflects
full-resolution branches, excessive precision, RGBA where Mono would suffice,
or stale caches.

## When not to use

Use SOPs when the operation is fundamentally mesh topology or point processing. Use a specialized compositor for editorial-heavy shot finishing beyond the intended slap-comp scope. Maintain old COP2 networks only for compatibility; SideFX recommends Copernicus for new work from 20.5 onward. Compiled COP networks do not support simulation and should not be chosen for feedback effects.

## Houdini 21 notes

Copernicus was beta in 20.5. H21 change notes mark texturing and slap comp
production ready and list cables, baking, Cache, SOP Invoke, UDIM settings, new
2D/3D filters, reaction-diffusion, flow blocks, and sparse GPU Pyro. The ROP
Image Output parameter and wiring behavior above is **documented/static** from
SideFX help. This pack did not run an H21 COP cook or file write, so none of
these steps are live-verified here. Confirm the installed H21 node help before
depending on a production-build-specific port or menu label.

## Official sources

- SideFX, [Introduction to Copernicus](https://www.sidefx.com/docs/houdini/copernicus/intro.html), accessed 2026-07-26.
- SideFX, [Copernicus glossary](https://www.sidefx.com/docs/houdini/copernicus/glossary.html), accessed 2026-07-26.
- SideFX, [ROP Image Output COP](https://www.sidefx.com/docs/houdini/nodes/cop/rop_image.html), accessed 2026-07-26.
- SideFX, [Houdini 21 Copernicus changes](https://www.sidefx.com/docs/houdini/news/21/copernicus.html), accessed 2026-07-26.
