# Houdini 21 end-to-end procedural production workflow

Pack version: 2.0.0

## Use for

Use this card when one deliverable spans several Houdini disciplines and needs a
coherent production path rather than disconnected demonstrations. It is the
umbrella workflow for deciding when modeling, simulation, LookDev, Solaris,
rendering, caching, and validation should hand work to one another. Load the
domain-specific cards for implementation details. Do not treat this card as a
substitute for current node help or a task-specific Build Brief.

## Context and core data

Begin with project units, axis conventions, frame range, FPS, Houdini build,
renderer target, output paths, and the user's acceptance evidence. The important
data boundaries are source geometry and attributes in SOPs, time-dependent
simulation data, material and texture inputs, USD prims and layers in Solaris,
camera and light state, render products, and versioned caches. Give each
boundary a named output and an owner. A downstream stage should consume an
explicit result rather than reaching into an unstable experiment.

## Recommended data flow

Follow the SideFX foundations pattern at a production scale:

`reference/brief -> SOP asset outputs -> optional simulation inputs and caches
-> material-ready geometry and attributes -> USD asset/shot assembly -> material
bindings -> lights and cameras -> render settings/products -> image validation`.

Keep high-level artist controls upstream. Cache only after a stage has a stable
contract, and retain the parameters and source outputs needed to reproduce it.
Use Solaris layers and composition for shot assembly rather than copying a whole
scene into one mutable layer.

## Step-by-step workflow

1. Record scale, orientation, time range, target Houdini 21 build, renderer,
   delivery format, representative view or frame, and the largest uncertainty.
2. Build or inspect the SOP-level source. Establish identity, dimensions,
   topology, normals, semantic groups, stable names, and required UVs or other
   surface coordinates before handing it downstream.
3. If the task is time-dependent, prepare sources, collisions, constraints, and
   low-cost proxy data. Test the earliest meaningful frames before increasing
   resolution, substeps, or iteration counts.
4. Cache a technically stable stage with versioned paths and explicit frame
   coverage. Reload the cache and compare a known frame before using it for
   LookDev or lighting.
5. Build only the material channels the visual target needs. Confirm primvars,
   texture color spaces, displacement scale, bindings, and Karma CPU/XPU support.
6. Assemble assets, variants, materials, lights, cameras, and render settings in
   Solaris using understandable layer ownership and stable USD prim paths.
7. Create render products and task-relevant AOVs, then render a bounded
   representative result. Increase quality only after camera, lighting,
   material, motion, and output-path errors are excluded.
8. Validate the actual deliverable from disk, not only a live viewport. Record
   the Houdini build, cache or USD versions, delegate, frame, and unresolved
   limitations with the handoff.

## Critical parameters and attributes

Track scene units, FPS, start/end frame, time scale, IDs such as `name` or
`id`, transformation data (`P`, `orient`, `scale`, `v`), normals and UV/primvar
ownership, material path attributes, USD prim paths, layer edit target, camera
path, render delegate, resolution, sampling controls, render-product path, and
cache frame tokens. The exact set is task-dependent; do not expose every
internal parameter merely because it exists.

## Cache, version, and performance

Separate interactive caches from delivery caches. Include a deliberate version
token and frame token where the output is time-dependent, and never assume a
file is valid solely because its name exists. Prototype at lower geometric,
voxel, particle, texture, or render resolution while preserving real-world
scale. Measure whether the bottleneck is SOP cooking, simulation, USD
composition, Hydra population, shader compilation, texture I/O, or rendering
before reducing quality. Preserve instancing and payload behavior when repeated
data does not need to be unique.

## Validation

Check node errors and empty outputs at each handoff. Compare bounds, element
counts, named groups, frame coverage, material bindings, active camera, light
visibility, renderer warnings, output path, and the file actually written.
Require a task-relevant visual result for visual completion. When real Houdini
or representative render evidence is unavailable, report the exact technical
checks that passed and label the visual result unverified.

## Common failures and troubleshooting

- If later stages drift after an upstream change, identify the first broken
  contract: names, paths, topology, primvars, cache version, or frame range.
- If simulation scale looks wrong, verify units, source velocity, collision
  thickness, and time scale before adding solver detail.
- If a material appears absent, inspect USD material binding and primvar names
  before rebuilding the shader.
- If a render is black or incomplete, verify camera, delegate, lights, purpose
  visibility, payload loading, material support, and render product before
  increasing samples.
- If a cached result disagrees with the graph, confirm Load from Disk state,
  version tokens, frame coverage, and whether upstream edits invalidated it.

## When not to use

Do not invoke this full sequence for one known parameter edit, one primitive, a
read-only query, or an isolated API error. Do not add simulation, USD, materials,
or rendering when the requested output ends earlier. A deliberate blockout can
stop after scale and primary-form validation.

## Houdini 21 notes

This summary targets Houdini 21. Use the current installed help to confirm node
versions and parameters. Houdini 21 expands Solaris, Copernicus, terrain, and
Karma workflows, but older project files may retain legacy networks. Preserve a
required legacy route instead of silently migrating it, while using the modern
Houdini 21 route for newly authored work.

## Official sources

- [H21 Foundations welcome](https://www.sidefx.com/tutorials/h21-foundations-welcome/) - SideFX, Houdini 21 learning path entry; accessed 2026-07-26.
- [Houdini 21 documentation](https://www.sidefx.com/docs/houdini/) - SideFX product documentation and current context index; accessed 2026-07-26.
- [Solaris and Karma](https://www.sidefx.com/docs/houdini/solaris/index.html) - SideFX USD scene-building and rendering guide; accessed 2026-07-26.
