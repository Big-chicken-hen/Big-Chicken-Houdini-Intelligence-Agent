# MaterialX layers, displacement, and volume materials for Karma

Pack version: 2.0.0

## Use for

Use this workflow when a Karma material requires physically motivated surface
layers, true displacement, emission, or volume scattering/absorption rather
than a single standard-surface response. It covers three related but distinct
deliveries: layered surface identity, a surface plus displacement terminal, and
a volume material bound to volume primitives. Choose only the branch the visual
goal requires; a complex graph is not inherently a better material.

## Context and core data

MaterialX surface materials combine BSDF, EDF, opacity, and optional
displacement through a surface-material output. Standard Surface or OpenPBR can
provide the base optical identity. Layer, Add, Multiply, or Mix operations
combine physically meaningful responses or signals, but each operation must
have an explainable mask and energy intent. Displacement consumes a scalar
height or vector signal and changes rendered geometry at a scene-scale
amplitude. Normal and bump modify shading response without changing silhouette.

On Karma CPU, a MaterialX volume can use VDF and EDF behavior with fields such
as density, temperature, flame, scatter, absorption, or emission, then bind a
volume-material output to a VDB or USD volume prim. This generic MaterialX
VDF/EDF route is not the documented XPU volume path. XPU volume work must use a
supported Karma Volume, XPU Pyro Preview, or Karma Whitewater shader path.
Karma does not support a Surface and Volume material on the same prim, so
create deliberate prim and binding boundaries.

## Recommended data flow

For layered surfaces:

`coordinates and semantic masks -> base substrate -> coat/deposit/wet or worn
layer branches -> physically justified layer/mix -> surface output`.

For relief:

`height source at known scale -> independent bump/normal preview lane -> bounded
displacement lane -> displacement output -> dicing/bounds validation`.

For Karma CPU volumes:

`cached named volume fields -> USD volume prim and field relationships ->
MaterialX geometry-property readers -> scatter/absorption/emission model ->
volume output -> Karma volume render validation`.

For Karma XPU volumes:

`cached named volume fields -> USD volume prim and field relationships ->
supported Karma Volume, XPU Pyro Preview, or Karma Whitewater path -> XPU
renderer messages and representative volume pixels`.

Keep the main graph vertical and branches horizontal. Share masks only when the
same physical cause should affect multiple channels. Do not use a subnet or
network box to hide tangled wiring.

## Step-by-step workflow

1. Write a short material model: substrate, layers, condition, recognition cues,
   macro/meso/micro scales, available coordinates or fields, Karma CPU/XPU
   target, and the image evidence that must reveal each substantial branch.
2. Establish the base surface identity first. Validate base color, dielectric
   or metallic behavior, IOR, roughness, transmission, subsurface, and emission
   as required before adding dirt, moisture, wear, or relief.
3. Build each surface layer from a defensible spatial mask: contact, edge,
   cavity, gravity, drainage, height, proximity, painted primvar, material ID,
   or authored texture. Add noise only as variation within that logic.
4. Combine layers with a MaterialX operation appropriate to the optical model.
   Check that the mask range, order, and layer energy produce the intended
   response at grazing and frontal angles.
5. For normal or bump, use the correct data signature and tangent/coordinate
   basis. For displacement, keep a separately named height lane with amplitude
   in scene units; do not reuse arbitrary color contrast as height. On XPU, use
   the documented float/scalar displacement route with MtlX Standard Surface or
   MtlX OpenPBR Surface. Treat vector displacement or displacement driven
   through another surface model as unsupported until the target delegate and
   production build prove otherwise.
6. Validate relief at a low displacement amplitude under grazing light. Check
   silhouette, cracks, tessellation, bounds, shading normals, and whether bump
   alone is sufficient before increasing render cost.
7. For a volume, inspect the actual cached field names, transforms, voxel size,
   ranges, and USD field relationships. On CPU, a separate MaterialX VDF/EDF
   volume material may map those fields to scatter, absorption, and emission.
   On XPU, choose the task-appropriate supported Karma Volume, XPU Pyro Preview,
   or Karma Whitewater route instead of assuming a general MaterialX VDF/EDF
   graph will render.
8. Bind surface and volume materials to their correct, separate prims. Confirm
   material paths, render contexts, delegate support, and purpose/visibility in
   the composed stage.
9. Render a bounded diagnostic result, then the task’s representative camera.
   Remove or bypass branches whose isolated effect is not visible or whose cost
   does not serve the target.

## Critical parameters and attributes

For surfaces, inspect base weight/color, metalness, specular IOR and roughness,
coat/fuzz, transmission, thin-walled state, subsurface, opacity, emission, layer
mask, mix mode, normal/bump strength, displacement scale, and material outputs.
Check `st`/`uv`, position, normal/tangent, object scale, and mask primvar
type/interpolation. For XPU displacement, confirm the surface model is MtlX
Standard Surface or MtlX OpenPBR Surface and the displacement signal is
float/scalar; node existence is not evidence for vector-displacement support.
For volumes, check field names, density and temperature ranges,
scatter/absorption coefficients, anisotropy, emission color/intensity, volume
transform, voxel size, material binding, step size or quality controls, and the
intended CPU/XPU shader path.

## Cache, version, and performance

Record the MaterialX definitions, Houdini build, delegate, texture versions,
volume-cache version, and field list. Excessive unique shade prims, high
resolution textures, deep displacement, opacity, layered transmission, and
dense volumes can be expensive. Use a simplified H21 viewport output for a very
complex path-traced network without changing final Karma semantics. Cache
volumes after simulation/post-process and validate the reloaded fields.
Displacement cost should be justified by silhouette or parallax evidence; use
normal/bump for sub-pixel relief.

## Validation

Solo or neutralize each substantial branch and state its visual role, position
logic, scale, affected channels, and validation view. Under diagnostic lighting,
check highlight breakup, layer boundaries, roughness, transmission, normal and
displacement scale, and energy balance. For XPU displacement, require both an
absence of support warnings and visible silhouette or parallax change from the
documented scalar path. For volumes, inspect fields separately, verify the
delegate-specific shader choice, then validate density silhouette, internal
scattering, emission, shadowing, temporal stability, and bounds. A CPU-only
VDF/EDF result does not validate XPU. These are documented/static support
expectations until renderer messages and pixels from the target build confirm
them; a valid graph or error-free cook alone is not visual completion.

## Common failures and troubleshooting

- Layer masks with no spatial cause create uniform grunge; replace them with
  authored geometric, environmental, or painted evidence.
- Reusing one noise for color, roughness, normal, and displacement creates
  synthetic correlation unless one physical process explains all four.
- Black seams or energy gain can result from an inappropriate combine
  operation, unbounded mask, inverted normals, or overlapping dielectric logic.
- Displacement cracks or missing silhouette may indicate UV discontinuity,
  wrong signature, insufficient dicing, incorrect bounds, or scale mismatch.
- XPU displacement may disappear when a vector signal or an unsupported
  surface model is used; reduce the test to documented float/scalar
  displacement on Standard Surface or OpenPBR before diagnosing dicing.
- A volume that renders empty may have wrong field paths/names, no volume
  material output, missing material binding, zero density, or unloaded cache.
- A general MaterialX VDF/EDF volume that works on CPU can still be unsupported
  on XPU; replace it with the appropriate supported Karma volume shader rather
  than treating higher samples as a fix.
- A surface/volume combination on one prim violates the supported material
  boundary; split the prims and bind separately.
- Slow renders may originate in opacity, nested transmission, high-frequency
  displacement, dense fields, or step size rather than sample count alone.

## When not to use

Do not add layers or displacement to a simple material whose recognition cues
come from base color and roughness alone. Do not use displacement where
normal/bump supplies the required evidence. Do not create a volume shader for
surface geometry or use MaterialX light shaders, which Karma does not support.
Do not generalize a CPU MaterialX volume or vector-displacement test into an
XPU compatibility claim.

## Houdini 21 notes

This card targets Houdini 21. H21 adds modern Karma/MaterialX capabilities,
including updated standard models and viewport support, but exact nodes and XPU
support vary by production build. The scalar Standard Surface/OpenPBR
displacement boundary and specialized XPU volume routes recorded here come
from SideFX's documented support matrix; this pack did not live-render them.
Confirm the installed H21 help, renderer messages, and representative pixels.
Current online docs may show H22 capabilities; do not backport them silently.

## Official sources

- [Using MaterialX in Solaris](https://www.sidefx.com/docs/houdini/solaris/materialx.html) - SideFX surface, displacement, volume, texture, and primvar guide; accessed 2026-07-26.
- [Karma materials](https://www.sidefx.com/docs/houdini/solaris/kug/materials.html) - SideFX bindings and material behavior guide; accessed 2026-07-26.
- [Karma XPU](https://www.sidefx.com/docs/houdini/solaris/karma_xpu.html) - SideFX delegate support reference; accessed 2026-07-26.
- [Houdini 21 Karma changes](https://www.sidefx.com/docs/houdini/news/21/karma.html) - SideFX version notes; accessed 2026-07-26.
