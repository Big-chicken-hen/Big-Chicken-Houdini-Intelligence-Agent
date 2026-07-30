# Performance profiling before optimization

Source kind: `community_tutorial`
Verification: `community_unverified`
Version: one source is H18.5 and the other spans releases; verify current Performance Monitor/compile capabilities.

## Use, prerequisites, and target

Use when a Houdini network cooks, simulates, renders, or interacts too slowly or uses excessive memory. Prerequisites are a representative scene/range, reproducible input, and a correctness reference. The target is a measured before/after change that preserves output semantics and identifies the dominant cost instead of distributing speculative micro-optimizations.

## Semantic network stages

Representative benchmark input → measurement markers → dominant-stage isolation → algorithm/data-layout change → cache/compile/parallel strategy → correctness comparison → repeated benchmark → `OUT_PROFILED`.

## Ordered workflow

1. Define the benchmark: Houdini version, machine context, frame/range, cold or warm cache, input element/voxel/item counts, expected output, and metric (cook time, memory, interactivity, render time).
2. Capture a baseline with Performance Monitor, node cook timing, task manager/process metrics, or renderer/simulation statistics appropriate to the stage. Run enough repetitions to distinguish noise and record median/representative values.
3. Identify the dominant node/stage. Bypass or isolate branches read-only and measure again. Do not optimize a node contributing a negligible percentage of total time.
4. Classify cost: excessive topology/voxel resolution; nested all-to-all algorithm; repeated file I/O; repeated attribute conversion/copy; time dependence; Python/HOM per-element loop; solver substeps; shader sampling; or dependency recook.
5. Apply one structural change. Examples: bounded spatial lookup instead of nested scan; native SOP/VEX instead of per-element Python; cache deterministic upstream data; reduce proxy resolution; pack/instance repeated geometry; delete unused cache attributes; use compile blocks only for compatible repeated SOP work.
6. Verify output equivalence with counts, attributes, bounds, numeric tolerances, image difference, or shot-specific visual checks. Speed without required correctness is a regression.
7. Repeat the same benchmark, including cold/warm distinction. Record time, memory, output difference, and tradeoff. Keep the change only if the improvement is meaningful and maintainable.
8. Set interactive/final quality controls and a cache boundary where appropriate. Finish at `OUT_PROFILED` with benchmark notes attached to the responsible network stage.

## Key responsibilities and measurements

Benchmark stage owns reproducibility; profiler owns evidence; isolation owns attribution; optimization owns one causal change; equivalence check owns correctness. Important fields are element/voxel/work-item counts, cook dependency, wall/cpu time, peak memory, cache status, file bytes, and output hash/tolerance.

## Executable parameter and connection contract

1. Place `Null SOP` boundaries named `IN_BENCHMARK` and `OUT_BENCHMARK` around the exact stage. Record frame, point/primitive/voxel counts, topology hash or schema summary, quality parameters, and whether upstream/file caches are cold or warm.
2. In **Performance Monitor**, start a new profile, cook the declared output/frame range exactly once, stop recording, and sort by inclusive wall time. Repeat at least three times under the same cache state and retain the median plus raw timings; do not mix first cold load with warm repetitions.
3. Attribute a bottleneck by bypassing one branch or replacing it with a cached/stashed equivalent, then repeat. The sum/parent relationships in the profile must support the claim; a node below roughly the run-to-run noise is not the primary target.
4. Apply one explicit structural change: cap `nearpoints` radius/count; replace per-element HOM with `Attribute Wrangle SOP`; use `Compile Begin/End SOP` only around compatible repeated work; instance/pack repeated geometry; reduce diagnostic voxel size; or add `File Cache SOP` after deterministic expensive data.
5. Keep quality controls concrete. For example, a volume proxy can use twice the reference voxel size, a curve proxy can use twice the segment length, and final returns to `1x`; these are relative comparison settings, not universal production values.
6. Compare before/after output with element counts, attribute schema, bounds, finite-value checks, numeric tolerance, or image difference. Then repeat the same cold/warm profile and record wall time, peak memory, cache bytes, and output delta.

## Data flow, cache, version, and performance tradeoffs

Caching trades disk and invalidation complexity for cook time. Packing/instancing trades editing flexibility for memory. Coarser simulation/render settings trade detail for speed. Compile blocks help only compatible graphs and can obscure debugging. Parallelism can worsen memory or I/O contention. Document the chosen tradeoff and retain an escape hatch.

## Common failures and repairs

- **Timing varies wildly:** background work, warm caches, or changing inputs; standardize benchmark and use repetitions.
- **Fast isolated node, slow full graph:** dependency recooks or downstream amplification; profile the integrated path.
- **Cache never hits:** time-dependent expression, changing metadata/path, or incomplete dependency signature.
- **Python bottleneck:** move bulk element math to native SOP/VEX, batch HOM calls, or reduce crossings.
- **Optimization changes result:** restore reference, define tolerance, and find a structural alternative.

## Checkpoints and observable evidence

- **P0 — reproducibility:** identical input/frame/quality/cache-state metadata accompanies every timing sample.
- **P1 — attribution:** the named node/stage owns a measured share larger than run noise; bypass/isolation reduces total time consistently.
- **P2 — equivalence:** required counts/schema/bounds or image difference pass the declared tolerance before speed is accepted.
- **P3 — improvement:** median time and peak memory are reported before/after with sample count; cold and warm results remain separate.
- **P4 — invalidation:** changing a declared dependency invalidates the cache/compiled result, while an unrelated edit does not recook the measured stage.

## When not to use this workflow

Do not optimize a non-reproducible scene, a stage outside the dominant cost, or a graph whose correctness contract is unknown. Avoid compile blocks around unsupported/time-dependent nodes, caches without an invalidation/version policy, and proxy-quality changes presented as equivalent final output.

## Provenance boundary

Richard Thomas's algorithm-optimization tutorial and Matt Estela's broad Houdini notes motivate measurement and algorithmic thinking. The benchmark protocol and equivalence gate are original synthesis. No performance claim is made for the current environment.

## Semantic expectations and verification checklist

- Baseline: version, inputs, range, counts, cache state, and metric are recorded.
- Attribution: the changed stage accounts for a meaningful measured share of baseline.
- Correctness: required topology/attributes/bounds or image result stays within documented tolerance.
- Cook evidence: repeated before/after runs use the same conditions and report representative time/memory.
- Cache evidence: cold and warm behavior are distinguished; invalidation occurs when declared dependencies change.
- Outcome: speedup/cost reduction and its quality, memory, disk, or maintenance tradeoff are explicit.

## Sources

- Richard Thomas, [Optimizing Algorithms for TDs And Artists](https://www.sidefx.com/tutorials/optimizing-algoruthms-in-houdini-for-tds-and-artists/).
- Matt Estela, [Houdini](https://www.tokeru.com/cgwiki/Houdini.html).
