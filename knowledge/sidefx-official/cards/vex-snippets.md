# VEX snippets and Attribute Wrangle

Pack version: 2.0.0

## Use for

Use VEX when an operation is naturally evaluated per point, primitive, vertex, voxel, or numbered iteration and a long sequence of small SOPs would hide the algorithm. Attribute Wrangle is well suited to attribute transforms, classifications, group creation, compact procedural rules, and controlled geometry creation. This card focuses on run classes, bindings, and geometry writes; use the companion neighbor/volume/debug card for spatial queries and volume-specific diagnosis. Prefer HOM for network authoring rather than per-element geometry processing.

## Context and core data

An Attribute Wrangle runs a CVEX snippet over the class selected by `Run Over`. Bound variables beginning with `@` read or write attributes on the current element. Unknown bindings default to float unless a type prefix is supplied. Important prefixes include `i@` for integer, `v@` for vector, `p@` for vector4/quaternion-like data, `s@` for string, and `d@` for dictionary. VEX exposes three relevant geometry views: functions such as `point()` read the input snapshot, `@attribute` modifies the current element, and `setpointattrib()` or similar functions queue writes to output geometry.

## Recommended data flow

Use:

`typed upstream attributes -> Wrangle with explicit owner and group -> optional reference geometry inputs -> narrow output attributes or groups -> native SOP consumers -> validation`

Create or normalize required attribute types before complex code. Keep one Wrangle responsible for one coherent transformation. If a second operation depends on values produced by the first, use another node so the cook boundary makes the dependency explicit.

## Step-by-step workflow

1. List every attribute, group, parameter, and input that the snippet will read or write, including owner and VEX type.
2. Choose the correct `Run Over` class and restrict the `Group` field when only a subset should be processed.
3. Declare non-float bindings explicitly and enable prototype enforcement for production code.
4. Add artist controls as spare or promoted parameters and read them with `ch`, `chi`, `chv`, or `chramp`.
5. Read other inputs with explicitly typed bindings or geometry functions, avoiding assumptions about matching element numbers.
6. Write the smallest necessary output, using direct bindings for the current element and `set*attrib` only for other elements.
7. Inspect compiler messages, resulting types, representative values, counts, and behavior across changing topology.

Two minimal documented reproductions make the run-class contract concrete; neither was live-run during this corpus rebuild. First, connect a Grid to a Point Wrangle and use `float u = relbbox(0, @P).x; v@Cd = set(u, 0.25, 1.0-u); i@group_right = @P.x > 0;`. The expected output has a point vector3 `Cd`, a point group named `right`, blue-to-red variation across the X bounds, and group membership only where X is positive. Second, use a Detail Wrangle set to run once on an empty input with `int a=addpoint(0,{0,0,0}); int b=addpoint(0,{1,0,0}); int pr=addprim(0,"polyline"); addvertex(0,pr,a); addvertex(0,pr,b);`. The expected MMB counts are two points, one polyline primitive, and two vertices.

## Critical parameters and attributes

- `Run Over` defines the current element and valid implicit indices.
- `@elemnum` and `@numelem` are generic iteration indices; `@ptnum`, `@primnum`, and `@vtxnum` are owner-specific.
- `Attributes to Create` can be narrowed from `*` to an allow-list.
- `Enforce Prototypes` catches accidental undeclared bindings and attribute-name typos.
- `@Frame`, `@Time`, and `@TimeInc` are the correct time bindings inside snippets.
- `@opinputN_name` reads a matching element from another input but requires explicit typing for non-float data.
- `@group_name` reads or writes group membership.
- `addpoint`, `addprim`, and `addvertex` create geometry; a polygon primitive must receive valid vertices.

## Cache, version, and performance

Direct `@attribute` access on the current element is generally cheaper than generic attribute functions. VEX execution is parallel where possible, so avoid serial assumptions and conflicting writes. Cache only after expensive, stable computation rather than around trivial Wrangles. In compiled SOP networks, VEX-based operations are preferred over local-variable expressions. H21 adds useful VEX functions, but reusable code should document its minimum Houdini version when it calls release-specific functions.

## Validation

MMB the Wrangle to read compile and runtime messages. In the Geometry Spreadsheet, confirm owner, type, tuple size, defaults, and value ranges. For the Point Wrangle reproduction, confirm `Cd` is vector3 on points and `right` is a point group, not an integer attribute named `group_right`. For the Detail Wrangle reproduction, require the exact two-point, one-primitive, two-vertex counts and a connected polyline. Test empty geometry, one element, normal production input, and altered topology. Visualize generated groups and vector fields. When creating geometry, check for primitives without vertices. Compare stable-ID-driven results across frames and parameter changes.

## Common failures and troubleshooting

- A typo silently creates a float attribute: enable prototype enforcement and restrict `Attributes to Create`.
- A non-float value reads incorrectly from another input: add the proper type prefix or use a typed geometry function.
- Code cannot read a value written earlier in the same cook: separate the dependency into another Wrangle.
- Multiple source elements overwrite one destination: redesign ownership or use an intentional aggregation mode such as `"add"`.
- Random behavior swims after topology changes: seed with stable `id` instead of `@ptnum`.
- A trigonometric result is wrong: VEX trigonometric functions use radians.
- `rand()` returns unexpected components: cast the seed or return value to the intended scalar/vector type.
- Time appears frozen: use `@Frame` or `@Time`, not `$F` in the snippet.

## When not to use

Do not use VEX to manipulate the Houdini UI, create broad node networks, manage files, or perform HDA definition operations; use HOM. Do not replace a clear, optimized SOP with custom code solely for compactness. Avoid a detail Wrangle that manually loops over millions of elements when a parallel per-element Wrangle expresses the same logic. Do not modify shared output elements from many threads without a deterministic aggregation design.

## Houdini 21 notes

Houdini 21 adds VEX functions including `pointprimuv`, `osd_limit`, `py_dumps`, and `py_loads`. It also adds OpenCL topology bindings and incorporates the OpenCL driver version into cached-kernel hashes. Core Wrangle binding, input/current/output, and parallel execution semantics remain consistent with prior versions. Mark code that depends on new H21 functions so older installations fail clearly.

## Official sources

- SideFX, [Using VEX expressions](https://www.sidefx.com/docs/houdini/vex/snippets), accessed 2026-07-26.
- SideFX, [Attribute Wrangle SOP](https://www.sidefx.com/docs/houdini/nodes/sop/attribwrangle.html), accessed 2026-07-26.
- SideFX, [VEX language reference](https://www.sidefx.com/docs/houdini/vex/lang.html), accessed 2026-07-26.
- SideFX, [What's new: VEX and OpenCL in Houdini 21](https://www.sidefx.com/docs/houdini/news/21/vex.html), accessed 2026-07-26.
- SideFX, [Procedural Thinking](https://www.sidefx.com/tutorials/procedural-thinking/), accessed 2026-07-26.
