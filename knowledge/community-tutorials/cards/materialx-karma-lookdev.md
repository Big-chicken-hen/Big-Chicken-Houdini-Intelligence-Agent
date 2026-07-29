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

## Data flow, cache, version, and performance

Procedural graphs can be recomputed per shading sample. Layer only complexity that changes the result, reuse masks, and consider baking stable heavy patterns through an explicit texture workflow. Displacement increases tessellation/memory; bump is cheaper but changes shading only. XPU support evolves, so a valid MaterialX graph can still differ by delegate. Never “fix” a binding failure by editing shader values.

## Common failures and repairs

- **Material is default gray:** inspect material prim generation and resolved USD binding, then shader output.
- **Texture swims:** coordinate space or object transform contract is wrong; use a stable reference/object space.
- **Roughness/metal response implausible:** mask range or color-space interpretation is wrong; inspect scalar values.
- **Black/unsupported XPU render:** isolate nodes and compare CPU; replace only confirmed unsupported operations.
- **Displacement clips/no effect:** scale, midpoint, bounds, or tessellation is insufficient.

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
