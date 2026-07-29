# Houdini 21 Copernicus Pyro and Legacy COP2 migration

Pack version: 2.0.0

Canonical ID: `copernicus-pyro-cop2-migration`

Houdini version: `21.x target; 20.5 migration baseline; Houdini 22 differences are explicitly excluded`

## Use for

Use this workflow for two related Houdini 21 transitions: building a bounded
fire or smoke experiment with the first-generation Copernicus Pyro block, and
rebuilding an important Legacy Compositing (`COP Network - Old`, or COP2)
network in Copernicus. Both tasks require explicit data semantics and
side-by-side validation. This is not a claim that a COP2 graph can be converted
node-for-node, or that the Houdini 22 Pyro Block 2.0 workflow exists unchanged
in Houdini 21.

## Context and core data

Legacy COP2 can carry multiple image planes on one wire and relies on COP2
plane, frame-scope, data-window, and display-window behavior. Copernicus carries
one typed layer, VDB, geometry, or attribute per wire; a cable bundles several
distinct wires without flattening their identities. Signature, Type Info,
resolution, border, precision, and color-space intent must therefore be made
explicit during migration.

Houdini 21 introduced COP Pyro as a sparse GPU workflow inside Copernicus.
The H21 release notes describe it as a fast 2D method with an infinite
timeline, while the H21-era node interfaces operate on sparse VDB fields and
offer camera-coverage and voxel-size controls. Houdini 22 documentation
reframes the workflow as 3D and introduces Pyro Block, Begin, End, and Configure
2.0 plus new implicit source and collision nodes. For an H21 target, use the
installed 1.0 node help and do not silently substitute the current 2.0 recipe.

## Recommended data flow

For migration, use:

`COP2 output contract and reference frames -> parallel Copernicus network ->
one semantic layer per wire -> cables only for named bundles -> explicit output
node -> pixel, frame, and consumer comparison -> controlled switchover`.

For H21 COP Pyro, use:

`H21 Pyro Configure 1.0/reference VDB -> H21-supported density, velocity, and
temperature sources -> Pyro Block Begin 1.0 -> necessary in-block shaping ->
Pyro Block End 1.0 -> VDB inspection or rasterization -> explicit cache/output`.

Keep source preparation outside the feedback block when it does not depend on
previous simulation state. Keep post-processing outside the block when it does
not feed the next frame.

## Step-by-step workflow

1. Record the exact Houdini build and inspect the installed node versions. Stop
   if the proposed graph depends on a Houdini 22-only 2.0 Pyro node.
2. For COP2 migration, inventory final planes, frame range, frame offsets,
   masks, data/display windows, pixel aspect, resolution, color handling,
   expressions, and every downstream consumer.
3. Render or save a small set of representative COP2 reference frames,
   including first, middle, last, and any discontinuity or animated transition.
4. Create a separate Copernicus network. Rebuild intent rather than copying
   node names: assign each layer a correct Signature and Type Info, and use a
   cable only when several named layers must travel together.
5. Apply documented semantic replacements where they fit: Clamp for the old
   Limit role, Contact Sheet for Mosaic, and Crop with Extract Tile for the
   Labs Demosaic role. Translate sequence tokens such as `$F4` to `<F4>` only
   after confirming Video Start Frame behavior in the installed H21 File COP.
6. Expose an explicit named output and write a bounded image sequence. Compare
   channels, alpha, windows, timing, values, and color transforms against the
   frozen COP2 reference before reconnecting consumers.
7. For H21 COP Pyro, start from an H21 Pyro Configure example or create the
   installed Configure/Block 1.0 nodes. Set voxel size or camera coverage from
   the intended screen-space detail and memory budget.
8. Connect only fields supported by the H21 Begin node and configure sourcing
   on the H21 End node. Test source activation and bounds before adding
   disturbance, turbulence, or small detail.
9. Choose timeline simulation for repeatable frame evaluation. Use Live
   Simulation only for exploration because it is not tied to the playbar or a
   deterministic cache; stash an accepted exploratory state if H21 supports
   that path.
10. Inspect density, velocity, temperature, active bounds, and representative
    slices. Rasterize or visualize after the block without feeding cosmetic
    work back into the solve.
11. Re-run the same bounded frames after every resolution, border, source, or
    migration change. Preserve the old COP2 output or prior Pyro cache until
    the replacement has passed the same evidence.

## Critical parameters and attributes

For migration, check layer Signature and Type Info, precision, resolution,
pixel scale, border, data/display windows, alpha convention, file sequence
token, start frame, and explicit output selection. A Copernicus wire does not
inherit COP2's multi-plane semantics; cable wire names become part of the
interface contract.

For H21 Pyro, check the installed 1.0 node's voxel size or camera coverage,
sparse activation, source mode, source scale, bounds, cache, start frame, and
checkpoint controls. Treat `density`, `v`, and `temperature` as the safe H21
baseline only after verifying their actual port signatures. Do not assume
Houdini 22's source-shape, collision-shape, flame, divergence, or 2.0 control
layout is present in an H21 build.

## Cache, version, and performance

Use Copernicus proxy resolution and the lowest precision that preserves the
needed range while rebuilding. A cable improves wiring organization but does
not reduce the cost of its member layers. Keep the old graph available as a
read-only reference until migration acceptance; an automatic cache hit is not
proof of parity.

Sparse Pyro is efficient only when activation remains bounded. Lowering voxel
size grows memory and compute cubically for a comparable 3D region. H21
Copernicus can move data between GPU and CPU when video memory is exhausted,
which may cause a sudden performance change. Compiled COP networks do not
support simulation. Live Simulation is an interactive sandbox, not a durable
cache or repeatable delivery result.

## Validation

For migration, compare the same files or viewer outputs at the same frames.
Check dimensions, data/display windows, channel names and count, alpha,
minimum/maximum values, edge/border behavior, frame offset, mask response,
color-space conversion, and the actual downstream material or export
consumer. A similar thumbnail is insufficient.

For H21 Pyro, verify that source fields appear on the intended frame, bounds
follow the source without runaway activation, velocity moves density in the
expected direction, and a repeated timeline cook produces the same sampled
field statistics and appearance. Inspect VDB slices and velocity rather than
judging only the final raster. Record this card as `documented/static`; no H21
COP cook was run while authoring it, so it is not live-verified.

## Common failures and troubleshooting

- A migrated graph loses maps because several COP2 planes were treated as one
  Copernicus layer; split them and preserve names with explicit wires or a cable.
- Frames shift because `$F#`, `<F#>`, and Video Start Frame semantics were
  assumed equivalent without a sequence test.
- Values or edges differ because data/display windows, border policy,
  precision, alpha, or color conversion changed.
- A Pyro graph fails in Houdini Core because COP Pyro requires DOP-level
  permissions; it is documented for FX, Apprentice, Indie, and Education.
- A tutorial graph has missing nodes because it uses Houdini 22 Pyro Block 2.0
  or new source/collision nodes. Rebuild from H21 help instead of renaming nodes.
- Scrubbing is slow because caching is disabled or checkpoints are too sparse;
  a stale result can instead mean the wrong cache or live mode is being viewed.
- The solve expands indefinitely because sparse activation or clip bounds are
  too permissive, or because a source is continuously active.

## When not to use

Do not migrate a stable COP2 graph merely to rename its context when it depends
on an unsupported operator or must remain compatible with an older delivery
environment. Document and isolate it instead. Do not use H21 COP Pyro when the
shot requires a Houdini 22-only 2.0 feature, a deeply customized DOP
microsolver network, or an established SOP/DOP sparse-Pyro cache and rendering
pipeline. Do not use a live simulation as a final deterministic sequence.

## Houdini 21 notes

Copernicus began as beta in 20.5; SideFX marks texturing and slap comp
production-ready in H21 and adds cables, VDB I/O, simulation controls, and the
first COP Pyro nodes. The H21 change note's “2D method” wording and current
Houdini 22 guide's “3D method” wording are not interchangeable. Houdini 22
explicitly introduces Pyro Block 2.0 and a revised recipe. Therefore this card
uses H21 release notes and installed 1.0 help as the authority, treats current
online 2.0 pages only as a version boundary, and requires an H21 build check
before implementation.

## Official sources

- SideFX, [Houdini 21 Pyro and FLIP changes](https://www.sidefx.com/docs/houdini/news/21/pyro.html), accessed 2026-07-26.
- SideFX, [Houdini 21 Copernicus changes](https://www.sidefx.com/docs/houdini/news/21/copernicus.html), accessed 2026-07-26.
- SideFX, [Copernicus for Houdini users](https://www.sidefx.com/docs/houdini/copernicus/transition.html), accessed 2026-07-27.
- SideFX, [Introduction to Copernicus](https://www.sidefx.com/docs/houdini/copernicus/intro.html), accessed 2026-07-26.
- SideFX, [Pyro Block Begin COP](https://www.sidefx.com/docs/houdini/nodes/cop/pyro_block_begin.html), accessed 2026-07-27.
- SideFX, [Pyro Configure COP](https://www.sidefx.com/docs/houdini/nodes/cop/pyro_configure.html), accessed 2026-07-27.
- SideFX, [Houdini 22 Pyro and Simulation changes](https://www.sidefx.com/docs/houdini/news/22/pyro.html), accessed 2026-07-27.
