# Pyro sourcing, solving, caching, and rendering

Source: https://www.sidefx.com/docs/houdini/pyro/intro.html

Separate Pyro work into source preparation, simulation, post-process, cache, and
rendering. Decide whether the effect is smoke, fire, explosion, dust, or another
buoyant volume before choosing fields and shaping controls.

Source geometry should produce intentional density, temperature, fuel, velocity,
and masks at a scale appropriate to the container and voxel size. Inspect source
volumes before blaming the solver. Establish scene units, bounds, frame range,
and collision geometry early.

Tune motion from large to small scale: source motion and buoyancy first, then
disturbance, turbulence, shredding, dissipation, and detail. More substeps or a
finer voxel size may solve a specific issue, but both increase cost.

Cache a stable simulation before expensive look development. Validate cache
paths, frame coverage, fields, and restart behavior. Typical failures are wrong
source fields, clipping bounds, mismatched scale, excessive noise, low-resolution
collisions, and reporting only an uncached viewport result.
