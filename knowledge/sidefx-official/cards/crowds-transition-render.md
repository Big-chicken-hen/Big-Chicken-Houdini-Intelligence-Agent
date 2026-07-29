# Crowd transition, motion, cache, and render workflow

Pack version: 2.0.0

## Use for

Use this workflow after agent definitions and initial states are valid. It builds trigger and transition logic, combines motion-path and steering behavior, introduces ragdoll or parent-child changes when required, caches the resulting crowd efficiently, and imports agents into Solaris through a crowd procedural or skel-based USD workflow.

## Context and core data

Crowd Trigger evaluates a condition. Crowd Trigger Logic combines conditions. Crowd Transition changes state, causing the target clip and behavior branch to become active. Behaviors influence agent particle velocity and can be state-specific or global. H21's SOP motion-path tools can generate and edit paths, turns, parent-child relationships, and triggers before DOP solving. Cached agent primitives share definitions; render import may preserve instanceability or bake deformation.

## Recommended data flow

Use `populated agents → state branches merged through merge_states → Crowd
Trigger or Trigger Logic → Crowd Transition → merge_transitions → Crowd Solver
→ crowd cache/import → render`. The connection order is operational: a trigger
feeds a transition; that transition must feed `merge_transitions`; state
definitions separately feed `merge_states`; both merged branches participate in
the Crowd Solver network. Keep path planning, ragdoll, cache, and render import
separate so a look-development change does not rerun behavior.

## Step-by-step workflow

1. Draw a state diagram listing source state, trigger condition, target state, and expected clip blend.
2. Create Crowd Trigger, set **Type** and its condition, and give **Trigger
   Name** a unique value. If several conditions are required, combine them with
   Crowd Trigger Logic using explicit `and`, `or`, or `not`.
3. Connect Crowd Trigger or Trigger Logic to Crowd Transition. Set **Input
   State**, **Output State**, and **Duration**, then connect Crowd Transition to
   the Merge DOP named `merge_transitions`. Multiple transitions may follow one
   trigger when that condition legitimately applies to different input states.
4. Test the transition on one deterministic agent before adding spatial or
   random triggers. Then add path following, avoidance, terrain, look-at, or
   target behaviors on the intended side of `merge_states`.
5. Simulate a representative subset and inspect trajectories, transition times, contacts, and failed states.
6. Cache the final agents with external definition references and an appropriate File SOP frame cache.
7. Import to Solaris, select procedural quality and skinning policy, then render representative on-screen and off-screen agents.

## Critical parameters and attributes

Crowd Trigger **Type**, condition parameters, **Group**, and unique **Trigger
Name** define which agents emit a condition. Crowd Trigger Logic combines
trigger results; Crowd Transition **Input State**, **Output State**, **Duration**,
and state blend define the change. A transition not connected to
`merge_transitions` does not enter the standard simulated transition branch.
Avoid simultaneously true transitions with the same input state because
SideFX documents the chosen transition as technically undefined. Random Delay
staggers H21 motion-path triggers. Ragdoll transitions require collision layers,
joint limits, and the correct animated/static handoff.

## Cache, version, and performance

Externalize clip and shape libraries so frame caches reference rather than embed them. Set File SOP Cache Frames to at least one; for smooth review, keeping the sequence length can avoid definition reloads. Keep agents instanceable as long as possible. The crowd procedural can optimize off-screen agents and prototype repeating shapes. Bake skinning only where export, motion blur, or a non-procedural consumer requires deformed geometry.

## Validation

Use Crowd Transition Test Simulation or one deterministic agent. Confirm the
agent begins in Input State; force the trigger false, then true; verify the
Crowd Trigger guide/attribute, transition activation, blend duration, Output
State, and current clip. Trace the wire physically as `Crowd Trigger -> Crowd
Transition -> merge_transitions`. Also trace `Crowd States -> merge_states ->
Crowd Solver`; these are separate responsibilities. Inspect foot contacts and
ragdoll/attachment changes around transition boundaries, then reload the final
cache and compare agent counts, clips, layers, and definition references.

## Common failures and troubleshooting

Agents stuck in a state usually have an impossible trigger, misspelled target
state, missing clip, reversed Trigger/Transition connection, or a transition
that never reaches `merge_transitions`. Abrupt pops can come from short Duration
or incompatible locomotion. Oscillation occurs when opposite transitions remain
true simultaneously. Competing transitions from the same input state are
undefined and should be made mutually exclusive. Child agents detach or
double-transform when parent rest transforms and detach rules disagree.

## When not to use

Do not build a state machine for a static crowd or a single procedural path with no behavioral branching. Avoid ragdoll for distant agents when a clip transition provides the same silhouette. Use hero KineFX rigs for close-up physical interaction requiring art-directed poses. If a downstream renderer supports only simple point instances, bake only the data it can consume.

## Houdini 21 notes

H21 change notes document turn-rate clip blending, parent-child motion paths,
time-dependent obstacles, three-dimensional steering, path orientation,
random trigger delays, dynamic agent emission in SOP Crowd Import LOP, and
Crowd Procedural off-screen quality. The `Trigger -> Transition ->
merge_transitions` and `States -> merge_states -> Solver` wiring above is
**documented/static** from SideFX help. No H21 transition simulation or render
was executed for this pack, so it is not live-verified.

## Official sources

- SideFX, [Crowd setup and transitions](https://www.sidefx.com/docs/houdini/crowds/setup.html), accessed 2026-07-26.
- SideFX, [Crowd state triggers and transitions](https://www.sidefx.com/docs/houdini/crowds/triggers.html), accessed 2026-07-26.
- SideFX, [Crowd agent states](https://www.sidefx.com/docs/houdini/crowds/states.html), accessed 2026-07-26.
- SideFX, [Crowd Trigger DOP](https://www.sidefx.com/docs/houdini/nodes/dop/crowdtrigger.html), accessed 2026-07-26.
- SideFX, [Crowd Transition DOP](https://www.sidefx.com/docs/houdini/nodes/dop/crowdtransition.html), accessed 2026-07-26.
- SideFX, [Houdini 21 Crowd changes](https://www.sidefx.com/docs/houdini/news/21/crowds.html), accessed 2026-07-26.
- SideFX, [Official Crowd learning collection](https://www.sidefx.com/learn/collections/crowds/), accessed 2026-07-26.
