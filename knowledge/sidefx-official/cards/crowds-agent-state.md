# Crowd agent definition, layout, and state workflow

Pack version: 2.0.0

## Use for

Use this workflow to turn one or more animated characters into efficient crowd agents, assign clips and layers, populate terrain or explicit points, and define the states and behaviors that drive a crowd simulation. It is appropriate for background and midground characters whose motion can be expressed through reusable clips and procedural steering rather than individual hero animation.

## Context and core data

An agent primitive references a shared agent definition containing a skeleton rig, shape library, animation clips, layers, and optional collision shapes. Agent Prep records important joint roles for terrain adaptation, foot planting, and look-at operations. Crowd Source copies agents to points and transfers agent-related attributes from those points. In DOP crowds, particles represent agent positions while states choose clips and determine which behavior branches affect each agent.

## Recommended data flow

Use `character and clip sources → Agent/Agent Clip → Agent Definition Cache →
Agent Prep → layout points/Crowd Source → one Crowd State per state-specific
branch → optional branch behavior nodes → Merge DOP (merge_states) → optional
global behaviors → Crowd Solver`. Put expensive definition construction before
the cache and lightweight prep metadata after it. The state merge is a real DOP
connection, not a conceptual list: every state branch must reach the same Merge
DOP, and that merged stream must reach the Crowd Solver.

## Step-by-step workflow

1. Validate the source character's skeleton, scale, facing axis, rest pose, and each animation clip.
2. Build the agent definition, add clips and shape layers, and give clips and layers stable descriptive names.
3. Cache the expensive definition, then configure Agent Prep joint roles and optional ragdoll collision data.
4. Create explicit layout points or a terrain surface and author density, orientation, scale, initial state, or clip-time attributes.
5. Populate agents with Crowd Source and inspect randomization before simulation.
6. Create a Crowd State DOP for each state. Set **State Name** and **Clip Name**;
   the default clip uses the node name through `$OS`, so rename deliberately or
   override Clip Name. Put state-specific behavior nodes after that Crowd State.
7. Connect every state branch to a Merge DOP named or recognized as
   `merge_states`. Place behaviors that apply to all states after this merge,
   then connect the merged result to Crowd Solver. Run a small subset and verify
   states, behavior scope, and terrain adaptation before increasing population.

## Critical parameters and attributes

State Name identifies the crowd state; Clip Name selects the animation clip and
defaults to `$OS`. State-specific behavior nodes belong between Crowd State and
`merge_states`; global behaviors belong between `merge_states` and Crowd Solver.
Crowd Solver combines multiple steering behaviors using weights and updates
agent velocity, orientation, and animation clips. Crowd Source can randomize
initial state, clip time, scale, and orientation. Point `density` restricts
surface scattering. Agent Prep needs correct joints for limbs, head, and feet.

## Cache, version, and performance

Agent Definition Cache prevents repeated cooking of heavy character and clip sources. Keep shared definition components as external references when saving frame sequences; otherwise every frame may embed a duplicate definition and slow viewport loading. Put Agent Prep after the cache because it is normally inexpensive. Prototype with proxy shapes, fewer agents, and short clips. Separate layout from simulation so density or point changes do not rebuild the character definition.

## Validation

Test each clip on one agent at its full time range. Check root locomotion, foot
contacts, joint names, shape layers, and facing direction. Inspect populated
points before agents are copied and verify initial state and scale attributes.
In the DOP network, trace each Crowd State branch into `merge_states`, then
trace the merge into Crowd Solver. Temporarily use one unmistakable behavior in
one branch and another after the merge: only agents in the first state should
receive the branch behavior, while all states should receive the post-merge
behavior. This is a documented validation procedure, not a live result here.

## Common failures and troubleshooting

Sliding feet usually indicate clip locomotion, incorrect Agent Prep joints, or
terrain adaptation errors. Agents facing sideways have an orientation or
reference-axis mismatch. An agent that never receives a state-specific behavior
may have a state branch bypassing `merge_states`, a mismatched State/Clip Name,
or the behavior placed on the global side of the merge. Missing layers or clips
may come from a stale definition cache or inconsistent names. Cooking the
definition every frame often means components were embedded instead of
referenced or the cache is placed after time-dependent preparation.

## When not to use

Use KineFX/APEX for a small set of hero characters that require individualized acting. A static crowd can stop after Crowd Source and instancing without a solver. Simple formation motion may use SOP motion paths instead of a DOP state simulation. Do not add ragdoll collision layers when agents never enter a physical state.

## Houdini 21 notes

H21 change notes document Agent SOP layer lists, FBX namespace removal, inputless
Agent Definition Cache overrides, and Crowd Source orientation/particle-scale
controls. The `Crowd State -> branch behaviors -> Merge -> Crowd Solver`
connection and parameter semantics above are **documented/static** from current
SideFX crowd help. This pack did not execute an H21 crowd simulation, so state
scope, clip playback, and solver behavior are not live-verified here.

## Official sources

- SideFX, [Setting up a crowd simulation](https://www.sidefx.com/docs/houdini/crowds/setup.html), accessed 2026-07-26.
- SideFX, [Crowd agent states](https://www.sidefx.com/docs/houdini/crowds/states.html), accessed 2026-07-26.
- SideFX, [Crowd State DOP](https://www.sidefx.com/docs/houdini/nodes/dop/crowdstate.html), accessed 2026-07-26.
- SideFX, [Crowd Solver DOP](https://www.sidefx.com/docs/houdini/nodes/dop/crowdsolver.html), accessed 2026-07-26.
- SideFX, [Crowd caches](https://www.sidefx.com/docs/houdini/crowds/caches.html), accessed 2026-07-26.
- SideFX, [Houdini 21 Crowd changes](https://www.sidefx.com/docs/houdini/news/21/crowds.html), accessed 2026-07-26.
