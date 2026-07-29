# FLIP meshing, whitewater, and cache workflow

Pack version: 2.0.0

## Use for

Use this workflow after a FLIP particle solve has the required motion. It creates durable particle and field caches, reconstructs a renderable liquid surface, generates secondary foam, spray, and bubbles, and separates expensive stages so surfacing and whitewater can be revised without rerunning the base liquid simulation.

## Context and core data

The base cache contains particles and selected container, collision, or velocity
fields. Fluid Compress can store a reduced representation and is intentionally
lossy. Particle Fluid Surface or Neural Point Surface reconstructs an SDF and
polygon mesh. Whitewater Source generates the emission field consumed by
Whitewater Solver. Whitewater Solver births and evolves foam, spray, and bubble
particles. Whitewater Post-Process is a distinct render-preparation stage: it
derives final particle `pscale`/`density` or produces a fog-volume or mesh
representation. File Cache then stores that post-processed delivery.

## Recommended data flow

Use `FLIP Solver → base particle/field File Cache → Particle Fluid Surface →
mesh File Cache`, with a separate standard whitewater branch `approved FLIP
cache → Whitewater Source → Whitewater Solver → Whitewater Post-Process → File
Cache → whitewater material/render`. Source, Solver, Post-Process, and File
Cache are four different responsibilities, not interchangeable labels or
optional synonyms. Preserve a reusable base-particle cache before destructive
compression or surfacing so meshing and whitewater can be versioned without
invalidating the liquid solve.

## Step-by-step workflow

1. Decide which particles, fields, IDs, velocities, and collision data downstream stages require.
2. Cache the approved FLIP solve with deterministic frame range, path, and version metadata.
3. Reload the cache with the live solver bypassed and compare volume, velocity, and frame coverage.
4. Build a coarse fluid surface, tuning influence scale, voxel size, filtering, dilation, and smoothing.
5. Cache the approved mesh independently and retain velocity or point correspondence needed for motion blur.
6. Generate the emission field with Whitewater Source from the cached FLIP
   particles and required surface/velocity fields; inspect source location and
   magnitude before solving.
7. Feed that source into Whitewater Solver and simulate foam, spray, and bubble
   particles. Validate birth, classification, depth, velocity, and lifecycle
   attributes before render shaping.
8. For a long Whitewater Solver run, enable Save Checkpoints and set a unique
   Base Folder, Base Name, and Version. Choose Checkpoint Trail Length for the
   retained recovery window and Checkpoint Interval for the acceptable amount
   of recomputation after interruption. When solver inputs change, advance
   Version or use a new Base Folder/Base Name because invalidated explicit
   checkpoint files are not deleted automatically. Resume from an existing
   checkpoint and verify a later known frame before treating recovery as
   proven.
9. Feed the solver output into Whitewater Post-Process. Choose and tune the
   required particle, fog-volume plus velocity, or mesh representation,
   including density, `pscale`, and boundary flattening where needed.
10. Place File Cache after Post-Process, write the approved render-ready stream,
   reload it, and only then bind the whitewater material and render.

## Critical parameters and attributes

Particle Fluid Surface resolution should relate to FLIP Particle Separation;
extreme oversampling cannot restore missing simulation detail. Influence Scale
and Droplet Scale affect continuity and detached droplets. Whitewater Source
controls where and how much emission enters the solver. Whitewater Solver owns
birth, aging, classification, advection, repellents, and related particle
behavior. Whitewater Post-Process owns the final representation and render
attributes such as `density` and `pscale`, plus optional edge flattening. `id`
supports stable retiming or interpolation and `v` is required for motion blur.
Whitewater Solver checkpoint identity consists of Base Folder, Base Name, and
Version. Checkpoint Trail Length controls retained history; `0` keeps all
checkpoint files. Checkpoint Interval controls how many simulation frames can
need recomputation after the latest saved checkpoint.

## Cache, version, and performance

Fluid Compress reduces disk use but loses information; test whether surfacing
and Whitewater Source can reconstruct the required fields. Cache the approved
FLIP base first, then version the whitewater source only when its calculation
is expensive, and always keep the final render cache after Whitewater
Post-Process distinct from solver checkpoint state. For a long solve, enable
Save Checkpoints with a collision-free Base Folder, Base Name, and Version;
choose Trail Length from recovery and disk-retention needs, and Interval from
the acceptable restart cost. A checkpoint is resumable simulation state, not
the render delivery. Explicit checkpoints remain on disk after invalidation,
and an earlier session's files are not cleaned by the current session's Trail
Length. Use checkpoint identity to isolate solver-input revisions. Avoid
retaining full-resolution grids that no downstream node reads.

## Validation

Compare cached particles with the solve at representative boundary and action
frames. Inspect mesh silhouette, thin sheets, disconnected droplets, volume
retention, normals, and temporal flicker. At Whitewater Source, confirm the
emission field overlaps energetic liquid regions. At Whitewater Solver, confirm
foam stays near the surface, spray leaves it, bubbles remain submerged, and
required `id`/`v` data survives. At Whitewater Post-Process, inspect the chosen
particles, fog volume, or mesh and its `density`, `pscale`, velocity, and
boundary transition. Reload the post-process File Cache and render that cached
stream. For checkpoint recovery, start a fresh solver evaluation against the
same Base Folder, Base Name, and Version, request a frame after the latest
checkpoint, confirm the earlier frames are not recomputed, and compare the
resumed result at a known frame with the uninterrupted solve. This order and
recovery contract are documented/static and were not live-simulated for the
pack. After changing a solver input, advance Version or the path and verify
that a Reset/fresh evaluation does not silently load the prior sequence.

## Common failures and troubleshooting

Flickering surfaces may come from an overly coarse surface voxel size, changing particle density, aggressive compression, or excessive smoothing. Blobby liquid can result from large influence radius; holes often need better particle density rather than unlimited dilation. Too little or too much whitewater often indicates insufficient or excessive energy in the base FLIP solve. Fix the liquid momentum before forcing source thresholds. Floating foam suggests an incorrect surface field, depth test, or advection setup.

Caching Whitewater Source output as the final delivery omits the simulation and
render preparation. Caching raw Whitewater Solver particles as though they were
Post-Process output can omit the intended density, scale, representation, or
boundary treatment. Restore the Source → Solver → Post-Process → File Cache
order before tuning the renderer.

A resumed solve that starts from frame one usually points to a changed Base
Folder, Base Name, Version, or unavailable checkpoint. Conversely, leaving
that identity unchanged after an upstream change can silently load stale
explicit files; invalidation does not delete them. Advance Version or the path
and verify the recovery frame. A short Trail Length may remove the desired
recovery frame, while an Interval that is too large increases recomputation
after interruption.

## When not to use

Do not generate whitewater for calm liquid where it adds no readable scale cue. Do not mesh particles when the intended render is points, mist, or an analytic ocean surface. Avoid Neural Point Surface when a deterministic conventional SDF reconstruction is required by the pipeline or when the pretrained look changes the approved shape. A distant sea may use procedural foam rather than secondary simulation.

## Houdini 21 notes

H21 introduces Neural Point Surface and optional neural reconstruction inside
Particle Fluid Surface. It also adds point-cloud partitioning for OpenCL
devices with limited memory. The classic Particle Fluid Surface remains
appropriate when predictable geometric controls are required. The four-stage
SOP Whitewater order above follows current SideFX documentation but was not
live-simulated against a particular H21 production build.

## Official sources

- SideFX, [Caching and previews for SOP FLIP](https://www.sidefx.com/docs/houdini/fluid/sopcaching.html), accessed 2026-07-26.
- SideFX, [SOP Whitewater](https://www.sidefx.com/docs/houdini/fluid/sopwhitewater.html), accessed 2026-07-26.
- SideFX, [Whitewater Solver SOP](https://www.sidefx.com/docs/houdini/nodes/sop/whitewatersolver.html), accessed 2026-07-26.
- SideFX, [Whitewater Post-Process SOP](https://www.sidefx.com/docs/houdini/nodes/sop/whitewaterpostprocess.html), accessed 2026-07-26.
- SideFX, [Caching simulations](https://www.sidefx.com/docs/houdini/dyno/cache), accessed 2026-07-26.
- SideFX, [Houdini 21 Pyro and FLIP changes](https://www.sidefx.com/docs/houdini/news/21/pyro.html), accessed 2026-07-26.
