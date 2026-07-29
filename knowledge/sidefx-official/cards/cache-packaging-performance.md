# File cache, versioning, and package boundaries

Pack version: 2.0.0

## Use for

Use this workflow when expensive SOP work must be reused, simulations need deterministic disk sequences, geometry must cross a durable cache boundary, or a Houdini project must be handed to another machine with explicit dependencies and versions. It covers File Cache configuration, cache validation, packed-disk delivery, project preflight, and a clean-environment reopen. Use the dedicated diagnostics/performance card for profiling methodology and the pack/copy/instances card for repetition design.

## Context and core data

Houdini cooks nodes to compute scene state. A File Cache can write `.bgeo.sc`, `.vdb`, or other geometry and later replace its input cook with disk reads. Packed primitives are lightweight references to embedded, shared, or disk geometry; they can be transformed and assigned attributes but not directly edited internally. Packed Disk Sequence primitives know the file sequence and can provide subframe blending when topology matches. Polygon soups compactly store many similar polygons but have limited support for topology and some vertex or primitive operations.

A portable project may need the HIP, textures, geometry and simulation caches, USD layers and their dependencies, HDA libraries, scripts, color configuration, and other external files referenced by parameters. `$HIP` resolves relative to the scene file and `$JOB` provides a shared project root. Houdini's Preflight view and `hou.fileReferences()` expose external references. **Project policy:** the package author still decides which files are required, generated, optional, licensed, or machine-specific, and whether redistribution is authorized.

## Recommended data flow

Use:

`working network -> Performance Monitor baseline -> identify stable expensive boundary -> File Cache with explicit path/version -> Load from Disk -> packed or regular downstream representation -> lightweight finishing -> OUT_*`

For repeated assets:

`source library cache -> Pack or Packed Disk -> Copy to Points/instancing -> viewport proxy display -> render/export`

For a project handoff, the following is an **editorial recommendation**, not an automatic SideFX packaging command:

`stable cache and output versions -> enumerate external references -> classify generated/source/optional dependencies -> rewrite portable paths under $HIP or $JOB -> copy only authorized required files -> open in a clean environment -> recook/render a bounded acceptance case`.

Keep editable construction upstream of packing. Put caches after expensive stable stages while leaving inexpensive art-direction controls downstream when possible.

## Step-by-step workflow

1. Record a Performance Monitor profile while performing the exact slow cook, playback, or viewport action.
2. Identify whether the cost is computation, memory, file I/O, geometry duplication, viewport draw, or repeated simulation.
3. Choose memory Cache, File Cache, packed disk geometry, polygon soup, or regular polygons according to the required editability.
4. Configure a unique cache base name, folder, version, frame policy, file type, and missing-frame behavior.
5. Write the cache, enable `Load from Disk`, and inspect metadata and representative frames before disconnecting attention from the source.
6. Pack only geometry that no longer needs internal deformation or topology edits, and choose a lightweight viewport display mode.
7. Compare the same Performance Monitor action and memory information after the change.
8. Run project preflight or inspect `hou.fileReferences("JOB")`; inventory textures, geometry, USD, HDA, scripts, OCIO, and other external dependencies, including sequence and UDIM members.
9. **Project policy:** classify dependencies as source, generated cache, deliverable, optional, licensed, or machine-specific. Move or copy only files the project is authorized to redistribute, and use `$JOB` or `$HIP` where those path semantics match the receiving environment.
10. Open the packaged HIP or top-level USD in a clean environment with the intended Houdini build and packages. Reload caches, resolve HDAs and textures, and run one bounded cook or render without relying on the original machine's absolute paths.
11. **Project policy:** document invalidation conditions, Houdini and plug-in versions, environment/package requirements, topology assumptions, frame coverage, missing optional assets, and exact cache and delivery roots.

## Critical parameters and attributes

- `.bgeo.sc` is the general compressed Houdini geometry default.
- Disable `Time Dependent Cache` for static inputs.
- Enable `Simulation` when each frame depends on the previous frame.
- Use `Version` and meaningful `Base Name` values to prevent collisions.
- `Missing Frame = Report Error` is safest when downstream empty geometry is unacceptable.
- `Packed Disk Primitive` delays loading and shares referenced geometry.
- `Packed Disk Sequence` supports sequence-aware subframe geometry when topology is consistent.
- `Display As` can use full geometry, points, bounds, centroid, or hidden representation.
- Attribute precision conversion reduces file size but changes numerical fidelity.
- MMB Memory, New, and Unique values distinguish referenced, newly allocated, and exclusively held memory.
- `$HIP` is scene-relative; `$JOB` is the explicit multi-scene project root.
- `hou.fileReferences("JOB")` can preserve `$JOB` tokens while enumerating external parameter references.
- Frame tokens, UDIM tokens, USD references/payloads, HDA libraries, OCIO files, and package-provided paths must be expanded as dependency families rather than checked as one literal file.

## Cache, version, and performance

**Project policy:** treat cache paths and file schemas as versioned contracts, define invalidation triggers for topology, parameters, attribute schema, Houdini compatibility, and simulation dependencies, and label package contents as source, regenerable cache, or final deliverable. SideFX documents the cache and packed-geometry mechanisms; it does not prescribe a universal studio version scheme. Do not cache every trivial stage. Use packed instances for many unchanged copies, retain regular polygons through topology editing, and use Unload only where measured ownership shows a benefit.

## Validation

Check first, middle, and last frames for bounds, element counts, primitive types, names, velocities, UVs, and material-related attributes. Confirm cache metadata such as host, software version, time, and cook time where available. Test a missing frame intentionally if the pipeline depends on strict failure. For Packed Disk Sequence, confirm topology consistency and motion blur at subframes. Compare source and cached hashes or representative geometry statistics. Measure performance with the same profile scope before and after optimization.

For a project package, the **editorial acceptance recommendation** is a dependency inventory with no unexplained outside references, followed by a reopen under the intended `$JOB`, HDA and plug-in resolution, representative sequence and UDIM loads, USD composition inspection, and one task-relevant cook or render. These are proposed validation steps; this corpus rebuild did not live-run them in Houdini 21.

## Common failures and troubleshooting

- A static cache writes many identical files: disable time dependence.
- A simulation differs on a farm: enable simulation sequencing and inspect the generating host and software metadata.
- A later node receives empty geometry: set missing frames to errors and verify the evaluated path.
- Two caches overwrite each other: use unique node names, base names, folders, or version directories.
- Packed geometry cannot be edited: unpack the required piece, edit, then repack if sharing is still valuable.
- Subframe motion blur breaks: topology differs between sequence samples or the wrong packed mode is used.
- A precision reduction changes shading or motion: restore higher precision for positions, normals, UVs, velocities, or other sensitive attributes.
- Memory does not fall after unload: Houdini may retain allocated memory for reuse; inspect node ownership rather than process size alone.
- A project opens only on the authoring machine: preflight absolute paths, environment-only variables, user-local HDA definitions, OCIO, plug-ins, and resolver configuration.
- A file-reference inventory looks complete but frames or tiles are missing: expand sequence, UDIM, and layered USD dependencies across the actual delivery range.
- A packaged HDA resolves differently: inspect the active definition and library load order; do not hide the conflict by embedding an unreported copy in the HIP.

## When not to use

Do not introduce a disk cache for a cheap node chain whose controls must remain interactive. Do not pack geometry before internal edits are finished. Avoid polygon soups for small geometry or networks with frequent topology operations. Do not use a memory Cache for long high-resolution sequences without a strict frame limit. **Project policy:** do not redistribute third-party or licensed content without permission, and do not rewrite a managed resolver or farm environment into local paths when the recipient requires it. Do not claim an optimization or portable package from intuition; verify it with representative scene data in the target context.

## Houdini 21 notes

H21 allows Ctrl-clicking Reset Simulation on DOP, SOP, and COP nodes to clear the cache and jump to the simulation start frame. Vulkan geometry update and drawing threading is enabled by default on Windows and Linux, improving several multi-object and deforming-geometry cases. File Cache and packed-primitive fundamentals remain stable. Current documentation may display a newer release, so record the exact H21 build used to generate production caches.

## Official sources

- SideFX, [File Cache SOP](https://www.sidefx.com/docs/houdini/nodes/sop/filecache.html), accessed 2026-07-26.
- SideFX, [Primitives](https://www.sidefx.com/docs/houdini/model/primitives.html), accessed 2026-07-26.
- SideFX, [Pack SOP and packed primitives](https://www.sidefx.com/docs/houdini/nodes/sop/pack), accessed 2026-07-26.
- SideFX, [Performance Monitor pane](https://www.sidefx.com/docs/houdini/ref/panes/perfmon.html), accessed 2026-07-26.
- SideFX, [Cooking](https://www.sidefx.com/docs/houdini/basics/cooking), accessed 2026-07-26.
- SideFX, [Houdini 21 user interface and scripting changes](https://www.sidefx.com/docs/houdini/news/21/viewport.html), accessed 2026-07-26.
- SideFX, [Project management and preflight](https://www.sidefx.com/docs/houdini/basics/project.html), accessed 2026-07-26.
- SideFX, [hou.fileReferences](https://www.sidefx.com/docs/houdini/hom/hou/fileReferences.html), accessed 2026-07-26.
