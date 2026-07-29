# Solaris stage organization and performance diagnosis

Pack version: 2.0.0

## Use for

Use this workflow when a Solaris stage opens, composes, updates, displays, or
renders slowly; when designing a large asset or shot assembly; or before
claiming a USD optimization improved performance. It separates SOP cooking,
USD authoring and composition, Hydra population, material and texture work, and
rendering so that the remedy matches the bottleneck. It does not prescribe one
universal layer count or payload policy.

## Context and core data

Performance depends on the composed stage and how it changes: prim count,
property count, layer count and strength, references and payloads, variants,
instances and prototypes, collections, time samples, SOP Imports, LOP recooks,
loaded/active/visible state, Hydra population, textures, shaders, geometry, and
the render delegate. A viewport delay after an upstream SOP edit is different
from a slow USD file open or a noisy Karma render.

Measure a representative stage and action. Record whether the test is initial
load, timeline change, LOP parameter edit, payload toggle, viewport population,
first render, or warm render. Preserve the same camera, delegate, payload state,
and assets between comparisons.

## Recommended data flow

Use:

`profile representative action -> classify SOP cook / LOP cook / USD compose /
Hydra populate / shader-texture / render cost -> identify invalidation boundary
-> change one structural cause -> reload or recook under the same conditions ->
compare timing, memory, and visual result -> retain or revert the hypothesis`.

Organize data as:

`versioned asset layers and payloads -> instance-friendly assembly -> focused
look overrides -> lights/cameras -> render settings/products`.

Keep expensive geometry generation upstream of stable caches or asset layers so
a lighting edit does not recook it. Use payloads for content that can safely
remain unloaded and instances for truly shared structure, not as ritual.

## Step-by-step workflow

1. Define the slow user action and capture the Houdini build, stage, frame,
   payload state, viewport/render delegate, timing, memory, and a representative
   visual result. Do not profile an empty substitute scene.
2. Inspect node cook times and stage changes. Determine whether a SOP Import or
   other upstream generator is recooking, LOPs are re-authoring broad data, USD
   composition is expensive, Hydra is repopulating, or rendering is dominant.
3. Inspect the stage hierarchy, layers, references, payloads, variants,
   instances, and property volume. Find unnecessarily unique data, overly broad
   edits, repeated imports, always-loaded heavy content, or unstable prim paths.
4. Stabilize expensive source geometry through a reviewed cache or asset layer.
   Narrow SOP imports and authoring patterns to the data that actually changes.
   Keep asset construction separate from shot look, lighting, and render config.
5. Introduce or repair payloads where deferred population helps. Preserve
   instancing for repeated immutable structure, and avoid per-instance opinions
   that silently expand prototypes into unique data.
6. Consolidate needlessly fragmented authorship only when measurement shows
   layer or property overhead. Do not flatten away intentional overrides,
   variants, provenance, or editability.
7. Repeat the same action from a comparable cold or warm state. Compare stage
   load, composition, population, interaction, first render, steady render, and
   memory separately. Confirm the visual and USD result did not change.
8. Record the optimization, evidence, assumptions, and remaining bottleneck.
   Stop when the task’s interactive or delivery target is met rather than
   trading away structure or quality for an unmeasured improvement.

## Critical parameters and attributes

Track payload loaded state, active and visibility state, instanceable/prototype
relationships, variant selections, purpose, population or selection scope,
SOP Import path and time dependency, layer identifiers and sublayer ordering,
time samples, frame range, texture resolution, delegate, and render settings.
For profiling, distinguish first-load from cached behavior and wall time from
memory. Prim count alone is insufficient: many authored properties, time
samples, relationships, or unique prototypes can also dominate.

## Cache, version, and performance

Use versioned geometry/USD caches at stable stage boundaries and verify them
before setting a load-from-disk path. Payload large assets whose internal data
is unnecessary for some tasks. Preserve instances and avoid needless topology
uniqueness. Keep texture and shader caches in mind when interpreting first
render cost. Avoid optimizing by lowering samples, resolution, subdivision, or
texture quality until the measured bottleneck is rendering or resource use.
Changing several structures at once destroys the diagnostic comparison.

## Validation

Measure the same representative operation before and after. Confirm layer and
prim stacks, payload state, instance count/prototypes, visible result, material
bindings, animation, and saved USD. Reload the stage to expose reliance on
session state. If a change improves viewport interaction but inflates file size
or breaks editability, report the tradeoff rather than calling it universally
faster. For render performance, validate pixels and AOVs in addition to timing.

## Common failures and troubleshooting

- Repeated SOP recooks after lighting edits indicate an invalidation boundary
  or import design problem; cache or isolate the stable generation stage.
- Loading every payload removes the main deferred-loading benefit.
- Per-instance edits can destroy sharing and increase prim, memory, and Hydra
  cost; inspect prototypes before blaming the renderer.
- Thousands of tiny opinions or broad property rewrites may burden composition,
  but flattening can break ownership. Consolidate only the measured hotspot.
- A slow first frame may be shader compilation or texture loading; compare warm
  behavior and do not mislabel it as stage composition.
- Low GPU utilization can still be limited by CPU composition, scene
  translation, I/O, memory pressure, or an unsupported path.
- Lower render samples cannot fix slow stage opening or Hydra population.

## When not to use

Do not perform this full diagnosis for a small responsive stage or one known
parameter edit. Do not add payloads to small always-needed content, force
instances on data that must be unique, or cache an unstable stage merely to
avoid finding a correctness bug.

## Houdini 21 notes

This card targets Houdini 21. Solaris, Hydra, and Karma performance can change
between production builds and delegates. Confirm current node and delegate
behavior with installed H21 help and a measured stage. Online documentation may
show a later Houdini version; use it for stable composition principles but do
not attribute later optimizations to H21 without version evidence.

## Official sources

- [Solaris performance](https://www.sidefx.com/docs/houdini/solaris/performance.html) — SideFX performance guidance; accessed 2026-07-26.
- [Solaris and USD](https://www.sidefx.com/docs/houdini/solaris/index.html) — SideFX Solaris workflow documentation; accessed 2026-07-26.
- [USD in Solaris](https://www.sidefx.com/docs/houdini/solaris/usd.html) — SideFX composition guidance; accessed 2026-07-26.
- [Houdini 21 Solaris changes](https://www.sidefx.com/docs/houdini/news/21/solaris.html) — SideFX version notes; accessed 2026-07-26.
