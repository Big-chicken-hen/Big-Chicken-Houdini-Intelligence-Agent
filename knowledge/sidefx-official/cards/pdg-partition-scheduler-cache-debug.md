# PDG partition, scheduler, cache, and debug workflow

Pack version: 2.0.0

## Use for

Use this workflow after basic work-item generation is correct, when a TOP graph must aggregate items, run locally or on a farm, reuse file caches, amortize startup cost, and provide actionable diagnostics for failed or blocked jobs. It is intended for production-scale graphs where scheduler environment and filesystem behavior are part of correctness.

## Context and core data

A partition item depends on multiple upstream work items and may merge their attributes or output-file lists. A scheduler launches commands with a working directory, temporary directory, script directory, environment, and resource limits. PDG cache modes determine whether existing output files are read, ignored, or overwritten. PDG Services keep compatible Houdini processes alive so repeated items avoid scene and process initialization.

## Recommended data flow

Use `validated processors → partition by frame/attribute or Wait for All → batched or service-enabled processors → scheduler → publish`. Configure shared paths and path mapping before farm submission. Keep logs and explicit output files per item. Use local scheduling for a reduced test range, then switch scheduler only after commands and paths are proven portable.

## Step-by-step workflow

1. Identify the exact aggregation key and choose Partition by Frame, Partition by Attribute, or Wait for All accordingly.
2. Inspect merged attributes and output lists on the resulting partition item.
3. Configure unique output, log, temporary, and working paths reachable by the selected scheduler.
4. Choose cache mode and state what makes an existing result valid.
5. Batch short sequential items or configure a PDG Service for compatible repeated Houdini work.
6. Run a small local cook, inspect command lines and logs, then submit the same subset to the farm.
7. Use Task Graph Table, item states, output validation, and scheduler logs to isolate failures before retrying.

## Critical parameters and attributes

Partitions can merge attributes as arrays; a downstream parameter expecting one scalar must select deliberately. `PDG_DIR` and `__PDG_DIR__` provide portable path roots, while PDG Path Map translates between platforms. Automatic cache reads files when present and invalidates downstream work after upstream writes. Automatic Ignore does not perform that invalidation. Always Read fails when files are absent. Always Write forces recomputation. Batch size must respect sequential dependencies and memory.

## Cache, version, and performance

Use PDG Services for HDA Processor, ROP Fetch, and Python Script when process startup is a substantial fraction of item time. Service blocks reserve a worker for a sequential feedback loop. Batch frames when one process can reuse scene state, as SideFX's FX tutorial demonstrates. Do not use Always Read as a substitute for provenance checks. Validate file existence and, where needed, metadata or manifest hashes before accepting caches.

## Validation

Inspect each partition's member list, merged attributes, and dependencies. Confirm that all farm workers can read inputs and write outputs through the same logical path. Compare the environment and command line of local and farm items. Enable scheduler output validation where available. Use cook-time visualization to distinguish compute bottlenecks from queue and startup overhead. Re-run one failed item in isolation before dirtying an entire graph.

## Common failures and troubleshooting

Blocked items usually wait on failed or missing dependencies. Missing outputs can result from a command reporting success without writing the declared file. Farm-only failures commonly involve non-shared `$HIP` paths, missing assets, different plugins, shell quoting, permissions, or path separators. Stale Automatic Ignore caches conceal upstream changes. Partitions that merge conflicting scalar attributes create surprising lists. Excessive jobs rather than tasks can trigger scheduler overhead or race behavior.

## When not to use

Do not partition merely to make the graph look organized. Avoid a global Wait for All when per-frame streaming is possible. Do not start a service for a single long item or for an executable that cannot reuse process state. A stateful simulation should use a sequential batch, feedback loop, or checkpointed solver rather than independent farm frames.

## Houdini 21 notes

The workflow above is documented/static. H21's restored pre-20.5 Deadline task mode, optional separate-job scheduling, HQueue container-job controls, and more efficient batch work-item event APIs are change-note-supported. These changes make scheduler choice and job granularity explicit H21 concerns. This corpus build did not live-verify a scheduler, cook, or farm job in H21.

## Official sources

- SideFX, [PDG cache modes](https://www.sidefx.com/docs/houdini/tops/pdg/cacheMode.html), accessed 2026-07-26.
- SideFX, [PDG Services](https://www.sidefx.com/docs/houdini/tops/services.html), accessed 2026-07-26.
- SideFX, [TOP file paths](https://www.sidefx.com/docs/houdini/tops/paths.html), accessed 2026-07-26.
- SideFX, [Houdini 21 PDG changes](https://www.sidefx.com/docs/houdini/news/21/pdg.html), accessed 2026-07-26.
