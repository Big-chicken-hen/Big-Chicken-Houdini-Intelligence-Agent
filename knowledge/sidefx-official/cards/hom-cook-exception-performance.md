# HOM cooking, exceptions, and performance

Pack version: 2.0.0

## Use for

Use this workflow when a HOM tool must control or observe cooking, distinguish node errors from Python API exceptions, run safely in `hython`, or measure Python and node-evaluation cost. It complements the node/parameter/geometry card with runtime behavior. Apply it to scripts that create many nodes, batch scenes, evaluate outputs, implement Python SOPs, or need actionable diagnostic reports without hiding partial scene changes.

## Context and core data

Houdini cooking evaluates dependencies to produce current scene state. Parameter changes, connections, time, display flags, geometry requests, and explicit calls can trigger cooks. Node cook errors are part of the node state; Python calls may additionally raise `hou.OperationFailed`, `hou.PermissionError`, `hou.ObjectWasDeleted`, `hou.LoadWarning`, or other exceptions. A Python SOP can raise `hou.NodeError` or `hou.NodeWarning` to communicate a deliberate user-facing state. Performance Monitor records node, script, viewport, and engine events.

## Recommended data flow

Use:

`preflight context and permissions -> pause avoidable updates or batch changes -> perform mutation -> request only required output cooks -> collect node errors/warnings and Python exceptions -> profile representative action -> report exact scope and partial state`

Separate expected user-correctable errors from unexpected programming defects. Preserve the original traceback for defects. Avoid repeated force-cooking of every intermediate node when one downstream output provides the required evidence.

## Step-by-step workflow

1. Define the output node, frame or range, and success evidence before mutating the scene.
2. Preflight node existence, parameter names, locked-asset permissions, file paths, and license assumptions.
3. Batch parameter and connection changes so Houdini does not repeatedly evaluate incomplete intermediate networks.
4. Cook only the relevant output or request its geometry, then collect node `errors()` and `warnings()`.
5. Catch specific expected `hou` exception classes and add node path, parameter, frame, and operation context.
6. Let unexpected exceptions retain a full traceback, while recording which mutations completed before failure.
7. Profile the exact slow action with Performance Monitor and separate Python time from SOP cook and viewport draw time.
8. Repeat the same measurement after one targeted change and report both results without overstating general speedup.

## Critical parameters and attributes

- `infoTree(..., force_cook=True)` can ensure an output is cooked before information is collected.
- `node.errors()` and `node.warnings()` report the most recent cook state.
- `hou.NodeError` marks a Python node as failed with a user-facing message.
- `hou.NodeWarning` permits output while signaling a problem.
- `hou.OperationFailed` commonly indicates unmet API preconditions or cook failure.
- `hou.PermissionError` commonly indicates locked-asset restrictions.
- `hou.ObjectWasDeleted` means a retained Python object no longer represents a live node.
- `hou.InterruptableOperation` supports progress and cancellation for long tasks.
- `hython` loads `hou`, accepts HIP files, and checks out a Houdini license.

## Cache, version, and performance

Avoid `force_cook=True` throughout traversal code; use it at the chosen verification outputs. Store resolved node objects during a live operation, but never retain them across destructive scene changes without validity checks. Disable Python SOP `Maintain State` unless measured benefit outweighs state-reset risk. Reuse stable disk caches instead of recooking expensive upstream networks in each batch. Record Houdini build, thread count, frame, cache state, and warm/cold conditions in performance evidence.

## Validation

Test success, invalid input, missing file, locked asset, interrupted operation, and deleted-node paths. Confirm a Python node presents a clear error or warning rather than a raw traceback for user mistakes. Run the batch twice to find unintended duplicate creation or stale state. For profiling, clear or document caches and use the same frame, display state, and action. Verify the output geometry or file, not merely that the Python function returned.

## Common failures and troubleshooting

- A script reports success before the node cooks: request the chosen output and inspect its cook state.
- Every node is force-cooked: verify only downstream evidence nodes.
- A broad `except Exception` hides the defect: catch expected `hou` classes and re-raise unknown exceptions.
- Python time appears high because of many path lookups: cache node and parameter objects within the operation.
- Repeated changes cook repeatedly: batch creation, parameters, and connections before requesting output.
- A headless job waits for UI: remove dialog calls and use command-line-safe reporting.
- Profiling results vary: distinguish warm cache, cold cache, viewport draw, and disk I/O.
- A cancellation leaves partial nodes: report the exact partial state and design a recoverable transaction boundary.

## When not to use

Do not force a cook merely to inspect static network metadata. Do not convert expected node warnings into fatal exceptions without a production requirement. Avoid using performance measurements from a tiny scene to justify broad architecture changes. Do not release a license or terminate a process while required work remains. Do not erase partial work automatically after an exception; preserve and report it unless the caller explicitly authorized rollback.

## Houdini 21 notes

H21 adds Ctrl-click Reset Simulation behavior that clears a simulation cache and jumps to its start frame. H21 also expands logging and viewer/runtime surfaces, while the core HOM exception classes and node cook diagnostics remain applicable. Record the H21 build because daily builds can change node implementations and cook performance even when the Python API is unchanged.

## Official sources

- SideFX, [Cooking](https://www.sidefx.com/docs/houdini/basics/cooking), accessed 2026-07-26.
- SideFX, [HOM introduction](https://www.sidefx.com/docs/houdini/hom/intro.html), accessed 2026-07-26.
- SideFX, [hou.NodeError](https://www.sidefx.com/docs/houdini/hom/hou/NodeError.html), accessed 2026-07-26.
- SideFX, [Command-Line Scripting](https://www.sidefx.com/docs/houdini/hom/commandline), accessed 2026-07-26.
- SideFX, [Performance Monitor pane](https://www.sidefx.com/docs/houdini/ref/panes/perfmon.html), accessed 2026-07-26.
