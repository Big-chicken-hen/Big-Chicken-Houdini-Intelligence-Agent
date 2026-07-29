# KineFX hierarchy and transform contracts

Source kind: `community_tutorial`
Verification: `community_unverified`
Version: one source is H19.5 and the other spans evolving KineFX notes; verify current transform attribute conventions.

## Use, prerequisites, and target

Use for importing, building, retargeting, posing, or deforming with KineFX. Inputs are a point skeleton with stable joint names/hierarchy and geometry with valid capture weights or a plan to create them. The target is an editable rig graph whose rest pose, animated pose, local/world transforms, parent mapping, and deformation binding are explicit.

## Semantic network stages

Skeleton import/build → hierarchy/name audit → rest-pose normalization → rig/pose operations → motion retarget/blend → deformation branch → export/cache → `OUT_KINEFX`. Geometry/capture enters from the left at the deformation stage.

## Ordered workflow

1. Import with the appropriate character/skeleton SOP or construct joints as points. Inspect joint string `name`, integer `parent` relationship (or the active schema), point order, scale, and handedness. Joint names must be unique.
2. Establish a rest skeleton. Use `Rig Doctor SOP`/current equivalent to repair or initialize transform fields only after preserving a copy for comparison. Inspect `transform`, `localtransform`, and `rest_transform`-style matrices available in the active build; do not assume all coexist.
3. Validate hierarchy: exactly one intended root per skeleton, no cycles, all parent indices/names resolve, and child transforms produce expected world positions. Visualize axes and bone links.
4. Build procedural controls with `Rig Attribute Wrangle SOP`, `Rig Pose SOP`, constraints, or an HDA/subnet. Operate in the intended local/world space. Keep control attributes separate from deform skeleton fields and give controls semantic names.
5. For retargeting, create a mapping table from source to target joint names. Normalize rest orientation/scale before applying source motion. Use dedicated retarget/motion-clip SOPs where available and inspect unmapped joints rather than silently dropping them.
6. Capture geometry with `Joint Capture Biharmonic SOP` or import weights, then inspect capture region/weight arrays. Normalize weights and limit excessive influences if the downstream format requires it. Deform with `Bone Deform SOP`/KineFX deformation using matching rest and pose skeletons.
7. Cache motion clips or posed skeleton attributes before heavy deformation when iteration benefits. Preserve joint identity, parent mapping, transforms, and sample rate.
8. Output separate `OUT_SKELETON`, `OUT_CONTROLS` if needed, and final `OUT_KINEFX`.

## Key responsibilities and fields

`name` identifies joints; hierarchy data identifies parents; rest matrices define bind space; local/world pose matrices define animation; capture attributes map geometry to joints. Import owns coordinate conversion, rig nodes own pose edits, retarget owns name/rest mapping, and deform owns weighted geometry transformation.

## Data flow, cache, version, and performance

Changing joint order or names after capture invalidates mappings. Cache skeleton/motion before dense mesh deformation. Matrix work is cheap relative to repeated high-resolution deformation; prototype on proxy geometry. Retarget failure is usually a mapping/rest-space problem, not a reason to stack compensating offsets. Exact attribute names and node replacements changed across Houdini releases, so query the current catalog/help before writing.

## Common failures and repairs

- **Joints jump on first pose:** rest and pose matrices use different spaces or were initialized twice.
- **Twisted limbs:** source/target rest orientations differ; normalize and visualize axes before retarget.
- **Geometry collapses:** weights reference missing joint names or rest skeleton differs from capture skeleton.
- **Unmapped controls:** source naming table incomplete; record unmatched names and provide explicit fallbacks.
- **Slow playback:** cache motion, deform lower-resolution geometry interactively, and avoid recomputing capture weights.

## Provenance boundary

Matt Estela's KineFX notes and bubble pins' series motivate spreadsheet/axis inspection. The contract-first hierarchy, retarget, and cache workflow is original synthesis. It has not been executed in the current Houdini version.

## Semantic expectations and verification checklist

- Presence: every joint has unique identity and required transform/hierarchy data.
- Mapping: every non-root parent resolves; retarget table reports mapped/unmapped joints; capture weights reference existing joints.
- Range: transforms and weights are finite; normalized capture weights sum near 1 for deforming points.
- Cook evidence: hierarchy cycle count is zero and joint count/name set remains stable across rest, pose, and cache.
- Cache evidence: reload preserves sample range, joint identities, parent mapping, and representative matrices.
- Visual evidence: displayed axes are coherent, roots stay anchored as intended, limbs do not twist unexpectedly, and mesh follows the posed skeleton without collapse.

## Sources

- Matt Estela, [Kinefx](https://www.tokeru.com/cgwiki/HoudiniKinefx.html).
- bubble pins, [KineFX Series](https://www.sidefx.com/tutorials/kinefx-series/).
