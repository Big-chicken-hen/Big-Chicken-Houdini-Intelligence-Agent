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

## Data flow, cache, version, and performance

Topology may change before packing, but piece identity must stabilize before constraints. Cache packed transforms, not repeatedly deformed high-resolution geometry. Convex hulls are fast but can misrepresent concavity; compound/concave shapes cost more. Excess constraints increase solve cost and can over-stiffen an object. Use physically meaningful pruning and measured collision/constraint complexity.

## Common failures and repairs

- **Pieces explode:** overlapping collision shapes, bad scale/mass, or constraints joining the wrong names.
- **Constraints ignored:** endpoint names do not resolve, constraint type is unsupported/misspelled, or branch is connected to wrong input.
- **Whole object never breaks:** strengths are too high, impacts too weak, or constraint network is excessively connected.
- **Render pieces detach:** transform application uses different/missing `name`.
- **Slow solver:** simplify collision proxies, prune constraints, and keep render geometry outside the solve.

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
