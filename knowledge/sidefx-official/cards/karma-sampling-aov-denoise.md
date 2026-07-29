# Karma sampling, AOV, denoising, and render-statistics workflow

Pack version: 2.0.0

## Use for

Use this workflow when a Karma render is noisy or slow, when a compositor needs
defined AOVs, when a denoised delivery must retain detail, or when a performance
claim needs renderer evidence. It turns beauty noise into a classified sampling
problem, creates only useful render vars, and validates both pixels and
statistics. It is not a preset of universally “good” sample values.

## Context and core data

Primary or pixel samples originate at the camera and resolve geometry edges,
small displacement, depth of field, and motion blur. Each primary hit can spawn
direct and indirect secondary rays for diffuse, reflection, refraction,
subsurface, and volumes. Primary samples therefore multiply secondary work.

Treat Karma CPU and XPU as separate sampling branches. The current SideFX
sampling guide documents CPU with Automatic or Path Traced secondary
convergence, while XPU supports Path Traced convergence only. On Karma Render
Settings, the top-level camera-ray control is labelled **Primary Samples** for
CPU parameters and **Path Traced Samples** for XPU parameters. Both can use the
Variance or Uniform pixel oracle for primary rays in the currently documented
workflow, but a CPU Automatic-convergence diagnosis cannot be copied directly
to XPU. These are documented/static facts from current online help, not a claim
that this pack live-tested every production build of H21.

AOVs are USD Render Var prims referenced by Render Products. They may record
beauty, lighting components, depth, normals, motion, primvars, cryptomatte,
shader-authored values, light-path expressions, or statistics. Useful
diagnostic channels include `cputime`, `primarysamples`, `indirectraycount`, and
direct/indirect component passes. Denoisers operate on an already sampled image;
they cannot restore structure that was never resolved.

## Recommended data flow

Use:

`record CPU or XPU -> representative noisy frame -> focused beauty and
diagnostic Render Vars -> classify camera-ray versus direct/indirect component
noise -> inspect sample/stat channels -> change the delegate-appropriate
control -> rerender the identical region -> compare time and detail -> apply
denoiser only after sufficient sampling -> inspect the written EXR`.

Keep the scene, camera, frame, crop, delegate, and color conditions constant
when testing one hypothesis. Do not change material, lighting, sampling, and
denoising simultaneously.

## Step-by-step workflow

1. Record Houdini build, Karma CPU/XPU delegate, camera, frame, resolution,
   crop, render time, hardware, light/material state, and a representative
   region where the problem is visible.
2. Create a Render Product with beauty and only the component/statistical AOVs
   needed to classify the issue. Include `primarysamples`, `cputime`, and
   relevant direct/indirect ray components rather than every available pass.
   In H21, when variance adaptation must protect a required AOV, open **Karma
   Render Settings ▸ Advanced ▸ Sampling ▸ Primary Samples** and add a
   space-separated list to **Planes**. Keep beauty `C` and the required plane
   together, for example `C foo`; otherwise the beauty plane may converge while
   `foo` remains undersampled.
3. Render a bounded baseline. Inspect beauty for jagged edges, small geometry,
   depth-of-field, and motion-blur noise, which implicate primary sampling.
   Inspect component AOVs to locate reflection, transmission, diffuse, volume,
   subsurface, or emissive noise.
4. Inspect `primarysamples`, `indirectraycount`, and time statistics. Decide
   whether the pixel oracle stopped too early, max secondary work is reached, a
   few objects dominate cost, or the render is blocked by a scene/support issue.
   Also verify that the expected AOV appears in the variance **Planes** list
   before compensating with globally higher sample counts.
5. Change one delegate-appropriate control. On CPU, first identify whether the
   render uses Automatic or Path Traced convergence: Automatic can vary direct
   and indirect work from a noise estimate, while Path Traced limits indirect
   work to one randomly selected direction per bounce. On XPU, tune Path Traced
   Samples and supported per-component/light quality controls; do not look for
   CPU Automatic convergence. For either delegate, use Uniform only when
   extreme DOF or motion makes variance testing overhead unhelpful.
6. Rerender the same region and compare noise, edge/detail retention, render
   time, sample distributions, and the beauty result. Stop increasing a control
   once another component becomes the limiting error.
7. Configure task-driven output AOVs. Confirm source type and name, data type,
   filter, precision, colorspace, LPE tags, cryptomatte identity, and Render
   Product ordering. Use material/global AOVs only when the required value
   cannot be obtained through a standard Render Var.
8. Apply OIDN, OptiX, an image filter, COP, or command-line denoising only after
   the baseline retains the needed texture, hair, motion, and highlight detail.
   Compare denoised and raw results at full display scale.
9. Render the requested frame range, then open the written multi-channel EXR.
   Validate frames, beauty, every required channel, channel order, precision,
   color handling, metadata, and representative temporal stability.

## Critical parameters and attributes

Start with the delegate-specific branch:

- **CPU:** Primary Samples; Variance or Uniform pixel oracle; Automatic or Path
  Traced convergence; Min/Max Secondary Samples and Secondary Noise Level only
  in the context where current help says they apply.
- **XPU:** Path Traced Samples; Variance or Uniform primary-ray oracle; Path
  Traced convergence only. Confirm XPU support for each AOV, filter, material,
  light, volume, or geometry feature before attributing a difference to noise.
- **H21 multi-plane variance:** Advanced ▸ Sampling ▸ Primary Samples ▸
  **Planes**. Include `C` plus each delivery-critical AOV whose variance must
  participate in the stopping decision.

For both branches, check component quality, ray limits, light-tree settings,
motion and DOF, volume quality, and displacement. For AOVs, check Render Var
path, source type (`raw`, `primvar`, `LPE`, or intrinsic/statistical), source
name, format/data type, sample blending/filter, precision, colorspace, ordered
vars, product path, EXR filename, and frame token. For denoising, check the
selected channels and whether albedo or normal guidance is required.

## Cache, version, and performance

Preserve the baseline settings and image for comparison. Render multiple frames
in one process when appropriate so USD composition, textures, and renderer
state need not reload for every frame. Primary samples multiply secondary work;
blindly increasing both can hide the true cost. More AOVs consume memory and
disk bandwidth, and high-precision or filtered data should be justified by a
consumer. First-render shader compilation and cold texture I/O should not be
confused with steady-state sampling cost. Version outputs so tests and approved
renders are not overwritten.

## Validation

Use matched crops or frames and compare raw beauty, component AOVs, sample/stat
passes, render time, and denoised result. The comparison record must include
delegate and convergence mode; never present a CPU/XPU comparison as a
single-control sampling test. For H21 multi-plane variance, compare the required
AOV with and without its name in **Planes**, confirm it is present in the
written EXR, and verify `C` is still listed.

Confirm visual detail rather than judging only an average noise metric. Open
final EXRs outside the live viewport and inspect channel names, types, values,
color interpretation, and frames. Verify a compositor can identify the intended
objects/materials and reconstruct or adjust the requested components. A noisy
isolated AOV is not automatically a failure if beauty and the downstream use
are correct.

## Common failures and troubleshooting

- Raising all samples together wastes time and prevents attribution. Use
  component AOVs to find the ray type or primary effect first.
- Beauty may converge while an important AOV does not; H21 variance behavior
  and output requirements must be configured and validated for that channel.
  Add the required name under Primary Samples ▸ Planes rather than only raising
  global samples.
- A CPU recipe that refers to Automatic convergence is invalid for XPU, which
  current SideFX help documents as Path Traced-only. Conversely, an XPU result
  does not prove CPU feature or AOV parity.
- A denoiser can erase texture, fine curves, contact shadows, or small
  highlights when input sampling is insufficient.
- Missing AOVs often result from a Render Var not referenced by the product,
  incorrect source/type, wrong global AOV material path, or unsupported delegate
  behavior.
- `Cd` imported through USD may be `displayColor`; reading the wrong primvar can
  produce an empty custom AOV.
- A slow first render may be compilation or asset loading, while a slow warm
  render may be sampling. Measure both conditions explicitly.
- True caustics, opacity/transmission maps, volumes, and very low roughness can
  require different scene or sampling decisions; no one sample preset fixes all.

## When not to use

Do not run this diagnosis for a correctness failure such as a black frame,
missing asset, broken binding, invalid output path, or unsupported shader.
Resolve scene and delegate errors first. Do not create AOVs without a consumer
or denoise a technically adequate image merely because the option exists.

## Houdini 21 notes

This card targets Houdini 21. Evidence status is explicit:

- **Change-note-supported for H21:** the H21 Karma page documents variance
  estimation across multiple planes and the Primary Samples ▸ Planes operation.
- **Documented/static:** the current sampling and Karma Render Settings pages
  document CPU Automatic/Path Traced versus XPU Path Traced-only convergence,
  the CPU **Primary Samples** label, and the XPU **Path Traced Samples** label.
  Current pages may describe a later production build, so confirm installed H21
  help before relying on an exact parameter or support boundary.
- **Live-verified:** none in this knowledge-pack build. No H21 GUI render,
  delegate comparison, or EXR inspection was executed.

## Official sources

- [Karma sampling](https://www.sidefx.com/docs/houdini/solaris/kug/sampling.html) - SideFX primary and secondary sampling guide; accessed 2026-07-26.
- [Reducing noise](https://www.sidefx.com/docs/houdini/solaris/kug/noise.html) - SideFX diagnostic AOV and denoising guidance; accessed 2026-07-26.
- [AOV workflows](https://www.sidefx.com/docs/houdini/solaris/support/aovs.html) - SideFX Render Var, material AOV, and EXR guide; accessed 2026-07-26.
- [Karma Render Settings LOP](https://www.sidefx.com/docs/houdini/nodes/lop/karmarendersettings.html) - SideFX CPU/XPU engine, sampling, AOV, and product parameter reference; accessed 2026-07-26.
- [Houdini 21 Karma changes](https://www.sidefx.com/docs/houdini/news/21/karma.html) - SideFX H21 sampling, AOV, and renderer notes; accessed 2026-07-26.
