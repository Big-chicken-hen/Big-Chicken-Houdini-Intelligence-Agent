# MaterialX and Karma lookdev validation

Source kind: `community_tutorial`
Verification: `community_unverified`
Version: creator pages identify H19/H19.5; confirm node support and Karma CPU/XPU parity in the active release.

## Use, prerequisites, and target

Use for procedural MaterialX surfaces rendered with Karma. Inputs need geometry with known UVs/normals/primvars and a Solaris material-binding path. The target is a readable MaterialX graph, stable coordinate/range contracts, valid USD binding, and a render comparison that separates shader errors from lighting or renderer-support differences.

## Semantic network stages

Geometry primvars → `Material Library LOP` → MaterialX coordinate/pattern stage → value remap/masks → `MtlX Standard Surface` → displacement/normal branches → material output → `Assign Material LOP` → Karma render settings → evidence render.

## Ordered workflow

1. Audit geometry: normals, UV set/primvar name, scale, tangent needs, and material assignment groups. In Solaris, confirm the geometry prim path before building the shader.
2. Inside `Material Library LOP`, create a named MaterialX subnet/material. Add explicit texture-coordinate or position nodes and document coordinate space. For procedural 3D patterns, transform position to the intended object/world/reference space.
3. Build pattern stages with named nodes by responsibility, such as primary noise, secondary breakup, edge/curvature mask, and color ramp. Normalize/remap outputs before connecting them to physically meaningful inputs.
4. Configure `MtlX Standard Surface`: base color/weight, roughness, metalness, specular IOR, transmission, emission, coat, and opacity as required. Keep values in sensible ranges unless a documented artistic reason overrides them.
5. Add normal/bump and displacement separately. Normalize or remap height around a known midpoint, set scale in scene units, and ensure geometry/render settings provide enough tessellation for displacement.
6. Publish the material under a stable USD prim path and bind it with `Assign Material LOP`. Verify the resolved binding relationship on representative prims, including subsets.
7. Configure Karma render settings, camera, light rig, sampling, and CPU/XPU delegate. Render a neutral diagnostic frame with diffuse/specular response visible before using complex shot lighting.
8. Compare CPU/XPU or viewport/final behavior for nodes that may lack parity. Record unsupported/fallback nodes, then cache textures or simplify only when measured.

## Key responsibilities and fields

Coordinate nodes own spatial stability; pattern nodes own masks; remap/ramp nodes own ranges; Standard Surface owns energy/material response; normal/displacement nodes own geometric shading detail; LOP binding owns prim-to-material mapping; Karma settings own delegate and sample behavior.

## Executable parameter and connection contract

1. In `Material Library LOP`, create a `USD MaterialX Builder` (H20+) named by responsibility, for example `MAT_brushed_metal`. Keep `Material VOP = *` or an explicit builder path and set `Material Path Prefix = /World/Looks/`; the resulting material prim must be `/World/Looks/MAT_brushed_metal`.
2. For UV textures, connect `MtlX Texcoord` to the coordinate input of `MtlX Image`; name the geometry primvar explicitly when a non-default UV set is required. Mark base-color images as color data in the project color policy, while roughness, metalness, normal, height, and masks remain non-color data. For procedural 3D patterns, transform `MtlX Position` into a documented object/reference space before noise.
3. Remap every scalar mask before use. The contract for `base`, `metalness`, `specular_roughness`, `transmission`, `coat`, and `opacity` is finite and normally `0..1`. Start a dielectric reference with `metalness = 0`; start a metal reference with `metalness = 1` and derive reflection color from `base_color`. Values are diagnostic endpoints, not a demand for binary production materials.
4. Connect the pattern/ramp result to `MtlX Standard Surface.base_color`; connect scalar masks to their matching named inputs. Start with `base = 1`, `specular_roughness = 0.5`, and the node's existing `specular_IOR`; then sweep roughness through `0.1`, `0.5`, and `0.9` under the same light to prove the response.
5. Send tangent-space normal maps through `MtlX Normalmap` before the Standard Surface `normal` input; do not connect a `0..1` RGB normal image directly to a `-1..1` vector input. Keep bump and true displacement on separate branches. For displacement, document midpoint and `scale` in scene units and enable sufficient dicing/tessellation and displacement bounds on the geometry.
6. Connect Standard Surface `out` to the builder's surface output. In `Assign Material LOP`, set `Primitives` to a resolvable USD path/pattern and `Material Path` to the exact `/World/Looks/...` prim. Use a separate assignment node so material creation and binding can be inspected independently.
7. Render a neutral gray environment/key-fill-rim test with a fixed camera and recorded Karma delegate. Hold lighting and sampling constant while soloing base color, roughness, normal, and displacement contributions. Compare Karma CPU and XPU only after the binding and MaterialX validation pass.

## Data flow, cache, version, and performance

Procedural graphs can be recomputed per shading sample. Layer only complexity that changes the result, reuse masks, and consider baking stable heavy patterns through an explicit texture workflow. Displacement increases tessellation/memory; bump is cheaper but changes shading only. XPU support evolves, so a valid MaterialX graph can still differ by delegate. Never “fix” a binding failure by editing shader values.

## Common failures and repairs

- **Material is default gray:** inspect material prim generation and resolved USD binding, then shader output.
- **Texture swims:** coordinate space or object transform contract is wrong; use a stable reference/object space.
- **Roughness/metal response implausible:** mask range or color-space interpretation is wrong; inspect scalar values.
- **Black/unsupported XPU render:** isolate nodes and compare CPU; replace only confirmed unsupported operations.
- **Displacement clips/no effect:** scale, midpoint, bounds, or tessellation is insufficient.

## Checkpoints and observable evidence

- **M0 — geometry:** the target USD prim path exists and reports the expected UV/normal/primvar names; UV diagnostic color is stable under object transforms.
- **M1 — graph types:** MaterialX connections have compatible scalar/color/vector types. Scalar probes for roughness/metal/masks are finite in `0..1`, and the normal branch passes through `MtlX Normalmap`.
- **M2 — binding:** Scene Graph Details shows the material prim at `/World/Looks/...` and a resolved material-binding relationship on every representative geometry/subset; there are zero unintended default-gray prims.
- **M3 — response isolation:** the roughness sweep visibly broadens reflection without changing base-color texture coordinates; a normal-only comparison changes shading but not silhouette; displacement changes silhouette/bounds when enabled.
- **M4 — delegate evidence:** fixed-camera CPU/XPU renders record renderer, samples, time, and image difference. Unsupported-node conclusions require an isolated failing node or log, not merely a different noisy image.

## When not to use this workflow

Do not use this procedural graph when a validated texture set already provides the required look more cheaply, when the target renderer cannot consume the chosen MaterialX nodes, or when a USD Preview Surface is the only delivery contract. Prefer bump over displacement when silhouette/parallax is irrelevant, and do not use shader changes to compensate for missing lights, bad normals, or an unresolved material binding.

## Provenance boundary

Rohan Dalvi's procedural MaterialX tutorial and Moeen Sayed's Karma project motivate graph-first lookdev. The staged responsibilities, neutral-light test, and parity checks are original synthesis; no paid or tutorial graph is reproduced.

## Semantic expectations and verification checklist

- Presence: material prim, shader outputs, required geometry primvars, render settings, and camera exist.
- Mapping: UV/primvar readers name existing fields; material binding resolves on intended geometry/subsets.
- Range: roughness/metalness/masks are finite and normally `[0,1]`; displacement scale is documented in scene units.
- Cook evidence: LOP/material graph cooks without invalid MaterialX connections or unresolved binding.
- Render evidence: neutral diagnostic render shows base, specular, roughness, normal, and displacement contributions separately.
- Delegate evidence: CPU/XPU differences are recorded and unsupported nodes identified rather than guessed.

## Sources

- Rohan Dalvi, [Extreme Hardcore Procedural Texturing in Houdini using MaterialX](https://www.sidefx.com/tutorials/extreme-hardcore-procedural-texturing-in-houdini-using-materialx/).
- Moeen Sayed, [Karma | A Beautiful Game](https://www.sidefx.com/tutorials/karma-a-beautiful-game/).
