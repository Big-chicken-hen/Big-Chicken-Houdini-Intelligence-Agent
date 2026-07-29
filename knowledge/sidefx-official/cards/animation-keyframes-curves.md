# Traditional keyframes and Animation Editor curve workflow

Pack version: 2.0.0

## Use for

Use this workflow for direct parameter, object, camera, light, and control
animation whose primary representation is a set of editable Houdini channels
and keyframes. It covers pose blocking, timing edits, curve refinement,
interpolation and slope decisions, range extension, selective key reduction,
baking, and delivery. It is intended for a modest number of authored controls;
whole-character retargeting, sampled signal processing, and simulation caching
belong to the KineFX/MotionClip, CHOP, and solver workflows instead.

## Context and core data

An animated parameter component owns a channel. A keyframe stores time or
frame, value, and curve data; a multi-component parameter therefore has
separate channels that may not share identical keys. The segment beginning at
a key uses a function such as `constant()`, `linear()`, or `bezier()` to
evaluate toward the next key. Bezier refinement also uses outgoing and incoming
slope and acceleration values. Houdini may tie those sides for continuity or
let the animator break them for a deliberate corner.

Keep these concepts separate. **Constant**, **Linear**, and **Bezier** are
segment interpolation choices. **Break Slopes** and **Tie Slopes** change
whether incoming and outgoing slopes are edited together; “break” is not
another interpolation type. Extrapolation describes evaluation before the
first key or after the last key and is separate again. In HOM, `hou.Keyframe`
exposes values, expressions, slopes, accelerations, and their incoming/automatic
states, while `hou.Parm` owns the ordered keys and channel extrapolation.

## Recommended data flow

Use `identify controls, FPS, frame range, and units → block sparse poses and
events → scope channels in the Channel List → edit timing in the Dope Sheet →
refine values and derivatives in the Animation Editor Graph view → set
before/after extrapolation → simplify or bake only when delivery requires it →
export selected channels and verify a round trip`. Preserve an editable source
branch before destructive reduction or baking.

Use each editor for its strength. The Channel List controls which channels are
visible and affected. The Dope Sheet is best for keys as timed events, group
retiming, holds, and spacing without changing values. The Animation Editor's
Graphs view—the curve or Graph Editor view—is best for value, interpolation,
slope, acceleration, overshoot, and continuity.

## Step-by-step workflow

1. Record the target Houdini 21 build, scene FPS, playback and export range,
   time unit, parameter components, and any locked, referenced, or expression-
   driven channels before setting keys.
2. Block the smallest useful set of poses and events. Use stepped/Constant
   segments only when a held value or blocking preview is intentional; do not
   hide missing in-betweens behind dense keys.
3. In the Channel List, isolate the exact components to edit. In the Dope Sheet,
   move, scale, copy, or align key times while checking that unrelated channels
   and hierarchy controls remain untouched.
4. Open the selected channels in the Animation Editor Graph view. Inspect value
   range and the segment between every important pair of keys before changing
   interpolation globally.
5. Assign segment functions deliberately: use **Constant** for a true hold,
   **Linear** for constant rate, and **Bezier** when shaped acceleration and
   smooth arrival or departure are required. Apply the choice only to the
   selected segments.
6. Refine Bezier slopes and acceleration/handle weight. Keep slopes tied when
   continuity is desired; use **Break Slopes** only when the incoming and
   outgoing motion require different directions. Recheck neighboring segments
   for overshoot after every break or retie.
7. Set before-first and after-last extrapolation separately. Use a constant
   extension for bounded motion; use an H21-supported cycle mode only for a
   deliberate loop, and a cycle-with-offset form only when accumulated motion
   is intended. Verify the exact `hou.parmExtrapolate` value in installed H21
   help before scripting it.
8. Test the first key, last key, extrema, direction changes, contact frames,
   subframes, and at least one sample outside each keyed range. For loops,
   compare value and apparent velocity at the seam.
9. Simplify or reduce keys only after motion is approved. Choose a tolerance
   from the task's visible or numerical error budget, protect contacts and
   events, and compare the reduced curve against the source at representative
   frames and subframes.
10. Bake only when expressions, constraints, CHOP exports, or procedural
    drivers must become ordinary keys for delivery. Sample at the required
    rate, keep the procedural source, and avoid per-frame keys when a sparser
    curve reproduces the result.
11. Export only the required channels with documented FPS, range, units,
    component order, and naming. Reload or re-import into a disposable target
    and compare key times, values, interpolation, and extrapolation.

## Critical parameters and attributes

Frame/time and value are the minimum key data. For Bezier segments, outgoing
`slope` and incoming `inSlope` describe direction; acceleration and incoming
acceleration control handle influence. Automatic and tied states affect whether
Houdini recomputes or couples these values. A manual slope can be overwritten
if an automatic state remains active, so inspect state as well as the number.

`hou.Parm.keyframes()` provides ordered keys for auditing. `setKeyframe()` or
`setKeyframes()` authors keys, while deletion and replacement should target the
intended parameter component rather than a tuple by assumption.
`setKeyframeExtrapolation()` controls the before or after side with a
`hou.parmExtrapolate` value. Treat UI labels, HOM enum names, and segment
expressions as three interfaces to the same channel behavior, but do not invent
an enum from a menu label.

## Cache, version, and performance

Traditional parameter curves are lightweight until dense baking multiplies
keys across many controls. Keep blocking, refined, reduced, and baked states in
an intentional editable hierarchy or versioned file rather than duplicating
every channel blindly. Dense keys increase HIP size, graph redraw cost,
selection noise, and merge difficulty; they also make meaningful manual edits
harder.

When converting from CHOPs, constraints, expressions, or imported animation,
decide whether the procedural source remains authoritative. Preserve it
disabled or in a source file before baking if later retiming or correction may
be required. Record FPS and range with exported curves because a numerically
identical frame number represents a different time at another rate.

## Validation

Audit the exact parameter paths and component channels, then list key frames,
values, segment expressions, automatic/tied slope states, and before/after
extrapolation. In the Dope Sheet, verify event order and spacing. In the Graph
view, inspect extrema, discontinuities, flat spots, overshoot, and derivative
changes. Scrub at subframes rather than judging only keyed frames.

For a loop, evaluate one cycle before and after the authored range and compare
the seam's value and apparent velocity. For cycle-with-offset behavior, confirm
the intended accumulated delta. For reduced or baked motion, compare source
and result over the full interval using task-relevant position, angle, lens,
exposure, or control-error tolerances. Reload exported channels into a
disposable target and verify FPS, range, values, interpolation, and component
mapping. These are documented/static checks; this corpus rebuild did not run a
live H21 Animation Editor session.

## Common failures and troubleshooting

A channel that does not move may be locked, expression-driven, scoped
incorrectly, keyed on another component, or overridden downstream. A Constant
segment creates an intentional pop; if the pop is accidental, inspect the
segment function rather than adding keys. Linear motion can look mechanical
because velocity changes abruptly at keys. Bezier overshoot usually comes from
automatic, excessive, or wrongly directed slopes/accelerations; inspect
neighboring segments before flattening the whole curve.

A sharp corner that unexpectedly smooths may still have tied or automatic
slopes. A discontinuity after **Break Slopes** may be intentional in slope but
should not create an unintended value jump. Loop pops come from mismatched
endpoint value or velocity, while cycle-with-offset drift may be a wrong
accumulated delta. Reduction can erase contacts or small but important events;
baking can create excessive keys, change subframe behavior, or sample at the
wrong FPS. An export that appears correct but returns on the wrong controls
usually has a path, namespace, component-order, or range mismatch.

## When not to use

Use CHOPs for filtering, audio-rate signals, spring/lag processing, or large
sampled channel sets. Use MotionClip or H21 animation tools for whole-character
clip retiming and retargeting. Keep an expression or constraint when it is the
clear reusable source of truth. Do not bake a simulation into parameter keys
when geometry or solver caches are the correct delivery. A static parameter
assignment needs no Animation Editor workflow.

## Houdini 21 notes

The channel, keyframe, Dope Sheet, Channel List, and graph-curving concepts are
established Houdini animation foundations. Current online help can expose
later UI refinements, so confirm the exact H21 menu labels, available
extrapolation values, reduction command, and bake options in installed H21
help before automating them. H21's APEX, KineFX, Motion Mixer, and Animation
Catalog additions complement rather than replace direct parameter animation.
This card is documented/static and change-note-supported where it mentions H21
features; none of its UI operations is live-verified in this pack.

## Official sources

- SideFX, [Animation](https://www.sidefx.com/docs/houdini/anim/index.html), accessed 2026-07-26.
- SideFX, [Keyframes](https://www.sidefx.com/docs/houdini/anim/keyframes.html), accessed 2026-07-26.
- SideFX, [hou.Keyframe](https://www.sidefx.com/docs/houdini/hom/hou/Keyframe.html), accessed 2026-07-26.
- SideFX, [hou.parmExtrapolate](https://www.sidefx.com/docs/houdini/hom/hou/parmExtrapolate.html), accessed 2026-07-26.
- SideFX, [hou.Parm](https://www.sidefx.com/docs/houdini/hom/hou/Parm.html), accessed 2026-07-26.
- SideFX, [Houdini 21 APEX, KineFX, animation, and audio changes](https://www.sidefx.com/docs/houdini/news/21/kinefx.html), accessed 2026-07-26.
