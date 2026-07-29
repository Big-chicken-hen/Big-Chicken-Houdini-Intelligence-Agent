# Scene Import, USD layout, and shot-layer handoff

Pack version: 2.0.0

## Use for

Use this workflow when an existing Houdini Object-level scene must enter
Solaris, when reusable USD assets must be laid out into a shot, or when camera,
light, transform, collection, and material work needs clean layer ownership.
It is especially useful for staged migrations in which some content remains at
`/obj` while new look development and rendering occur in `/stage`. It does not
replace Component Builder for publishing reusable leaf assets.

## Context and core data

Scene Import LOP can translate selected Object-level geometry, transforms,
cameras, lights, materials, bundles, hierarchy, and animation to USD. It imports
nothing when its Objects pattern is blank. Object containment and input wiring
can become USD hierarchy unless flattening or input-ignore options change that
behavior. SOP geometry translation controls topology time samples, primvar
selection, indexed attributes, subsets, capture weights, and UV-to-`st`
conversion.

Shot layout uses referenced or payloaded assets, Xform prims, instances,
variants, collections, transforms, visibility/purpose, camera and light prims,
and deliberate layer boundaries. A Layer Break starts a new active layer for
following edits and marks all earlier sublayers as pre-break so a USD ROP does
not write that context into the new file. It does not hide the earlier content
from the composed in-memory stage. Configure Layer edits active-layer metadata;
it creates another active layer only when **Start New Layer** is enabled, and
that option does not replace the Layer Break output-boundary semantics. Every
LOP outputs a composed stage, so layer ownership must be inspected separately
from the visible result.

## Recommended data flow

Use:

`audited /obj hierarchy or published USD assets -> narrow Scene Import and/or
Reference/Asset Reference -> stable shot prim hierarchy -> variant and payload
selections -> Layer Break before new-layer opinions -> layout
transforms/instances -> collections and material overrides -> camera and
lights -> Configure Layer metadata on the intended active layer -> render
settings -> USD save and clean reopen`.

Prefer published USD references for stable reusable assets. Use Scene Import
for selected legacy or live Houdini content that genuinely must cross into
Solaris. Place the Layer Break before the first opinion that belongs in the
deliverable layer: upstream context stays composed for editing, while only the
post-break opinions are written. Use Configure Layer for metadata such as save
path, default prim, units, time codes, and comments; leave **Start New Layer**
off when configuring the layer that already contains those post-break edits.

## Step-by-step workflow

1. Inventory the source: object paths, parenting, display/render flags,
   materials, cameras, lights, animation, packed data, units, and frame range.
   Decide which items should be published assets and which require Scene Import.
2. For Scene Import, set a narrow root and explicit Objects pattern. Choose
   geometry, camera, light, and material filters intentionally. Preview the
   generated prim paths and resolve name conflicts rather than enabling broad
   automatic renaming without review.
3. Configure SOP geometry translation. Mark topology static unless it truly
   changes; include only necessary primvars and subsets; confirm UV-to-`st`,
   visibility, purpose, capture, transforms, and time sampling.
4. Reference published component files at stable shot paths. Select supported
   variants and payload state, and preserve instanceability for repeated assets.
   Do not import or duplicate internal asset geometry into the shot layer.
5. Insert a Layer Break immediately before the first shot, look, or lighting
   opinion that the new output layer must own. Confirm the composed context is
   still visible, while the active layer after the break initially contains no
   upstream scene opinions.
6. Build the shot hierarchy and layout with Transform/Edit or appropriate
   layout tools. Use one clear transform owner per decision, maintain scale and
   ground/contact relationships, and avoid hidden compensation transforms.
7. Create collections for meaningful downstream selections such as lighting or
   material assignment. Apply look overrides, cameras, and lights in their
   owned branches or layers, preserving asset-local bindings unless the shot
   intentionally overrides them.
8. Add Configure Layer when the active layer needs explicit save-path or layer
   metadata. Do not treat it as interchangeable with Layer Break: with **Start
   New Layer** off it edits metadata on the current active layer; with the
   option on it starts a layer for subsequent nodes but does not establish the
   Layer Break rule that excludes all pre-break layers from the written file.
9. Add render settings and inspect the composed stage at representative frames.
   Validate payloads, variants, instances, materials, lights, cameras, purpose,
   animation, and layer ownership before writing USD.
10. Save the intended layers, reopen the top-level USD in a clean stage, and
   verify relative/resolver paths, frame range, shot composition, and one
   representative render from the written result.

## Critical parameters and attributes

For Scene Import, check Root Object, Objects, Force/Exclude Objects, destination
path, filter, hierarchy flattening, Ignore Inputs, visibility policy, bundles
as collections, import SOP geometry, references versus sublayers, packed USD
primitive handling, topology mode, imported/indexed/constant primvars, subsets,
UV-to-`st`, materials, camera/light selection, and diagnostics. For layout,
check prim path, Xform operation order, instance/prototype status, variant
selection, payload loaded state, purpose, visibility, collection membership,
camera path, and layer save path. For layer ownership, check the Layer Break
position, the active layer, Configure Layer's **Start New Layer** state, Save
Path, default prim, units, and time-code metadata.

## Cache, version, and performance

Scene Import can be expensive when broad patterns translate many Object-level
networks or animated topology. Import only the required objects and attributes,
mark topology static when valid, and cache expensive SOP sources. Prefer
references/payloads and instancing over duplicating geometry into shot layers.
Unloaded payloads cannot be matched or edited by downstream prim patterns, so
design the loaded state around the task. Use versioned asset and shot layers;
avoid relying on a mutable HIP-only anonymous layer for delivery.

## Validation

Compare selected source objects to resulting USD prims: transforms, hierarchy,
geometry bounds, purpose, visibility, materials, lights, cameras, frame range,
and representative time samples. Use the Scene Graph Tree, Details, and Layers
panes to inspect composed values and authored opinions. At the Layer Break,
verify that the context still composes but is marked before the boundary; after
the first editing node, verify that the active layer contains only the intended
new opinions. Configure Layer with **Start New Layer** off must not be credited
with creating an isolation boundary. Turn on a node’s Debug flag only as a
bounded way to isolate its layer changes. Save and reopen USD, then verify
context layers were not accidentally copied into the authored layer and no
local-only absolute paths are required. These checks are documentation-backed
and static in this pack; they were not reproduced in a live Houdini 21 scene.

## Common failures and troubleshooting

- An empty Scene Import result usually means the Objects field is blank or its
  root/pattern excludes the intended nodes.
- Unexpected hierarchy or duplicate paths can result from object containment,
  input wiring, flattening, destination paths, or conflicting names.
- Playback cost can explode when topology is time sampled even though only
  positions change; choose Static or None only when the topology contract
  supports it.
- Missing shading data often comes from excluded attributes, incorrect owner or
  interpolation, failed UV-to-`st` conversion, or materials imported at an
  unexpected path.
- Downstream edits that miss an asset may target an unloaded payload, an
  instance proxy, or a stale prim path.
- A written layer that contains upstream context usually has no Layer Break
  before the new opinions, or the break is downstream of the edits that were
  meant to be delivered.
- An intended deliverable edit missing from disk often sits before the Layer
  Break and was therefore classified as context. Move the boundary upstream of
  that edit.
- Configure Layer did not isolate following edits: **Start New Layer** was off.
  Turning it on starts another active layer, but a Layer Break is still required
  when the output must discard all earlier layers.
- Lighting that works only inside the HIP may depend on an imported headlight,
  local texture, anonymous layer, or unsaved camera.

## When not to use

Do not Scene Import an entire Object-level scene merely because it exists. A
published asset should normally enter through USD composition, and a simple
SOP-only task need not enter Solaris. Do not flatten hierarchy or author all
topology as animated without a verified requirement.

## Houdini 21 notes

This card targets Houdini 21. Scene Import 2.0 predates H21, but current online
help may include later translation options. Confirm node parameters in installed
H21 help. H21 adds Solaris integration and path-expression improvements that
may aid collections and assignments; do not assume later file-plugin or node
features are present in every H21 production build. The Layer Break and
Configure Layer statements above are grounded in current SideFX documentation
and remain unverified in a live H21 build during this corpus rebuild.

## Official sources

- [Scene Import LOP](https://www.sidefx.com/docs/houdini/nodes/lop/sceneimport.html) - SideFX Object-to-USD translation reference; accessed 2026-07-26.
- [Layer Break LOP](https://www.sidefx.com/docs/houdini/nodes/lop/layerbreak.html) - SideFX definition of the active-layer break and USD-output exclusion behavior; accessed 2026-07-26.
- [Configure Layer LOP](https://www.sidefx.com/docs/houdini/nodes/lop/configurelayer.html) - SideFX metadata reference and **Start New Layer** behavior; accessed 2026-07-26.
- [How LOPs work](https://www.sidefx.com/docs/houdini/solaris/about_lops.html) - SideFX layer, payload, layout, and output guide; accessed 2026-07-26.
- [Writing out USD](https://www.sidefx.com/docs/houdini/solaris/output.html) - SideFX output workflow; accessed 2026-07-26.
- [Houdini 21 Solaris changes](https://www.sidefx.com/docs/houdini/news/21/solaris.html) - SideFX version notes; accessed 2026-07-26.
