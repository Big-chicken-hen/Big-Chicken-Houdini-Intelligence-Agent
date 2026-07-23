# Shader Translation

Translate the algorithm and visual intent, not the source text. ShaderToy/GLSL execution semantics do not map one-to-one to VEX, Copernicus, MaterialX, DOPs, Solaris, or Karma.

## Concept mapping

| GLSL/ShaderToy concept | Houdini interpretation |
| --- | --- |
| Fragment coordinates and resolution | Explicit normalized image coordinates in COPs, UV/primvar coordinates on geometry, or camera rays; preserve aspect ratio and pixel footprint |
| Time, frame, and mouse inputs | Exposed Houdini parameters driven by time, keyframes, or user controls; separate deterministic seed from animation time |
| Texture channels/buffers | COP inputs/layers, texture files, MaterialX Image nodes, geometry attributes, or cached intermediate fields |
| Derivatives and antialiasing | Target-specific filtering, derivatives when supported, or controlled supersampling; verify at delivery resolution |
| Hash/noise/cell math | VEX or COP math for dense procedural work; MaterialX nodes only when the required operations and iteration are supported |
| SDF/ray marching | COP image synthesis for a screen effect, SOP/VDB geometry for a real asset, or a renderer-supported volume approach |
| Feedback and history buffers | COP/SOP/DOP solver or cache only when the visual actually needs prior-frame state |
| Fragment color | COP image/AOV output, material response, geometry attribute, or explicit Karma render result |

Do not claim that arbitrary loops, dynamic indexing, custom GLSL functions, screen-space derivatives, or ray marching can be pasted into a Karma MaterialX graph. MaterialX is a portable node-graph standard with renderer-specific support and limitations.

## Translation workflow

1. Find the original source and identify the author, canonical URL, displayed license, inputs, and publication context.
2. Decompose the effect into coordinates, time/state, stochastic structure, geometry or masks, lighting/shading, compositing, and output transforms.
3. Decide whether the deliverable is an image-space effect, actual geometry, a simulation, a reusable texture/material, or a rendered shot.
4. Rebuild the minimal algorithm in the chosen Houdini context with user-facing controls. Re-derive small formulas; do not transliterate an unknown-license source.
5. Validate static shape, motion continuity, filtering, scale, and render compatibility separately.

## Rain-window example

The canonical inspiration commonly called **Heartfelt** is by Martijn Steinrucken (BigWings): [original ShaderToy](https://www.shadertoy.com/view/ltffzl). Re-check the author page and displayed license at research time. If the license is absent, inaccessible, or unclear, record it as unknown and use only conceptual observations.

Conceptually separate:

- multi-scale droplet placement and lifecycle;
- rounded stationary drops, elongated moving drops, and trails;
- a height/mask field converted to normals or distortion;
- background refraction/blur, highlights, and optional condensation;
- temporal variation that avoids obvious popping and repetition.

Choose the implementation from the shot:

- Use Copernicus for a primarily screen-space rain mask, normals, refraction, blur, and compositing pipeline.
- Feed COP-authored maps to a MaterialX/Karma glass material when the window must live in a lit 3D scene.
- Use SOP/VEX geometry or instanced droplets for close silhouettes, parallax, shadows, or macro shots.
- Add a solver only for persistent trails, merging, or history-dependent motion; otherwise use deterministic time functions.

Judge the result at the target camera and resolution. Compare droplet scale distribution, trail direction, refraction strength, highlight breakup, background focus, and temporal stability—not merely whether circles move down a pane.

Relevant primary references: [Copernicus workflows](https://www.sidefx.com/docs/houdini/copernicus/working_with_cops.html), [MaterialX in Solaris](https://www.sidefx.com/docs/houdini/solaris/materialx), and the [MaterialX specification](https://materialx.org/Specification.html).