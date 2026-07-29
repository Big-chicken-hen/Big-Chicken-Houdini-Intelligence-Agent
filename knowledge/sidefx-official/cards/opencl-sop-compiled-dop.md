# OpenCL SOP and compiled SOP-in-DOP workflow

Canonical ID: `opencl-sop-compiled-dop`

Houdini version: `21` (current online node pages may identify themselves as Houdini 22)

Pack version: 2.0.0

## Use for

Use this workflow after profiling identifies a data-parallel SOP calculation that may benefit from OpenCL, or when a SOP Solver DOP spends significant time copying Geometry that a compiled SOP block can safely update in place. These are related optimization routes but not the same feature. OpenCL SOP executes a kernel over bound geometry or volume data. SOP Solver DOP can invoke a compiled SOP block for Geometry. The latter does **not** mean that an arbitrary DOP network, DOP microsolver chain, or field solve can be placed inside a SOP Compile Block.

## Context and core data

OpenCL SOP binds constants, ramps, attributes, volumes, or VDB data to an OpenCL kernel. In the explicit Bindings interface, parameter order—not the descriptive parameter name—determines the kernel signature. In `@`-binding mode, `#bind` declarations and generated code reduce boilerplate, but the expanded implementation is informational and must not be treated as a stable API. The global work size can be rounded up for device efficiency, so every kernel must guard its element index against the bound length. Readable and writable flags control host/device transfers and whether data is copied back.

A compiled SOP block is bounded by Block Begin Compile and Block End Compile nodes and obeys strict static-dependency rules. When SOP Solver DOP enables **Invoke Compiled Block**, a SOP path that identifies a compiled block—or an Output SOP connected directly to it—can update the solved Geometry in place. The original solved data arrives through a named input, `data` by default. This path works for Geometry, not scalar, vector, or matrix fields.

## Recommended data flow

OpenCL route:

`small CPU/VEX reference -> explicit input attributes/volumes -> OpenCL bindings and bounds-safe kernel -> generated-code/build-log check -> numerical comparison -> optional consecutive OpenCL nodes inside one compiled SOP block -> OUT_*`

Compiled SOP-in-DOP route:

`initial DOP Geometry -> SOP Solver DOP with Invoke Compiled Block -> Block Begin Compile named data -> compilable SOP operation -> Block End Compile -> updated DOP Geometry`

Create and validate the ordinary result before optimizing it. Keep the CPU/VEX or non-invoked SOP path available long enough to compare values and timing from the same initial state. Put only measured hot work in the optimized region.

## Step-by-step workflow

1. Reproduce the desired result with a native SOP or VEX reference and profile the same geometry size, frame, cache state, and device conditions.
2. Define the OpenCL work item explicitly: point or primitive attribute, volume voxel, VDB value, or bounded workset. List every input, output, class, tuple size, precision, and read/write role.
3. Configure bindings in signature order, or enable `@`-binding and generate the interface from concise `#bind` declarations. Write only to the first SOP input and guard every global ID against the real bound length.
4. Begin with one deterministic operation. Compare selected values and aggregate ranges against the reference before adding iterations, neighborhood reads, optional bindings, or multiple outputs.
5. For algorithms where parallel writers can collide, separate gather and write-back work into two passes rather than relying on execution order. Use Worksets and barriers only with the documented workgroup boundary.
6. During diagnosis, enable **Finish Kernels**, inspect Generated Code, and capture compiler logs. Disable forced **Recompile Kernel** after development.
7. If several OpenCL SOPs form a measured hot chain, enclose compatible nodes in one compiled SOP block so intermediate data can remain on the device; compare cold and warm timings to the original chain.
8. For a DOP geometry solve, first make the ordinary SOP Solver path correct, including start-frame behavior, simulation space, timestep assumptions, and reset behavior.
9. Build a compiled SOP operation with a Block Begin Compile whose **Input Name** matches the SOP Solver primary input, normally `data`; remove dynamic geometry path references and non-compilable nodes.
10. Point SOP Solver to the compile end or a directly connected Output SOP, set **Data Name** to the intended Geometry data, and enable **Invoke Compiled Block**.
11. Bind extra inputs and outputs only when required, matching their Input Names and Fetch Time semantics. Treat Solve Metadata as explicit detail attributes rather than hidden path access.
12. Reset the DOP network and compare a representative frame sequence, geometry counts, attributes, positions, warnings, and Performance Monitor evidence between invoked and ordinary paths.

## Critical parameters and attributes

- OpenCL **Run Over** determines work size from the first writable attribute, first writable volume, or worksets. Rounded global size requires an index-length guard.
- **Readable**, **Writeable**, **Optional**, class, tuple size, and precision must match the actual attribute or volume. The same attribute cannot be bound at conflicting precisions.
- **Iterations** can keep data on the device across repeated evaluations; **Include Iteration**, time, and timestep bindings must be enabled only when used.
- **Use Write Back Kernel** supports a second pass for race-free output. **Finish Kernels** improves error attribution during debugging.
- Workset begin and length arrays define independent ranges. A single-workgroup path requires the synchronization pattern shown by generated code.
- SOP Solver **Invoke Compiled Block**, **SOP Path**, **Data Name**, **Primary Input**, extra Input Names, Fetch Time, and **SOP Output is in Simulation Space** define the in-place solve contract.
- Block Begin Compile **Input Name** is the matching key used by SOP Solver; Block End Compile **Primary Path** disambiguates multi-output blocks.

## Cache, version, and performance

OpenCL is not automatically faster than VEX. Kernel compilation, buffer allocation, CPU-to-GPU transfer, synchronization, and result readback can dominate small or irregular jobs. A non-OpenCL SOP between kernels can force data back to the CPU; grouping a compatible chain in a compiled block can avoid that transfer. Frequently changing a detail string used as kernel options causes expensive recompilation. Keep forced recompilation off after prototypes stabilize.

GPUs do not provide the same virtual-memory behavior expected from CPU workloads, so oversized buffers can fail rather than merely slow down. Record device, driver, precision, warm-up state, and geometry size with timing evidence. H21 includes the OpenCL driver version in cached-kernel hashes, so a driver change legitimately recompiles kernels. For DOP comparisons, reset to the same initial state; a warm simulation cache is not a valid comparison with a cold solve.

## Validation

For OpenCL, use a small deterministic input whose expected result can be calculated independently. Compare point or voxel samples, min/max and finite-value checks, counts, bounds, and a checksum or tolerance-based statistic against the CPU/VEX reference. Test the last valid index, a padded global range, a missing optional input, and the selected precision. During development, force completion so an error is reported at the responsible node, inspect generated signatures, and distinguish first-run compilation time from warm execution.

For the compiled SOP-in-DOP route, reset and solve the same short frame range through both paths. Confirm identical Geometry data names, point/primitive counts, required attributes, object-space interpretation, and values within a stated tolerance. Check the non-compilable badge and compile warnings; place the display flag after Block End when measuring compiled behavior. Then compare Performance Monitor evidence for copy/cook cost. This workflow is documented/static only: no H21 GPU kernel, driver, DOP solve, or performance claim was live-verified during this corpus audit.

## Common failures and troubleshooting

- Kernel compilation succeeds but data is wrong: the binding order, class, tuple size, precision, or read/write flag does not match the function signature.
- The last work items corrupt memory: the kernel assumed global size equaled attribute length and omitted its bounds guard.
- Neighbor updates flicker or vary by device: several work items write the same location; redesign as gather plus write-back.
- Performance regresses: the workload is too small, kernels recompile, data crosses CPU/GPU boundaries, or synchronization dominates.
- Allocation fails: lower the working-set size or precision within a verified error budget; do not assume CPU-style swapping.
- A volume binding errors: transforms or resolution do not align, or the kernel assumed unsupported precision. Current OpenCL SOP documentation states volume data bindings use 32-bit precision.
- The compiled block rejects a node: move it outside, replace it with a compilable operation, or keep the ordinary path.
- Strict nesting or internal-reference errors appear: route every crossing through a Block Begin and replace dynamic geometry paths with named/spare inputs.
- SOP Solver sees empty current Geometry through Object Merge or DOP Import: the data has been removed temporarily for in-place processing; consume the named `data` input instead.
- The invoked solve is empty or updates the wrong stream: SOP Solver and Block Begin Input Names or Data Name do not match.
- A field solve does not invoke: this compiled path supports Geometry only. Use an appropriate field microsolver, including Gas OpenCL when justified, rather than pretending the DOP field network is compiled.
- Timing unexpectedly uses the ordinary cook: a display flag is inside the block, compilation failed, or a fallback hid the compile failure.

## When not to use

Prefer a native SOP or VEX when the operation is simple, the dataset is modest, maintainability matters more than a measured speedup, or hardware variability would make delivery fragile. Do not use OpenCL for algorithms that require uncontrolled cross-work-item ordering or device memory beyond the available budget. Do not enable compiled invocation before the ordinary SOP Solver is correct and reproducible. Do not use this route for DOP fields, arbitrary microsolvers, or as evidence that an entire DOP network has been compiled. A SOP-level solver that already exposes the required operation may be clearer than a custom DOP network.

## Houdini 21 notes

OpenCL SOP and compiled SOP blocks predate H21; Invoke Compiled Block SOP is documented as existing since 16.5. H21 adds OpenCL topology bindings for half-edge and tetrahedral adjacency and includes the driver version in cached-binary hash keys. The SideFX OpenCL-for-VEX tutorial was authored for Houdini 20.5, while the current online node pages opened for this audit may show Houdini 22. Those sources support the core workflow, but H22-only bindings or UI additions must not be projected backward. Confirm exact node parameters and generated signatures in installed H21 help before implementation. Verification here is documented/static and source-reviewed, not live H21 or GPU verified.

## Official sources

- SideFX, [OpenCL SOP](https://www.sidefx.com/docs/houdini/nodes/sop/opencl.html), accessed 2026-07-27.
- SideFX, [OpenCL for VEX users](https://www.sidefx.com/docs/houdini/vex/ocl.html), accessed 2026-07-27.
- SideFX, [OpenCL SOP for VEX users](https://www.sidefx.com/tutorials/opencl-sop-for-vex-users/), accessed 2026-07-27.
- SideFX, [What's new: VEX and OpenCL in Houdini 21](https://www.sidefx.com/docs/houdini/news/21/vex.html), accessed 2026-07-26.
- SideFX, [SOP Solver DOP](https://www.sidefx.com/docs/houdini/nodes/dop/sopsolver.html), accessed 2026-07-27.
- SideFX, [Compiled blocks](https://www.sidefx.com/docs/houdini/model/compile.html), accessed 2026-07-26.
- SideFX, [Block Begin Compile SOP](https://www.sidefx.com/docs/houdini/nodes/sop/compile_begin.html), accessed 2026-07-27.
- SideFX, [Block End Compile SOP](https://www.sidefx.com/docs/houdini/nodes/sop/compile_end.html), accessed 2026-07-27.
