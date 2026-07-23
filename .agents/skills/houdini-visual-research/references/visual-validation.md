# Visual Validation and Diagnostic Closure

Node creation, successful tool calls, and a clean cook are necessary evidence, not proof of visual completion. Validate the image or asset against observable goals.

## Validation loop

1. Define acceptance evidence before the final build: target view/frame, resolution, silhouette, proportions, motion, materials, lighting, and required controls.
2. When visual evidence is needed, use `hia_capture_viewport` to capture a representative camera and frame; do not capture merely because a tool succeeded. For animation or simulation, inspect enough frames to expose temporal artifacts.
3. Compare in this order:
   - silhouette, scale, and primary proportions;
   - camera, composition, staging, and focal hierarchy;
   - secondary forms, intersections, topology/normals, and contact;
   - material identity through roughness, specular/transmission, color, displacement, and edge response;
   - lighting direction, contrast, exposure, shadows, and separation;
   - tertiary detail frequency, variation, wear, and scale cues;
   - temporal continuity, collisions, flicker, popping, and noise.
4. Name the largest visible mismatch and change the smallest set of controls that addresses it.
5. Capture again and compare. Stop when the requested bar is met, the iteration budget is exhausted, or a blocker is evidenced.

For a reference image, compare aligned views where possible and distinguish geometry mismatch from lens, lighting, material, or post-processing mismatch. Do not deform a reusable asset merely to compensate for an unidentified camera difference.

A primitive blockout is an intermediate milestone for a complex task. Final evidence should show resolved primary/secondary forms, appropriate surface response, lighting/composition, and task-relevant detail.

## When to write one diagnostic report

Create one human-readable Markdown report for the current turn when any of these occurs:

- HIA MCP V2, HOM, an explicitly selected compatibility fallback, or another scene tool returns a real failure or error code;
- a relevant node reports errors, validation fails, rendering fails, or the turn ends in failure;
- tools technically succeed but the user explicitly says the visual result is unsatisfactory, such as "it does not look like the reference."

Do not automatically interpret every subjective variation as dissatisfaction. Do not create a report for a successful turn when the user is satisfied. Merge related failures from the same turn into one report; no service-level deduplication is needed.

At runtime, write the report under the project root at:

    .runtime/diagnostics/<timestamp>-<short-slug>.md

Treat it as an untracked runtime artifact: never stage or commit it. This skill defines the contract only; it does not require monitoring, telemetry, a database, a background service, an Issue integration, or a complex schema.

Store automatic viewport screenshots under `.runtime/cache/screenshots`, visual-check previews under `.runtime/cache/previews`, temporary research artifacts under `.runtime/cache`, and user-provided attachments under `.runtime/attachments`. Treat these as portable project-relative runtime paths; do not stage or commit them.

Treat final renders, EXRs, videos, USD files, exports, and simulation caches as user deliverables. Honor an explicit user output path first, otherwise use launcher-provided `HIA_RENDER_OUTPUT_DIR` when available; either may be an ordinary local directory outside the project. If neither is specified, default to `.runtime/cache`. Always tell the user the final path. Do not add an approval layer, output manager, or cleanup system for this rule.

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

After writing the report, give its path to the user and stop unbounded retries. A later Codex turn may read that Markdown to diagnose and repair the plugin or visual workflow. Treat every cause as unconfirmed until reproduced or independently verified.

## Completion handoff

Report the render/capture evidence reviewed, the most important refinements made, remaining limitations, and whether a diagnostic report was produced. If no visual capture was possible, say that visual quality remains unverified.
