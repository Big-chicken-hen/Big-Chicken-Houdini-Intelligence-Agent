# Vellum constraint and stability workflow

Source: https://www.sidefx.com/docs/houdini/vellum/overview.html

Vellum represents cloth, hair, soft bodies, balloons, grains, and related
effects through particles and constraints. Choose the constraint model from the
material behavior, not from the desired object name.

Before solving, validate scale, point spacing, topology, thickness, pinning,
rest state, collision geometry, and constraint visualization. Use named groups
for controlled regions. Establish a stable low-resolution setup before adding
subdivision.

Substeps improve collision and fast-motion sampling; constraint iterations
improve convergence. They solve different problems. Increase them only after
identifying missed motion, weak convergence, topology errors, penetration, or
unrealistic parameters.

Validate several key frames. Check constraint breakage, penetrations, energy
growth, floating pieces, and whether caches match the live solve. Common failures
are incorrect scale, oversized collision thickness, contradictory constraints,
and using more iterations to conceal a modeling error.
