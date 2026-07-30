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

## Executable parameter and connection contract

1. Connect source particles to input 1 (**Sources**) of `FLIP Solver SOP`, the `FLIP Container SOP` surface to input 2 (**Container**), and merged `FLIP Collide SOP` outputs to input 3 (**Collisions**). Input 4 is reserved for boundary flow; ordinary colliders do not belong there.
2. Copy the `Particle Separation` value from `FLIP Container SOP` to the solver's `Particle Separation` exactly. Keep it positive and expose one shared quality parameter rather than typing two independent values. Record `Grid Scale` and preserve its default for the first volume-retention test; change it only after a force-free short solve demonstrates compression or expansion.
3. Start with `Time Scale = 1`. Keep `Min Substeps` at its default and set `Max Substeps` only high enough to let the solver respond to the measured motion. For a controlled comparison, test ceilings `1`, `2`, then `4` on the same short frame window; do not simultaneously change particle separation, collider resolution, and substeps.
4. For closed 3D collision volumes use `Particle Collisions = Move Outside Collision`; use **Particle** mode for open or 2D collision geometry when the prepared `FLIP Collide SOP` representation agrees. Keep `Surface Extrapolation > 0`. If sticky motion is intentional, `Stick Scale` is a blend where `0` contributes none and `1` fully matches collision velocity.
5. Choose `Velocity Transfer = FLIP (Splashy)` for a noisy high-energy baseline or `APIC (Swirly)` when small-scale swirling motion and lower surface noise are the goal. Record the choice because it materially changes the result.
6. If `Enable Particle Narrow Band` is on, retain reseeding: the solver requires it. Record `Velocity Band`, `Pressure Band`, and `Source Band`, and confirm that the particle band covers all exposed surfaces. Turn `Waterline` off when input 4 supplies boundary transfer. For a tank not touching boundaries, first validate with the simpler initial-surface/container contract.
7. With `Apply Particle Separation` enabled, begin with `Separation Iterations = 1`; increase only if measured compression persists. Validate `Birth Threshold`, `Death Threshold`, and `Oversampling Bandwidth` through particle-count plots instead of judging only the mesh.
8. Cache the solver's particle output before meshing. Feed the disk-loaded particles to `Particle Fluid Surface SOP`; preserve its default `Voxel Scale`, `Influence Scale`, and filter for the reference, then vary one control at a time. The surface branch must never read the live solver when **Load from Disk** is active.

## Data flow, cache, version, and performance

Particle separation dominates particle count roughly cubically. Collision and surface voxel scales must be evaluated relative to it. Narrow band can reduce work when its assumptions fit the shot, but it needs coverage checks. Cache particles before meshing so surface settings can iterate without resimulation. Never combine frames from caches with different separation, source geometry, or solver settings.

## Common failures and repairs

- **Fluid leaks:** inspect SDF sign/thickness/resolution and tunneling first; then consider substeps.
- **Volume disappears or swells:** inspect reseeding, source/sink volumes, boundary conditions, and particle coverage.
- **Explosive first frame:** particles overlap collider or begin with extreme velocity; visualize initial state.
- **Mesh blobby/thin:** adjust surface influence/filter relative to separation; do not resimulate until particles are proven wrong.
- **Cache changes midrange:** path version collision or missing frames. Verify filenames, frame count, and reload-only cook.

## Checkpoints and observable evidence

- **F0 — initial state:** source particle count is non-zero, `P`, `v`, and `pscale` are finite, no source point lies inside the collision SDF, and solver/container `Particle Separation` values are identical.
- **F1 — collision contract:** `Show Collision` displays every collider at the expected transform and thickness. A short gravity-only solve shows no persistent points on the forbidden side; failures are grouped and counted rather than hidden by surfacing.
- **F2 — particle solve:** on a ten-to-twenty-frame diagnostic window, record min/max particle count, maximum speed, and domain occupancy. Counts change only through declared sources, sinks, reseeding, or boundary behavior.
- **F3 — disk independence:** first/middle/last particle cache files reload with the same point attributes and no solver recook. Changing `Particle Fluid Surface` parameters changes only the mesh cook.
- **F4 — surface:** compare particle spheres and mesh silhouette on the same frame. Thin sheets, bubbles, and splash tips remain represented; polygon count, open-boundary count, and bounds stay within the shot contract.

## When not to use this workflow

Do not use FLIP for a dry granular material that Vellum grains handles more directly, a nearly rigid viscous blob that can be animated or deformed procedurally, a distant ocean surface that needs only a spectral representation, or a gas. Avoid narrow-band FLIP when the shot requires meaningful deep interior particles everywhere or when its boundary assumptions cannot be satisfied.

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
