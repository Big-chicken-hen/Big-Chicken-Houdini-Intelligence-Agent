# For-Each loops and compiled blocks

Pack version: 2.0.0

## Use for

Use For-Each blocks when the same network must process every named piece, when a result must feed back into the next iteration, or when one geometry stream must be processed against successive elements of another. Use compiled blocks after profiling shows that many independent piece iterations dominate cook time. This card intentionally excludes the main copying and instancing workflow, which belongs in the dedicated pack/copy/instances card.

## Context and core data

Piecewise loops commonly partition primitives by string `name` or integer `piece`. A metadata Block Begin can expose detail attributes `iteration`, `numiterations`, `value`, and `ivalue`. Feedback loops pass the previous iteration's output into the next. Compiled blocks trade dynamic network behavior for parallel and in-place execution. They prohibit dynamic internal references and require strict block nesting.

## Recommended data flow

Piece workflow:

`create stable name/piece -> Block Begin: Extract Piece -> per-piece SOP chain -> Block End: Merge Each Iteration`

Feedback workflow:

`Block Begin: Fetch Feedback -> bounded operation -> Block End with conservative Max Iterations`

Compiled workflow:

`Compile Begin -> top-level For-Each and compilable chain -> For-Each End with Multithread when Compiled -> Compile End`

## Step-by-step workflow

1. Decide whether the task is independent piece processing, fixed repetition, or sequential feedback; choose the simplest valid construct.
2. Create stable `name`, `piece`, `id`, or variant attributes before entering the block.
3. Configure Block Begin and Block End methods explicitly, then test with one iteration or `Single Pass`.
4. Add a metadata import only when the internal network genuinely needs iteration values.
5. Test accumulation, merge behavior, and piece ordering before increasing the iteration count.
6. Profile the working uncompiled network and identify whether iteration overhead is material.
7. If compilation is justified, enclose the loop in a compiled block, enable multithreading on the top-level loop, and fix all boundary violations.
8. Validate ordering, counts, transforms, and performance against the uncompiled result.

## Critical parameters and attributes

- `Piece Attribute` chooses the partition or source variation.
- `Max Iterations`, `Single Pass`, `Start Value`, and `Increment` control loop evaluation.
- `Stop Condition` is evaluated before an iteration and is unavailable in compiled blocks.
- `Multithread when Compiled` distributes independent iterations.
- `Block Path` binds Begin nodes and metadata imports to the intended End node.
- `Method` distinguishes Fetch Feedback, Extract Piece or Point, Fetch Input, and Fetch Metadata behavior.

## Cache, version, and performance

Compile only after the ordinary loop is correct and measured. Compiled blocks are most valuable for large numbers of independent pieces. Avoid nested multithreading at several loop levels because task counts can explode. Cache an expensive finished loop result at a stable boundary. Preserve piece identity attributes in the cache so downstream selection remains deterministic.

## Validation

Start at `Max Iterations=1`, increase gradually, and use `Single Pass` to inspect representative pieces. Compare pre- and post-loop piece counts, names, bounds, and point/primitive totals. Record a Performance Monitor profile before and after compilation using the same cook. Check the non-compilable SOP badge and ensure the display flag is after Compile End when measuring compiled behavior.

## Common failures and troubleshooting

- A feedback loop grows exponentially: reduce iterations and inspect whether each pass duplicates the entire input.
- Point or primitive order changes after merging: depend on `id`, `name`, or `piece`, not indices.
- Compilation reports an unsupported node: replace it, move it outside, or keep the block uncompiled.
- A wire crosses a compiled boundary: insert the proper Block Begin in Fetch Input mode and enforce strict nesting.
- An expression references a node inside the block by path: use a spare input or explicit block input.
- Stop Condition appears ignored: it is not supported in compiled blocks.
- A piece loop receives groups rather than disjoint pieces: convert group membership to a `name` or integer partition attribute first.

## When not to use

Do not use a For-Each loop solely for translation, rotation, scale, or source-piece selection that a dedicated copying workflow handles natively. Do not compile a small or infrequently cooked network without profiling evidence. Avoid compiled blocks when the design depends on `stamp()`, local variables, dynamic internal path references, unsupported nodes, or early stopping. Do not use feedback for operations that are mathematically independent and parallelizable.

## Houdini 21 notes

The fundamental For-Each and compiled-block workflows predate H21 and have no documented H21 semantic break. H21 makes UV Fuse compilable, which may allow additional UV operations inside compiled chains. Current SideFX pages may display a newer documentation version, so H21 compatibility should be checked against node availability and the H21 change list.

## Official sources

- SideFX, [Looping in geometry networks](https://www.sidefx.com/docs/houdini/model/looping.html), accessed 2026-07-26.
- SideFX, [Compiled blocks](https://www.sidefx.com/docs/houdini/model/compile.html), accessed 2026-07-26.
- SideFX, [Compiled SOPs H16 Masterclass](https://www.sidefx.com/tutorials/houdini-16-masterclass-compiled-sops/?collection=30), accessed 2026-07-26.
