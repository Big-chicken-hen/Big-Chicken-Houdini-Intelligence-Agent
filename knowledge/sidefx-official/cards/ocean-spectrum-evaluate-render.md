# Ocean spectrum, evaluation, and render workflow

Pack version: 2.0.0

## Use for

Use this workflow for procedural ocean surfaces, layered wind-wave spectra, guided or tank-based local interaction, extended surfaces to the horizon, and Karma ocean rendering. It separates inexpensive analytic wave motion from the costly FLIP region so only interactions that require fluid dynamics are simulated.

## Context and core data

Ocean Spectrum creates `phase`, `frequency`, and `amplitude` volumes describing
waves from wind and depth. Ocean Evaluate deforms geometry from those volumes,
while render-time ocean sampling can displace a broad surface without dense
geometry. Multiple spectra can be merged. Contribution or suppression masks
modulate local amplitude. A guided ocean layer or tank supplies a bounded FLIP
region when local interaction requires simulation. In Houdini 21, the Karma
Ocean LOP renders this ocean with Karma CPU; it is not an XPU render route.

## Recommended data flow

Use `Ocean Spectrum layers → merge and masks → Ocean Evaluate for preview or
cached spectra/foam → optional guided/tank FLIP region → extended ocean surface
→ Karma Ocean LOP → Karma CPU render`. Keep whitewater as its own prepared and
cached render stream. Keep the procedural spectrum as reference motion around
the simulated patch and blend the FLIP surface into the extended ocean rather
than simulating an unbounded sea. Do not label the H21 Karma Ocean stage as an
XPU farm job.

## Step-by-step workflow

1. Establish shot scale, camera height, water depth, wind direction, and required interaction region.
2. Create a base spectrum with suitable Grid Size, Depth, Wind Speed, Directional Bias, and Wave Scale.
3. Add a second spectrum only when a distinct wavelength band or direction is needed, then merge and mask it.
4. Preview the surface with Ocean Evaluate at moderate Resolution Exponent and inspect a full shot range.
5. Choose Guided Ocean Layer, Wave Tank, Beach Tank, Flat Tank, or Ocean Flat Tank according to interaction and tracking needs.
6. Cache the local FLIP region, build the extended surface, and verify a seamless transition to the spectrum.
7. Cache the spectra and foam inputs required by Karma Ocean, author the Karma
   Ocean LOP, and select Karma CPU for the representative render and final
   output. If XPU is a hard delivery requirement, stop and choose a separately
   documented and validated ocean procedure instead of silently changing the
   delegate.

## Critical parameters and attributes

Resolution Exponent creates power-of-two volumes: eight produces 256 by 256.
Grid Size defines the world-space period and affects repetition scale. Depth
changes dispersion. Wave Scale combines with wind speed to set amplitude.
Directional Bias and Directional Movement align waves with wind. Chop sharpens
crests but excessive values invert the surface. Time Offset and Time Scale align
layered spectra; Loop Period must be long enough to avoid temporal
quantization. For the H21 Karma Ocean delivery, record the Karma Ocean prim,
cached spectra/foam paths, camera, render product, and Karma CPU delegate.

## Cache, version, and performance

Use lower Resolution Exponent for layout because every increment increases
spectral texture resolution substantially. Evaluate geometry only where the
camera or simulation requires it; prefer render-time displacement for the
extended sea. Cache spectra, local FLIP, surface, foam, and whitewater at
separate invalidation boundaries. H21 Karma Ocean render jobs must select the
Karma CPU delegate; an available XPU worker does not make the node
XPU-compatible. Disable whitewater branches while refining the ocean and
disable ocean branches while refining whitewater.

## Validation

Inspect close, middle, and horizon regions through the render camera. Look for
spectrum tiling, blocky displacement, inverted crests, discontinuity at the
FLIP boundary, and mismatched velocity. Compare Ocean Evaluate preview with the
Karma Ocean displacement from the same cached spectrum. Confirm renderer
messages identify Karma CPU, the expected spectra and foam files resolve, and a
written frame contains displaced silhouette and specular response. Check foam
and whitewater scale against crest size and camera distance. This is a
documented/static acceptance path until run in the target H21 build.

## Common failures and troubleshooting

Close-up blockiness usually calls for a modest Resolution Exponent increase, not arbitrary subdivision everywhere. Visible tiling needs better grid scale, multiple spectra, or evolving contribution masks. Inverted peaks indicate excessive Chop. A tank that diverges from the surrounding ocean may lack guidance velocity or consistent spectrum timing. Beach Tank cannot follow a moving domain. Foam caches can appear detached when they use a different evaluated surface or time offset.

A flat or missing H21 Karma Ocean result on an XPU farm is a delegate mismatch,
not a reason to increase samples or spectral resolution. Route the Karma Ocean
render to Karma CPU and confirm cached spectrum paths and renderer messages.
Karma Whitewater supporting XPU does not make the separate Karma Ocean LOP
XPU-compatible.

## When not to use

Do not run FLIP for an ocean with no local interaction; use spectrum
displacement alone. Do not use a guided surface layer for deep underwater
displacement or large overturning volumes. Do not choose H21 Karma Ocean when
the delivery contract requires XPU; select and validate another documented
procedure explicitly. Use a river or FLIP boundary workflow when directional
current and terrain confinement dominate more than wind-wave statistics.

## Houdini 21 notes

The established Ocean Spectrum and tank tools remain the basis in H21. The
H21 Karma Ocean LOP is a Karma CPU renderer path. H21's Karma Whitewater VOP
supports CPU and XPU, but that shader capability does not change the Karma Ocean
LOP's CPU-only boundary. These are SideFX-documented support statements; this
pack did not live-render the node or submit a farm job.

## Official sources

- SideFX, [Oceans workflow](https://www.sidefx.com/docs/houdini/fluid/oceans.html), accessed 2026-07-26.
- SideFX, [Ocean Spectrum SOP](https://www.sidefx.com/docs/houdini/nodes/sop/oceanspectrum.html), accessed 2026-07-26.
- SideFX, [Karma Ocean LOP](https://www.sidefx.com/docs/houdini/nodes/lop/karmaocean.html), accessed 2026-07-26.
- SideFX, [Houdini 21 Karma changes](https://www.sidefx.com/docs/houdini/news/21/karma.html), accessed 2026-07-26.
