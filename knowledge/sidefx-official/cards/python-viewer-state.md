# Python Viewer State authoring and validation

Pack version: 2.0.0

Canonical ID: `python-viewer-state`

Houdini version: `21.x target; current online HOM pages may show Houdini 22`

## Use for

Use this workflow when an HDA or project tool needs a purposeful viewport
interaction: parameter handles, guided placement, drawing, component
selection, guide geometry, a contextual menu, or a nodeless inspection mode.
It covers state scope, registration, event handling, undo, debugging, and
project distribution. It does not replace the SOP network that performs the
actual procedural operation, and it is not a reason to put ordinary parameter
editing behind a custom UI.

## Context and core data

A Python Viewer State is a registered state type with a unique internal name, a
human-readable label, a node category, a factory class, and optional handles,
selectors, state parameters, menus, drawables, and hotkeys. Houdini constructs
the state class with `state_name` and a viewer object, then calls lifecycle and
interaction handlers with a `kwargs` dictionary. Node-aware states usually edit
an HDA instance; nodeless states operate without a bound node and cannot bind
handles.

The mandatory file-state entry point is `createViewerStateTemplate()`. It
returns a `hou.ViewerStateTemplate`, binds the state class with `bindFactory`,
and uses the correct category such as `hou.sopNodeTypeCategory()` or, for H21
Copernicus, `hou.copNodeTypeCategory()`. A state can be embedded in an HDA or
stored in a `viewer_states` directory on `HOUDINI_PATH`. Project-distributed
file states should live in the project plug-in root and be exposed by a package
rather than written into a user's preference directory.

## Recommended data flow

Use:

`interaction contract -> node-aware or nodeless choice -> HDA section or
project viewer_states module -> unique template registration -> tool script
creates/selects the node when needed -> state events update parameters ->
procedural node network cooks the result -> bounded draw/guide feedback ->
single undo action -> Viewer State Browser validation -> clean-session package
test`.

Keep geometry generation and heavy computation in native nodes, verbs, or the
asset network. The viewer state should translate user intent into parameters,
selection, or compact state, not rebuild dense geometry on every mouse event.

## Step-by-step workflow

1. Define the interaction in one sentence, including what the user manipulates,
   what node or parameter changes, what visual feedback appears, and what one
   undo operation should restore.
2. Choose a node-aware HDA state when the interaction edits a durable asset.
   Choose a nodeless state only for an inspector or tool that genuinely has no
   owning node. If selection is needed before node creation, let the shelf/tool
   script collect it and create the node.
3. Choose storage. Embed an asset-specific state in the HDA's Viewer State
   section; otherwise place a uniquely named module in the project's
   `viewer_states` folder and expose that folder through the existing package.
4. Generate or write the smallest template: a state class, required
   `createViewerStateTemplate()`, category, `hou.ViewerStateTemplate`,
   `bindFactory`, and an icon if useful. Confirm the state appears once in the
   Viewer State Browser without registration errors.
5. Implement only necessary lifecycle handlers. Use `onEnter` for an existing
   node and `onGenerate` for a nodeless entry; Houdini calls one, not both.
   Release transient state in `onExit`, and do not assume every `onInterrupt`
   is followed by `onResume`.
6. Add the narrowest interaction primitive: a bound handle for parameter
   manipulation, a selector for geometry choice, state parameters for tool
   options, or a drawable for guides. Prefer a tool script for staged,
   multi-type selection that must happen before a node exists.
7. During a drag, update the owning parameters and let the node network produce
   geometry. Preserve drawable objects as state-instance references and move
   or update existing guides instead of recreating them on every event.
8. Group a continuous gesture with `hou.SceneViewer.beginStateUndo()` and
   `endStateUndo()`. Use `hou.undos.group()` for a bounded sequence inside one
   function. Do not suppress undo for user-visible changes.
9. Reduce event cost: disable unused mouse-drag events or redraws through
   `state_flags`, avoid forced cooks inside pointer-motion loops, and consume an
   event only when the state actually handles it.
10. Trace, inspect, and reload the state in Viewer State Browser. Reload a
    file-based state with the browser or `hou.ui.reloadViewerState`; reload an
    embedded state through the Viewer State Editor.
11. Test activation, interruption, exit, undo, parameter persistence, and
    package discovery in a clean H21 session with the intended node category.

## Critical parameters and attributes

The state type name must be globally unique enough for distribution. The
template category must match the intended network. `bindFactory` must point at
the implementation class. Distinguish `onEnter`, `onGenerate`, `onExit`,
`onInterrupt`, and `onResume`; a missing `node` key is valid in nodeless or
deleted-node situations. `onMouseEvent` receives a UI event and should return a
value consistent with whether the event was consumed.

Handles are the standard parameter-manipulation mechanism. State parameters
are tool UI, not automatically HDA parameters. Guide drawables are world-space
objects and need a retained Python reference. `state_flags["mouse_drag"]` and
`state_flags["redraw"]` can avoid unnecessary event and draw work. An H21
Copernicus state uses `hou.copNodeTypeCategory()` and `hou.Drawable2D` when the
same interaction must operate in Composite and Scene viewers.

## Cache, version, and performance

Do not cook a large asset, rebuild guide geometry, allocate drawables, or query
the full scene on every mouse move. Cache immutable lookups on the state
instance, update transforms or parameters, and request redraw only when the
visual state changed. Keep a clean boundary between transient interaction data
and durable node parameters so reload and undo remain predictable.

File-state reload is a development aid, not deployment proof. A state that
works because of a manually loaded module may still be absent after restart.
Package the `viewer_states` resource and any Python modules together, then test
startup discovery without a user-local copy masking the result.

## Validation

In Viewer State Browser, verify exactly one registered state, its source,
category, bound node type, handles, selectors, parameters, and absence of
errors or warnings. Enter the state from both node creation and an existing
node when both paths are supported. Exercise a click, drag, cancel,
interruption, viewer change, node deletion, and exit. One continuous gesture
should produce one meaningful undo item and restore the prior parameter values.

For a guide, confirm its transform, visibility, pick behavior, and redraw after
parameter changes. For a file state, modify a harmless label and prove reload
updates the registered state. Finally, launch a clean H21 process with only the
intended project package, create or select the owning node, enter the state,
and repeat the minimum interaction. This card is `documented/static`; no GUI
state was registered or exercised during authoring, so it is not live-verified.

## Common failures and troubleshooting

- The state is absent because `createViewerStateTemplate()` is missing, returns
  the wrong object, or the file is outside a scanned `viewer_states` folder.
- Registration fails because another state uses the same internal name or the
  template category does not match the node type.
- The tool creates duplicate nodes because both the tool script and state try
  to own creation. Keep creation in the tool script for node-associated states.
- A drag fills the undo stack because begin/end state undo does not bracket the
  full gesture, or parameter writes occur outside the bracket.
- Guides disappear because the drawable lost its Python reference, or appear
  misplaced because world-space transforms were treated as local.
- Interaction stutters because every pointer event forces a heavy cook,
  rebuilds geometry, or redraws without a visible change.
- A handler crashes after deletion because it assumes `kwargs["node"]` always
  exists. Validate the node at lifecycle boundaries.
- A state works only on one machine because it was saved in a user preference
  directory rather than the distributed project package.

## When not to use

Do not create a Viewer State for a simple parameter change, a one-off shelf
script, or an operation already served by a standard handle. Do not use Python
mouse events as a replacement for a fast SOP, VEX, or HDK algorithm. Avoid a
nodeless state when an HDA should own the result and expose reproducible
parameters. Do not add a custom state merely to hide a poorly designed asset
interface.

## Houdini 21 notes

The general state lifecycle predates H21, but H21 adds Copernicus Python states
in both Composite and Scene viewers, `hou.Drawable2D`, `hou.Viewport2D`, and
additional Compositor Viewer handle APIs. H21's Composite Viewer has important
limits: no HUD Info Panel, scene geometry, viewport selection/group-list
support, flipbooking, snapping, viewport prompts, or Scene Viewer
`beginStateUndo`/`endStateUndo`. Test the same COP state first in Composite View
and then in Scene View, and design around the shared subset.

Current online HOM pages may display Houdini 22 additions or revised
limitations. Confirm each handler, template binding, and Copernicus capability
against the installed H21 help before treating it as available.

## Official sources

- SideFX, [Writing custom viewer states in Python](https://www.sidefx.com/docs/houdini/hom/python_states.html), accessed 2026-07-27.
- SideFX, [Python state creating and editing nodes](https://www.sidefx.com/docs/houdini/hom/state_node.html), accessed 2026-07-27.
- SideFX, [Python states supporting undo](https://www.sidefx.com/docs/houdini/hom/state_undo.html), accessed 2026-07-27.
- SideFX, [Python state guide geometry](https://www.sidefx.com/docs/houdini/hom/state_guides.html), accessed 2026-07-27.
- SideFX, [Python state Copernicus](https://www.sidefx.com/docs/houdini/hom/state_cops.html), accessed 2026-07-27.
- SideFX, [Viewer State Browser](https://www.sidefx.com/docs/houdini/ref/windows/viewer_state_browser.html), accessed 2026-07-27.
- SideFX, [Houdini 21 user interface, viewport, and scripting changes](https://www.sidefx.com/docs/houdini/news/21/viewport.html), accessed 2026-07-26.
