# FLIP scale, collision, reseeding, and cache checks

Source kind: `community_tutorial`
Verification: `community_unverified`
Version: creator pages identify H20 and H21; confirm FLIP Solver SOP controls in the active build.

## Use, prerequisites, and target

Use for tanks, pours, splashes, moving containers, or object-fluid interaction. Inputs must be watertight enough for the chosen fill method, consistently scaled, and animated before the simulation boundary. The target is a reproducible particle solve plus a separately cached surface workflow, with explicit particle separation, collision resolution, reseeding, and frame evidence.

## Semantic network stages

Source/collider preparation → `FLIP Container SOP` or source-volume setup → `FLIP Solver SOP` → particle diagnostics → particle `File Cache SOP` → `Particle Fluid Surface SOP` → mesh cleanup → mesh cache → `OUT_FLIP_SURFACE`. Collider geometry enters a side input; do not merge it into the fluid particle stream.

## Ordered workflow

1. Audit physical scale, frame rate, closed surfaces, normals, and animation velocity. Name fluid geometry `IN_FLUID_SOURCE` and colliders `IN_COLLIDERS`. Visualize holes and thin features before simulating.
2. Choose domain and particle separation. Build a container/tank or source particles from a volume. Confirm particles fill the intended interior without starting inside colliders. Record separation and approximate particle count as the quality contract.
3. Prepare collisions at a resolution compatible with particle separation. Inspect collision SDF/guide, sign, thickness, transform, and collider velocity. Thin walls need sufficient thickness or a finer collision representation; solver substeps cannot repair a fundamentally missing wall.
4. Configure `FLIP Solver SOP`: gravity, boundary behavior, substeps/CFL controls, reseeding, particle radius scale, viscosity/surface tension when required, and narrow-band behavior if used. Change one physical family at a time.
5. Add sources, sinks, and forces through named inputs/fields. Validate emitted particle count and inherited `v`. For moving sources, ensure source motion produces reasonable velocity instead of teleporting particles.
6. Run a short wedge of diagnostic frames. Inspect particle coverage, speed range, collision penetration, volume loss/gain, and reseeding density before launching a full range.
7. Cache particles/fields first with `File Cache SOP`. Use a versioned path, fixed frame range, and metadata note containing separation, substeps, and source version. Reload from disk and bypass the solver.
8. Surface cached particles with `Particle Fluid Surface SOP`. Tune influence scale, voxel scale, filtering, and adaptivity against silhouette retention. Cache the mesh separately and end at `OUT_FLIP_SURFACE`.

## Key responsibilities and fields

The source stage owns `P`, `v`, particle identity, and emission groups. The solver owns particle motion and any surface/viscosity attributes. Collision representation owns inside/outside and motion. Particle Fluid Surface owns VDB reconstruction and polygon conversion; it must not hide particle-level simulation faults.

## Data flow, cache, version, and performance

Particle separation dominates particle count roughly cubically. Collision and surface voxel scales must be evaluated relative to it. Narrow band can reduce work when its assumptions fit the shot, but it needs coverage checks. Cache particles before meshing so surface settings can iterate without resimulation. Never combine frames from caches with different separation, source geometry, or solver settings.

## Common failures and repairs

- **Fluid leaks:** inspect SDF sign/thickness/resolution and tunneling first; then consider substeps.
- **Volume disappears or swells:** inspect reseeding, source/sink volumes, boundary conditions, and particle coverage.
- **Explosive first frame:** particles overlap collider or begin with extreme velocity; visualize initial state.
- **Mesh blobby/thin:** adjust surface influence/filter relative to separation; do not resimulate until particles are proven wrong.
- **Cache changes midrange:** path version collision or missing frames. Verify filenames, frame count, and reload-only cook.

## Provenance boundary

Sergio Neza's explanatory FLIP series and Svava Jóhannsdóttir's box example motivate the scale-and-debug focus. The staged cache/surface design and checks are original synthesis. Exact H21+ defaults are not verified here.

## Semantic expectations and verification checklist

- Presence: simulation particles have finite `P` and `v`; required source/collision inputs cook.
- Mapping: source and collider branches connect to their intended solver inputs; surface reads the cached particle stream.
- Range: separation is positive; particle speeds and counts remain within shot-specific documented bounds.
- Cook evidence: the diagnostic frame window advances without fatal warnings and does not unexpectedly recook when reading particle cache.
- Cache evidence: every requested particle and mesh frame exists, is non-empty, and belongs to one settings version.
- Visual evidence: no unexplained leaks, initial explosions, particle voids, boundary clipping, or temporal surface popping.

## Sources

- Sergio Neza, [FLIP Fluids Simply Explained](https://www.sidefx.com/tutorials/houdini-tutorial-introduction-to-flip-fluids-simply-explained-part-1/).
- Svava Jóhannsdóttir, [How to FLIP (a box)](https://www.sidefx.com/tutorials/how-to-flip-a-box/).
