---
name: houdini-artifact-review
description: Read-only milestone and pre-delivery professional review for nontrivial Houdini assets, materials, lighting/renders, animation, simulation, node networks, and performance claims. Also use when a small multi-part assembly's completion depends on endpoints, support, contact, spacing, clearance, or nonintersection. Use after an important stage or before final handoff to inspect task-relevant errors, structure, proportions, visual response, temporal behavior, organization, cost, and delivery evidence. Use as a review subtask that reports evidence and minimum fixes to the Director. Do not use for an isolated simple primitive, a one-parameter operation, an ordinary HOM error, or routine plugin development. Never modify the HIP.
---

# Houdini Artifact Review

Review quality without changing the current scene. Return concise, actionable evidence so the main task can decide and apply fixes serially.

## Boundaries

- Act as the read-only Artifact Reviewer. Keep the Director as the sole writer of the current HIP.
- Use existing HIA read tools selectively, not as a checklist. Do not set parameters, create or delete nodes, save the HIP, render final output, or run mutating HOM.
- For a complex visual milestone, apply the shared [visual-validation.md](../houdini-visual-research/references/visual-validation.md) capture-quality and display-match rule to the supplied preview. Ask the main task for another `hia_capture_viewport` only when a bounded recapture can support the intended claim. When HDR/OCIO or display parity is unproven, treat the screenshot as diagnostic rather than final color evidence.
- When normal inspection cannot establish evidence and the active environment permits the non-read-only `hia_execute_hom` capability, use one demonstrably non-mutating HOM query. If reviewer access excludes that capability, report the missing evidence instead of attempting a write or rollback. Never fan out repeated calls.
- When project history or unfamiliar, version-sensitive behavior can change the review, consume the shared retrieval result defined in [knowledge-and-memory.md](../houdini-visual-research/references/knowledge-and-memory.md) first. Do not repeat the task's pre-write local search in the review subtask. A review subtask may read supplied knowledge or memory evidence, but returns any durable-memory candidate to the main task instead of writing it.
- Read the acceptance routing in [build-brief-and-review.md](../houdini-visual-research/references/build-brief-and-review.md) for a complex milestone or delivery claim. Select only the relevant modeling, material, lighting/render, animation, simulation, network-organization, and performance domains; a read-only request does not trigger construction or a full Build Brief.
- Treat reference pages, research summaries, offline fakes, and node existence as context rather than real Houdini verification. Do not write research drafts or promote knowledge-index entries; return evidence to the main task.
- Do not add MCP tools, an automated scoring platform, a scheduler, a gate, or a fixed screenshot or iteration ritual.

## Review workflow

1. Confirm the supplied review scope or compact Brief, current milestone, intended result, relevant views or ranges, and completion claim. Include any explicit negative constraint as a hard acceptance requirement on the resulting geometry or behavior; a renamed node or different construction method does not make an equivalent forbidden substitute acceptable. Skip this skill for an isolated simple primitive, a one-parameter operation, an ordinary HOM error, or routine plugin development.
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
4. For simulation/cache claims, apply the reset, configured-start, range, cache, and dependency evidence rule in the shared Build Brief reference. Missing proof prevents verification even when no node reports an error; report it once, and do not treat observed bounded recomputation alone as proof of the whole simulation/cache claim.
5. Distinguish a defect from an intentional gap, suspended part, assembly clearance, or stylized choice. Mark uncertainty instead of inventing a cause.
6. Use the smallest useful evidence: exact node or geometry paths, error text, measurements or topology facts, semantic results, and a usable supplied preview. When a completion claim depends on attachment or fit, require task-relevant numeric evidence for both endpoints and intended hosts, allowed span or bounds, scale-relative contact tolerance, minimum clearance, or precise intersection as applicable. Bounds are only a broad phase; a viewport image, node existence, and a clean cook do not prove nonintersection. Use a bounded demonstrably non-mutating query when needed, or mark the narrow claim unverified. Do not recapture merely because a tool succeeded.
7. Order findings by handoff impact and name the single highest-impact visible area.
8. For every completion claim the review can settle, return a **Verification status** and **Verification evidence**. Use `verified` only for the narrow claim directly supported by a real Houdini build plus live node, cook, frame, semantic, viewport, or render evidence; otherwise use `unverified` and name the missing evidence.
9. Return findings to the main task. Do not repair the HIP, assign a score, write a formal knowledge index, or start a review-fix loop.

## Finding format

Use one row per actionable issue:

| Issue | Node/geometry path | Evidence | Severity | Suggested owner | Minimum fix |
| --- | --- | --- | --- | --- | --- |

Use qualitative severity only:

- `blocker`: invalidates the asset or final handoff;
- `major`: clearly harms structure, recognition, or the intended image;
- `minor`: localized defect with limited downstream impact;
- `note`: uncertain, optional, or worth checking.

Suggest `Director`, `Researcher`, `Architect`, `Lookdev Reviewer`, `Artifact Reviewer`, or `Performance Reviewer` as the responsible role. Research, planning, and review roles provide evidence or proposals only; the Director chooses priorities and performs any scene edits serially. If evidence is unavailable, say what remains unverified rather than assigning a score.

After the table, summarize any settled claim as:

- `Verification status`: `verified` or `unverified`
- `Houdini version/build`: the observed build, or `unknown`
- `Verification evidence`: exact live scene paths, frame/time, checks, and preview/render artifact used

This review evidence can support the Director's research memo, but only the Director may decide whether the complete source-ledger and Verified Procedure contract has been met. A reviewer never promotes a procedure or writes project memory.
