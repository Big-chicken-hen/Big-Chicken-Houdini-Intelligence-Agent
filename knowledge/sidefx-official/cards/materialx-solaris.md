# MaterialX in Solaris

Source: https://www.sidefx.com/docs/houdini/solaris/materialx

Use MaterialX for portable shader graphs intended for Solaris and Karma. Build
the material in a Material Library or appropriate builder, expose artist-facing
controls, and bind it to stable USD prim paths.

Plan by physical responsibilities: base color, roughness, metallic or dielectric
response, transmission, subsurface behavior, coat, normal or bump, and
displacement. Do not add noise merely to make a graph look complex.

Texture inputs need explicit color-space intent. Color textures and data maps
must not be treated identically. Verify UVs or other primvars at the geometry,
then verify the shader reads the intended primvar and type. Keep displacement
scale consistent with scene units.

For Karma XPU, verify node support and the actual renderer result. Typical
failures are missing bindings, wrong primvar names, wrong color spaces,
unsupported nodes, excessive displacement, and a plausible material that does
not match the visual reference.
