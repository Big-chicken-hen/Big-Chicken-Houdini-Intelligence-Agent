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

## Executable parameter and connection contract

1. Connect imported skeleton points to `Rig Doctor SOP` after all intended joint add/remove/reparent edits. Freeze topology at a named rest frame using the node's `Stash` or `Time Shift` method. Require one unique point string `name` per joint, resolvable parents, finite `P`, and finite matrix3 `transform`.
2. Branch the frozen skeleton into a rest output and into `Rig Pose SOP`. Use **Pre-Multiply** for ordinary layered FK edits, **Post-Multiply** for parent-space reproportioning, **Override** only for baked replacement, and **From Rest Pose** for restoring the named rest attribute. Record mode per control because the same numeric rotation has different space semantics.
3. For skin capture connect closed rest skin to input 1, capture-pose skeleton to input 2, and optional animated pose to input 3 of `Joint Capture Biharmonic SOP`. Set `Skin Group`/`Skeleton Group` only when non-empty tested groups are required. The first output must contain point `boneCapture`; unmatched or excluded short bones must be reported.
4. Connect captured skin to input 1, the exact capture/rest skeleton to input 2, and posed skeleton to input 3 of `Bone Deform SOP`. Matching is by skeleton point `name`; `P` plus matrix3 `transform` provide rest and deformed transforms. Enable `Deform Normals` when normals must follow and keep `Delete Capture Attributes` off until all downstream reconstruction/export consumers are known.
5. Validate capture weights: each deforming point references existing joint names, weights are finite/non-negative, and their sum is approximately one (for example within `1e-3`). Keep zero-weight or missing-joint points in named diagnostic groups.
6. Cache the skeleton/motion branch before dense skin deformation. Preserve sample range, `name`, hierarchy, `P`, `transform`, rest fields, and frame rate. The cached pose and rest skeleton must keep identical joint-name sets and counts.

## Data flow, cache, version, and performance

Changing joint order or names after capture invalidates mappings. Cache skeleton/motion before dense mesh deformation. Matrix work is cheap relative to repeated high-resolution deformation; prototype on proxy geometry. Retarget failure is usually a mapping/rest-space problem, not a reason to stack compensating offsets. Exact attribute names and node replacements changed across Houdini releases, so query the current catalog/help before writing.

## Common failures and repairs

- **Joints jump on first pose:** rest and pose matrices use different spaces or were initialized twice.
- **Twisted limbs:** source/target rest orientations differ; normalize and visualize axes before retarget.
- **Geometry collapses:** weights reference missing joint names or rest skeleton differs from capture skeleton.
- **Unmapped controls:** source naming table incomplete; record unmatched names and provide explicit fallbacks.
- **Slow playback:** cache motion, deform lower-resolution geometry interactively, and avoid recomputing capture weights.

## Checkpoints and observable evidence

- **K0 — hierarchy:** unique-name count equals joint count, root count matches the design, parent-miss and cycle counts are zero, and displayed axes have finite scale.
- **K1 — rest/pose split:** rest geometry remains unchanged while one known `Rig Pose` rotation affects only the intended joint subtree in the selected multiplication mode.
- **K2 — capture:** `boneCapture` exists on every intended skin point; weight sum tolerance passes and all referenced names exist in both rest and pose skeletons.
- **K3 — deform:** neutral pose reproduces rest skin within position tolerance; a test bend deforms the intended region without collapse, NaNs, or unexpected root motion.
- **K4 — cache:** disk-loaded motion preserves joint count/names, hierarchy, sample range, and representative transforms; dense mesh playback no longer recooks capture.

## When not to use this workflow

Do not use KineFX skinning for a simple rigid object transform, a topology-changing creature effect, or a deformation better represented by a lattice/point deform. Do not recapture weights when only animation changes, and do not mix APEX-specific rig attributes into this SOP skeleton contract without an explicit conversion stage.

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
