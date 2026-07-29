# PDG work-item and dependency workflow

Pack version: 2.0.0

## Use for

Use this workflow when a repeatable process can be divided into work items with explicit inputs, outputs, attributes, and dependencies. Typical uses include wedges, simulation variants, asset generation, per-frame processing, rendering, compositing, conversion, and publishing. PDG should describe deterministic work and ordering, not serve as an informal list of commands or a second reasoning system.

## Context and core data

TOP nodes construct a Procedural Dependency Graph. Processor nodes fan out and create work items that perform work or carry data. Partitioner nodes fan in, group upstream items, create dependencies, and optionally merge attributes. Work items inherit attributes from their parent and record input and output files with tags. Scheduler nodes execute ready commands locally or on a farm.

## Recommended data flow

Use `source data/File Pattern/Range/Wedge → processor stages → per-item output files → matching downstream processors → meaningful partitions → final publish`. Preserve frame or wedge identity through attributes. Add Wait for All only when a downstream item truly requires every upstream result, such as assembling a movie or manifest.

## Step-by-step workflow

1. Define the smallest independently useful unit of work and its expected output.
2. Create source items and inspect `pdg_index`, `pdg_frame`, `pdg_input`, and
   custom attributes in the work-item info panel. A processor child
   automatically receives its parent's output file list as its input list.
3. Add a processor that consumes the first upstream file with
   `` `@pdg_input.0` `` in a string path parameter, or `` `@pdg_input` `` when
   the node expects the built-in input selection. Give its result a unique path,
   such as ``$HIP/pdg/result_`@pdg_index`.bgeo.sc``, and register the produced
   file as an output rather than relying only on command success.
4. Connect downstream items by data dependency rather than relying on visual node order.
5. Add partitions only where several results must be aggregated or synchronized.
6. Generate the graph without cooking and inspect item counts, dependencies, and expected files.
7. Cook a small local subset, validate outputs, then select a completed item and
   resolve `` `@pdg_output.0` `` in a downstream string parameter. The value
   must equal the first registered output path and the file must exist. If a
   second output is expected, verify `` `@pdg_output.1` `` independently.

## Critical parameters and attributes

Built-ins use the `pdg_` prefix: `@pdg_index`, `@pdg_frame`, `@pdg_input`,
and `@pdg_output`. Work-item attributes are arrays; `.0` selects the first
component, so `@pdg_output.0` is the first registered output and
`@pdg_output.1` is the second. The HScript function
`pdgoutput(1, "", 0)` is equivalent to `@pdg_output.1`. In a string parameter,
wrap the `@` expression in backticks; numeric parameters can use an attribute
expression directly. Use `P@name` when PDG must win over an ambiguous geometry
attribute or context option.

Generate When controls whether items are known before cooking or created from
upstream results. File tags distinguish geometry, images, logs, and other
results. Unique output paths are mandatory when items run concurrently.

## Cache, version, and performance

Caching is part of the work-item contract. Automatic cache mode reads an existing valid file or cooks when it is missing and invalidates downstream caches when upstream writes change. Avoid tasks so small that process startup and scheduling dominate useful work. Batch adjacent frames or use PDG Services for repeated HDA, ROP, or Python work. Preserve fine dependencies where they permit useful overlap between stages.

## Validation

Generate before cook and inspect work-item dots, attributes, expected outputs,
and dependency lines. For one representative child, compare the parent's
`pdg_output` list with the child's `pdg_input` list; corresponding paths and
file tags should match. In a string parameter, verify `` `@pdg_input.0` ``
resolves to the first incoming file. After cooking, verify
`` `@pdg_output.0` `` resolves to the first actual output and
`` `@pdg_output.1` `` fails or resolves only when a second output is registered.
Open the actual file; a green item alone does not prove semantic correctness.

## Common failures and troubleshooting

Unexpectedly missing items often come from dynamic generation that has not
received upstream results. An empty `@pdg_input` usually means the parent did not
register an output file, the dependency is wrong, or the requested index does
not exist. Literal text such as `@pdg_output.0` in a filename means a string
parameter omitted backticks. Accidental serialization follows an early Wait for
All or a partition that groups too broadly. Overwritten files indicate
non-unique paths. A file holder item may look complete without executing a
command. Thousands of tiny processes require batching, not more scheduler slots.

## When not to use

Use a direct ROP or File Cache for a one-off single output with no meaningful variation or dependency graph. A frame-dependent simulation cannot be parallelized naively per frame; batch sequential frames, use feedback, or rely on solver checkpoints. Do not wrap every trivial parameter edit in a work item. TOPs coordinates deterministic tasks and does not replace creative or technical judgment.

## Houdini 21 notes

H21 change notes document efficient batch add/remove events, a Partition by
Frame filter, and scheduler changes including restored Deadline task scheduling
by default. The `@pdg_input`, `@pdg_output.0`, component indexing, backtick, and
parent-output-to-child-input behavior above is **documented/static** from
SideFX PDG help. This pack did not generate or cook an H21 TOP graph, so the
example resolutions and file existence checks are not live-verified here.

## Official sources

- SideFX, [Introduction to PDG and TOPs](https://www.sidefx.com/docs/houdini/tops/intro.html), accessed 2026-07-26.
- SideFX, [Using PDG attributes](https://www.sidefx.com/docs/houdini/tops/attributes.html), accessed 2026-07-26.
- SideFX, [PDG common how-tos](https://www.sidefx.com/docs/houdini/tops/commonhowto.html), accessed 2026-07-26.
- SideFX, [pdgoutput expression](https://www.sidefx.com/docs/houdini/expressions/pdgoutput.html), accessed 2026-07-26.
- SideFX, [PDG FX workflow tutorial](https://www.sidefx.com/docs/houdini/tops/tutorial_pdgfxworkflow.html), accessed 2026-07-26.
- SideFX, [Houdini 21 PDG changes](https://www.sidefx.com/docs/houdini/news/21/pdg.html), accessed 2026-07-26.
