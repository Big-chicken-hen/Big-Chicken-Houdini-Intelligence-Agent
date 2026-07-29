# SOP procedural modeling and attributes

Pack version: 2.0.0

## Use for

Use this workflow for editable geometry systems built from SOP nodes, especially when later stages must respond predictably to changed dimensions, topology, masks, variants, or art-direction controls. It is appropriate when a network needs stable semantic selections, reusable piece identities, and explicit outputs rather than a single destructive modeling result. Prefer a clear native SOP when it already expresses an operation well; use attributes to carry decisions downstream, not as a substitute for understandable network structure.

## Context and core data

SOP geometry contains a detail, primitives, points, and vertices. Attributes belong to one of those owners. If the same attribute name exists on several owners, Houdini resolves the more specific value first: vertex, then point, then primitive, then detail. Common conventions include point `P`, `N`, `v`, `id`, `pscale`, `scale`, and `orient`; primitive `name` or `piece`; vertex `uv`; and detail metadata. Point and primitive numbers are transient indices, not durable identities. Attribute type qualifiers such as position, vector, normal, quaternion, color, and texture coordinate affect how transforms and tools interpret values.

## Recommended data flow

Build from coarse to fine:

`inputs or generators -> scale/topology normalization -> primary form -> stable names, IDs, groups, and masks -> structural branches -> local detail -> merge -> normals and UVs -> packing or cache -> OUT_*`

Keep topology-changing operations before downstream selections that depend on topology. Place semantic attributes near the stage where their meaning becomes valid. Use separate branches for independent subsystems and merge them only after each branch has a clear responsibility. End with an explicitly named output and remove abandoned experiments.

## Step-by-step workflow

1. Inspect incoming geometry, world scale, primitive types, attribute owners, and existing names before adding construction nodes.
2. Establish the primary dimensions and silhouette with the fewest stable generators and transforms possible.
3. Normalize topology where required, then create stable `name`, `piece`, or `id` attributes before any stage that needs persistent correspondence.
4. Derive groups and masks from attributes or geometric tests instead of hard-coded component numbers.
5. Build structural and detail branches separately, preserving semantic names across merges and copies.
6. Recompute or validate normals, UVs, bounds, and output groups after the final topology-changing stage.
7. Add a named `OUT_*` node, inspect its cooked geometry, and only then introduce caching, packing, or asset wrapping.

A minimal documented reproduction, not live-run during this corpus rebuild, is `Grid (Rows 3, Columns 4) -> Attribute Create -> Group Expression -> OUT_CHECK`. On Attribute Create, set Class to Point, Name to `class_tag`, Type to Integer, and Value to `7`. On Group Expression, create the point group `tagged` with the VEX expression `i@class_tag == 7`. The expected output is 12 points, a point integer attribute whose value is `7` on every point, and 12 members in `tagged`. Changing Grid Rows to 5 should produce 20 points and 20 group members without editing a component-number list. MMB and the Geometry Spreadsheet provide the expected count, owner, storage, and membership evidence.

## Critical parameters and attributes

- `P` is point position; changing it deforms geometry.
- `N` is a direction and must be treated as a normal rather than an arbitrary float tuple.
- `id` should remain stable when point order may change.
- `name` and `piece` are preferred for piecewise operations and matching.
- `pscale` is uniform scale; `scale` is a three-component non-uniform scale.
- `orient` is a quaternion used by copying and instancing.
- `uv` is commonly a vertex texture-coordinate attribute so seams can hold different values on shared points.
- Attribute Create's class, storage type, tuple size, precision, and type qualifier must match the intended consumer.

An Attribute Create node evaluates each value expression from its input geometry, not from attributes created earlier in the same node. Split dependent creations into successive nodes.

## Cache, version, and performance

Delay expensive detail until the primary form is approved. Use lower attribute precision only when a measured error budget permits it. Avoid repeatedly converting between polygon soups and regular polygons because conversion cost can exceed any memory benefit. Profile before compiling or caching. Cache at semantic boundaries where expensive upstream work is stable and downstream art direction remains live. When an HDA exposes this network, version behavior changes rather than silently altering completed scenes.

## Validation

Use MMB node information and the Geometry Spreadsheet at each major stage. Confirm point and primitive counts, primitive types, attribute owner, storage type, tuple size, value range, group membership, bounds, and warnings. The minimal reproduction above passes statically only when `class_tag` is a point integer, `tagged` follows the generated point count, and no component-number selection was introduced. Test at least a small, nominal, and large parameter value. Temporarily visualize IDs, masks, normals, and piece names. After topology edits, verify that selections still identify the intended regions and that no unexpected disconnected or degenerate components appear.

## Common failures and troubleshooting

- A selection drifts after remeshing or extrusion: replace element-number selections with a regenerated group or stable attribute.
- A point attribute appears ignored: look for a same-named vertex attribute overriding it.
- A copied part changes unpredictably: seed randomness with `id` or `name`, not point number.
- A transform changes an attribute incorrectly: check its type qualifier.
- A group is empty after a topology change: recreate it after that change or transfer a semantic attribute through the operation.
- An attribute has the wrong tuple type: delete or cast the conflicting attribute before recreating it.
- A network cooks but produces fragile results: move identity creation earlier and isolate topology-changing stages.

## When not to use

Do not build an attribute-heavy framework for a one-off primitive, a single parameter edit, or a task already solved clearly by one native SOP. Do not preserve point numbers as persistent IDs across topology changes. Do not convert to packed geometry before internal deformation or topology editing is complete. Avoid opaque chains of tiny attribute nodes when one well-named SOP or Wrangle communicates the operation better.

## Houdini 21 notes

Houdini 21 adds Attribute Sort, Separate Pieces, Split by Point Attribute, Edge Relax, Unsubdivide, and other modeling tools. Poly Bridge and Poly Extrude can generate side UVs, several deformation nodes gained mask parameters, Group SOP improved unshared-edge performance, and UV Fuse became compilable. These additions strengthen attribute-driven construction but do not change the fundamental geometry-owner and precedence rules.

## Official sources

- SideFX, [Geometry attributes](https://www.sidefx.com/docs/houdini/model/attributes.html), accessed 2026-07-26.
- SideFX, [Geometry](https://www.sidefx.com/docs/houdini/model/index.html), accessed 2026-07-26.
- SideFX, [Attribute Create SOP](https://www.sidefx.com/docs/houdini/nodes/sop/attribcreate.html), accessed 2026-07-26.
- SideFX, [What's new: Modeling, geometry, and terrains in Houdini 21](https://www.sidefx.com/docs/houdini/news/21/model.html), accessed 2026-07-26.
- SideFX, [H21 Foundations: Welcome](https://www.sidefx.com/tutorials/h21-foundations-welcome/), accessed 2026-07-26.
