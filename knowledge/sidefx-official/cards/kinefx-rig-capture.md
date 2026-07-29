# KineFX rig, character assembly, and capture workflow

Pack version: 2.0.0

## Use for

Use this workflow to prepare a character for procedural rigging and animation in Houdini 21. It covers skeleton cleanup, joint orientation, capture weights, APEX rig construction, packed character assembly, and deformation evaluation. It is intended for new H21 character work rather than deprecated object-level bone rigs.

## Context and core data

KineFX represents a skeleton as geometry points with hierarchy and transform attributes. Shape geometry carries capture weights. APEX stores rig logic as a graph that can be assembled procedurally and evaluated later. The animation environment expects a packed character folder structure: an animation scene contains character folders, and each character contains elements such as `.skel`, `.shp`, and `.rig`. Only correctly packed data appears in the Animate state.

Capture and deformation are separate SOP stages. Joint Capture Biharmonic is a
smooth volume-based starting point for closed rest geometry; Joint Capture
Proximity is faster and supports open or simpler geometry but usually needs
more refinement; Joint Capture Paint can create or edit the resulting weights.
All three work around the `boneCapture` point attribute. The deformation
contract has three distinct streams: captured rest geometry, the capture-pose
skeleton used when weights were created, and the animated skeleton that drives
the result.

## Recommended data flow

Use `rest mesh + capture-pose skeleton + test/animated skeleton → choose Joint
Capture Biharmonic or Joint Capture Proximity → Joint Capture Paint refinement
→ captured rest geometry with boneCapture + pass-through capture/animated
poses → Joint Deform inputs 1/2/3 → APEX rig components/Autorig Builder → APEX
Pack Character or Scene Add Character → APEX Scene Animate → APEX Scene
Invoke/deformation → export`. Keep all three capture/deform streams, the rig
graph, and shape data available as named stages for inspection.

## Step-by-step workflow

1. Import or create the mesh and rest skeleton, then normalize units,
   namespaces, joint names, transforms, and the pose at which capture is
   authored.
2. Orient joints consistently and define metadata for sides, controls, limits,
   and important anatomical roles. Preserve an inspectable capture-pose branch
   before any animation.
3. Choose the initial capture method deliberately: use Joint Capture Biharmonic
   for closed organic rest geometry that benefits from smooth volumetric
   weights; use Joint Capture Proximity for open/simple geometry or a faster
   distance-based starting point; use Joint Capture Paint for authored creation
   or local refinement rather than as an unexplained mandatory pass.
4. Wire rest geometry to capture input 1, the capture-pose skeleton to input 2,
   and a Rig Pose or other animated test skeleton to input 3. Confirm output 1
   carries `boneCapture`; outputs 2 and 3 preserve the capture and animated
   skeleton streams.
5. Refine weights with Joint Capture Paint where deformation tests show a real
   need. Preserve mesh point count after the paint snapshot, normalize
   influences, and inspect representative joint weights rather than smoothing
   every region indiscriminately.
6. Connect captured rest geometry to Joint Deform input 1, capture pose to input
   2, and animated pose to input 3. The `boneCapture` paths must match joint
   `name` values on both skeleton inputs.
7. Test simple joint rotations through Joint Deform before building the complete
   APEX rig; fix capture/rest mismatches at this stage rather than compensating
   in controls.
8. Assemble APEX autorig components or a custom APEX graph, keeping rig logic
   separate from evaluation, then add skeleton, shape, and rig to a packed
   character folder with a unique character name.
9. Enter the Animate state, verify controls and selection sets, pose a
   representative range of motion, invoke the rig and Joint Deform downstream,
   and inspect the evaluated character before export.

## Critical parameters and attributes

Joint `name`, parent relationships, local transforms, rest transforms, and
orientation axes must be stable. `boneCapture` is a packed point index-pair
attribute containing capture-region references and normalized weights; its
capture paths must resolve to the skeleton `name` attribute. Joint Deform reads
the captured rest geometry on input 1, capture pose on input 2, and animated
pose on input 3; the skeleton poses are represented by point `P` and
`transform`. H21 Skeleton SOP defaults to +X primary and +Z secondary axes;
imported rigs may use another convention. Packed folder paths and extensions
are functional data, not decoration. APEX graph input/output bindings must
match geometry dictionaries and named rig ports.

## Cache, version, and performance

APEX delayed evaluation avoids repeatedly deforming the shape while rig logic is assembled. Keep expensive deformation after animation edits and display lower-cost skeleton or proxy shapes during setup. Pack character elements instead of duplicating geometry across branches. Cache imported source files and finalized animation only where recomputation is expensive; a procedural rig generally remains more useful than an early baked result.

## Validation

Inspect the rest pose and zeroed controls before animation. On captured rest
geometry, confirm `boneCapture` exists and sample points resolve to the expected
joints with normalized weights. Display the same animated-pose branch through
Joint Deform, rotate major joints, and verify axes, limits, volume preservation,
mirrored behavior, and that inputs 2 and 3 have matching joint names. Use the
Rig Tree to confirm scene, character, and element paths. Enter Animate state and
ensure every intended control appears and drives the correct joints. Evaluate
through APEX Scene Invoke and compare the skeleton with the deformed shape for
offsets or double transforms. This wiring is grounded in SideFX documentation
but was not executed in a live Houdini 21 scene during this corpus rebuild.

## Common failures and troubleshooting

Missing controls usually mean the rig element is absent, incorrectly named, or
outside the packed character structure. Biharmonic capture failing on the
initial mesh often indicates open/non-manifold rest geometry; use suitable
closed geometry or choose Proximity and refine it. Exploding or static skin can
mean `boneCapture` is missing, capture paths do not match joint `name`, the
capture and animated skeleton topologies differ, or Joint Deform inputs 2 and 3
were swapped. Twisted limbs often come from inconsistent joint orientation,
negative scale, or mismatched capture/rest transforms. Weight painting becomes
invalid when upstream point count changes without updating its snapshot.
Double deformation occurs when already deformed shape geometry is fed back into
Joint Deform input 1.

## When not to use

Do not build a full character rig for a static prop or a rigid mechanical object that only needs object transforms. A crowd agent may use a lighter agent-definition workflow after a hero rig has generated clips. Maintain an existing legacy object-level rig only when conversion risk exceeds the benefit; do not start new H21 production work there without a compatibility reason.

## Houdini 21 notes

H21 adds the interactive Autorig Builder, expanded APEX components, APEX Pack/Unpack Character, updated Electra-style mechanical conventions, and new control shapes. APEX Autorig Component 1.0 is no longer supported. Joint-axis defaults changed, so H20-era or external rigs should be inspected rather than silently adopting H21 defaults.

The Joint Capture Biharmonic, Joint Capture Proximity, Joint Capture Paint, and
Joint Deform nodes predate H21, but exact parameter labels should still be
confirmed in installed H21 help. The three-input wiring and capture-method
selection above are documentation-backed and not live-verified here.

## Official sources

- SideFX, [KineFX character toolset](https://www.sidefx.com/docs/houdini/character/kinefx/overview.html), accessed 2026-07-26.
- SideFX, [Skin capture](https://www.sidefx.com/docs/houdini/character/kinefx/capture.html), accessed 2026-07-26.
- SideFX, [Joint Capture Biharmonic SOP](https://www.sidefx.com/docs/houdini/nodes/sop/kinefx--jointcapturebiharmonic.html), accessed 2026-07-26.
- SideFX, [Joint Capture Proximity SOP](https://www.sidefx.com/docs/houdini/nodes/sop/kinefx--jointcaptureproximity.html), accessed 2026-07-26.
- SideFX, [Joint Capture Paint SOP](https://www.sidefx.com/docs/houdini/nodes/sop/kinefx--jointcapturepaint.html), accessed 2026-07-26.
- SideFX, [Joint Deform SOP](https://www.sidefx.com/docs/houdini/nodes/sop/kinefx--jointdeform.html), accessed 2026-07-26.
- SideFX, [Character folder structure](https://www.sidefx.com/docs/houdini/character/kinefx/packedcharacterformat.html), accessed 2026-07-26.
- SideFX, [Houdini 21 APEX and KineFX changes](https://www.sidefx.com/docs/houdini/news/21/kinefx.html), accessed 2026-07-26.
