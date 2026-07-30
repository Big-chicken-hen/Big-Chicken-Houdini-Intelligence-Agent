# Copernicus layer, resolution, and color contracts

Source kind: `community_tutorial`
Verification: `community_unverified`
Version: both sources discuss Houdini 20.5-era Copernicus; recheck renamed nodes and color-management behavior.

## Use, prerequisites, and target

Use for texture generation, compositing, masks, image processing, or geometry-assisted image workflows in Copernicus. Inputs need known resolution, layer/plane names, data type, color space, and frame range. The target is a graph where resolution and layer semantics are explicit, scalar data is not accidentally color-transformed, and saved outputs match the in-graph result.

## Semantic network stages

Input/layer creation → resolution/domain normalization → mask/pattern branches → compositing/filtering → color/data transform → preview/evidence → file output → `OUT_COP_IMAGE`. Keep mask branches to the left and merge downward into the main image stream.

## Ordered workflow

1. Create/import the image with the appropriate Copernicus input node. Record resolution, aspect, frame range, layer names, components, data type, and declared color space. Distinguish color layers from data layers such as masks, normals, depth, or IDs.
2. Establish a single working domain. Use explicit resize/domain/reference controls so downstream nodes do not inherit an accidental first-input resolution. Name the reference layer.
3. Build procedural masks/patterns in separate branches. For each, record expected range and whether it is scalar or vector. Clamp or remap before using a mask as blend weight. Preserve high dynamic range when values legitimately exceed 1.
4. Composite with explicit foreground/background/mask connections and operation. Inspect premultiplication/alpha assumptions; unpremultiply and premultiply only at controlled boundaries.
5. Apply filters with scale tied to pixel or normalized coordinates intentionally. For transforms, record pivot, interpolation, wrap/border behavior, and whether frame-dependent motion is expected.
6. Perform color-space transforms only on color data. Keep normal/depth/ID/mask layers in data interpretation. Verify display transform is not baked twice.
7. Add preview diagnostics: layer selector, min/max sampling, checkerboard alpha, and a small set of pixel probes. Compare before/after nodes on representative frames.
8. Write through the current Copernicus output/ROP path with explicit filename token, format, bit depth, channels/layers, color-space policy, and frame range. Reload one output and end at `OUT_COP_IMAGE`.

## Key responsibilities and fields

Input owns layer metadata; domain stage owns resolution; procedural nodes own masks; composite owns alpha math; color transform owns interpretation; file output owns channel/bit-depth persistence. Important contract fields are resolution, component count, layer name, alpha/premultiplication state, numeric range, and color/data role.

## Executable parameter and connection contract

1. In a current `COP Network`, create an explicit `Layer COP` named `DOMAIN_REF`. Set `Signature = RGBA` for color or **Mono** for a scalar data layer, enter the delivery `Resolution`, choose `Precision`, and set `Border`. A practical reference might be `2048 x 2048`, `16-bit` float color, and **Clamp**; use `32-bit` for height/derivative work where 16-bit precision is insufficient.
2. Connect `DOMAIN_REF` to `size_ref` inputs of generators such as `Fractal Noise COP`, `Ramp COP`, or `Constant COP`. A mask branch uses Mono signature and a `0..1` range. Use **Wrap** border only for a deliberately tileable texture and prove it with a `3 x 3` tile visualization.
3. Load files through the current `File COP`, set `File Name`, sequence token/frame policy, and required AOV ports. Tag color inputs with the project OCIO interpretation; treat roughness, normal, depth, ID, and masks as data and do not send them through a display/color transform.
4. Use `Transform 2D COP` with recorded `Translate`, `Rotate` in degrees, `Scale`, `Pivot`, `Filter`, and `Border`. Keep `Rasterize` off for a lossless layer-space transform chain and rasterize deliberately later; turn it on only when the pixel result at the existing domain is required.
5. For RGBA operations put `Premultiply COP` at the explicit unpremultiply/premultiply boundaries. Use `OCIO Transform COP` only on declared color layers. Probe alpha-zero pixels before and after compositing so hidden RGB does not create fringes.
6. Connect the final named `Null COP`/output to `ROP Image Output COP`. Set the COP port/AOV mapping, filename including frame token, frame range, file format, precision, and output color space. For multiple outputs use `Add AOVs from COP` and verify each **Port** entry.
7. Use the network `Pixel Scale` proxy (for example `1:2`) only during authoring; final output must cook at `1:1`. Reload a written frame through `File COP` and compare resolution, signature, precision, color/data policy, and representative pixels.

## Data flow, cache, version, and performance

Resolution multiplies per-pixel cost; prototype at a proxy resolution but keep scale-dependent filters parameterized. Repeated resizes lose detail and add cost, so normalize domain once. Cache or write heavy stable intermediate layers when downstream iteration benefits, retaining data type and color-space metadata. Copernicus is evolving; current node/type names must be queried before automatic network authoring.

## Common failures and repairs

- **Black/blank layer:** wrong layer/plane selected, domain mismatch, or data outside display range.
- **Halo at alpha edges:** premultiplication assumptions conflict; inspect RGB where alpha is zero.
- **Mask behaves like color:** color transform or multi-component interpretation applied to scalar data.
- **Texture changes resolution midgraph:** implicit domain inheritance; insert explicit reference/domain stage.
- **Saved output differs:** output bit depth, channel list, or color transform differs from preview; reload and compare numerically.

## Checkpoints and observable evidence

- **O0 — domain:** node info reports the declared resolution, signature, precision, pixel aspect, and border on every main branch.
- **O1 — data roles:** each output port is labeled color or data; mask/depth/normal/ID branches have no OCIO display transform and mask extrema remain in the declared range.
- **O2 — alpha/transform:** checkerboard inspection shows no fringe; alpha-zero RGB behavior is documented; a transform changes only the intended placement and uses the recorded filter/border.
- **O3 — proxy/final:** a `1:2` proxy and `1:1` final preserve normalized layout while pixel-scale filters change according to their declared unit.
- **O4 — output:** first/middle/last files reload with matching port names, resolution, precision, and representative pixel tolerances; there is no double display transform.

## When not to use this workflow

Do not use Copernicus for geometry operations whose truth belongs in SOPs, for a simple one-time image conversion already handled by a deterministic file tool, or for a color transform when the input is non-color data. Avoid 16-bit fixed/float storage for a measured height/derivative range that cannot preserve the required precision.

## Provenance boundary

Matt Estela's COP notes and Moeen Sayed's Copernicus guide motivate layer-first troubleshooting. The domain/color contract and output validation are original synthesis. Exact H20.5+ node interfaces are unverified.

## Semantic expectations and verification checklist

- Presence: required color/data layers exist with named components at the output.
- Mapping: masks connect to mask inputs, color transforms touch only declared color layers, and output channels map to intended layers.
- Range: mask values are finite and within expected bounds; pixel probes preserve intended HDR/data ranges.
- Cook evidence: representative frames cook at the declared resolution without domain or missing-layer errors.
- Cache/output evidence: output files cover the requested frames and reload with matching resolution, layers, type, and representative pixel values.
- Visual evidence: no unexpected alpha halo, double color transform, clipped mask, or frame-to-frame domain jump.

## Sources

- Matt Estela, [Cops](https://tokeru.com/cgwiki/HoudiniCops.html).
- Moeen Sayed, [The Ultimate Copernicus Guide](https://www.sidefx.com/tutorials/the-ultimate-copernicus-guide/).
