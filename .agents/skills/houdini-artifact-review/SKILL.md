---
name: houdini-artifact-review
description: Read-only milestone and pre-delivery quality review for recognizable, nontrivial Houdini assets. Use after an important modeling stage or before final handoff to inspect node errors, disconnected or floating parts, intersections, outliers, support and contact, proportions, materials, and visual readability. Use as a review subtask that reports evidence and minimum fixes to the main task. Do not use for a simple Box, a one-parameter operation, an ordinary HOM error, or routine plugin development. Never modify the HIP.
---

# Houdini Artifact Review

Review quality without changing the current scene. Return concise, actionable evidence so the main task can decide and apply fixes serially.

## Boundaries

- Act as a read-only review subtask. Keep the main task as the sole writer of the current HIP.
- Use existing HIA read tools selectively, not as a checklist. Do not set parameters, create or delete nodes, save the HIP, render final output, or run mutating HOM.
- When normal inspection cannot establish evidence, use a bounded read-only HOM query through `hia_execute_hom` or a representative `hia_capture_viewport` capture. Never make either mandatory or fan out repeated calls.
- Do not add MCP tools, an automated scoring platform, a scheduler, a gate, or a fixed screenshot or iteration ritual.

## Review workflow

1. Confirm the milestone, intended asset, relevant views, and completion claim. Skip this skill for a simple Box or another deterministic primitive request.
2. Inspect only what can change the handoff decision:
   - node errors and failed or suspect stages;
   - disconnected, isolated, floating, intersecting, or statistically outlying parts;
   - structural support, contact, grounding, assembly logic, and plausible clearances;
   - silhouette, scale, proportions, and hierarchy of primary versus secondary forms;
   - material assignment, surface response, lighting separation, and image readability;
   - complex node-network editability: overlaps, backward wires, conspicuously long crossings, default names, disconnected experiments, and unclear outputs; report evidence without auto-layout or scoring.
3. Distinguish a defect from an intentional gap, suspended part, assembly clearance, or stylized choice. Mark uncertainty instead of inventing a cause.
4. Capture the smallest useful evidence: exact node or geometry paths, error text, measurements or topology facts, and a view or screenshot only when it materially supports the finding.
5. Return findings to the main task. Do not repair the HIP or start an automatic review-fix loop.

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
