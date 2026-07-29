# Copernicus feedback, texture, and output workflow

Pack version: 2.0.0

## Use for

Use this workflow for H21 Copernicus feedback simulations, reaction-diffusion or flow blocks, procedural material maps, geometry texture baking, multi-map cable handling, and delivery to Solaris/MaterialX, SOPs, or image files. It is appropriate when state over time and downstream texture semantics must remain explicit.

## Context and core data

Feedback networks carry prior state through Block Begin and Block End nodes and require an initial condition, timestep behavior, and boundary policy. Material maps remain separate typed layers, often base color, roughness, normal, height, opacity, or IDs. Cables group several wires without flattening them into one layer. In H21, Preview Material COP can assemble maps for inspection, while an `op:` path lets MtlX Image inside a Material Library read a COP result interactively.

## Recommended data flow

Use `initial layers/geometry → feedback or texture-generation stages →
cache/stash checkpoint → map normalization and channel typing → Cable Pack or
Preview Material → live consumer or ROP Image Output COP`. For geometry baking,
use `prepared low/high/optional cage SOPs → Bake Setup → Bake Geometry Textures
→ selected map outputs or All Outputs → ROP Image Output COP`. Keep simulation
state separate from final color-space conversion.

## Step-by-step workflow

1. Define the feedback state, initialization frame, time step, resolution, precision, and border behavior.
2. Build and test one iteration with feedback disabled so the local operation is numerically understandable.
3. Enable the block, limit growth or diffusion, and preview a short low-resolution range for stability.
4. Cache or stash a useful intermediate state before expensive texture finishing.
5. For geometry transfer, prepare UV'd **low** geometry and detailed **high**
   geometry in SOPs. Add **Bake Setup** in COPs and set the generated low/high
   SOP Import paths. Supply an optional **cage** with the same topology as low
   and set **Tracing Mode = Cage Mesh**, or use the documented automatic cage
   controls when a custom cage is unnecessary.
6. In Bake Geometry Textures, enable only the required maps under **Texture
   Maps**, inspect each named output, and validate tangent-space normals against
   the exact low-mesh normals, tangents, UVs, and triangulation used downstream.
7. For a multi-map export, connect the Bake Geometry Textures **All Outputs**
   port to ROP Image Output COP, click **File Layout ▸ Add AOVs from Input**,
   and include the literal `<LAYER>` token in **Output File**. Render to disk;
   `<LAYER>` must expand to each selected map name. Reload every written map in
   the actual MaterialX/Karma or external consumer.
8. For non-bake textures, pack related maps into a cable or Preview Material
   COP with descriptive names. Use `op:` only for interactive H21 work whose
   HIP context remains available; write files for portable delivery.

## Critical parameters and attributes

Block simulation controls determine whether the feedback updates live or by
frame. Resolution and timestep jointly affect stability; changing either can
change the visual regime. Border behavior determines whether patterns clamp,
repeat, mirror, or encounter a constant boundary. Cache Max Frames limits
memory. Clear Cache when Change Upstream prevents stale state by default. Cable
wire names control input override matching and material-map identity.

For Bake Geometry Textures, check `low`, `high`, optional `cage`, Tracing Mode,
UV Attribute, normals/tangent policy, Ray Bias, Match Pieces by Name, sampling,
and UV boundary filling. A custom cage must enclose the high mesh and match low
topology. On ROP Image Output, refresh **Add AOVs from Input** after changing
enabled maps, map each AOV to the correct port, and use `<LAYER>` when one render
must create distinct filenames.

## Cache, version, and performance

Use proxy resolution during feedback tuning because both iteration count and pixel count multiply cost. Cache an expensive time-dependent upstream branch only when reuse exceeds recomputation cost. Turning off Clear Cache when Change Upstream creates a sticky comparison, not a trustworthy final result. Stash stores an input snapshot with the node; Cache stores temporal frames in memory. Save final texture files when external render or farm jobs cannot resolve live `op:` references.

## Validation

Test the initialization frame, several early iterations, a representative mature
state, and the final frame. Look for numerical blow-up, frozen feedback, border
seams, and resolution-dependent changes. For baking, compare the low mesh,
high-mesh silhouette/detail, and cage coverage; inspect missing rays, skew,
hard-edge seams, padding, map range, and tangent-space orientation. Confirm the
All Outputs cable exposes exactly the maps enabled under Texture Maps. After
rendering, list the files produced by `<LAYER>`, reopen each file, and compare
its name, resolution, channels, and values with the corresponding COP output.

## Common failures and troubleshooting

An unchanged feedback result may mean the previous-state wire is disconnected,
the block is not simulating, or a sticky cache is masking edits. Exploding
values need a smaller timestep, bounded operation, lower gain, or higher
precision. Baking misses often come from a cage that does not enclose high
geometry, unsuitable Ray Bias, low/high piece mismatch, or missing normals and
tangents. Hard-edge seams require matching UV seams and padding. If only one
file is overwritten, `<LAYER>` is absent or Add AOVs from Input is stale. A cable
connected to an ordinary input loops the node over every member; accidental
cable fan-out can multiply work. Interactive `op:` textures can fail in external
renders if the HIP context is unavailable.

## When not to use

Do not compile a COP feedback network because compiled COP networks do not support simulation. Use a static noise or filter chain when no temporal state is required. Write image files instead of `op:` references for portable assets and render-farm delivery. Use SOP or DOP simulation when three-dimensional geometry or volume dynamics, rather than image-space evolution, is the actual problem.

## Houdini 21 notes

H21 change notes add Simulate and Live Simulation controls to Block End,
Reaction-Diffusion blocks, Flow blocks, sparse GPU Pyro, Cache and Stash COPs,
cable nodes, Bake Geometry Textures, and UDIM context options. The low/high/cage,
Bake Setup, All Outputs, Add AOVs from Input, and `<LAYER>` steps above are
**documented/static** from SideFX node help. They were not executed in an H21
GUI during this pack build and are not live-verified. H21 cables cannot be
nested. The USD Material COP and Texture Material Library shown in current
online help are Houdini 22 additions and must not be treated as H21 steps.

## Official sources

- SideFX, [Working with Copernicus nodes](https://www.sidefx.com/docs/houdini/copernicus/working_with_cops.html), accessed 2026-07-26.
- SideFX, [Copernicus cables](https://www.sidefx.com/docs/houdini/copernicus/cables.html), accessed 2026-07-26.
- SideFX, [Cache COP](https://www.sidefx.com/docs/houdini/nodes/cop/cache.html), accessed 2026-07-26.
- SideFX, [Bake Geometry Textures COP](https://www.sidefx.com/docs/houdini/nodes/cop/bakegeometrytextures.html), accessed 2026-07-26.
- SideFX, [ROP Image Output COP](https://www.sidefx.com/docs/houdini/nodes/cop/rop_image.html), accessed 2026-07-26.
- SideFX, [Houdini 21 Copernicus changes](https://www.sidefx.com/docs/houdini/news/21/copernicus.html), accessed 2026-07-26.
