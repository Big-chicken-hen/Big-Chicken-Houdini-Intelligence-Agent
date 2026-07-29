# MaterialX authoring and binding in Solaris

Pack version: 2.0.0

## Use for

Use this workflow for a new material intended for Karma, for a portable
MaterialX look, or when SOP geometry must become a correctly bound USD material
in Solaris. It covers the complete handoff from geometric coordinates and
semantic masks to a material, USD binding, and representative render evidence.
It is not a catalog of shader nodes. Begin by describing the substrate, surface
layers, condition, recognition cues, spatial scale, target delegate, and visual
evidence that will prove the material works.

## Context and core data

The main data is a USD stage containing renderable prims, a Material Library LOP
that authors USD materials, a MaterialX graph with a surface output and optional
displacement output, and material bindings on stable prim paths. Geometry may
provide vertex `st` or `uv`, object or world position, normals, tangents,
material IDs, and task-specific primvars such as masks or color. Texture inputs
must distinguish color data from scalar or vector data. Karma CPU and XPU are
separate validation targets even when they share the same MaterialX graph.

For new Karma work, prefer the modern Solaris/USD path: author a MaterialX
network in a Karma Material Builder or USD MaterialX Builder inside a Material
Library LOP, then bind it with Assign Material or an equivalent USD binding.
Do not default to legacy `/mat`, Principled Shader, Mantra, or arbitrary VOP
materials. Preserve a legacy route only when the user requests it, the existing
project requires it, or another renderer is the stated target.

## Recommended data flow

Use this semantic flow:

`SOP geometry and primvars -> SOP Import/reference into USD -> Material Library
LOP -> MaterialX inputs and coordinates -> semantic masks -> substrate and
layers -> optical channels -> independent normal/displacement lanes -> material
outputs -> USD material binding -> Karma render settings/product -> validation`.

Keep the graph readable as a vertical main trunk with horizontal branches for
coordinates, masks, texture channels, and reusable layers. A shared mask should
have a meaningful hub or dot. Name nodes by responsibility. `layoutChildren()`
is only an initial arrangement; finish substantial graphs with semantic
placement and remove ineffective experimental branches.

## Step-by-step workflow

1. Inspect the target prim paths, geometry scale, normals, UV or alternative
   coordinates, semantic groups, material bindings, texture paths, and the
   active Karma delegate. Record the visual cues the material must produce.
2. Choose a substrate and only the physically motivated layers. Establish base
   color, dielectric or metallic identity, roughness, and IOR before adding
   wear, deposits, wetness, or decorative variation.
3. In a Material Library LOP, create a Karma Material Builder for general Karma
   authoring or a USD MaterialX Builder when a strictly portable MaterialX
   graph is required. Confirm current node types in installed help when the
   Houdini build is uncertain.
4. For a color-bearing MtlX Image, use a color signature and the project's
   tested OCIO/file-rule path. For roughness, metallic, masks, displacement,
   and other data, use the matching float or vector signature. For a normal-map
   image specifically, set Signature to Vector3 before MtlX Normalmap so no
   color transform is applied. Do not treat the MaterialX Color Space field as
   a reliable conversion: it is metadata that Hydra currently does not pass to
   the render delegate.
5. Build semantic masks from evidence such as UVs, position, curvature, cavity,
   height, proximity, painted attributes, or material IDs. Noise may vary a
   signal, but it is not itself a material concept. Share a signal across
   channels only when one physical cause explains that correlation.
6. Connect the material surface and optional displacement outputs, author them
   into a stable material path, and bind that material to the intended USD
   prims. Inspect the composed stage rather than assuming a SOP group name
   became a valid USD selection.
7. Validate under a bounded diagnostic light or reflection environment, then
   in the target camera when the task requires it. Check highlight shape,
   roughness range, transmission, normal and displacement scale, and layer
   blending before increasing render quality.
8. Inspect the written render and renderer messages. Preserve editable source
   graphs, resolved texture paths, material paths, delegate, and version notes
   with the handoff.

## Critical parameters and attributes

Check material path, binding strength, surface and displacement output names,
base color, metalness, specular IOR, roughness, transmission, opacity, coat,
subsurface parameters, emission, normal/bump strength, and displacement scale
only when the target needs them. Confirm `st`/`uv` or another coordinate
primvar, tangent basis when required, each MtlX Image signature, the tested
OCIO/file-rule conversion, channel extraction, `<UDIM>` expansion, wrap/filter
behavior, and object scale. MaterialX filename tokens such as `{frame}` are not
currently evaluated by Houdini or Karma; `<UDIM>` is the documented exception.
MaterialX string inputs also cannot be connected, including a string promoted
through a material's public interface, so keep required filenames and geometry
property names authored at a supported input or use a documented USD Preview
interoperability workaround. A normal map and a height map are not
interchangeable. Opacity, transmission, and refraction also have different
render and shadow consequences.

## Cache, version, and performance

Record the Houdini build, MaterialX node definitions, Karma delegate, and texture
versions. Prefer tiled, mipmapped production textures and stable project paths.
Avoid repeatedly evaluating expensive procedural branches when a reviewed map
or upstream attribute can represent the same stable signal. Displacement can
increase dicing and memory cost; validate its world-space amplitude before
raising subdivision. XPU support can differ from CPU, so node validity in the
editor is not proof of delegate support. Do not bake a procedural result merely
to hide an unresolved coordinate or binding problem.

## Validation

Inspect the Material Library for errors, the composed USD material prim, the
binding relationship on the target prim, texture resolution and paths, required
primvars, MtlX Image signatures, resolved `<UDIM>` files, and both surface and
displacement terminals. Prove color conversion with a known color patch or
pixel value rather than the Color Space metadata label. Render a representative
region with the intended delegate. Use a light that reveals highlight width,
grazing response, transmission, and surface relief. Compare scale-dependent
features against the object and camera. A graph that cooks without error is
not complete if the material identity or recognition cues are absent.

## Common failures and troubleshooting

- A gray result usually points to an un-authored material, wrong material path,
  missing binding, unsupported node, or failed texture before it points to
  insufficient shader complexity.
- Wrong hue or contrast often comes from a wrong MtlX Image signature, an OCIO
  file-rule mismatch, treating data as color, or applying a conversion twice;
  changing only MaterialX Color Space metadata is not a dependable correction.
- A normal map with softened or shifted directions often entered the graph as
  Color3. Set its image signature to Vector3 and then verify the normal-map
  node's tangent basis and required primvars.
- A promoted filename or geometry-property name that will not connect is a
  MaterialX string-input limitation. Author the string at a supported point;
  do not replace the failure with an unverified token expression.
- A sequence using `{frame}` will not advance because Houdini and Karma do not
  currently evaluate MaterialX filename tokens. Use a supported resolved path
  workflow; reserve `<UDIM>` for tiled textures.
- Swimming patterns indicate an unsuitable coordinate space or unstable
  primvar; repair the source coordinate rather than adding more noise.
- Flat relief can be a missing normal terminal, wrong tangent basis, tiny
  amplitude, or insufficient displacement tessellation.
- Excessive darkening or invisible glass can involve geometry thickness,
  normals, lighting, transmission settings, opacity, or delegate support.
- If wear appears everywhere equally, replace generic grunge with masks tied to
  contact, edges, cavities, gravity, drainage, or another defensible cause.

## When not to use

Do not build this workflow for a simple color assignment, a material-agnostic
geometry inspection, or a renderer that does not consume MaterialX. Do not
migrate a required legacy shader without authorization. A purely procedural
world-space material may not need UVs, while a texture, decal, bake, or external
exchange normally does; state that decision explicitly.

## Houdini 21 notes

This card targets Houdini 21. New Karma material work should use
Solaris/USD, Material Library LOPs, and MaterialX. Current SideFX documentation
may display a later Houdini version, so confirm node availability and CPU/XPU
support using the installed H21 help or current node help rather than writing a
versioned internal node name into a reusable plan. The limitations above are
documented support statements, not evidence that this pack live-tested a
specific production build. H21 viewport and Karma material behavior still does
not make the viewport a substitute for a representative Karma render.

## Official sources

- [Creating materials for Karma](https://www.sidefx.com/docs/houdini/solaris/kug/materials.html) — SideFX Karma user guide; accessed 2026-07-26.
- [Using MaterialX with Solaris](https://www.sidefx.com/docs/houdini/solaris/materialx.html) — SideFX material authoring guide; accessed 2026-07-26.
- [Solaris and USD](https://www.sidefx.com/docs/houdini/solaris/index.html) — SideFX Solaris workflow documentation; accessed 2026-07-26.
- [Intro to Look Development and Lighting](https://www.sidefx.com/tutorials/intro-to-look-development-and-lighting/) — SideFX tutorial; accessed 2026-07-26.
