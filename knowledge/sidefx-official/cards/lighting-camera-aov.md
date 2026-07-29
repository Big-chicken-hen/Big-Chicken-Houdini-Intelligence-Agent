# Solaris lighting, camera, render product, and AOV workflow

Pack version: 2.0.0

## Use for

Use this workflow when lighting a Karma shot, establishing a render camera,
building render products and AOVs, or diagnosing a render whose composition,
illumination, channels, or output files are wrong. It treats lighting, camera,
and outputs as one evidence-producing system while keeping their responsibilities
separate. Use the MaterialX and Karma XPU cards for deeper shader or delegate
work.

## Context and core data

The working context is a composed USD stage in Solaris. Author or edit a USD
camera with Camera LOP and USD lights with Light LOP. Camera LOP owns framing,
lens, aperture, focus, shutter, and camera exposure. Light LOP owns type, shape,
size, intensity, exposure, color, normalization, shadowing, and links.

Keep the USD render relationship explicit. A `RenderSettings` prim identifies
the camera and references one or more `RenderProduct` prims. Each
`RenderProduct` represents an output file or buffer, may override the camera,
and holds an ordered relationship to `RenderVar` prims. Each `RenderVar`
describes one computed channel: its output name, data type, source type, source
name, and sample blending. A Render Var that exists on the stage but is not
ordered by the selected product is not part of that product. Karma Render
Settings LOP can author all three kinds of prim, while Karma Render Products
LOP is useful when multiple products share common settings or use different
cameras/output destinations.

The beauty image should communicate form and material. Diagnostic AOVs should
answer concrete downstream or troubleshooting needs; adding every available
channel increases I/O and ambiguity without improving evidence.

## Recommended data flow

Use:

`composed shot stage -> Camera LOP -> purpose-driven Light LOPs -> verify
camera/light USD prims -> Karma Render Settings -> RenderSettings relation to
RenderProduct -> product orderedVars relation to focused RenderVars -> bounded
Karma render -> inspect pixels, relationships, and files`.

Keep camera, lights, and render settings in clear Solaris layers or branches.
For new Karma work, carry MaterialX materials through Material Library LOPs.
Do not compensate for wrong material identity with arbitrary lights, or repair
bad framing by deforming the asset.

## Step-by-step workflow

1. Record the delivery resolution, aspect ratio, frame range, FPS, color
   pipeline, target Karma delegate, output format, required channels, and the
   representative view or frames that must prove the result.
2. Choose or create the camera with Camera LOP. Set the camera prim path,
   transform, focal length, aperture, clipping, focus distance, f-stop, and
   shutter from the visual goal. Camera **Exposure** is log base 2: `+1`
   doubles image-plane intensity and `-1` halves it. Preserve the user’s
   existing camera unless a new camera is part of the request.
3. Establish one Light LOP purpose at a time: reveal primary form, create
   readable reflections, separate depth, or motivate an emitter. Light
   **Exposure** multiplies power by `2^exposure`; **Intensity** is the linear
   factor. Decide **Normalize Power** intentionally: when enabled it divides
   intensity-scaled power by emitting surface area or angular size, so resizing
   a light changes shadow/highlight geometry without automatically changing its
   total normalized power. When disabled, size and emitted power remain coupled.
4. Inspect material response under the chosen environment. Adjust geometry,
   normals, material roughness/IOR, or lights according to the diagnosed cause;
   do not use light intensity to conceal a broken binding or unsupported shader.
5. Create Karma Render Settings with explicit `RenderSettings` prim path,
   camera path, resolution, delegate, frame token, and output. Inspect the
   Scene Graph Details: the selected `RenderSettings` prim must reference the
   intended product. If multiple deliverables or cameras are needed, author
   products with Karma Render Products rather than treating filenames as
   implicit renderer state.
6. Add only required Render Vars. For each product, inspect its ordered Render
   Vars and confirm every target `RenderVar` prim has the intended output name,
   data type, source type, source name, and sample blending. Include beauty plus
   only channels needed for compositing, identification, or diagnosis, such as
   depth, normals, motion, emission, cryptomatte, or a shader AOV. Do not assume
   a Render Var is rendered merely because it exists under `/Render`.
7. Render a bounded representative frame or region. Inspect clipping, focus,
   composition, highlights, shadow shape, exposure, noise, motion, materials,
   AOV channel names, and renderer warnings before raising quality.
8. Render or export the requested range and inspect actual files on disk. Check
   frame coverage, dimensions, channels, data windows, color handling, metadata,
   output location, and representative early/middle/late frames.

## Critical parameters and attributes

For Camera LOP, check prim path, transform, focal length, horizontal/vertical
aperture, near/far clipping, focus distance, f-stop, shutter interval, and
log-base-2 Exposure. Resolution and aspect conform policy belong to render
settings, even though they must agree with camera aperture.

For Light LOP, check prim path/type, transform, width/height/radius/angle,
Intensity, Exposure (`2^x` multiplier), Normalize Power, color or temperature,
single-sided emission, shadowing, visibility, collection/linking, and LPE tag.
Normalize Power is a power-versus-size policy, not an automatic “physically
correct” switch; record the chosen behavior before resizing emitters.

For output, check the selected `RenderSettings` prim, its camera and product
relationships, each product’s file destination and ordered Render Vars, and
each `RenderVar` source name/type, data type, sample blending, and channel name.
Render Product camera overrides are deliberate per-product exceptions. Motion
AOVs also depend on shutter and geometry time samples.

## Cache, version, and performance

Record Houdini build, delegate, camera and render-settings prim paths, and output
version. Reuse stable geometry, simulation, USD, and texture caches so lighting
iterations do not recook unrelated work. Light count, shadowing, volumes,
motion blur, depth of field, high-resolution textures, and AOV count affect
cost. EXR channels increase memory and disk I/O. Tune samples only after
separating convergence noise from stage, material, or output errors. Never
overwrite an approved delivery without an explicit versioning decision.

## Validation

Validate the authored USD relationships before judging beauty. In Scene Graph
Details, confirm Camera and Light prim paths; the selected `RenderSettings`
camera and products relationships; each `RenderProduct` product name, optional
camera override, and ordered vars; and each `RenderVar` name/source/type. Then
open the written output and verify its filename, dimensions, beauty, and exact
requested channels. A missing channel with a valid Render Var usually means the
product relationship or selected RenderSettings prim is wrong.

For light behavior, resize a representative area light while holding Intensity
and Exposure constant: with Normalize Power enabled, overall emitted power
should remain normalized while highlight/shadow shape changes; with it disabled,
the size change can alter total output. For exposure, a controlled `0 -> +1`
change should double the relevant camera or light energy according to the
parameter being tested. These are reproducible validation procedures, not
claims that this pack executed them in H21.

## Common failures and troubleshooting

- A black render can be a wrong camera, absent lights, exposure, visibility,
  unloaded payload, render purpose, or selected product. Diagnose before adding
  samples.
- Clipped or distorted framing points to aperture, focal length, aspect ratio,
  camera transform, or clipping planes.
- Flat material response may be insufficient reflection structure, incorrect
  roughness/IOR, missing normals, or geometry without useful bevels.
- Missing AOVs usually indicate an unreferenced Render Var, wrong source name or
  type, unsupported delegate feature, or inspecting the wrong product/file.
  Trace `RenderSettings -> RenderProduct -> ordered RenderVars`; folder
  placement under `/Render` alone does not create the relationship.
- Unexpected brightness after resizing an area light often comes from an
  unrecorded Normalize Power choice. Unexpected two-stop changes often come from
  treating logarithmic Exposure as a linear multiplier.
- Empty motion data can result from absent time samples, shutter configuration,
  topology changes, or the wrong channel convention.
- Cryptomatte problems often trace to identifiers, names, product/channel
  configuration, or downstream interpretation rather than image sampling.
- Output collisions follow missing frame/version tokens or multiple products
  writing the same path.

## When not to use

Do not build a lighting and AOV pipeline for a simple parameter read, an
unrendered geometry-only deliverable, or a direct color change that needs no
visual proof. Do not add channels without a consumer or diagnostic question.
Do not permanently alter the user’s camera, viewport, or lights during a
read-only review.

## Houdini 21 notes

This card targets Houdini 21 and modern Solaris/Karma workflows. Evidence status
is explicit:

- **Documented/static:** current SideFX Camera, Light, Karma Render Settings,
  Karma Render Products, and Render Var pages support the parameter semantics
  and USD relationships summarized here. Those online pages may represent a
  later build than H21.
- **Change-note-supported for H21:** renderer changes should be claimed only
  where the H21 Karma change page says so; this card does not reinterpret
  current-page additions as H21 additions.
- **Live-verified:** none in this knowledge-pack build. Confirm installed H21
  node help and run the relationship, exposure, normalization, and file checks
  before claiming a production shot verified.

Prefer MaterialX and USD-native Render Settings/Product/Var authoring for new
Karma work. Legacy Mantra output and `/mat` workflows are compatibility routes
only when explicitly required.

## Official sources

- [Solaris and Karma](https://www.sidefx.com/docs/houdini/solaris/index.html) — SideFX scene, light, camera, and render workflow index; accessed 2026-07-26.
- [Camera LOP](https://www.sidefx.com/docs/houdini/nodes/lop/camera.html) - SideFX camera, optics, shutter, and log-base-2 exposure reference; accessed 2026-07-26.
- [Light LOP](https://www.sidefx.com/docs/houdini/nodes/lop/light.html) - SideFX light intensity, exposure, Normalize Power, shaping, and linking reference; accessed 2026-07-26.
- [Karma Render Settings LOP](https://www.sidefx.com/docs/houdini/nodes/lop/karmarendersettings.html) - SideFX RenderSettings, product, Render Var, camera, delegate, and output reference; accessed 2026-07-26.
- [Karma Render Products LOP](https://www.sidefx.com/docs/houdini/nodes/lop/karmarenderproducts.html) - SideFX multi-product, ordered Render Vars, camera, and output reference; accessed 2026-07-26.
- [Render Var LOP](https://www.sidefx.com/docs/houdini/nodes/lop/rendervar.html) — SideFX AOV authoring reference; accessed 2026-07-26.
- [Karma AOV 2.0](https://www.sidefx.com/docs/houdini/nodes/vop/kma_aov-2.0.html) — SideFX shader AOV reference; accessed 2026-07-26.
- [Cryptomatte](https://www.sidefx.com/docs/houdini/render/cryptomatte.html) — SideFX identification-matte guide; accessed 2026-07-26.
- [Intro to Look Development and Lighting](https://www.sidefx.com/tutorials/intro-to-look-development-and-lighting/) — SideFX tutorial; accessed 2026-07-26.
