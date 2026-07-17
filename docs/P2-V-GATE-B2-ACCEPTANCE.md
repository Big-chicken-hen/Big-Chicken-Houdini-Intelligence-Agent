# P2-V Gate B2 acceptance

## Acceptance record

- Acceptance date: 2026-07-17
- Gate: P2-V Gate B2, read-only Houdini capability slice
- Acceptance method: user-executed manual acceptance in a real Houdini GUI session
- Houdini build displayed by the Panel: `21.0.440`
- Read-only protocol profile displayed by the Panel: `0.2.0`
- Result: B2B manual GUI acceptance passed

This was a human-observed GUI acceptance performed by the user in a real Houdini session. It was **not** an automated Houdini test, a headless HOM test, or evidence produced by the offline fake-Houdini test suite.

## Accepted observations

The user accepted the following observations from the live Panel and scene inspection:

- The live Catalog matched the reviewed Catalog.
- `houdini_scene_info` was available.
- `houdini_node_type_info` was available.
- The allowlisted node-type summary reported `5/5` available:
  - `Object/geo`
  - `Sop/box`
  - logical `Sop/transform`, correctly resolved to Houdini's live internal type name `xform`
  - `Sop/merge`
  - `Sop/null`
- The scene revision remained `0` throughout the acceptance.
- The pre-existing `/obj/geo1` node was present before the Panel was opened; its SOP network was empty and remained unchanged.
- No node was created, modified, connected, disconnected, or deleted.
- No parameter value, selection, current scene state, or scene dirty state changed.
- No cook, render, HIP save, cache, or HDA operation occurred.

These observations establish only the finite read-only B2 capability slice. They do not establish any write-path, cook, render, persistence, or graph-transaction behavior.

## Evidence boundary

This record is the user's manual acceptance statement. It does not claim that an automated test controlled Houdini or independently measured the GUI observations.

No repository screenshot artifact is asserted by this document. It does not invent or cite a screenshot, screenshot path, automation log, HOM transcript, or other automated Houdini evidence. Offline unit-test results, when reported separately, are evidence for the Python contracts and fakes only and must not be represented as live Houdini GUI evidence.

## Capabilities that remain disabled

- No real MCP service was registered or started; real MCP registration remains incomplete.
- `houdini_graph_validate` remains disabled.
- `houdini_graph_apply` remains disabled.
- `houdini_graph_verify` remains disabled.
- All Houdini scene-write capabilities remain disabled.
- Gate B2 does not authorize node creation, parameter writes, wiring, deletion, cook, render, save, cache, HDA operations, or arbitrary Python, HScript, shell, `eval`, or `exec` execution.
- The work did not enter B2C or B3, and this acceptance does not authorize either stage.

## Gate conclusion

B2A's offline implementation and the user's B2B real-Houdini GUI acceptance are complete. Gate B2 remains bounded to the two read-only tools above and awaits the user's separate authorization for any final commit. No later-phase capability is implied by this acceptance.
