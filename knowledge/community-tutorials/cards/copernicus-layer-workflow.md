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

## Data flow, cache, version, and performance

Resolution multiplies per-pixel cost; prototype at a proxy resolution but keep scale-dependent filters parameterized. Repeated resizes lose detail and add cost, so normalize domain once. Cache or write heavy stable intermediate layers when downstream iteration benefits, retaining data type and color-space metadata. Copernicus is evolving; current node/type names must be queried before automatic network authoring.

## Common failures and repairs

- **Black/blank layer:** wrong layer/plane selected, domain mismatch, or data outside display range.
- **Halo at alpha edges:** premultiplication assumptions conflict; inspect RGB where alpha is zero.
- **Mask behaves like color:** color transform or multi-component interpretation applied to scalar data.
- **Texture changes resolution midgraph:** implicit domain inheritance; insert explicit reference/domain stage.
- **Saved output differs:** output bit depth, channel list, or color transform differs from preview; reload and compare numerically.

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
