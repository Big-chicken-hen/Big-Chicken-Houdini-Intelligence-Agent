# Karma XPU workflow and compatibility checks

Source: https://www.sidefx.com/docs/houdini/solaris/karma_xpu.html

Karma CPU and Karma XPU do not have identical feature support. Choose the
delegate deliberately and validate materials, lights, volumes, displacement,
AOVs, and render settings against that delegate.

Start with a low-resolution representative frame. Confirm camera, resolution,
delegate, device availability, material binding, lights, and render products
before increasing samples. A black or incomplete image is usually a scene,
binding, support, or lighting problem rather than a reason to raise samples.

Use MaterialX-compatible shading for portable XPU work. Separate noise diagnosis
from feature support: samples can reduce Monte Carlo noise but cannot enable an
unsupported feature or repair a missing texture.

For final validation, inspect the actual output file, frame range, AOV names,
color management, missing textures, warnings, and representative image regions.
Record the delegate and Houdini build with the result.
