# RBD packed pieces and constraint networks

Source kind: `community_tutorial`
Verification: `community_unverified`
Version: sources identify H17.5/H18.5-era workflows; verify current RBD Bullet Solver SOP attributes.

## Use, prerequisites, and target

Use for fractured objects, breakable assemblies, glue/soft/hard constraints, or controlled destruction. Inputs are non-overlapping fracture pieces with stable names, proxy collision geometry, and intentional constraints. The target is a packed-piece solve where every constraint endpoint resolves by identity and breakage can be inspected independently of render geometry.

## Semantic network stages

Fracture/assembly → piece naming → pack/collision properties → constraint construction → `RBD Bullet Solver SOP` → transform/cache output → high-resolution transform application → `OUT_RBD`. Constraint creation is a left branch joining the solver's constraint input.

## Ordered workflow

1. Fracture or assemble geometry, then create a unique primitive string `name` per piece before packing. Check uniqueness and that no intended piece has zero volume or degenerate bounds.
2. Use `RBD Configure SOP` or explicit packed-primitive setup to establish active/animated state, mass/density, collision shape, friction, bounce, and proxy geometry. Keep high-resolution render geometry separate but sharing `name`.
3. Build candidate constraint lines between intended pieces using proximity/connectivity logic. Each constraint primitive must identify both endpoints through the current contract, commonly endpoint `name` values and position. Restrict generation by groups, material region, distance, or direction to avoid a meaningless dense graph.
4. Assign constraint semantics and properties: `constraint_name` (for example glue, hard, soft), strength/break threshold, stiffness, damping, and optional propagation fields supported by the active workflow. Separate types into named groups when solver relationships differ.
5. Inspect the constraint network before simulation. Draw lines, color by type/strength, count unresolved endpoints, and histogram connection degree. Repair missing names or duplicates before raising strength.
6. Connect packed pieces, constraints, and colliders to `RBD Bullet Solver SOP`. Set Bullet substeps/constraint iterations based on tunneling or joint error. Add activation rules only after a stable baseline solve.
7. Cache packed transforms and useful breakage/constraint state. Apply transforms to render pieces by `name` using the appropriate transform-pieces workflow; do not unpack heavy render geometry inside the solver.
8. Validate a reload-only branch and output `OUT_RBD`, plus an optional constraint-debug output.

## Key responsibilities and fields

Fracture owns piece topology; naming owns stable identity; packing owns transform representation; configure owns physical/collision properties; constraint branch owns endpoints, `constraint_name`, and strength; solver owns motion/break state. `name` is the central mapping field. Exact Bullet attribute names differ across node versions, so inspect the node's spreadsheet/help rather than inventing a field.

## Executable parameter and connection contract

1. Build a vertical render-geometry branch `RBD Material Fracture SOP -> Assemble SOP` (or the current fracture/packing equivalent). On `Assemble SOP`, enable creation of a primitive string `name` and packed geometry. The value must be non-empty and unique per packed primitive.
2. Branch proxy pieces into `RBD Configure SOP`. Set `Active`, `Animated`, `Collision Geometry`, `Density`, `Friction`, and `Bounce` explicitly for the task. Contract ranges are `density > 0`, `friction >= 0`, and normally `0 <= bounce <= 1`; retain the starting defaults until a controlled physical test justifies a change.
3. From unpacked named pieces, build constraints with `Connect Adjacent Pieces SOP` or explicit lines, then use `RBD Constraint Properties SOP`. Constraint primitives need endpoint identity that resolves to the piece `name`; declare `constraint_name` such as `Glue`, `Hard`, or `Soft` using the spelling emitted/accepted by the active nodes. Keep strength finite and non-negative. Do not invent a raw attribute when the SOP can author the active schema.
4. Feed configured packed pieces to input 1 of `RBD Bullet Solver SOP`, constraint geometry to the constraint input, and collider geometry to its collision input. Keep high-resolution render pieces outside the solver and later drive them with `Transform Pieces SOP` using `name`.
5. On the solver, use `Global Substeps = 1` for the baseline. If fast pieces tunnel, raise `Bullet Substeps` in a measured sequence such as `1 -> 2 -> 4`; using Bullet substeps is more efficient than raising global substeps when no custom substepped solver output is required. If joints visibly violate their target while collisions are detected, increase `Constraint Iterations` separately.
6. Record `Collision Padding` in scene units and enable `Shrink Collision Geometry` when padding would otherwise enlarge shapes. Padding must be small relative to the smallest proxy dimension. Use `Solve Tolerance` only after a stable reference: larger tolerance may stop iteration earlier and trade accuracy for speed.
7. Cache packed simulation geometry and any required broken-constraint output. `Transform Pieces SOP` input 1 receives high-resolution named pieces and the transform/reference inputs receive the cached packed pieces according to the active node interface. Confirm the `name` set matches before accepting the result.

## Data flow, cache, version, and performance

Topology may change before packing, but piece identity must stabilize before constraints. Cache packed transforms, not repeatedly deformed high-resolution geometry. Convex hulls are fast but can misrepresent concavity; compound/concave shapes cost more. Excess constraints increase solve cost and can over-stiffen an object. Use physically meaningful pruning and measured collision/constraint complexity.

## Common failures and repairs

- **Pieces explode:** overlapping collision shapes, bad scale/mass, or constraints joining the wrong names.
- **Constraints ignored:** endpoint names do not resolve, constraint type is unsupported/misspelled, or branch is connected to wrong input.
- **Whole object never breaks:** strengths are too high, impacts too weak, or constraint network is excessively connected.
- **Render pieces detach:** transform application uses different/missing `name`.
- **Slow solver:** simplify collision proxies, prune constraints, and keep render geometry outside the solve.

## Checkpoints and observable evidence

- **R0 — pieces:** packed primitive count equals unique non-empty `name` count; proxy bounds and volumes are positive and no two proxies begin with unintended deep overlap.
- **R1 — constraints:** every line has two resolvable endpoints, a recognized `constraint_name`, and finite strength. Display color by type and log unresolved endpoints as zero.
- **R2 — baseline solve:** with a simple ground and one impact, intact bonds hold before the intended load. Record penetrating-contact count, maximum speed, and constraint-error/broken counts.
- **R3 — quality isolation:** a Bullet-substep change reduces missed collisions without changing the constraint graph; an iteration change reduces joint error without changing collision proxy geometry. If neither metric changes, revert the extra cost.
- **R4 — reconstruction:** disk-loaded packed pieces preserve the `name` set and transforms. High-resolution pieces follow them one-to-one with no unmatched names, double transforms, or render-geometry cooking inside the solver.

## When not to use this workflow

Do not use Bullet packed RBDs for continuously deforming rubber/cloth, fluid fracture, or a hero bend that requires finite-element deformation. Avoid dense glue networks for an object that can remain a single rigid piece until an authored trigger; activation or a simpler break switch may be clearer and cheaper.

## Provenance boundary

Matt Estela's constraint-network notes and Akil Tasi's RBD tutorial motivate identity-first constraints. The staged topology and validation gates are original synthesis. Current attribute schemas remain unverified.

## Semantic expectations and verification checklist

- Presence: every packed piece has a non-empty unique `name`; every constraint has a declared type and endpoints.
- Mapping: constraint endpoints and render pieces resolve to exactly one packed `name`.
- Range: mass and collision extents are positive; constraint strengths and velocities are finite.
- Cook evidence: unresolved-endpoint count is zero and piece/constraint counts are recorded before and after solve.
- Cache evidence: each frame reloads the same piece identities and expected transform/break fields.
- Visual evidence: intact regions hold, impacts break intended bonds, collisions do not visibly interpenetrate, and render pieces follow packed transforms exactly.

## Sources

- Matt Estela, [Constraint Networks 3: A new hope](https://www.tokeru.com/cgwiki/ConstraintNetworks3.html).
- Akil Tasi, [RBD Constraints](https://www.sidefx.com/tutorials/houdini-tutorial-rbd-constraints/).
