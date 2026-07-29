# Visual Validation and Diagnostic Closure

Node creation, successful tool calls, and a clean cook are necessary evidence, not proof of visual completion. Validate the image or asset against observable goals.

## Validation loop

1. Select the Direct, Focused, or Full route from `build-brief-and-review.md`. Define only the acceptance evidence and semantic expectations needed for the claim.
2. For a Direct task, execute after the concise local lookup, perform one necessary targeted validation, and stop when it passes. Do not require a Brief, external research, screenshot, or artifact review. Simple deterministic tasks do not enter the visual loop.
3. For a Focused task, use only the one or two evidence steps that address its uncertainty. Capture only when the claim is visual and inspection or semantic validation cannot settle it.
4. For a Full visual task, identify only meaningful visible milestones. Normally consider completed primary structure, completed materials or lighting when in scope, and final handoff, but merge adjacent stages and skip unchanged or inapplicable ones. Capture a same-frame bounded preview or selected animation/simulation key frames only when the image will settle a current claim; do not automatically capture every stage.
5. Inspect the `hia_capture_viewport` result's `quality_status`, `quality_reasons`, `quality_metrics`, `display_match`, and `hdr_display_mismatch_risk` before judging the pixels. If capture quality is failed or warning, or display matching is `unverified`, recapture only when a bounded setting change can fix the evidence; otherwise request human inspection or mark the visual claim unverified.
6. A viewport screenshot is not final color evidence when HDR transfer, OCIO/view transform, display profile, or delegate parity is not proven. Use a renderer output or another color-managed reference when available, and state the limitation instead of inferring a match.
7. Give a usable preview and relevant technical or semantic evidence to read-only `$houdini-artifact-review` only when that independent review can change the handoff. Compare in this order:
   - silhouette, scale, and primary proportions;
   - camera, composition, staging, and focal hierarchy;
   - secondary forms, intersections, topology/normals, and contact;
   - material identity through roughness, specular/transmission, color, displacement, and edge response;
   - lighting direction, contrast, exposure, shadows, and separation;
   - tertiary detail frequency, variation, wear, and scale cues;
   - temporal continuity, collisions, flicker, popping, and noise.
8. Include floating parts, penetration, support and contact, exposure, transparency, and reference consistency where relevant. Distinguish a true asset mismatch from camera, lens, lighting, display transform, or post-processing differences.
9. Name the largest visible mismatch and let only the main task change the smallest set of controls that addresses that one area. Recapture only after a material visual change or an evidence-quality correction, then stop when the requested bar is met or a blocker is evidenced.

For a reference image, compare aligned views where possible and distinguish geometry mismatch from lens, lighting, material, or post-processing mismatch. Do not deform a reusable asset merely to compensate for an unidentified camera difference.

A primitive blockout is an intermediate milestone for a complex task. Final evidence should show resolved primary/secondary forms, appropriate surface response, lighting/composition, and task-relevant detail.

## Bounded recovery and the single-report rule

Intermediate failures are not report triggers by themselves. Preserve the original error, inspect the active Houdini version/build and relevant local help, then make a bounded in-scope compatibility, syntax, parameter, or workflow correction and revalidate. Do not retry the same failing action indefinitely or hide partial scene changes.

Create exactly one human-readable Markdown report for the current Thread/Turn only when either condition is true:

- bounded recovery ends with a final meaningful failure in HIA MCP V2, HOM, an explicitly selected compatibility fallback, a relevant node, validation, rendering, or another scene workflow;
- tools finish but the user explicitly reports that the visual or procedural result is unsatisfactory.

Do not automatically interpret every subjective variation as dissatisfaction. If an intermediate error is recovered and the user is satisfied, do not create a report. If a report is required, merge the original failure, bounded recovery attempts, warnings, and final state into the same per-Turn report path; update that report rather than creating another file for the Turn. No service-level deduplication is needed.

At runtime, write the report under the project root at:

    .runtime/diagnostics/<timestamp>-<short-slug>.md

Treat it as an untracked runtime artifact: never stage or commit it. This skill defines the contract only; it does not require monitoring, telemetry, a database, a background service, an Issue integration, or a complex schema.

Use the actual path returned by `hia_capture_viewport`: a safely saved HIP may use its sibling `.hia/screenshots`, while an unsaved or unsafe scene falls back to `.runtime/cache/screenshots`. Store other temporary visual-check previews under `.runtime/cache/previews`, temporary research artifacts under `.runtime/cache`, and user-provided attachments under `.runtime/attachments`. Do not stage or commit these runtime artifacts.

Treat final renders, EXRs, videos, USD files, exports, and simulation caches as user deliverables. Honor an explicit user output path first, otherwise use environment-provided `HIA_RENDER_OUTPUT_DIR` when available; either may be an ordinary local directory outside the project. If neither is specified, default to `.runtime/cache`. Always tell the user the final path. Do not add an approval layer, output manager, or cleanup system for this rule.

## Diagnostic report contract

Include only information needed to reproduce and fix the problem:

- timestamp, Houdini build, plugin/project version when available, and thread/turn ID;
- a short summary of the user's goal;
- expected result and actual result;
- failed tool or workflow stage, original error text, and error code;
- relevant node paths, scene revision, and dirty state;
- minimal reproduction steps;
- user-provided screenshot or attachment paths, when present and authorized;
- whether the scene changed and the safest current recovery recommendation;
- possible causes only under a **Pending verification hypotheses** heading.

If a field is unavailable, say unknown; do not invent it. Preserve the original error text needed for diagnosis, but redact secrets and irrelevant private data.

Never include Bearer tokens, Authorization headers, cookies, login details, credentials, complete unrelated conversation history, large blocks of user source code, or private content from paths outside the project. Prefer project-relative paths when they identify the same artifact.

After writing the report, give its path to the user and stop unbounded retries. A later Codex turn may read that Markdown to diagnose and repair the plugin or visual workflow. Treat every cause as unconfirmed until reproduced or independently verified. Do not promote diagnostic hypotheses into the formal knowledge index.

## Completion handoff

Report the render/capture evidence reviewed, the most important refinements made, remaining limitations, and whether a diagnostic report was produced. If no visual capture was possible, say that visual quality remains unverified.
