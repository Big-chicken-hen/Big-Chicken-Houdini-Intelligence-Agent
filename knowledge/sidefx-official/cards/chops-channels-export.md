# CHOP channels, processing, and export workflow

Pack version: 2.0.0

## Use for

Use this workflow for procedural parameter motion, animation filtering, audio-driven effects, spring or lag response, cycle and sequence operations, imported channel cleanup, device input, and efficient transfer of sampled data between parameters, geometry, and CHOP networks. It is suitable when time-series processing is clearer than per-frame SOP or expression logic.

## Context and core data

A CHOP contains named channels, each an array of 32-bit floating-point samples. All channels in one CHOP share a sample rate and interval. Nodes fetch or generate channels, process them, and optionally export them to parameters. The Motion FX View displays samples but can itself trigger cooking. Geometry CHOP can read SOP attributes as channels; Channel SOP converts CHOP samples back to point positions or attributes.

## Recommended data flow

Use `File/Fetch Parameters/Geometry/Audio CHOP → scope and rename → filter/math/noise/lag/spring/sequence → Null or Stash → explicit parameter export, chop() sampling, or Channel SOP`. Place a final named Null before consumers. Use Stash only when the upstream motion must be frozen or a live dependency would create a cycle.

## Step-by-step workflow

1. Define the required time interval, sample rate, units, channel naming convention, and downstream destination.
2. Fetch or generate source channels and inspect their actual sample count and extend conditions.
3. Rename and scope channels before applying filters so patterns select only intended data.
4. Apply one class of operation at a time and compare bypassed versus processed curves.
5. Resample or stretch deliberately when inputs use different rates or durations.
6. Create a final Null or Stash and map channels to exact parameter paths or Channel SOP attribute components.
7. Test playback, subframes, out-of-range times, scene reload, and disabled export behavior.

## Critical parameters and attributes

Sample Rate controls temporal resolution independently of scene frames per second. Channel Range can use the full animation, current frame, or explicit start/end. Extend Left and Right determine values outside the interval. Scope patterns select channels by name. Export Prefix combines with channel names to derive parameter paths; explicit mappings override prefix export. Geometry CHOP's Animated mode may create one channel per attribute component per point, which can become very large.

## Cache, version, and performance

Most CHOPs are not frame-dependent even when they hold animation, because Houdini samples their stored curve rather than recooking its shape. CHOPs that read changing SOPs, external devices, or animated control inputs may recook each frame. Disable Motion FX graph updates when not inspecting curves. Stash breaks upstream dependency tracking and saves motion inside the HIP, so large stashes increase file size. Avoid unnecessarily high audio-rate sampling for slow control motion.

## Validation

Inspect channel labels, intervals, sample rates, minimum and maximum values, and discontinuities. Toggle bypass on each processing stage. Verify an exported parameter changes only when the final export flag is enabled. Test negative time, post-range time, and subframe playback to confirm extend behavior. For Geometry/Channel transfer, compare point count and component naming. Save and reopen the scene when using Stash or explicit export mappings.

## Common failures and troubleshooting

No export usually means a disabled export flag, incorrect scope, wrong channel name, or invalid parameter path. A manual channel mapping prevents Export Prefix behavior for that channel. Unexpected jitter can be aliasing from a low sample rate or rate conversion. Slow playback may come from a frame-dependent Geometry CHOP or an actively updating Motion FX View. Dependency cycles can occur when a parameter drives geometry that a CHOP reads and then exports back to that parameter.

## When not to use

Use ordinary keyframes for a small number of directly animated parameters. Use MotionClip or H21 Motion Mixer for whole-character clip editing. Use SOPs when the operation is fundamentally spatial geometry processing rather than a time series. Avoid CHOP exports when a readable parameter expression or direct node connection expresses the dependency more clearly.

## Houdini 21 notes

H21 rewrites Houdini's audio subsystem on FFmpeg, changes supported formats, and raises the cross-platform maximum sample rate to 48 kHz. FBX CHOP adds namespace-removal behavior aligned with KineFX import. The core channel representation and export workflow remain stable, but legacy object-level character uses of CHOPs should not replace the H21 APEX animation workflow without a reason.

## Official sources

- SideFX, [Channel nodes and CHOP networks](https://www.sidefx.com/docs/houdini/nodes/chop/index.html), accessed 2026-07-26.
- SideFX, [Channel and audio formats: CHOP internals](https://www.sidefx.com/docs/houdini/io/formats/channel_formats.html), accessed 2026-07-26.
- SideFX, [Export channels window](https://www.sidefx.com/docs/houdini/ref/windows/channelexport.html), accessed 2026-07-26.
- SideFX, [Houdini 21 APEX, KineFX, animation, and audio changes](https://www.sidefx.com/docs/houdini/news/21/kinefx.html), accessed 2026-07-26.
