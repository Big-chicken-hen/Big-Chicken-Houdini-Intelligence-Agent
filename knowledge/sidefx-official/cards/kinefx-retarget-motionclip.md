# KineFX retargeting and MotionClip workflow

Pack version: 2.0.0

## Use for

Use this workflow to transfer animation between characters with different proportions or joint layouts, clean imported motion, extract locomotion, sequence or blend clips, reduce dense motion-capture keys, and return the result to an editable H21 APEX animation scene.

## Context and core data

Retargeting compares a source animated skeleton with a target rest skeleton, aligns representative poses, maps joints, and transfers motion while compensating for morphology. A MotionClip stores an entire skeleton animation as packed geometry primitives, one pose per sample. This makes time-based operations procedural and visible as geometry. The target character may ultimately store the retargeted result as an animation clip in its packed character scene.

The executable Houdini 21 SOP route is the established KineFX topology:
separate source-animation and target-character imports feed Rig Match Pose,
whose target and source outputs feed Map Points, then Full Body IK. A skinned
target is displayed by sending the solved target skeleton to Joint Deform input
3 while the target import supplies captured rest geometry and capture pose to
inputs 1 and 2. Biped Setup and Biped Retarget are Houdini 22 nodes and are not
valid substitutes for this H21 workflow.

## Recommended data flow

Use `H21 source animation import output + H21 target character/rest output →
Rig Match Pose(target input 1, source input 2) → Map Points(target input 1,
source input 2) → Full Body IK(target input 1, source driver input 2) → optional
inverse Transform By Attribute for target size matching → Joint Deform(rest
geometry input 1, target capture pose input 2, solved target input 3) → foot/root
refinement → MotionClip → cycle/retime/blend/sequence/key extraction →
MotionClip Evaluate or APEX Animation from Skeleton → Scene Animate/Motion
Mixer`. Keep imported rest/capture outputs, matched source/target, solved target,
and final controls as separate inspectable outputs.

## Step-by-step workflow

1. Import source animation and the target character/rest skeleton through
   H21-available KineFX import nodes. Record units, frame rate, namespace
   handling, rest/capture outputs, and whether the source has a usable static
   rest pose; use Rig Stash Pose when a separate static source rest pose is
   required.
2. Connect the target animated/rest skeleton output to Rig Match Pose input 1
   and the source animated skeleton to input 2. Match reference poses without
   overwriting the original rest data, and retain both outputs.
3. Connect Rig Match Pose target output 1 and source output 2 to the corresponding
   Map Points inputs. Map the root and task-relevant chains; use sparse mappings
   for Physical Full Body IK and denser mappings for FABRIK rather than mapping
   every joint by habit.
4. Connect Map Points target output 1 and source output 2 to Full Body IK inputs
   1 and 2. Solve the transferred target skeleton and inspect root trajectory,
   joint rotations, contact frames, and limb reach at representative frames.
5. If Rig Match Pose applied a size transform to the target, insert Transform By
   Attribute after Full Body IK using the recorded transform-delta attribute
   with inversion enabled. Do not add this node when the target was not resized.
6. For a skinned target, connect its captured rest geometry to Joint Deform
   input 1, its capture-pose skeleton to input 2, and the Full Body IK result
   (or inverse-transformed result) to input 3. This is the deformation preview
   and delivery check, not an optional generic display branch.
7. Correct foot plants, root motion, orientation, and target-specific joint
   limits on the solved H21 network. Preserve the source, mapping, and target
   capture branches so failures can be isolated.
8. Convert the approved solved skeleton to MotionClip at the intended sample
   rate and clip range. Apply only needed locomotion extraction, retiming,
   cycling, sequencing, blending, or key-pose reduction, then evaluate back to
   animation and compare with the pre-clip solve.

## Critical parameters and attributes

Source and target `name` patterns, rest transforms, hierarchy, scale, and joint orientation drive correspondence. MotionClip uses `clipinfo` when present; otherwise Sample Rate and Frame Range define its extent. MotionClip Retime can use time, frame, or speed channels. Sequence and Blend work best when underlying skeleton topology is the same. Extract Locomotion separates or reapplies root movement. Key-pose extraction trades editable sparsity against motion fidelity.

For the H21 retarget core, preserve Rig Match Pose's target-first/source-second
input order, the mapping attribute authored by Map Points, and Full Body IK's
target-first/source-driver-second order. Physical Full Body IK generally prefers
sparse principal targets; FABRIK generally prefers a denser mapping and more
iterations. Joint Deform must receive target rest geometry with `boneCapture`,
target capture pose, and the solved target skeleton in that order.

## Cache, version, and performance

Convert dense imported motion to MotionClip once, then perform broad retiming and sequencing without repeatedly cooking external files. Do not bake every frame to control channels until manual editing requires it. Reduce keys after retarget cleanup, not before, because removed contact samples can amplify foot sliding. Store reusable clips in the H21 animation catalog or motion mixer, but keep the source and mapping recipe available.

## Validation

Compare source and target side by side at rest, extreme reach, direction changes, and foot contacts. Display joint axes and root paths. Measure foot movement during planted intervals and confirm that extracted locomotion starts and ends coherently. Evaluate the MotionClip back to a skeleton and compare sampled poses with the pre-clip transfer. After control conversion, verify that rig limits and full-body IK do not alter the approved silhouette unexpectedly.

Inspect the network topology itself: Rig Match Pose and Map Points must retain
both target/source streams, Full Body IK must output the target solve, and Joint
Deform inputs 1/2/3 must come from target rest geometry, target capture pose, and
the solved target. Confirm no Biped Setup or Biped Retarget node appears in an
H21 deliverable. This topology is supported by SideFX documentation but was not
executed in a live Houdini 21 build during this corpus rebuild.

## Common failures and troubleshooting

Foot sliding follows incorrect root scale, locomotion extraction, contact mapping, or unmatched frame rate. Flipped limbs indicate rest-pose or axis disagreement. Translation on joints that should rotate may reflect a mismatched hierarchy. Blending unrelated skeleton topologies creates missing or misdirected transforms; retarget both clips to the same target before blending. Over-aggressive key reduction removes impacts and contact changes. Clip loops pop when root position, heading, or end-pose velocity does not match.

An empty or immobile solve often means target/source inputs were reversed or
Map Points did not author usable pairs. A correct skeleton with an undeformed
mesh usually indicates wrong Joint Deform inputs, missing `boneCapture`, or
capture paths that do not match target joint names. A network that depends on
Biped Setup/Biped Retarget is a Houdini 22 network and must be rebuilt with the
H21 Rig Match Pose → Map Points → Full Body IK route rather than relabeled as
H21-compatible.

## When not to use

Do not retarget when the source and target are already the same rig and only clip editing is needed. Do not use MotionClip as the final manual animation interface; return to Scene Animate, layers, Motion Mixer, or control channels for performance work. A one-frame pose transfer does not require an entire MotionClip pipeline.

## Houdini 21 notes

H21 introduces APEX Animation from Skeleton, Motion Mixer, Animation Catalog, full-body IK, inherited animation layers, and clip-oriented Scene Add Animation 2.0. The dedicated constraint tool was removed in favor of Animate-state hotkeys. H21 FBX imports can normalize joint scales and remove namespaces, which should be chosen consistently across source and target.

H21 supports the SOP retarget chain documented above. SideFX marks Biped Setup
and Biped Retarget as **Since 22.0**, so current online pages for those nodes
must not leak into an H21 implementation. APEX Animation from Skeleton can move
an approved solved skeleton into the H21 animation environment, but it does not
replace the source/target pose matching and mapping contract.

## Official sources

- SideFX, [Transferring animation](https://www.sidefx.com/docs/houdini/character/kinefx/retargeting.html), accessed 2026-07-26.
- SideFX, [Rig Match Pose SOP](https://www.sidefx.com/docs/houdini/nodes/sop/kinefx--rigmatchpose.html), accessed 2026-07-26.
- SideFX, [Map Points SOP](https://www.sidefx.com/docs/houdini/nodes/sop/kinefx--mappoints.html), accessed 2026-07-26.
- SideFX, [Full Body IK SOP](https://www.sidefx.com/docs/houdini/nodes/sop/kinefx--fullbodyik.html), accessed 2026-07-26.
- SideFX, [Joint Deform SOP](https://www.sidefx.com/docs/houdini/nodes/sop/kinefx--jointdeform.html), accessed 2026-07-26.
- SideFX, [MotionClips](https://www.sidefx.com/docs/houdini/character/kinefx/motionclips.html), accessed 2026-07-26.
- SideFX, [Biped Setup SOP](https://www.sidefx.com/docs/houdini/nodes/sop/kinefx--biped_setup.html), accessed 2026-07-26.
- SideFX, [Biped Retarget SOP](https://www.sidefx.com/docs/houdini/nodes/sop/kinefx--biped_retarget.html), accessed 2026-07-26.
- SideFX, [Houdini 21 APEX and KineFX changes](https://www.sidefx.com/docs/houdini/news/21/kinefx.html), accessed 2026-07-26.
