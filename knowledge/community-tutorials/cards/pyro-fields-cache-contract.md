# Pyro field sourcing, bounds, and cache contracts

Source kind: `community_tutorial`
Verification: `community_unverified`
Version: sources span H17.5 and later DOP/SOP workflows; confirm the active sparse Pyro interfaces.

## Use, prerequisites, and target

Use for smoke, fire, explosions, or custom gaseous fields. Inputs are animation at real-world-like scale plus source attributes/volumes. The target is a sparse Pyro network whose source mappings, container bounds, voxel size, solver fields, and cache payload can be inspected and reproduced.

## Semantic network stages

Geometry source → `Pyro Source SOP` → `Volume Rasterize Attributes SOP` → source-field inspection → `Pyro Solver SOP` → field inspection → `File Cache SOP` → render conversion/output. Keep collision preparation on a left branch into the solver's collision input.

## Ordered workflow

1. Audit scene scale, frame rate, source velocity, and emitter bounds. Apply transforms before simulation and name the boundary `IN_PYRO_SOURCE`. Fast motion may need substeps or trail-derived velocity.
2. Use `Pyro Source SOP` to create purposeful source attributes such as `density`, `temperature`, `burn`, `flame`, and `v`. Do not author every field by habit. Visualize each scalar attribute and velocity before rasterization.
3. Rasterize with `Volume Rasterize Attributes SOP`. List the exact attributes, choose voxel size, and map velocity to the expected vector volume. Add padding sufficient for filter width and motion. Inspect volume names, classes, resolutions, and active bounds.
4. Configure `Pyro Solver SOP` sourcing so source volume names map to solver fields with intended operations: add, copy, maximum, or pull according to the desired accumulation. General principle: the destination field and operation are part of the contract; similar-looking smoke can hide a wrong mapping.
5. Set sparse bounds and voxel size from a controlled quality parameter. Enable/adapt expansion to follow active fields while retaining a safety margin. Tune dissipation, buoyancy, cooling, disturbance, turbulence, and shredding one family at a time; avoid compensating for bad sourcing with extreme shaping.
6. Validate collisions from the solver's guide/diagnostic views. Collision SDF sign, thickness, transform, and velocity must match the solver domain.
7. Cache simulation fields with `File Cache SOP` after the solver. Include only fields required for rendering or downstream work, but retain at least density and any temperature/flame/velocity fields used later. Use an explicit frame range and non-overwriting versioned path.
8. Reload the cache in a separate branch, bypass live simulation, and compare field list, bounds, frame coverage, and a representative viewport/render. Finish at `OUT_PYRO_CACHE`.

## Key fields and responsibilities

`density` controls smoke presence; `temperature` commonly drives buoyancy/cooling; `vel` is the vector velocity field; `flame`/`burn` roles depend on the chosen combustion workflow. Pyro Source authors point fields, Rasterize converts them to volumes, Solver evolves destination fields, and File Cache proves persistence. Treat these meanings as workflow contracts, not universal renderer bindings.

## Data flow, cache, version, and performance

Voxel count grows cubically as voxel size decreases; sparse bounds still become expensive when padding or active regions expand. Prototype at coarse resolution, keep shaping scale-aware, then lower voxel size at a controlled cache stage. Never mix cache frames generated with different voxel sizes or source schemas under one version. Older DOP-network examples remain useful conceptually, but current SOP-level Pyro parameters can differ.

## Common failures and repairs

- **No smoke:** inspect rasterized volume names and source-to-destination mapping before increasing density.
- **Domain clips:** increase adaptive padding/expansion or correct source/collision bounds; verify guides on the failing frame.
- **Fire rises unrealistically:** check scale, temperature range, buoyancy, and time scale as a system.
- **Flickering cache:** missing frames, mixed versions, or changing topology/bounds; inspect files and cache metadata.
- **Excess memory:** coarsen voxel size, restrict fields and bounds, and cache only downstream-required volumes after validation.

## Provenance boundary

Matt Estela's DOP notes and Mark Spevick's fire/flames tutorial motivate field-oriented debugging. The sparse SOP topology, mapping table, and cache evidence are original synthesis. Exact solver parameter availability is unverified.

## Semantic expectations and verification checklist

- Presence: rasterized and simulated volume names match the declared field map.
- Mapping: every source field names one destination and operation; collision input resolves to the intended geometry/SDF.
- Range: fields contain finite values; density is non-negative; active bounds enclose meaningful voxels with margin.
- Cook evidence: solver advances across the requested frame range without recooking unrelated upstream topology.
- Cache evidence: expected files exist for every frame; reload lists the required fields and matching resolution/bounds.
- Visual evidence: source emission, motion, dissipation, and collision response are visible without container clipping or one-frame discontinuities.

## Sources

- Matt Estela, [Dops](https://www.tokeru.com/cgwiki/HoudiniDops.html).
- Mark Spevick, [PyroFX Fire & Flames](https://www.sidefx.com/tutorials/pyrofx-fire-flames/).
