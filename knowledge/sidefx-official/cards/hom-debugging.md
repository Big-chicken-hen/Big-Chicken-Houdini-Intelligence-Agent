# HOM nodes, parameters, and geometry

Pack version: 2.0.0

## Use for

Use the Houdini Object Model for node-network construction, parameter editing, structured scene inspection, and controlled geometry access. HOM is the application-level Python API; it complements rather than replaces VEX. This card focuses on node, parameter, and geometry operations. Use the companion cook/exception/performance card for evaluation control, exception policy, profiling, and batch-runtime diagnosis.

## Context and core data

The `hou` module exposes nodes, parameters, geometry, panes, assets, hip files, performance tools, and exceptions. Houdini automatically imports it in its Python shell and `hython`. Since Houdini 20, `hou.Node` is a generalized network-item abstraction and traditional operator-node methods live on `hou.OpNode`. A Python reference may outlive a deleted Houdini node and then raises `hou.ObjectWasDeleted`. Locked HDA contents reject modifications with `hou.PermissionError`. Cook and API failures commonly raise `hou.OperationFailed`.

## Recommended data flow

Use:

`resolve explicit context -> validate node types and paths -> create or query in a cohesive batch -> set parameters -> connect and name -> apply flags and layout -> force only necessary cooks -> inspect errors, warnings, and info -> save only when authorized`

Separate discovery from mutation. Store node and parameter objects in local variables. Prefer relative paths inside assets and explicit absolute roots in one-off scene tools. Make batches restartable or preflighted when partial execution would be expensive.

## Step-by-step workflow

1. Determine the execution context and obtain the node from an explicit path, `kwargs["node"]`, or `hou.pwd()` as appropriate.
2. Validate that required nodes, parameter names, input counts, and permissions exist before changing anything.
3. Create nodes with meaningful names, then set parameter values by internal parameter name.
4. Connect data flow in semantic order and apply display, render, bypass, or template flags only after connections are valid.
5. Position related nodes coherently and avoid repeatedly relayouting the network during construction.
6. Cook only the outputs needed for verification, then inspect node errors, warnings, geometry, and information trees.
7. Catch expected `hou` exceptions narrowly, report context and corrective action, and preserve the original traceback for unexpected failures.
8. Save the HIP or HDA definition only when the caller explicitly requests that external state change.

A minimal documented network-authoring reproduction, not live-run during this corpus rebuild, is:

```python
obj = hou.node("/obj")
old = obj.node("workflow_probe")
if old is not None:
    old.destroy()
geo = obj.createNode("geo", "workflow_probe")
for child in geo.children():
    child.destroy()
source = geo.createNode("grid", "SOURCE_GRID")
out = geo.createNode("null", "OUT_RESULT")
out.setInput(0, source)
out.setDisplayFlag(True)
out.setRenderFlag(True)
out.moveToGoodPosition()
out.cook(force=True)
assert out.inputs()[0] == source
assert not out.errors()
```

The expected result is one geometry container with exactly two SOP children, one connection from `SOURCE_GRID` to `OUT_RESULT`, display/render flags on the output, and an empty `errors()` tuple. Run this only in a disposable scene or approved scratch network because it intentionally replaces an existing `/obj/workflow_probe`.

## Critical parameters and attributes

- `hou.node(path)` may return `None`; check before calling methods.
- `hou.pwd()` is relative to the evaluating parameter or current node.
- `hou.OpNode.asCode()` is useful for discovering HOM calls that reproduce nodes and parameter settings.
- `hou.NodeError` and `hou.NodeWarning` communicate user-correctable Python-node states.
- `infoTree(verbose, debug, output_index, force_cook)` exposes cooked node information.
- Before reading dynamic hide/disable condition results in non-graphical Houdini, or before the owner has appeared in a Parameter Pane, call `node.updateParmStates()`. SideFX documents this as `hou.Node.updateParmStates()`; on H20+ operator-node class pages it is exposed on `hou.OpNode`. Then query `parm.isHidden()` or `parm.isDisabled()`. This call is for evaluating parameter UI conditionals, not a prerequisite for ordinary parameter values.
- `hou.HDADefinition.updateFromNode()` saves an unlocked instance to its definition.
- `matchCurrentDefinition()` restores a node to the installed definition.
- `hython` initializes Houdini and `hou` automatically and consumes an appropriate license.
- Importing `hou` into regular Python runs Houdini initialization scripts.

## Cache, version, and performance

Batch node creation and parameter changes to avoid unnecessary intermediate cooks, and reuse resolved objects inside the batch. Keep Python SOP `Maintain State` off unless a measured need and a clear reset design justify it. Move dense geometry loops to VEX, SOP verbs, or native nodes. Use the companion cook/exception card for update modes and exception boundaries, and the diagnostics card for measured Performance Monitor evidence instead of repeating those workflows here.

## Validation

Check returned nodes and parameter objects, expected node types, input connections, and flags. Query `errors()` and `warnings()` after the relevant cook, and inspect output geometry counts and attributes when geometry is the deliverable. The minimal reproduction above has explicit child-count, connection, flag, and error expectations. Test scripts in a clean scene and inside locked assets where applicable. Verify that rerunning a tool does not create unintended duplicates. To validate a hide/disable conditional outside a loaded Parameter Pane, record the result before and after `node.updateParmStates()` and accept only the post-update result.

## Common failures and troubleshooting

- `NoneType` errors after `hou.node`: the path is wrong or relative to an unexpected context.
- `hou.PermissionError`: the target is inside a locked asset or an operation requires another scope.
- `hou.ObjectWasDeleted`: a cached Python object refers to a destroyed node; resolve the node again.
- `hou.OperationFailed`: inspect method preconditions, node category, parameter type, and current cook errors.
- HDA callback errors are not visible in the node: check the console and preserve the traceback.
- Parameter or Python-node errors are unclear: MMB the node and inspect the last traceback frame.
- A shelf tool behaves differently in a viewer: tool scripts receive interaction context and should not depend on undocumented internal utilities.
- Headless hidden/disabled checks are wrong: call `node.updateParmStates()` before `isHidden()` or `isDisabled()`. If the script needs business logic rather than presentation state, query the controlling parameter directly.

## When not to use

Do not use HOM for dense per-point or per-voxel loops that VEX or native SOPs can execute in parallel. Do not use UI selection as the only source of context in a reusable tool. Avoid broad exception catches that hide partial changes. Do not call undocumented factory-tool modules as a stable API when public HOM or `stateutils` can express the task. Do not save scenes or asset definitions as an implicit side effect of inspection.

## Houdini 21 notes

H21 adds `hou.Drawable2D` and `hou.Viewport2D` for two-dimensional Scene and Composite viewer work. The important H20 class split between generalized `hou.Node` and operator-specific `hou.OpNode` remains relevant to H21 scripts. General node creation, parameters, HDA definition methods, exceptions, command-line use, and viewer-state debugging workflows remain applicable.

## Official sources

- SideFX, [HOM introduction](https://www.sidefx.com/docs/houdini/hom/intro.html), accessed 2026-07-26.
- SideFX, [Python scripting](https://www.sidefx.com/docs/houdini/hom/), accessed 2026-07-26.
- SideFX, [Tool scripts](https://www.sidefx.com/docs/houdini/hom/tool_script.html), accessed 2026-07-26.
- SideFX, [Command-Line Scripting](https://www.sidefx.com/docs/houdini/hom/commandline), accessed 2026-07-26.
- SideFX, [hou.OpNode, including updateParmStates](https://www.sidefx.com/docs/houdini/hom/hou/OpNode.html), accessed 2026-07-26.
- SideFX, [hou.Parm hidden and disabled state](https://www.sidefx.com/docs/houdini/hom/hou/Parm.html), accessed 2026-07-26.
- SideFX, [hou.NodeError](https://www.sidefx.com/docs/houdini/hom/hou/NodeError.html), accessed 2026-07-26.
- SideFX, [Houdini 21 user interface and scripting changes](https://www.sidefx.com/docs/houdini/news/21/viewport.html), accessed 2026-07-26.
