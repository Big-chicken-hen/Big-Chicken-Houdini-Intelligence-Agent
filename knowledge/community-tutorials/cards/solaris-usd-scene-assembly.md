# Solaris USD scene assembly and lighting

Source kind: `community_tutorial`
Verification: `community_unverified`
Version: one source is marked H20.5; verify current LOP names, schemas, and renderer delegates.

## Use, prerequisites, and target

Use when assembling assets, variants, payloads, materials, lights, cameras, or render settings in Solaris. Inputs are geometry/assets with deliberate USD prim paths and known units/up axis. The target is a composed stage where each major opinion has a readable layer location, paths remain stable, and viewport/render results can be traced back to authored LOPs.

## Semantic network stages

Asset import/reference → path/kind organization → composition and variants → material library/binding → layout → lights/camera → render settings/products → USD/cache/export → `OUT_STAGE`. Use left-side branches for reusable material and light rigs, merging them downward into the stage trunk.

## Ordered workflow

1. Create or import the asset through `SOP Import LOP`, `Reference LOP`, `Payload LOP`, or an asset-reference workflow. Define the root prim path, default primitive, units, and up-axis policy. Avoid anonymous `/geo1`-style paths in publishable layers.
2. Organize stable namespaces such as `/World/Assets`, `/World/Looks`, `/World/Lights`, and `/World/Cameras` when compatible with the project. Set model `kind` and instanceability only when composition semantics support them.
3. Compose deliberately. Use sublayers for ordered layer stacks, references/payloads for asset arcs, variants for discrete alternatives, and inherits/specializes only with a documented reason. Inspect the Scene Graph Tree and Layer Stack after each arc.
4. Author transforms/layout with `Edit LOP`, `Transform LOP`, point instancing, or layout tools. Keep asset definition separate from shot overrides. Confirm xform op order and avoid writing broad opinions into a weak/unintended layer.
5. Build materials in `Material Library LOP`, assign stable material prim paths, and bind with `Assign Material LOP`/material linker. Check the resolved relationship on representative geometry prims.
6. Add lights and camera under named prim paths. Verify exposure/intensity units, transforms, and collection/light-link membership. Add render settings/products/vars appropriate to Karma or the chosen delegate.
7. Inspect composition: muted layers, unresolved assets, variant selections, payload load state, instance/prototype behavior, and strongest opinions. Use Scene Graph Details and layer inspection, not viewport appearance alone.
8. Export/cache through a deliberate USD ROP or layer-save boundary. Reopen the published stage in a clean branch and end at `OUT_STAGE`.

## Key responsibilities and fields

Prim paths are the primary identity contract. Composition arcs determine dependency/loading behavior; layer order determines opinion strength; material bindings are relationships; variants are authored selections, not filesystem naming tricks. Render settings name camera, products, and render vars. Exact schemas depend on active Houdini/USD versions.

## Data flow, cache, version, and performance

Payloads can defer heavy asset loading; references load their referenced content according to stage policy. Instanceable prims reduce memory but restrict unique descendant edits. Keep shot overrides in stronger, small layers instead of flattening everything. Cache/export only after asset resolver paths and layer dependencies are portable. A flattened stage can aid delivery but loses useful composition structure; retain the authored source layers.

## Common failures and repairs

- **Asset absent:** unresolved path, unloaded payload, wrong default prim, or muted layer; inspect composition diagnostics.
- **Edit has no effect:** a stronger layer overrides it or edit targets the wrong prim/layer.
- **Material missing:** assignment path/pattern does not resolve or material prim is outside expected namespace.
- **Instancing breaks overrides:** instanceable prototype cannot accept per-instance descendant edits; move variation to supported primvars/variants.
- **Render differs from viewport:** delegate, render settings, camera, lights, or MaterialX support differ; inspect bound settings and logs.

## Provenance boundary

Matt Estela's Solaris notes and Berika Lobzhanidze's scene/lighting workflow motivate stage inspection. The namespace, layer gates, and publish checks are original synthesis. Current USD/Houdini defaults remain unverified.

## Semantic expectations and verification checklist

- Presence: expected root, assets, materials, lights, camera, and render settings prims exist.
- Mapping: references/payloads resolve; material and light relationships target existing prims; render product names a valid camera/output.
- Composition: variant selections and strongest opinions are recorded; no unintended muted/unresolved layer.
- Cook evidence: the LOP trunk cooks without unresolved asset errors and Scene Graph counts are plausible.
- Cache/export evidence: reopened USD has the same required prim paths, layer dependencies, variants, and bindings.
- Visual evidence: representative asset, material, lighting, and camera match between the authored stage and clean reopened render test.

## Sources

- Matt Estela, [Lops and Solaris](https://www.tokeru.com/cgwiki/HoudiniLops.html).
- Berika Lobzhanidze, [Solaris Scene Creation/Lighting Workflow](https://www.sidefx.com/tutorials/solaris-scene-creationlighting-workflow-tutorial/).
