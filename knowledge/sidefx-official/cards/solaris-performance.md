# Solaris performance and stage organization

Source: https://www.sidefx.com/docs/houdini/solaris/performance.html

Solaris performance is strongly affected by composition, prim count, layer
structure, payload policy, instancing, and unnecessary recooks. Optimize the
structure before lowering visual quality.

- Use payloads when large assets need not be loaded for every task.
- Preserve instancing for repeated assets instead of making unique copies.
- Avoid thousands of tiny edits through needlessly fragmented layers.
- Keep expensive SOP imports from recooking when only lighting changes.
- Measure with representative assets instead of diagnosing an empty scene.

Separate generation, assembly, look, lighting, and render configuration so a
change in one area does not invalidate unrelated work. When a scene is slow,
record whether the delay is SOP cooking, USD composition, Hydra population,
shader compilation, texture loading, or rendering.

Common anti-patterns are exploding instances, loading every payload, repeatedly
rebuilding the stage, and treating lower render samples as a solution to
composition or viewport bottlenecks.
