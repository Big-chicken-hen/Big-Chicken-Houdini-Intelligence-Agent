# SOP attributes and VEX data contracts

Source kind: `community_tutorial`
Verification: `community_unverified`
Version: source pages do not pin one common Houdini version; verify node names and signatures in the active build.

## Use, prerequisites, and target

Use this playbook when a SOP network creates, converts, promotes, or consumes geometry attributes with VEX. Start with cookable geometry and identify the downstream node that needs each value. The target is an editable network where every important attribute has an explicit owner, storage type, tuple size, range, and consumer. No downstream node should depend on an accidental default or an attribute on the wrong element class.

## Semantic network stages

1. **Input audit** — `Null SOP` named `IN_GEOMETRY`, Geometry Spreadsheet, and optional `Attribute Delete SOP`.
2. **Contract creation** — `Attribute Create SOP`, `Attribute Wrangle SOP`, or a node that natively authors the field.
3. **Class conversion** — `Attribute Promote SOP` only when the semantic owner really changes.
4. **Consumption** — copy, sweep, shading, simulation, grouping, or export nodes.
5. **Evidence/output** — `Attribute Visualize SOP`, diagnostic wrangle, and `Null SOP` named `OUT_ATTR_CONTRACT`.

Connect these vertically. Put debug visualization on a side branch from the post-contract stream, not in the production trunk.

## Ordered workflow

1. Freeze the input boundary with `IN_GEOMETRY`. Record point, vertex, primitive, and detail counts. Inspect existing names in the Geometry Spreadsheet; do not assume `Cd`, `N`, `up`, `orient`, `name`, or `id` already has the desired class and type.
2. Write a small contract table before authoring: for example `mask = point float[1], 0..1`; `orient = point quaternion float[4]`; `piece = primitive string`; `seed = detail integer`. This is a general Houdini principle, not a claim that every tutorial uses this table.
3. Prefer a native SOP when it expresses the operation clearly. Use `Attribute Create SOP` for a declared default or `Attribute Wrangle SOP` for derived values. Set Run Over to the intended owner. Common bindings include `f@mask`, `i@id`, `s@name`, `v@N`, `p@orient`, and `detail(0, "seed", 0)`. Do not use an untyped `@value` when tuple shape matters.
4. Derive values in dependency order. For a normalized mask, compute the raw measurement first, guard a zero-width interval, then clamp: `f@mask = clamp(fit(value, low, high, 0.0, 1.0), 0.0, 1.0);`. Treat this as an original illustrative expression, not copied tutorial code.
5. If an owner must change, place `Attribute Promote SOP` immediately before its new consumer. Choose Source/Original Class, Destination Class, and Promotion Method deliberately. Average is suitable for a smooth aggregate; Maximum, Minimum, or First Match can preserve different semantics. Rename the result if the promoted meaning differs.
6. Wire the consumer and explicitly bind its attribute parameter. Examples: Copy to Points reads `orient`, `scale`/`pscale`, and `N`; Sweep reads curve frame and width attributes; material or simulation nodes read their named masks. Never assume the consumer will choose the intended field when multiple attributes exist.
7. Add a diagnostic branch. Use `Attribute Visualize SOP` for scalar/vector range, or a detail wrangle that counts missing/non-finite values. Keep it bypassable. Finish the production trunk at `OUT_ATTR_CONTRACT`.

## Data flow, caching, and cost

Attribute dependencies flow from owner creation to conversion to consumption. A cache boundary belongs after expensive deterministic derivation and before interactive consumers; cache only attributes that are needed to reconstruct the next stage. Point wrangles are parallel but can be wasteful when repeatedly calling broad searches. Detail wrangles serialize work and should not replace simple native SOPs without measurement.

## Common failures and repairs

- **All values are zero:** confirm Run Over class, input index, spelling, and that the source attribute exists before the wrangle.
- **Red attribute type warning:** an earlier node created the same name with a different storage or tuple size. Rename or delete/recreate it explicitly.
- **Promoted result looks smeared:** the aggregation method destroyed discontinuities. Keep the attribute on vertices or use a non-averaging method.
- **Copy orientation rolls:** distinguish `N`/`up` frame construction from a quaternion `orient`; inspect tuple size and normalize inputs.
- **Cache reload changes behavior:** the cache omitted a string/name/id attribute or converted its class. Compare pre/post schemas and counts.

## Provenance boundary

Matt Estela's Joy of Vex and Moritz's attribute lesson motivate the attribute-first teaching approach. The contract table, staged topology, expression, and checks here are this project's synthesis. All parameter names and behavior remain unverified in the current Houdini build.

## Semantic expectations and verification checklist

- Presence: every declared attribute exists on the intended owner at `OUT_ATTR_CONTRACT`.
- Mapping: consumer parameters name the declared attributes; promoted outputs have documented source and destination classes.
- Type/range: storage and tuple sizes match the contract; normalized masks are finite and within `[0,1]`; orientation quaternions are finite.
- Cook evidence: the trunk cooks without attribute-type warnings, and element counts change only at intended topology stages.
- Cache evidence: pre/post cache counts, attribute names, classes, and representative values agree.
- Visual evidence: attribute visualization has the expected spatial pattern, with no unexplained uniform field, seam, or roll.

## Sources

- Matt Estela, [Joy of Vex](https://tokeru.com/cgwiki/JoyOfVex.html).
- Moritz, [Houdini In Five Minutes 03: Attributes](https://entagma.com/houdini-in-five-minutes-03-attributes/).
