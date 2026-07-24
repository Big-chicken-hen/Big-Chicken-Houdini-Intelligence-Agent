---
name: houdini-artifact-review
description: Read-only milestone and pre-delivery quality review for recognizable, nontrivial Houdini assets. Use after an important modeling stage or before final handoff to inspect node errors, disconnected or floating parts, intersections, outliers, support and contact, proportions, materials, and visual readability. Use as a review subtask that reports evidence and minimum fixes to the main task. Do not use for a simple Box, a one-parameter operation, an ordinary HOM error, or routine plugin development. Never modify the HIP.
---

# Houdini Artifact Review

Review quality without changing the current scene. Return concise, actionable evidence so the main task can decide and apply fixes serially.

## Boundaries

- Act as a read-only review subtask. Keep the main task as the sole writer of the current HIP.
- Use existing HIA read tools selectively, not as a checklist. Do not set parameters, create or delete nodes, save the HIP, render final output, or run mutating HOM.
- For a complex visual milestone, review the supplied low-resolution stage preview first. Ask the main task for another `hia_capture_viewport` only when that evidence cannot support the intended claim.
- When normal inspection cannot establish evidence, use a bounded read-only HOM query through `hia_execute_hom`. Never fan out repeated calls.
- Treat reference pages, research summaries, offline fakes, and node existence as context rather than real Houdini verification. Do not write research drafts or promote knowledge-index entries; return evidence to the main task.
- Do not add MCP tools, an automated scoring platform, a scheduler, a gate, or a fixed screenshot or iteration ritual.

## Review workflow

1. Confirm the complex visual milestone, intended asset, relevant views, and completion claim. Skip this skill for a simple Box, a one-parameter operation, an ordinary HOM error, or routine plugin development.
2. Inspect only what can change the handoff decision:
   - node errors and failed or suspect stages;
   - disconnected, isolated, or statistically outlying parts;
   - floating or intersecting parts, structural support, contact, grounding, assembly logic, and plausible clearances;
   - silhouette, scale, proportions, composition, and hierarchy of primary versus secondary forms;
   - material assignment and response, lighting separation, exposure, transparency, and image readability;
   - consistency with the supplied visual reference, while separating asset defects from camera, lens, lighting, or post-processing differences;
   - complex node-network editability: overlaps, backward wires, conspicuously long crossings, default names, disconnected experiments, and unclear outputs; report evidence without auto-layout or scoring.
3. Distinguish a defect from an intentional gap, suspended part, assembly clearance, or stylized choice. Mark uncertainty instead of inventing a cause.
4. Use the smallest useful evidence: exact node or geometry paths, error text, measurements or topology facts, and the supplied stage preview. Do not recapture merely because a tool succeeded.
5. Order findings by handoff impact and name the single highest-impact visible area.
6. For every completion claim the review can settle, return a **Verification status** and **Verification evidence**. Use `verified` only for the narrow claim directly supported by a real Houdini build plus live node, cook, frame, viewport, or render evidence; otherwise use `unverified` and name the missing evidence.
7. Return findings to the main task. Do not repair the HIP, assign a score, write a formal knowledge index, or start a review-fix loop.

## Finding format

Use one row per actionable issue:

| Issue | Node/geometry path | Evidence | Severity | Suggested owner | Minimum fix |
| --- | --- | --- | --- | --- | --- |

Use qualitative severity only:

- `blocker`: invalidates the asset or final handoff;
- `major`: clearly harms structure, recognition, or the intended image;
- `minor`: localized defect with limited downstream impact;
- `note`: uncertain, optional, or worth checking.

Suggest `main HIP writer`, `modeling-plan subtask`, `material-plan subtask`, or `research subtask` as the owner. Planning and review subtasks provide evidence or proposals only; the main task chooses priorities and performs any scene edits serially. If evidence is unavailable, say what remains unverified rather than assigning a score.

After the table, summarize any settled claim as:

- `Verification status`: `verified` or `unverified`
- `Houdini version/build`: the observed build, or `unknown`
- `Verification evidence`: exact live scene paths, frame/time, checks, and preview/render artifact used

This review evidence can support the main task's research memo, but only the main task may decide whether the complete source-ledger and real-Houdini promotion contract has been met.
