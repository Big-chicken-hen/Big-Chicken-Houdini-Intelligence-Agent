# Pyro cache, shading, and render workflow

Pack version: 2.0.0

## Use for

Use this workflow after a Pyro simulation's motion has been approved. It prepares fields for compact disk storage, separates simulation from look development, builds a viewport and render representation, and validates the final volume in Solaris/Karma. It is designed for shots where cache size, field naming, scattering, color management, and render cost must be controlled explicitly.

## Context and core data

Raw Pyro output may contain scalar Houdini volumes or VDBs for density, temperature, flame, and auxiliary masks, plus vector velocity. Rendering normally needs only a subset. Pyro Post-Process converts, culls, resamples, and compresses fields. Pyro Bake Volume creates visualization data and can build a matching material, including an optional scatter volume for interior light scattering. File Cache stores the approved result independently of the live solver. In Solaris, Volume LOP authors a volume prim plus field prims that reference named fields in `.vdb` or `.bgeo` files; a Material Library authors the volume material and Assign Material creates the USD binding.

## Recommended data flow

Use `Pyro Solver with checkpoints → Pyro Post-Process → File Cache → Volume LOP → Material Library with a renderer-supported volume shader → Assign Material → Render Vars/Products → Karma Render Settings → USD Render ROP`. If velocity is required for motion blur, retain and transform it correctly. Keep the raw simulation cache when later post-process or shading variations are likely; optionally create a second lightweight render cache.

## Step-by-step workflow

1. List the fields required for shading, motion blur, relighting, and downstream effects.
2. For a long solve, enable Pyro Solver **Save Checkpoints** and set an explicit Base Folder, Base Name, Version, Checkpoint Trail Length, and Checkpoint Interval. Choose a trail long enough to retain a known recovery point. If any upstream solver input changes, advance Version or use a new Base Folder/Base Name because invalidated explicit checkpoints are not deleted automatically.
3. Remove unused debug fields and compute min/max metadata where it helps later inspection.
4. Convert scalar fields to sparse VDBs, merge velocity components to a vector VDB, and test 16-bit storage.
5. Cull inactive regions and resample noncritical fields at a larger voxel size.
6. Write a deterministic frame-range File Cache, reload it with the live solver bypassed, and separately test restarting the simulation from a saved checkpoint.
7. In Volume LOP, set the volume primitive path, file path, source Fields, and any source-to-destination field renames. Require errors for missing production files.
8. Author and bind the material. For Karma CPU, use a CPU-supported volume network; for XPU, use a route listed as supported for the target build, such as Karma Volume or the supported Pyro Preview/Karma-specific path, rather than assuming generic MaterialX VDF/EDF support.
9. Bind density, emission/flame, temperature/color, and velocity names deliberately, add only required Render Vars to the Render Product, and render representative frames in the final OCIO display.

## Critical parameters and attributes

Convert to VDB discards inactive zero regions. For Houdini volumes, 16-bit Float reduces both memory and disk storage; for VDBs, the 16-bit option affects serialized files rather than the in-memory VDB representation. Either choice can reduce precision and may cost conversion time. Voxel Size Scale of two gives half the resolution per axis and one eighth of the voxels. Cull Mask Volume restricts the active VDB region. Flame Density ensures emissive but smokeless regions remain renderable. Velocity must remain a vector field with correct units for volume motion blur.

Checkpoint Version selects a separate on-disk recovery set; Trail Length controls how much recent history this Houdini session retains, and Interval controls the frame spacing between checkpoints. A larger interval saves disk but increases re-simulation after failure. Volume LOP `Fields` names must exist in the file, and Field Customizations may rename the source field to the USD field prim consumed by the material. The USD material binding must target the volume prim or an appropriate ancestor, and each requested AOV must be an ordered RenderVar of the active RenderProduct.

## Cache, version, and performance

SideFX reports that the Pyro Solver's Optimize Exports setup can reduce disk use dramatically, in some cases around ninety percent, but that is not a quality guarantee. Compare before and after. Checkpoints are solver state for crash recovery; File Cache/VDB frames are exchange and rendering data, and one does not replace the other. Explicit checkpoints remain after invalidation, and a previous session's files are not cleaned by the current Trail Length. Before relying on recovery, stop a bounded test after a checkpoint, restart from the documented recovery frame, and compare the next frame's field names, bounds, and extrema with an uninterrupted solve. After changing a solver input, advance Version or the path and prove that the old sequence is not reused. In Karma, reduce volume step or shadow quality only after checking silhouettes and self-shadow. H21 adds a faster volume velocity blur lookup mode and permits shadow step rates below one, trading accuracy for speed.

## Validation

Compare raw and processed density, flame, velocity extrema, active bounds, and file sizes on several frames. Reload the cache in a fresh playback pass. For long simulations, prove that the selected checkpoint exists, can be resumed, and produces the same next-frame field schema and representative statistics as the uninterrupted test. In Solaris, inspect the volume and field prim paths, resolved asset paths, field relationships, material binding, and ordered RenderVars. Check alpha for unexpected density introduced only to carry emission. Verify velocity blur direction and magnitude. Compare CPU and XPU only with a material path supported by both; a missing XPU volume is a support-route failure, not evidence that the cache is empty.

## Common failures and troubleshooting

Black or invisible fire often means the emissive field was removed, renamed, or bound under a different USD field name. Excessive cache size comes from dense volumes, oversized active regions, debug fields, full-resolution velocity, or an unlimited checkpoint trail. A failed restart can come from the wrong Base Folder/Version or an interval that never wrote the expected frame. More dangerously, an unchanged checkpoint identity after upstream edits can silently reuse stale explicit files; Reset does not delete them. Advance Version or the path and verify the recovery frame. Flickering can result from over-aggressive lossy resampling or changing active topology. Wrong motion blur usually indicates scalar velocity components were not merged or transformed as vectors. CPU output that disappears on XPU can indicate an unsupported generic VDF/EDF route. A viewport match can still render differently if OCIO, material bindings, RenderVars, or volume step settings differ.

## When not to use

Do not build a second render cache when the raw sparse VDBs are already small and iteration is unlikely. A static cloud can be cached as one frame. Do not use Pyro Bake Volume as a substitute for a studio's established Solaris volume-material workflow. Avoid 16-bit or aggressive resampling for fields used in precise downstream simulation or scientific measurement.

## Houdini 21 notes

The workflow above is documented/static. H21 additions such as ML Volume Upres, direct VDB point and level-set rendering in Karma, volume-light support in XPU, and volume motion-blur improvements are change-note-supported. Treat ML upres as a visual post-process and compare temporal behavior. Current H22 documentation may contain newer Karma volume capabilities that are not part of H21. This corpus build did not live-verify the card in an H21 GUI, solve, or render.

## Official sources

- SideFX, [Introduction to Pyro: post-processing and rendering](https://www.sidefx.com/docs/houdini/pyro/intro.html), accessed 2026-07-26.
- SideFX, [Pyro Solver SOP](https://www.sidefx.com/docs/houdini/nodes/sop/pyrosolver.html), accessed 2026-07-26.
- SideFX, [Caching simulations](https://www.sidefx.com/docs/houdini/dyno/cache), accessed 2026-07-26.
- SideFX, [Volume LOP](https://www.sidefx.com/docs/houdini/nodes/lop/volume.html), accessed 2026-07-26.
- SideFX, [Material Library LOP](https://www.sidefx.com/docs/houdini/nodes/lop/materiallibrary.html), accessed 2026-07-26.
- SideFX, [Assign Material LOP](https://www.sidefx.com/docs/houdini/nodes/lop/assignmaterial.html), accessed 2026-07-26.
- SideFX, [Karma Volume VOP](https://www.sidefx.com/docs/houdini/nodes/vop/kma_volume.html), accessed 2026-07-26.
- SideFX, [Karma XPU support and limits](https://www.sidefx.com/docs/houdini/solaris/karma_xpu.html), accessed 2026-07-26.
- SideFX, [Karma AOVs](https://www.sidefx.com/docs/houdini/solaris/support/aovs.html), accessed 2026-07-26.
- SideFX, [Houdini 21 Karma changes](https://www.sidefx.com/docs/houdini/news/21/karma.html), accessed 2026-07-26.
