# Solaris and USD composition basics

Source: https://www.sidefx.com/docs/houdini/solaris/usd.html

Treat a Solaris stage as composed opinions, not as a single mutable scene file.
Before editing, identify which layer should own the opinion and whether the
asset should be referenced, payloaded, instanced, or authored directly.

- References compose reusable assets; payloads allow deferred loading.
- Variants represent intentional alternatives, not arbitrary duplicated trees.
- Instances suit repeated assets that share structure.
- Primvars carry renderer- or shader-facing data with explicit interpolation.
- Stronger opinions can override weaker ones without deleting the weaker layer.

Keep asset structure, shot assembly, look overrides, lighting, and render
settings in understandable layers. Use stable prim paths and prefer composition
over copying complete assets into a shot layer.

When debugging, inspect the composed stage, active layer, prim path, authored
opinion, variant selection, payload load state, material binding, and instance
status. Typical failures are editing the wrong layer, confusing references with
payloads, breaking instancing, unstable paths, and incorrect primvar types.
