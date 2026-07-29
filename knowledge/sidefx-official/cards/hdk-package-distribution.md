# HDK plug-in build and package distribution

Pack version: 2.0.0

Canonical ID: `hdk-package-distribution`

Houdini version: `21.x target; compiler, platform, and HDK API version specific`

## Use for

Use this workflow when a feature genuinely requires a compiled C++ Houdini
plug-in: a custom operator, command, expression function, renderer integration,
or native algorithm whose API access or measured performance cannot be met by
HOM, VEX, OpenCL, SOP verbs, or an HDA. It covers selecting the installed HDK,
building a DSO, staging resources, loading through a Houdini package, and
diagnosing a clean deployment. It is not a generic project-file package or an
HDA versioning guide.

## Context and core data

The Houdini Development Kit is the C++ header, library, sample, and build-tool
set under the installed `$HFS/toolkit`. An HDK plug-in is a platform-native
shared library loaded as a Houdini DSO, commonly from a `dso` directory on
`HOUDINI_PATH`. Its compatibility depends on Houdini's HDK API/ABI, operating
system, architecture, compiler runtime, and dependent libraries. The
`HDK_API_VERSION` macro changes when SideFX declares an ABI break; finer
Houdini build macros remain available when source needs a specific API.

A distributable plug-in root may contain `dso`, `otls`, `pythonX.Ylibs`,
`viewer_states`, `python_panels`, `toolbar`, help, and icon resources. A JSON
package exposes that root through `hpath` and can select by
`houdini_version`, `houdini_os`, `houdini_python`, and
`houdini_platform_build`. The package is configuration; it does not make one
binary portable across incompatible systems.

## Recommended data flow

Use:

`measured native requirement -> installed H21 build and compiler inventory ->
small official sample or isolated proof -> hcustom for a small plug-in or
CMake/HDK makefiles for a multi-file project -> staged platform/version plug-in
root -> JSON package with narrow selectors -> clean-process load -> minimal
operator cook -> package and DSO diagnostics -> release artifact plus build
record`.

Compile outside the Houdini installation, install into a controlled project or
release staging root, and let the package expose that root. Keep one source
tree, but separate binary outputs by operating system, architecture, Houdini
version/API boundary, and compiler platform where compatibility requires it.

## Step-by-step workflow

1. Confirm that HDK is the right layer. Record the missing API or measured
   bottleneck that prevents a node, VEX/OpenCL, HOM, or HDA solution.
2. Record the target Houdini 21 major, minor, build, platform build, operating
   system, architecture, Python variant if packaged modules are included, and
   the compiler range reported by the installed tools.
3. Build an unmodified or minimally adapted sample from that installation's
   `$HFS/toolkit/samples`. Use the Houdini Command Line Tools environment so
   include paths, libraries, and compiler settings come from the target build.
4. Use `hcustom` for a small single-purpose plug-in and inspect its echoed
   compiler/linker settings when diagnosing a mismatch. For a multi-file
   product, use SideFX's HDK makefile fragments or CMake with
   `find_package(Houdini REQUIRED)`, the imported `Houdini` target, and
   `houdini_configure_target`.
5. Direct installation to a staging root instead of relying on `hcustom`'s
   user-directory default. Place the binary under the root's `dso` directory
   and add only required help, icons, HDAs, Python modules, or viewer states.
6. Create a valid package JSON that adds the staging root with `hpath`. Apply
   explicit version, OS, Python, or `houdini_platform_build` conditions when
   several binary variants are distributed. Use `requires` only for a real
   package dependency and `load_package_once` only when duplicate discovery
   must be prevented.
7. If DSOs share a dependent library, use a platform-correct runtime search
   strategy. `preload_libraries` can load declared dependencies before other
   binary plug-ins. Do not expect setting `LD_LIBRARY_PATH` or
   `DYLD_LIBRARY_PATH` from a package to repair a process that has already
   started.
8. Start a clean H21 process with only the intended package location. Confirm
   package resolution, DSO load, operator registration, help/icon discovery,
   and the absence of an unintended user-local copy.
9. Create the smallest valid node or command invocation, cook or execute it,
   and compare a deterministic output with the proof case. Exercise one
   expected error path without crashing Houdini.
10. Diagnose failures with `HOUDINI_PACKAGE_VERBOSE` set before startup,
    `hconfig -xa`, and `HOUDINI_DSO_ERROR`. Record missing symbols,
    dependencies, compiler/runtime mismatches, duplicate operators, and package
    precedence before rebuilding.
11. Deliver the source revision, build recipe, target Houdini/build and
    compiler metadata, package file, binary/resource tree, third-party notices,
    and the exact smoke-test evidence. Never label an untested binary variant
    compatible by inference.

## Critical parameters and attributes

`$HFS/toolkit` must come from the target installation. On Windows,
`hcustom --output_msvcdir` and `--output_compiler_range` reveal the selected
toolchain boundary. `hcustom --cflags` and `--ldflags` help reproduce SideFX
settings in another build system. In CMake, include
`$HFS/toolkit/cmake` in `CMAKE_PREFIX_PATH`, link the imported `Houdini`
target, and use `houdini_configure_target` for expected DSO properties.

Check `HDK_API_VERSION` when maintaining conditional source across ABI
boundaries, but do not treat an unchanged macro as proof that every dependency
is compatible. Package selectors are strings. `hpath` exposes the standard
resource folders. `preload_libraries` addresses shared plug-in dependencies;
package order, `process_order`, `requires`, and `load_package_once` influence
which configuration wins. `HOUDINI_PACKAGE_VERBOSE` must be set before launch,
not inside the package being debugged.

## Cache, version, and performance

Keep debug and optimized outputs separate and never measure a debug DSO as a
release-performance result. Incremental compilation is useful, but a change to
Houdini headers, compiler runtime, compile definitions, linked SDKs, or
generated parameter headers should trigger an appropriate clean rebuild.
Record the exact HDK and build-tool inputs rather than relying on a binary's
filename.

Do not distribute one unqualified `dso` directory containing incompatible
builds and hope loader order selects safely. A package can route each H21
platform/build to its correct root. Keep large third-party SDKs outside the
binary when licensing or system deployment requires it, and document how their
runtime libraries are resolved.

## Validation

Validate in a process that did not compile the plug-in and has no accidental
developer paths. Inspect package diagnostics to prove which JSON file and
`HOUDINI_PATH` elements loaded. Confirm the DSO registers the intended operator
or command exactly once, its help and icon resolve, a minimal input cooks to the
expected output, parameter defaults are correct, and expected invalid input
returns a controlled error rather than a crash.

Repeat the smoke test for every claimed OS, architecture, H21 build/API
boundary, and compiler platform. Test a missing dependent library so the
diagnostic is understandable, then restore it. Disable the package and confirm
the custom operator disappears, proving that another installation is not
masking the test. This card is `documented/static`; no DSO was compiled, loaded,
or live-tested while authoring it, so it is not live-verified.

## Common failures and troubleshooting

- The operator is absent because the package did not expose the intended root,
  the DSO is in the wrong folder, or registration failed during library load.
- An undefined symbol or immediate load failure usually indicates an ABI,
  compiler-runtime, or dependent-library mismatch. Inspect the exact DSO error
  before changing package order.
- The plug-in works only on the build machine because a user-local `dso`,
  environment variable, or undeclared library path masks missing deployment
  content.
- Houdini loads the wrong variant because package conditions are too broad,
  multiple packages add competing roots, or alphabetical/process order differs.
- A package-set Linux or macOS library path has no effect because the dynamic
  loader needed it before Houdini processed packages. Use a valid rpath,
  startup environment, or documented preload design.
- A CMake build finds the wrong Houdini because `CMAKE_PREFIX_PATH` points at a
  different installation's toolkit.
- A later Houdini build crashes with an older DSO because ABI compatibility was
  assumed. Compare `HDK_API_VERSION`, installed headers, and compiler boundary,
  then rebuild and retest.

## When not to use

Do not use HDK for ordinary node creation, parameter automation, procedural
geometry that VEX or SOPs express clearly, or a Python Viewer State. Do not ship
a compiled operator when an HDA is sufficient and easier to inspect and
version. Do not confuse HDK with the binary-compatible Houdini Engine C API;
they solve different integration problems. Avoid installing development builds
into the Houdini installation or user preference tree when a controlled
project package can expose an isolated staging root.

## Houdini 21 notes

Online HDK pages are generated for the current Houdini release and may show a
Houdini 22 compiler or API. For an H21 plug-in, the installed H21
`$HFS/toolkit`, its `HoudiniConfig.cmake`, Platform Build, headers, libraries,
and `hcustom` output are authoritative. SideFX aims for compatibility within a
major line but documents that HDK ABI breaks can occur, including occasionally
within an `X.Y` release. Build and test against every H21 target that the
release claims.

The JSON package mechanism predates H21, but individual package keys and UI
features can evolve. Use the installed H21 package help for final syntax. This
workflow deliberately relies on the core package file and `HOUDINI_PATH`
mechanism rather than treating a current Package Browser archive UI as an H21
requirement.

## Official sources

- SideFX, [HDK getting started](https://www.sidefx.com/docs/hdk/_h_d_k__intro__getting_started.html), accessed 2026-07-27.
- SideFX, [Compiling HDK code](https://www.sidefx.com/docs/hdk/_h_d_k__intro__compiling.html), accessed 2026-07-27.
- SideFX, [HDK build tools](https://www.sidefx.com/docs/hdk/_h_d_k__intro__tools.html), accessed 2026-07-27.
- SideFX, [HDK compatibility](https://www.sidefx.com/docs/hdk/_h_d_k__intro__compatibility.html), accessed 2026-07-27.
- SideFX, [Debugging custom HDK plug-ins](https://www.sidefx.com/docs/hdk/_h_d_k__intro__debugging.html), accessed 2026-07-27.
- SideFX, [Houdini packages](https://www.sidefx.com/docs/houdini/ref/plugins.html), accessed 2026-07-27.
