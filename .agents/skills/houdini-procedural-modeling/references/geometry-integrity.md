# Geometry Integrity

Use this workflow only when penetration, overlap, or self-intersection could affect a substantial asset. Keep it bounded, evidence-driven, and subordinate to the asset's intended construction.

## Bound the question

- Check the current selection, one asset root, a specified group, an object pair, or a stated frame interval. Do not scan the entire HIP by default.
- Establish intended contacts, clearances, thickness, collision behavior, and acceptable tolerance before labeling a contact as a defect.
- Set tolerances relative to asset scale, local feature size, and expected thickness. Do not use one universal world-space threshold.
- Use `hia_inspect` and `hia_geometry_summary` only as needed to record paths, topology class, bounds, packed or instance state, scale, and animation context.

## Screen before precise testing

1. Use AABB or packed bounds as a broad phase to reject non-overlapping candidates. Treat bound overlap as a candidate, not proof of penetration.
2. For repeated instances, inspect source bounds and point or packed placement, orientation, and scale before unpacking detailed geometry. Narrow the suspect instance pairs first.
3. Run precise checks only on the remaining region, object pair, primitive set, or frame interval. Use a bounded Houdini-node or HOM diagnostic through `hia_execute_hom` when existing summaries cannot establish the result.

## Match the detector to the geometry

- For closed solids, use reliable inside/outside, signed-distance, intersection-curve, or overlap-volume evidence appropriate to their topology.
- For open shells, thin sheets, or cloth, use surface proximity and triangle-intersection reasoning with intended thickness, collision radius, normals, and adjacent topology. Do not pretend an open sheet is a watertight volume.
- For self-intersection, compare spatially plausible non-adjacent primitive pairs and exclude legitimate shared edges or neighboring faces.
- For animation or simulation, sample key stages and suspicious intervals first. When a problem appears, refine around the affected time; do not default to an exhaustive every-frame scan.

## Classify proportionally

Distinguish:

- `intended contact`: required support, joint, seam, nesting, or collision contact;
- `tolerance`: shallow proximity or penetration within the justified relative tolerance;
- `penetration`: clear unintended overlap that affects construction or appearance;
- `severe`: deep overlap, broad affected area or volume, repeated failure, or topology-damaging self-intersection.

Do not infer severity from a raw distance alone. Consider affected scale, duration, visibility, function, and whether the measurement is trustworthy.

## Preserve useful evidence

Report the object or part pair, node and geometry paths, affected region or group, frame or interval, chosen tolerance, severity, and likely upstream cause. Include penetration depth, area, or volume only when it can be obtained reliably; otherwise state the evidence limitation.

When useful, create separate diagnostic groups or attributes rather than altering the source geometry. Use `hia_capture_viewport` only for a representative view that materially supports the finding. Never permanently change the user's viewport, camera, material, display flags, or pane layout.

## Fix the cause, not the symptom

- Prefer upstream dimensions, spacing, orientation, openings, thickness, guides, placement, or collision parameters.
- Do not default to Boolean repair, Peak, Smooth, arbitrary point pushing, or broad remeshing merely because an overlap exists.
- If intent is ambiguous, or a change could damage topology, UVs, rigging, animation, or simulation behavior, report optional minimum fixes and let the user or main task choose. Do not mutate the scene speculatively.
- Keep any approved repair confined to the responsible subsystem and use the smallest coherent edit.

## Recheck the same scope

After a repair, repeat the same object or group scope, tolerance logic, precise detector, and relevant frame samples. Use `hia_validate` only when it adds technical evidence. Confirm both that the reported overlap is resolved and that the edit introduced no new gaps, deformation, topology or UV damage, rigging problems, or animation and simulation side effects.

Stop after the scoped claim is supported. Do not create a resident scanner, automatic whole-scene repair loop, new MCP tool, scoring system, Agent, or planning service.
