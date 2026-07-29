# Solaris Component Builder, variants, and payload delivery

Pack version: 2.0.0

## Use for

Use this workflow when a reusable SOP-built asset must become a structured USD
component with render geometry, optional display or simulation proxies,
materials, intentional variants, a payload, and a versioned file set. It is for
asset publishing and reuse, not arbitrary shot assembly. The result should be a
single meaningful root component that can be referenced or instanced in other
USD stages without exposing unstable SOP internals.

## Context and core data

SideFX Component Builder creates a Solaris network around Component Geometry,
Material Library, Component Material, and Component Output LOPs. Optional
Component Geometry Variants nodes combine alternate geometry branches. The
output uses a USD component-kind root with geometry and material scopes, a
class prim for downstream overrides, and separate layers for the main file,
payload, geometry, materials, and optional extras. Component Geometry can expose
default render geometry, a lighter proxy purpose, and a simulation proxy.
Named SOP primitive groups can become USD `GeomSubset` prims for material
binding.

The component root path, model kind, asset identifier, variant set names,
default selections, purpose, prim paths, material paths, proxy contracts, and
output version are public interfaces. Treat them as deliberately as an HDA
interface.

## Recommended data flow

Use:

`stable SOP output and semantic groups -> Component Geometry render/proxy/
simproxy outputs -> optional Component Geometry Variants -> Material Library
with MaterialX -> Component Material bindings -> optional authored extras ->
Component Output -> versioned main USD plus payload/geometry/material layers ->
clean-stage reference and variant validation`.

The main component file should remain lightweight and compose the heavier data
through the generated payload structure. Author variants only for supported,
named alternatives. A work-in-progress history is not a variant set.

## Step-by-step workflow

1. Define one root component identity, real-world scale, up axis, stable prim
   hierarchy, render/proxy requirements, material subsets, supported variants,
   output version, and downstream validation evidence.
2. Create the Component Builder network snippet in a LOP network and inspect its
   generated nodes. Rename the output and root prim meaningfully rather than
   accepting a temporary node name as the published asset identity.
3. Inside Component Geometry, bring in the reviewed SOP result. Connect render
   geometry to the default output and create cheaper proxy or convex simulation
   proxy outputs only where downstream use justifies them. Validate bounds and
   orientation across all purposes.
4. Promote only necessary primitive groups to subset groups. Confirm the
   resulting `GeomSubset` paths and membership; do not rely on element numbers
   that can change when upstream topology changes.
5. If alternatives are required, create one Component Geometry branch per
   intentional geometry variant and combine them through Component Geometry
   Variants. Give the variant set and each selection stable semantic names.
6. Author new Karma materials through a Material Library with MaterialX, or
   reference a shared material library at the expected material scope. Bind
   the component or its subsets through Component Material, and create material
   variants only when they are an actual supported choice.
7. Configure Component Output metadata, default variant selections, root path,
   payload/proxy options, save location, and version. Keep extra overrides
   limited to asset-owned information that belongs in the generated extras
   layer.
8. Save the component file set, inspect the generated main, payload, geometry,
   material, variant, and thumbnail files, and verify their dependencies and
   relative or resolver-managed paths.
9. Reference the main component file into a clean stage. Test default and
   non-default variants, loaded and unloaded payload states, instanceability,
   material bindings, proxy/render purposes, bounds, and one representative
   Karma render.

## Critical parameters and attributes

Check Component Output root prim, model kind, asset name and identifier, default
variants, output location and version, and payload behavior. In Component
Geometry, check source mode, default/proxy/simproxy outputs, subset groups,
purpose, topology and attribute translation. For variants, check variant set
name, selection names, default selection, and whether each branch preserves the
public prim hierarchy. For materials, check material path, `GeomSubset`
membership, binding strength, and render context. Check meters per unit, up
axis, `extent`, `kind`, purpose, and any public primvars used downstream.

## Cache, version, and performance

Component Builder separates the heavy geometry into a payload and preserves a
bounding box so unloaded assets remain identifiable. Keep render geometry out
of the lightweight proxy. Preserve instancing by avoiding unique opinions on
every reference. Version the containing directory or main component contract
without casually changing root paths or variant names. Cache expensive SOP
generation before publishing, then confirm the component was rebuilt from the
intended cache version. Large numbers of variants multiply files and testing
cost; publish only choices with a real consumer.

## Validation

Inspect the component in the Scene Graph Tree, Layers, and Details panes. Confirm
one root component, expected `geo` and `mtl` scopes, class inheritance, purpose
branches, subsets, bindings, variants, payload arc, default prim, and bounds.
Open the main file in a clean process or stage. Unload and reload its payload,
switch every supported variant, display proxy and render purposes, create more
than one reference or instance, and render a representative view. Validate the
files on disk rather than only the in-memory LOP stage.

## Common failures and troubleshooting

- A component that cannot be referenced cleanly often has no single root prim,
  an unstable root path, or an incorrect default prim.
- Missing faces or materials usually trace to changing SOP group membership,
  absent `GeomSubset` generation, wrong material paths, or incompatible variant
  hierarchies.
- An unloaded asset without useful bounds has a broken extent/proxy contract.
- A variant that changes unrelated paths makes downstream overrides fragile;
  keep the public hierarchy compatible or document the incompatibility.
- Instances that become unique may have per-instance overrides or material
  bindings authored at the wrong location.
- Old files appearing after republish indicate output-version or dependency
  paths still resolve to a previous component directory.
- A thumbnail is not delivery validation; it may use a different proxy,
  delegate, camera, or lighting context from the target.

## When not to use

Do not wrap one disposable SOP result in a full component package when the user
only needs a geometry cache. Do not use Component Builder to encode a whole
shot or nested assembly as a leaf component. Do not create variants for
intermediate experiments, frame history, or unsupported combinations.

## Houdini 21 notes

This card targets Houdini 21. Current online Component Builder help may describe
later Houdini behavior, so confirm generated node versions and output structure
with installed H21 help. The core H21 workflow remains Component Geometry,
Material Library/Component Material, Component Output, payload-based geometry,
and optional geometry/material variants. For new Karma materials, replace the
simple preview-only material examples in older tutorials with the project’s
modern MaterialX/Karma policy while retaining an appropriate preview output.

## Official sources

- [Component Builder](https://www.sidefx.com/docs/houdini/solaris/component_builder.html) - SideFX component authoring and output workflow; accessed 2026-07-26.
- [USD in Solaris](https://www.sidefx.com/docs/houdini/solaris/usd.html) - SideFX composition concepts; accessed 2026-07-26.
- [Writing out USD](https://www.sidefx.com/docs/houdini/solaris/output.html) - SideFX layer and dependency output guidance; accessed 2026-07-26.
- [Houdini 21 Solaris changes](https://www.sidefx.com/docs/houdini/news/21/solaris.html) - SideFX version notes; accessed 2026-07-26.
