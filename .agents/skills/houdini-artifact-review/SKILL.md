---
name: houdini-artifact-review
description: Read-only milestone and pre-delivery professional review for nontrivial Houdini assets, materials, lighting/renders, animation, simulation, node networks, and performance claims. Also use when a small multi-part assembly's completion depends on endpoints, support, contact, spacing, clearance, or nonintersection. Use after an important stage or before final handoff to inspect task-relevant errors, structure, proportions, visual response, temporal behavior, organization, cost, and delivery evidence. In a project-team Full Goal, use inside the independent Visual Review or Technical Review project Thread to report evidence and minimum fixes to Supervisor; in a single-AI route, the same Panel Thread may perform the visual and technical reviews only as separated read-only passes. Do not use for an isolated simple primitive, a one-parameter operation, an ordinary HOM error, or routine plugin development. Never modify the HIP.
---

# Houdini Artifact Review

Review quality without changing the current scene. Return concise, actionable evidence so Supervisor can decide and Execution can apply a bounded fix.

## Boundaries

- Act inside either the independent read-only Visual Review Thread or the independent read-only Technical Review Thread. Keep those claims and evidence separate, and keep Execution as the sole writer of the current HIP.
- In a project-team Full Goal, both review Threads are required and run in parallel after every stage; never collapse either into a Supervisor pass. Only the explicit single-AI route may keep both reviews in the original Thread, and it must still perform them as separated read-only passes.
- In a Full Panel project, consume the real scene facts, diffs, captures, renders, and errors routed from Execution. Bridge enforces an empty `hia_mcp_v2` and `houdini_intelligence` inventory for this non-Execution role, so do not request or assume direct HIA/HOM access. For a standalone review outside that project-role boundary, use existing HIA read tools selectively according to their live contract, not as a checklist. Never set parameters, create or delete nodes, save the HIP, render final output, or run mutating HOM.
- For a complex visual milestone, apply the shared [visual-validation.md](../houdini-visual-research/references/visual-validation.md) capture-quality and display-match rule to the supplied preview. Ask Supervisor to route another `hia_capture_viewport` request only when a bounded recapture can support the intended claim. When HDR/OCIO or display parity is unproven, treat the screenshot as diagnostic rather than final color evidence.
- When supplied project evidence cannot establish a claim, report the missing evidence to Supervisor so Execution can acquire a bounded observation. Do not attempt to bypass the Bridge-enforced tool inventory. In a standalone review where the live contract permits the non-read-only `hia_execute_hom` capability, use at most one demonstrably non-mutating HOM query; otherwise report the missing evidence instead of attempting a write or rollback. Never fan out repeated calls.
- When project history or unfamiliar, version-sensitive behavior can change the review, consume the shared retrieval result defined in [knowledge-and-memory.md](../houdini-visual-research/references/knowledge-and-memory.md) first. Do not repeat the task's pre-write local search in the review subtask. A review subtask may read supplied knowledge or memory evidence, but returns any durable-memory candidate to the Supervisor instead of writing it.
- Read the acceptance routing in [build-brief-and-review.md](../houdini-visual-research/references/build-brief-and-review.md) for a complex milestone or delivery claim. For a Full Goal stage, also read [full-goal-blueprint.md](../houdini-visual-research/references/full-goal-blueprint.md) completely. Supervisor receives the complete blueprint for initial and material-revision authorization; after that authorization, review the supplied complete current-stage card, hard constraints, and latest evidence through the assigned visual or technical lane without requesting the whole blueprint in every loop. Select only the relevant modeling, material, lighting/render, animation, simulation, network-organization, and performance domains; a read-only request does not trigger construction or a Full Blueprint.
- When this review role actually exposes native subagent tools and has genuinely parallel, non-overlapping read-only claims, dispatch bounded internal subagents and synthesize their evidence within the assigned review lane. They never replace the independent Visual Review or Technical Review project Threads, never touch the HIP, and must not be invented when the tools or suitable work are unavailable.
- Treat reference pages, research summaries, offline fakes, and node existence as context rather than real Houdini verification. Do not write research drafts or promote knowledge-index entries; return evidence to the Supervisor.
- Do not add MCP tools, an automated scoring platform, a scheduler, a gate, or a fixed screenshot or iteration ritual.

## Review workflow

1. Confirm the assigned visual or technical review lane, supplied complete current-stage card or bounded review scope, current milestone, intended result, relevant views or ranges, and unchanged completion claim. Include every applicable User fact and explicit negative constraint as a hard acceptance requirement on the resulting geometry or behavior; a renamed node or different construction method does not make an equivalent forbidden substitute acceptable. Skip this skill for an isolated simple primitive, a one-parameter operation, an ordinary HOM error, or routine plugin development.
2. Inspect only what can change the handoff decision:
   - node errors and failed or suspect stages;
   - disconnected, isolated, or statistically outlying parts;
   - floating or intersecting parts, structural support, contact, grounding, assembly logic, and plausible clearances;
   - silhouette, scale, proportions, composition, and hierarchy of primary versus secondary forms;
   - material assignment and response, lighting separation, exposure, transparency, and image readability;
   - animation timing, continuity, constraints, deformation, contact, popping, or penetration when motion is in scope;
   - simulation sourcing, collisions, scale, stability, continuity, cache validity, and representative phases when simulation is in scope;
   - consistency with the supplied visual reference, while separating asset defects from camera, lens, lighting, or post-processing differences;
   - complex node-network editability: overlaps, backward wires, conspicuously long crossings, default names, disconnected experiments, and unclear outputs; report evidence without auto-layout or scoring.
   - measured performance evidence for the scoped cook, geometry, instances, time dependence, cache, or render when a performance claim is in scope; never infer cost from network size alone.
3. Compare retrieved technical rules with the explicit semantic expectations and current `hia_validate` evidence. A clean cook is not proof of a correct field mapping, active required field, finite/plausible velocity, binding, dependency, or range. Preserve an unproven result as one visible risk instead of repeating it.
4. For simulation/cache claims, apply the reset, configured-start, range, cache, and dependency evidence rule in the shared risk-routing and review reference. Missing proof prevents verification even when no node reports an error; report it once, and do not treat observed bounded recomputation alone as proof of the whole simulation/cache claim.
5. Distinguish a defect from an intentional gap, suspended part, assembly clearance, or stylized choice. Mark uncertainty instead of inventing a cause.
6. Use the smallest useful evidence: exact node or geometry paths, error text, measurements or topology facts, semantic results, and a usable supplied preview. When a completion claim depends on attachment or fit, require task-relevant numeric evidence for both endpoints and intended hosts, allowed span or bounds, scale-relative contact tolerance, minimum clearance, or precise intersection as applicable. Bounds are only a broad phase; a viewport image, node existence, and a clean cook do not prove nonintersection. Use a bounded demonstrably non-mutating query when needed, or mark the narrow claim unverified. Do not recapture merely because a tool succeeded.
7. Order findings by handoff impact and name the single highest-impact visible area.
8. For every completion claim the review can settle, return a **Verification status** and **Verification evidence**. Use `verified` only for the narrow claim directly supported by a real Houdini build plus live node, cook, frame, semantic, viewport, or render evidence; otherwise use `unverified` and name the missing evidence.
9. Return findings to the owning Visual Review or Technical Review Thread and Supervisor. Do not repair the HIP, assign a score, write a formal knowledge index, or start a reviewer-owned loop. Supervisor waits for both review returns, selects the minimum coherent repair, Execution applies it, and both Threads receive the same claims again in parallel until verified or genuinely blocked.

## Finding format

Use one row per actionable issue:

| Issue | Node/geometry path | Evidence | Severity | Suggested owner | Minimum fix |
| --- | --- | --- | --- | --- | --- |

Use qualitative severity only:

- `blocker`: invalidates the asset or final handoff;
- `major`: clearly harms structure, recognition, or the intended image;
- `minor`: localized defect with limited downstream impact;
- `note`: uncertain, optional, or worth checking.

Suggest `Supervisor`, `Planning`, `Execution`, `Visual Review`, or `Technical Review` as the responsible project role. Lookdev Reviewer, Artifact Reviewer, and Performance Reviewer are professional specialties inside the two review Threads, not additional project roles. Planning and both review Threads provide plans or evidence only; Supervisor chooses priorities and Execution alone performs scene edits. If evidence is unavailable, say what remains unverified rather than assigning a score.

After the table, summarize any settled claim as:

- `Verification status`: `verified` or `unverified`
- `Houdini version/build`: the observed build, or `unknown`
- `Verification evidence`: exact live scene paths, frame/time, checks, and preview/render artifact used

This review evidence can support the Supervisor's research memo, but only the Supervisor may decide whether the complete source-ledger and Verified Procedure contract has been met. A reviewer never promotes a procedure or writes project memory.
