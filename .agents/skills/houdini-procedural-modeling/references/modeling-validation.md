# Modeling Validation

Use the smallest evidence set that can support the requested completion claim.

## Check technical integrity

Verify only the relevant items:

- expected asset root and explicit outputs exist;
- required inputs are connected and relevant nodes have no blocking errors;
- output geometry exists with plausible bounds, scale, orientation, and location;
- required groups, attributes, names, and material regions are present;
- repeated systems produce expected relationships without unintended overlaps;
- continuous paths and sweeps preserve intended profile, frame, seam, and scale behavior;
- hosts, anchors, supports, and affected dependents remain correct after representative upstream changes;
- important controls make local, predictable changes;
- required UV or alternative coordinate sets and stable semantic groups remain valid after topology or parameter changes;
- costly stages, quality controls, instances, and caches behave as intended;
- the graph has no overlapping nodes, reverse wires, obvious crossings, needlessly long wires, default important names, disconnected experiments, or ambiguous final output.

Use `hia_validate`, `hia_scene_diff`, inspection, or a bounded read-only HOM query only when it contributes evidence. Do not call every available tool.

## Prove assembly relationships numerically

Whenever completion depends on attachment or fit, including in a small multi-part assembly, define only the relevant numeric invariants before authoring: both endpoints resolve to their intended hosts, the component stays within its intended span or bounds, required contact is within a scale-relative tolerance, required clearance has a measured minimum, and forbidden intersection has an appropriate precise result. Recheck the same invariants after the write and after any upstream control change that can move the parts.

Use AABB or packed bounds as a broad phase: disjoint bounds can reject a pair, while overlapping bounds identify a candidate rather than prove penetration. Neither a viewport image, an error-free cook, nor generic validation silently proves precise nonintersection. Use a bounded native Houdini diagnostic or demonstrably non-mutating HOM calculation when the claim requires endpoint distance, closest distance, or surface intersection evidence. If the measurement cannot be observed reliably, keep that narrow completion claim unverified.

Apply explicit negative constraints to the observed result, not merely to node names or implementation. A semantically equivalent substitute for a forbidden form fails acceptance even if it was produced by a different node, script, or geometry representation.

## Prove retrieved semantics

For a Focused or Full change, translate only implementation-relevant knowledge into explicit expectations before authoring: the target path, group or attribute, ownership/type, mapping, relationship, range or tolerance, affected dependency, and representative control change. After the write, ask the current `hia_validate` semantic-expectation capability to test those claims through its live contract. Do not copy its payload schema into this reference. A clean cook or nonempty mesh is insufficient when the researched claim concerns a host relationship, stable material region, expected placement range, or dependency update.

Treat an unproven semantic result as a real risk. State the missing observation once and choose a bounded additional check only when it can settle the claim; do not repeat the warning or launch a tool cascade. For a simulation/cache handoff, apply the evidence rule in the shared risk-routing and review reference rather than inferring freshness from a clean cook.

## Check visual identity

Use a representative viewport or render view only when visual evidence is needed. Judge:

- recognizable silhouette and overall proportions;
- primary, secondary, and tertiary hierarchy;
- defining components and negative spaces;
- grounding, supports, contacts, clearances, intersections, and thickness;
- edge treatment and repeated-detail scale;
- whether the result still reads as a generic primitive assembly;
- whether simplification matches the requested style and delivery purpose.

Keep the user's camera, viewport, pane layout, current network, and display state unchanged or restore any temporary inspection state. Do not prescribe fixed capture or iteration counts.

## Test editability selectively

Change representative high-value controls only when safe and necessary. Confirm that dimensions, proportions, counts, spacing, variation, or quality affect the intended subsystem without breaking unrelated parts. Avoid exhaustive parameter sweeps and do not save over the user's scene merely for validation.

## Correct and stop deliberately

When evidence reveals a high-impact issue:

1. Attribute it to the responsible subsystem or to camera, material, lighting, or post rather than guessing.
2. Change the smallest affected region or control set.
3. Recheck enough evidence to determine whether the intended improvement occurred.
4. Stop when the requested completion standard is supported or when remaining uncertainty requires user input or unavailable visual evidence.

Do not create a scoring system, fixed-count ritual, or backend automatic review-fix engine. In a project-team Full Goal, route live technical claims through Technical Review and actual-image claims through Visual Review in parallel, using `$houdini-artifact-review` inside the applicable read-only Thread. Let Supervisor select the minimum coherent fix, let only Execution apply it, and repeat both reviews without a fixed iteration count until verified or genuinely blocked.

## State completion honestly

- Call work a technical test only when the construction route is demonstrated without a visual completion claim.
- Call work a blockout or proxy when only scale, primary masses, and major proportions were requested or resolved.
- Call a stylized or low-poly asset complete only when its simplification is intentional and its defining structure is present.
- Call a procedural asset complete only when identity, structure, controls, readable outputs, relevant performance behavior, and both technical and visual evidence support the claim.

If representative visual evidence is unavailable, report: **technically checked; visually unverified**.
