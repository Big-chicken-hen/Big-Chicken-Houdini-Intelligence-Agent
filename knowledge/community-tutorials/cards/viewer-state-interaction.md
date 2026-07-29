# Viewer State interaction and undo boundaries

Source kind: `community_tutorial`
Verification: `community_unverified`
Version: one source is H21 and the sample Gist is unversioned; query current viewer-state callbacks before implementation.

## Use, prerequisites, and target

Use when an HDA needs viewport picking, dragging, handles, drawables, or contextual interaction beyond ordinary parameters. Prerequisites are a stable HDA interface and a non-interactive fallback for the core operation. The target is a registered state whose lifecycle, selection rules, device events, parameter writes, drawables, and undo grouping remain deterministic and testable.

## Semantic network stages

HDA geometry/interface → state registration/template → enter/resume lifecycle → selector/handle/drawable setup → event interpretation → bounded parameter/network update → undo commit/cancel → exit cleanup. Viewport code controls presentation and parameters; it should not replace the asset's procedural graph.

## Ordered workflow

1. Define the interaction contract: state name/label, supported node type/category, selectable geometry, event gestures, parameters changed, snapping rules, and cancel behavior. Specify a keyboard/parameter-only fallback.
2. Create the viewer-state module and `createViewerStateTemplate` (or current equivalent). Register selectors, handles, menu actions, and icons with stable names. Keep registration free of scene mutation.
3. In lifecycle callbacks such as enter/resume, validate the current node, cache only safe references, initialize drawables/handles, and read current parameters. Exit/interrupt paths must hide drawables and release transient state.
4. Implement `onMouseEvent` or the current device callback as a small state machine: detect reason/button/modifiers, build a viewport ray, intersect only approved geometry, validate hit primitive/position/normal, then update preview state.
5. Start an undo block at the beginning of a drag/action, update parameters during interaction, and close or cancel it once. Do not create one undo step per mouse move. Avoid direct destructive topology edits in event callbacks when parameter changes can drive the HDA.
6. Implement handles with explicit parameter bindings and coordinate conversions. For drawables, update bounded geometry only when inputs change and separate display style from semantic data.
7. Handle failure paths: node deleted, selection changed, no hit, viewport interrupted, escape/cancel, state reload, and undo/redo. A miss should leave parameters unchanged.
8. Register/reload in a test session, activate on a fresh HDA instance, execute one click and one drag, undo/redo each, exit/reactivate, and verify no stale drawable or callback.

## Key responsibilities and fields

Template owns registration; lifecycle owns transient resources; selector/ray logic owns mapping from screen to scene; event state machine owns gesture interpretation; HDA parameters own durable state; the node network owns geometry; undo owns one user action. Relevant event values include device reason, buttons/modifiers, ray origin/direction, hit primitive, hit position/normal, and bound parameter names.

## Data flow, version, and performance

Viewport events can fire frequently. Cache bounded display geometry, avoid full-network cooks on every mouse move when a preview parameter or deferred final cook works, and never run blocking external work in callbacks. APIs and callback signatures evolve, so local help/current samples must be checked. The public Gist provides ideas but no displayed reuse license; this card copies no code.

## Common failures and repairs

- **State never appears:** template name/category/node-type registration mismatch or module load error.
- **Picking wrong object:** selector mask or intersection geometry is too broad; restrict and validate hit IDs.
- **Undo floods history:** block begins/ends per event; move boundaries to gesture start/end.
- **Drawable remains after exit:** cleanup path missing for interrupt/resume/delete.
- **Laggy drag:** callback triggers expensive full cook; update lightweight preview and commit once.

## Provenance boundary

Mohamad Salame's Python States tutorial and minami110's public sample collection motivate lifecycle/event coverage. The state machine, undo policy, and tests are original synthesis; no Gist code is reproduced.

## Semantic expectations and verification checklist

- Presence: viewer-state template, selectors/handles/drawables, and all bound HDA parameters exist.
- Mapping: a valid hit maps to the correct scene position/normal/parameter; a miss maps to no write.
- Range: hit indices are valid; parameter values obey HDA ranges; drawable geometry remains bounded.
- Interaction evidence: click/drag/cancel produce documented outcomes and exactly one undo step per completed action.
- Cook evidence: the HDA output changes only through its declared parameters and remains cookable after state exit/reload.
- Visual evidence: handles/drawables align with geometry, disappear on exit, and do not persist after node deletion or undo.

## Sources

- Mohamad Salame, [Python States](https://www.sidefx.com/tutorials/python-states/).
- minami110, [Houdini viewer state samples](https://gist.github.com/minami110/afebe30d7ed0e0086ee2a93962cba61f).
