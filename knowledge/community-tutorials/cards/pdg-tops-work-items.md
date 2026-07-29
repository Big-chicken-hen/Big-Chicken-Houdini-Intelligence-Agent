# PDG TOP work-item and artifact contracts

Source kind: `community_tutorial`
Verification: `community_unverified`
Version: one source is H19.5 and the other spans multiple releases; verify current TOP scheduler/node parameters.

## Use, prerequisites, and target

Use for wedges, batch geometry, simulations, renders, data conversion, or dependency orchestration. Inputs need a bounded item set, deterministic parameterization, and project-local or explicitly approved output paths. The target is a TOP graph whose work-item attributes, dependencies, dirty state, scheduler behavior, and output artifacts are independently verifiable.

## Semantic network stages

Item generation → attribute normalization → partition/map/expand → work execution → artifact validation → gather/report → `OUT_PDG`. External executables or Houdini cooks sit in execution nodes; validation follows them as a separate stage.

## Ordered workflow

1. Define the work-item contract: unique key, frame/range, wedge parameters, input files, output path, expected artifact type, and retry policy. Decide which attributes are static and which are generated during cook.
2. Generate items with `Wedge TOP`, `Range Generate TOP`, `File Pattern TOP`, `Geometry Import TOP`, or a custom generator. Preview the task table before cooking. Ensure the expected item count and unique key cardinality.
3. Normalize attributes with `Attribute Create TOP`, `Attribute Copy TOP`, expressions, or Python Processor only when native nodes cannot express the mapping. Use explicit attribute syntax and avoid hidden dependence on UI selection/current frame.
4. Shape dependencies with partitioners, mappers, or expand/collapse nodes. Confirm whether downstream work is one-to-one, many-to-one, or one-to-many and inspect dependency edges.
5. Execute with an appropriate Houdini, ROP, command, or service node using the existing scheduler. Parameterize paths from work-item attributes, quote safely, and keep outputs under approved roots. Do not create a new worker/service for this pack.
6. Add an artifact-validation stage that checks expected path, non-zero size, frame/token match, and optional application-specific metadata. A successful process exit without its expected file is not a successful item.
7. Test dirtying: change one input attribute/file and confirm only dependent items become dirty. Regenerate only when item topology must change; recook when execution results changed.
8. Gather/report success, failure, duration, and output paths. End at `OUT_PDG` with no unintentionally dirty or cooked-from-stale items.

## Key responsibilities and fields

Generators own item cardinality; attributes own parameter contracts; dependency nodes own fan-in/fan-out; processors own side effects; scheduler owns execution placement; validators own artifact truth. Common fields include `work_item.index`, frame, wedge values, input/output file attributes, and custom stable IDs. Exact expression syntax varies by node and version.

## Data flow, cache, version, and performance

PDG caching is valid only when dependencies and output declarations are complete. Large dynamic graphs cost memory; generate bounded items and partition intentionally. Avoid recooking Houdini sessions when batch nodes can reuse them through existing supported mechanisms, but do not introduce a new worker. Store artifacts in versioned paths and never let two items overwrite the same file.

## Common failures and repairs

- **All items recook:** attributes or files are marked as broad dependencies; isolate the actual inputs.
- **Items succeed but files missing:** declare/validate output paths and fail the item on absent artifacts.
- **Duplicate overwrite:** unique key is not included in path; validate path uniqueness before execution.
- **Graph stalls:** inspect dependency cycle, scheduler slots, and upstream failures.
- **Different local/farm result:** environment/path assumptions are implicit; make them item attributes and approved scheduler configuration.

## Provenance boundary

Matt Estela's TOP notes and Daniel Lee's PDG introduction motivate task-table-first reasoning. The work-item contract, validation stage, and dirtying test are original synthesis. Scheduler behavior is not verified in the current runtime.

## Semantic expectations and verification checklist

- Presence: every item has required ID, parameter, input, and output attributes.
- Mapping: one-to-one/fan-in/fan-out dependencies match the declared graph semantics; output paths are unique.
- Range: item counts and wedge/frame ranges match the contract; indices and numeric parameters are finite/in bounds.
- Cook evidence: success/failure counts reconcile with total items and changing one input dirties only its dependency closure.
- Cache/artifact evidence: every successful item has an existing non-empty artifact with matching token/frame; stale or partial artifacts are rejected.
- Operational evidence: no path escapes approved roots and no new database, worker, scheduler, or service is introduced.

## Sources

- Matt Estela, [Tops](https://www.tokeru.com/cgwiki/HoudiniTops.html).
- Daniel Lee, [Intro to PDG](https://www.sidefx.com/tutorials/intro-to-pdg/).
