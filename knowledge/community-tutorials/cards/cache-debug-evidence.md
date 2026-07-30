# Cache minimization and debugging evidence

Source kind: `community_tutorial`
Verification: `community_unverified`
Version: sources identify H16/H19.5-era workflows; verify current File Cache and packed-disk interfaces.

## Use, prerequisites, and target

Use when simulations or geometry are expensive, caches are too large, or disk reload behaves differently from live data. Inputs are a validated upstream stream and a documented downstream reconstruction requirement. The target is a versioned cache that stores only necessary data, covers the intended frames, reloads without upstream cooking, and reconstructs the required result.

## Semantic network stages

Live result → schema/count audit → removable-data branch → optional pack/quantize/compress → `File Cache SOP` → filesystem evidence → reload-only branch → reconstruction/comparison → `OUT_CACHE_VALIDATED`.

## Ordered workflow

1. Define the cache contract before deleting data: frame range, geometry/volume type, topology behavior, required attributes/groups/volumes, downstream consumers, path root, version token, and expected reload mode.
2. At `IN_CACHE_SOURCE`, record point/primitive/voxel counts, bounds, attribute owner/type/tuple, groups, packed names/transforms, and representative values. Save a lightweight diagnostic snapshot, not upstream copyrighted/tutorial data.
3. Remove only proven-unused data with `Attribute Delete SOP`, group deletion, volume-field pruning, or render/simulation separation. Keep stable `name`/`id`, transform, velocity, material, UV, rest, or reconstruction fields when downstream needs them.
4. Choose representation: packed primitives or packed disk primitives for repeated rigid pieces; VDB/sparse volumes for fields; lower-precision/quantized attributes only after numeric tolerance tests; topology-plus-transform separation when render geometry can be reconstructed.
5. Configure `File Cache SOP` with an approved versioned project path, explicit frame range, extension, load-from-disk behavior, and overwrite policy. Ensure the filename contains required frame/item tokens and no parallel task collides.
6. Write a short test range first. Verify file existence, non-zero size, monotonically expected frames, aggregate bytes, and no temporary/partial files presented as final.
7. Build a reload-only branch that starts from the cache node/file reader with live upstream bypassed. Reconstruct high-resolution/render data, then compare schema, counts, bounds, mappings, and visual result against the live reference.
8. Only after passing, write the full range. End at `OUT_CACHE_VALIDATED` and record cache version/settings beside the graph.

## Key responsibilities and fields

Audit owns the keep/delete decision; pruning owns schema reduction; representation owns reconstruction strategy; File Cache owns persistence; filesystem checks own artifact truth; reload branch owns independence; comparison owns fidelity. `name`, `id`, `P`, transforms, `v`, topology, material/UV, volume field names, bounds, and frame tokens are common contract fields, not a universal mandatory list.

## Executable parameter and connection contract

1. Connect validated data to `File Cache SOP` through a named `IN_CACHE_SOURCE`. Prefer `File Path = Constructed`; set a unique `Base Name`, project-contained `Base Folder`, enabled numeric `Version`, and `File Type = .bgeo.sc` for general Houdini geometry or `.vdb` only when all primitives are VDBs.
2. Turn `Time Dependent Cache` off for truly static data. For animation set `Evaluate As = Frame Range`, explicit `Start/End/Inc`, and enable simulation-aware evaluation when frames depend on previous frames. The resolved filename must contain a frame token and no two items/nodes may resolve to the same path.
3. Before save, declare required point/vertex/primitive/detail attributes, groups, packed references, and volume names. Use File Cache's attribute deletion/cast controls or upstream `Attribute Delete SOP` only from this list. Quantize/cast one field at a time and measure absolute/relative error; identity and transform fields remain at sufficient precision.
4. Set `Enable Load from Disk on Save` and, for validation, `Missing Frame = Report Error`. Click **Save to Disk** only after a short-range path preview; this workflow does not require background save. After writing, `Load from Disk` must be on and live upstream bypassed.
5. Use **Load = Info** or **Info Bounding Box** for cheap metadata checks, and **All Geometry** for schema/value comparison. Use **Packed Disk Primitive** only when delayed/shared loading fits downstream operations; it is not a substitute for in-memory editable geometry.
6. Compare first/middle/last frames: file exists, non-zero bytes, expected frame token, matching counts/volume list/bounds, and required attribute owner/type/tuple. Then reconstruct render geometry from stable `name`/`id` and compare the declared visual/numeric tolerance.
7. Never mix settings under one version. Any change to topology, source schema, voxel/particle resolution, frame range, precision, or reconstruction mapping creates a new cache version and a deliberate downstream switch.

## Data flow, version, and performance tradeoffs

Smaller caches read faster but can shift cost into reconstruction or lose fidelity. Packed disk data reduces memory but depends on stable external files. Quantization reduces bytes but requires measured tolerance. Compression can trade CPU for I/O. Never mutate or overwrite an established cache version during validation; write a new version and switch explicitly.

## Common failures and repairs

- **Reload is empty:** wrong frame token/path, load mode, extension, or missing file; inspect the resolved path.
- **Render differs:** required attribute/group/material/volume was pruned; compare schema and restore only the dependency.
- **Files huge:** cache includes high-resolution render data, unused fields, or unbounded attributes; revisit reconstruction plan.
- **Frames pop:** mixed versions, changing topology without stable mapping, or missing frames.
- **Upstream still cooks:** reader/bypass is not the active data path or a parameter expression references live upstream.

## Checkpoints and observable evidence

- **D0 — contract:** frame range, resolved path/version, keep/delete schema, topology policy, and reconstruction consumer are recorded before writing.
- **D1 — short write:** expected test files exist, are non-empty, have unique frame tokens, and contain no partial file accepted as final.
- **D2 — reload independence:** `Load from Disk` with live source bypassed cooks successfully; Performance Monitor shows no expensive upstream cook.
- **D3 — fidelity:** required schema, counts, bounds, stable IDs, and representative numeric values pass the declared tolerance; reduction error is measured.
- **D4 — full range:** first/middle/last plus total frame count and aggregate bytes agree with the contract, and reconstructed motion/shading shows no pop.

## When not to use this workflow

Do not cache cheap rapidly changing data whose invalidation cost exceeds recomputation, do not use packed-disk loading when downstream must edit unpacked topology, and do not quantize identity, index, or precision-sensitive fields without a proven tolerance. Never treat an empty result from a missing frame as a valid cache.

## Provenance boundary

VFX Magic/Hiral Joraval's data-optimization tutorial and Mohamed Nagy's simulation caching tutorial motivate reduction plus persistence. The contract, reload-only gate, and artifact checks are original synthesis; no project files or tutorial text are copied.

## Semantic expectations and verification checklist

- Presence: every declared reconstruction field/group/volume exists after reload; intentionally removed data is listed.
- Mapping: stable piece/joint/material identities and frame tokens resolve exactly once.
- Range: files cover the requested frames, are non-empty, and numeric reduction error stays within documented tolerance.
- Cook evidence: reload-only evaluation succeeds with live upstream bypassed and does not trigger the expensive source stage.
- Cache evidence: path, version, per-frame existence/size, aggregate bytes, settings, and incomplete-file policy are recorded.
- Visual evidence: representative first/middle/last frames preserve required shape, motion, shading, and continuity without popping.

## Sources

- VFX Magic / Hiral Joraval, [Reduce Cache Size | Data Optimization Techniques](https://www.sidefx.com/tutorials/reduce-cache-size-by-90-data-optimization-techniques/).
- Mohamed Nagy, [Caching Geometry from Simulation](https://www.sidefx.com/tutorials/caching-geometry-from-simulation/).
