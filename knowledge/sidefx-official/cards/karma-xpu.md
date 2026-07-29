# Karma XPU setup, compatibility, and render diagnosis

Pack version: 2.0.0

## Use for

Use this workflow when Karma XPU is the intended interactive or final render
delegate, when deciding between Karma CPU and XPU, or when a Solaris render
looks different between delegates. The goal is to prove that the stage,
materials, lights, devices, render settings, and requested outputs work in XPU
before spending time on quality. It is also appropriate for an explicit
compatibility review of an existing Karma scene.

## Context and core data

Karma consumes a USD stage in Solaris. The relevant data includes renderable
USD prims and purposes, MaterialX materials and bindings, lights, a camera,
Render Settings, Render Product and Render Var prims, texture and volume files,
device selection, and the final output. Karma CPU and Karma XPU share many
workflows but not identical feature support or performance characteristics.
XPU uses supported CPU and GPU devices; a device being present does not prove
that every material, volume, AOV, or light feature is supported.

For new Karma shading, use MaterialX rather than VEX-based Principled or legacy
Mantra material networks. Treat delegate selection as part of the design brief:
the renderer affects supported nodes, noise behavior, memory limits, and the
validation result.

The documented XPU matrix has two important boundaries. Displacement Style
supports MtlX Standard Surface and MtlX OpenPBR Surface with float/scalar
displacement; it is not evidence for arbitrary vector displacement or every
surface model. XPU USD volumes use supported Karma Volume, XPU Pyro Preview, or
Karma Whitewater shader paths rather than general MaterialX VDF/EDF volume
graphs.

## Recommended data flow

Use:

`composed USD stage -> MaterialX bindings and lights -> camera -> Karma Render
Settings -> Render Product and Render Vars -> XPU device/delegate selection ->
low-cost representative render -> message/support diagnosis -> bounded quality
tuning -> output-file validation`.

Establish correctness before quality. A black image, missing object, absent
material, or unsupported feature is not a sampling problem. Compare CPU only
when the comparison can isolate delegate support from changes in scene state.

## Step-by-step workflow

1. Record the exact Houdini build, operating system, intended Karma delegate,
   available XPU devices, target frame, camera, resolution, output format, and
   the visual or AOV evidence required for acceptance.
2. Inspect the composed USD stage. Verify payloads are loaded as intended,
   render purpose and visibility are correct, the camera is active, lights are
   present, and material bindings resolve to authored MaterialX outputs. Mark
   every displaced surface and volume prim for an explicit XPU support check.
3. Create or inspect Karma Render Settings and a Render Product with an explicit
   output path. Add only task-relevant Render Vars; begin with the beauty result
   and diagnostic outputs that can answer a specific question.
4. Select Karma XPU and render a low-resolution representative frame or region.
   Watch renderer messages for unsupported features, missing assets, device
   failures, texture problems, excessive memory, or shader compilation issues.
5. If the result is incomplete, isolate one layer at a time: geometry and
   purpose, camera, lighting, material binding, MaterialX support, volumes,
   displacement, AOV configuration, then device availability. Reduce XPU
   displacement to a float/scalar test on Standard Surface or OpenPBR, and
   reduce a volume to its supported Karma volume shader before raising samples.
6. When necessary, render the same bounded view with Karma CPU. Keep stage,
   camera, frame, color settings, and output intent constant. Use the difference
   to form a compatibility hypothesis, then confirm it against current SideFX
   support documentation or installed help.
7. After correctness is established, adjust sampling and quality controls
   according to the visible noise source. Judge convergence in representative
   highlights, shadows, transmission, volumes, motion, and displacement rather
   than relying only on elapsed time.
8. Inspect the file actually written, channel names, metadata, color result,
   resolution, frame token, and representative regions. Record remaining
   CPU/XPU differences instead of silently accepting them.

## Critical parameters and attributes

Confirm render delegate, device selection, camera path, frame, resolution,
pixel aspect, shutter and motion settings, purpose/visibility, light exposure,
material and texture paths, Render Product path/type, and Render Var source/type.
For displaced prims, record the MtlX Standard Surface or OpenPBR surface model,
float/scalar height signal, displacement bounds, scale, and visible silhouette
test. For volumes, record the USD field relationships and whether the material
uses Karma Volume, XPU Pyro Preview, or Karma Whitewater. Sampling controls must
match the actual noise source; more camera samples cannot repair an unsupported
shader or missing light. Check volume resolution and tiled texture residency
against each device's unpooled memory. Preserve object IDs, primvars, and
cryptomatte names when downstream compositing relies on them.

## Cache, version, and performance

Record the Houdini build and driver because XPU support evolves. Shader
compilation and texture loading can dominate the first render, so compare warm
and cold timings honestly. Reuse stable USD, geometry, texture, and simulation
caches rather than forcing upstream recooks during render diagnosis. Preserve
instancing and delayed payload loading. Large textures, dense volumes, deep
displacement, and unique geometry can exhaust device memory; measure the
actual bottleneck before reducing visual quality. A lower-resolution diagnostic
render is evidence only for the features it resolves.

## Validation

The support table in this card is a documented/static expectation, not a live
renderer result. Runtime validation requires XPU messages plus pixels or files.
Check the stage in the Scene Graph, verify an active camera and lights, inspect
material bindings, confirm the XPU delegate and devices, and read warnings.
For displacement, compare a bounded on/off render and inspect silhouette or
parallax; for volumes, confirm the intended fields drive a supported XPU shader
and produce density/emission evidence. Open the resulting image and confirm
beauty and requested AOVs exist with the expected dimensions and naming.
Compare a bounded CPU result only if it isolates a support difference. For
animation, sample frames that expose motion, topology, visibility, and cache
boundaries rather than assuming one frame generalizes.

## Common failures and troubleshooting

- Black frames commonly result from camera, visibility, purpose, lights,
  exposure, payloads, or output selection. Check those before sampling.
- Gray or fallback materials point to binding, texture, MaterialX output, or
  unsupported-node issues.
- AOV files with missing channels indicate Render Product/Render Var
  configuration or unsupported source data, not beauty-sample insufficiency.
- A displaced surface that works on CPU but stays flat on XPU may use vector
  displacement or a surface model outside the documented Standard
  Surface/OpenPBR float path.
- A CPU MaterialX VDF/EDF volume that vanishes on XPU is not validated by the
  CPU result; rebuild the bounded test with Karma Volume, XPU Pyro Preview, or
  Karma Whitewater as appropriate.
- CPU/XPU differences may also be precision, device memory, or another
  documented feature boundary. Reproduce with the smallest unchanged stage.
- Grain that decreases with samples is convergence noise; stable incorrect
  structure, color, or missing features requires scene diagnosis instead.
- A device disappearance or crash requires checking renderer messages and
  resources; do not indefinitely retry the same expensive render.

## When not to use

Do not force XPU when the user requires a CPU-only feature, an existing pipeline
mandates another renderer, or the task is only a simple parameter read. Do not
use CPU as a silent fallback after XPU failure; report the incompatibility and
obtain direction when the output contract would change.

## Houdini 21 notes

This card targets Houdini 21, but Karma XPU capability changes across production
builds. Confirm support using the installed H21 help and the active renderer,
especially for materials, volumes, AOVs, displacement, and devices. SideFX
current documentation may describe a later release. Do not infer H21 support
from a node merely existing in a later online help page. The matrix statements
above are grounded in SideFX documentation and remain unverified for a specific
H21 production build until that build emits clean renderer messages and the
required pixels or files.

## Official sources

- [Karma XPU](https://www.sidefx.com/docs/houdini/solaris/karma_xpu.html) — SideFX delegate and support documentation; accessed 2026-07-26.
- [Karma](https://www.sidefx.com/docs/houdini/solaris/karma.html) — SideFX Karma workflow guide; accessed 2026-07-26.
- [Creating materials for Karma](https://www.sidefx.com/docs/houdini/solaris/kug/materials.html) — SideFX Karma materials guide; accessed 2026-07-26.
- [Karma A Beautiful Game](https://www.sidefx.com/tutorials/karma-a-beautiful-game/) — SideFX Karma tutorial; accessed 2026-07-26.
