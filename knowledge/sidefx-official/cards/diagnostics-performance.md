# General diagnostics and performance

Pack version: 2.0.0

## Use for

Use this workflow when a Houdini scene is slow, produces wrong results, displays warnings, runs out of practical memory, differs across machines, or needs defensible performance evidence before delivery. It is context-neutral: apply it to SOPs, scripts, viewport interaction, asset instances, and caches. The goal is to isolate one failing claim, capture reproducible evidence, make the smallest coherent correction, and recheck the same evidence.

## Context and core data

Houdini performance is distributed across dependency cooking, geometry allocation, Python or HScript execution, disk I/O, viewport updates, GPU drawing, simulation caches, and rendering. A visible slowdown does not identify its cause. Node MMB information exposes cook state, geometry counts, attributes, and memory categories. Performance Monitor records timed events for nodes, scripts, viewports, and Houdini Engine. Error badges, info windows, consoles, Geometry Spreadsheet, cache metadata, and scene statistics provide complementary evidence.

## Recommended data flow

Use:

`state the failing output or slow action -> record exact scene/build/frame/cache state -> reproduce once -> collect node, geometry, memory, and profile evidence -> rank the dominant cause -> change one coherent region -> reproduce under identical conditions -> report result and remaining limits`

Keep correctness and performance diagnosis separate. First prove the output is valid; then optimize the measured bottleneck without changing accepted behavior.

## Step-by-step workflow

1. Define one concrete symptom, affected output, frame or range, and expected result.
2. Record Houdini build, hardware-relevant mode, current scene path, active cache state, and reproduction action.
3. Inspect node badges, MMB info, upstream errors, geometry counts, bounds, attributes, and missing files before profiling.
4. Capture a Performance Monitor profile around only the reproduction action and identify dominant event categories.
5. Form the narrowest hypothesis supported by both correctness and timing evidence.
6. Change one region, such as a loop, cache boundary, representation, query radius, viewport display, or Python batch.
7. Clear or preserve caches consistently and repeat the identical action.
8. Compare output equivalence, timings, memory categories, and any new warning; record unresolved risks explicitly.

## Critical parameters and attributes

- Performance Monitor recording scope must exclude unrelated idle or setup work.
- Cook time, wall time, call count, and self versus inclusive cost answer different questions.
- Node MMB `Memory` includes referenced data, `New` shows data not referenced by inputs, and `Unique` shows data held only by the node.
- Geometry point/primitive counts and intrinsic memory reveal data growth.
- Display mode, instance percentage, viewport renderer, and texture cache affect draw cost.
- Cache state determines whether a profile measures computation or loading.
- Stable `id`, `name`, and file-version metadata help compare outputs.
- Exact frame, time sample, and simulation start state are part of reproducibility.

## Cache, version, and performance

This card records a controlled measurement; it does not repeat the implementation recipes in the [cache and packaging card](cache-packaging-performance.md) or the [HOM cook and exception card](hom-cook-exception-performance.md). Record whether the run is cold, warm, memory-cached, or disk-cached, plus Houdini build and scene revision. Optimize only the measured dominant event category. A faster result that changes topology, attributes, motion, or image quality is a tradeoff, not an equivalent pass.

## Validation

Recheck the same node output, frame range, action, and Performance Monitor scope. Record self time, inclusive time, call count, and the dominant event before and after the change. Compare counts, bounds, key attributes, warnings, and representative visual evidence. Distinguish one-time compilation or file-system warm-up from steady behavior. For memory, inspect ownership categories rather than relying only on process size. For cross-machine differences, compare Houdini build, driver, paths, environment, and cache versions.

## Common failures and troubleshooting

- The profile is dominated by setup: narrow recording to the exact action.
- A node looks slow because its inclusive time contains expensive inputs: inspect self cost and upstream events.
- Process memory stays high after unloading: Houdini may retain allocation for reuse; inspect node ownership and Unique memory.
- A compiled loop is slower: iteration count is too small, setup dominates, or nested task overhead exploded.
- Cached playback is still slow: viewport drawing or disk bandwidth, not upstream cooking, is dominant.
- Results differ after optimization: packing, precision reduction, or changed evaluation order altered the data contract.
- Cross-machine output differs: compare build, math reproducibility settings, drivers, and external cache versions.
- A warning disappears but output is empty: strict validation was replaced by silent fallback rather than a fix.

## When not to use

Do not profile before the failing action is reproducible. Do not optimize an unverified or incorrect output. Avoid comparing runs with different frames, cache states, display modes, or source versions. Do not apply compiled blocks, packing, precision reduction, or unloading globally. Do not claim a general speedup from a single noisy measurement or omit a quality tradeoff.

## Houdini 21 notes

H21 makes Vulkan production-ready and enables threaded Vulkan geometry updates and drawing by default on Windows and Linux. It also reports the OpenCL driver version in Houdini information and includes it in cached-kernel hashes, so driver changes can trigger recompilation. These changes make renderer, driver, and warm-up state important parts of H21 performance evidence.

## Official sources

- SideFX, [Performance Monitor pane](https://www.sidefx.com/docs/houdini/ref/panes/perfmon.html), accessed 2026-07-26.
- SideFX, [Cooking](https://www.sidefx.com/docs/houdini/basics/cooking), accessed 2026-07-26.
- SideFX, [Primitives and memory interpretation](https://www.sidefx.com/docs/houdini/model/primitives.html), accessed 2026-07-26.
- SideFX, [Houdini 21 user interface and viewport changes](https://www.sidefx.com/docs/houdini/news/21/viewport.html), accessed 2026-07-26.
- SideFX, [Houdini 21 VEX and OpenCL changes](https://www.sidefx.com/docs/houdini/news/21/vex.html), accessed 2026-07-26.
