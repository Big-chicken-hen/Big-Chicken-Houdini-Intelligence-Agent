# Copernicus image and texture workflow

Source: https://www.sidefx.com/docs/houdini/news/21/copernicus.html

Copernicus is Houdini's image and texture processing environment. It is
version-sensitive, so confirm node names and parameters from the active
installation before generating a network.

Define resolution, pixel aspect, layer semantics, color space, frame behavior,
and final use: texture, mask, height field, compositing element, simulation
field, or render input. Keep color layers distinct from scalar data and vector
fields.

Organize the graph as semantic stages: source, normalization, masks or fields,
main operation, detail, color management, and output. For iterative flow,
ripple, reaction-diffusion, or fluid-like effects, make state, initialization,
boundary behavior, time step, and feedback explicit. Validate at low resolution
before increasing cost.

When translating ShaderToy or GLSL, preserve the mathematical behavior but
adapt coordinates, time, sampling, inputs, and outputs to Copernicus. Do not copy
unknown-licensed shader code verbatim. Typical failures are mismatched
resolution, incorrect coordinates, unstable feedback, and judging only a single
preview instead of the output consumed downstream.
