# Rest groom, guide transfer, and hair generation workflow

Canonical ID: `groom-guides-uv-transfer`

Houdini version: 21 (documented/static; not live-verified)

Pack version: 2.0.0

## Use for

Use this workflow to author a rest-pose groom from a skin surface, organize and
style sparse guide curves, transfer those guides to another skin through
matching topology or UV correspondence, and generate dense render curves with
traceable skin attributes. It covers the geometry handoff before optional
Vellum guide simulation and before material or lighting work. It is not a
replacement for the Vellum hair-constraint workflow, and it does not assume
the new Houdini 22 render-time guide-deformation stack.

## Context and core data

The stable source of truth is a static rest skin plus rooted guide curves.
Houdini expects the first vertex of every guide primitive to be the root.
Groups and masks identify regions for length, direction, clumping, density, or
other operations. The skin's `uv` attribute may be point- or vertex-owned, but
the owner and attribute name must be stated consistently wherever it is used.
Guide Transfer can use direct topology correspondence or UV correspondence and
can emit the primitive `guideorigin` attribute. Hair Generate interpolates
dense curves from the skin and guides and can transfer selected skin attributes
to generated hair primitives at each root.

## Recommended data flow

Use:

`validated static rest skin and UVs -> Guide Groom -> ordered Guide Process
operations -> Guide Transfer when a second skin is required -> Hair Generate
at rest -> optional H21-supported deform/simulate stage -> curve cache ->
Solaris/render handoff`

Keep sparse authored guides, dense generated curves, rest skin, animated skin,
and optional simulated guides as separate named streams. Groom on the static
mesh. Do not feed an animated skin into interactive rest-groom operations and
then try to recover a stable rest relationship afterward.

## Step-by-step workflow

1. Freeze and name the rest skin, confirm physical scale and outward normals,
   and inspect the intended `uv` set, owner class, seams, UDIMs, and overlaps.
2. Create sparse guides with Guide Groom. Confirm that each curve runs from
   root to tip, use only enough segments for the required silhouette, and name
   meaningful guide groups instead of relying on primitive numbers.
3. Layer guide operations by purpose: establish direction and length first,
   then separation or parting, clump structure, and finally smaller variation.
   Restrict each operation with a deliberate group or skin mask.
4. When moving a groom between skins, use Guide Transfer in Direct mode only
   for matching topology. Use the UV method when topology or object placement
   differs, and set both UV Attribute and Attribute Class to the real source
   contract. Enable `guideorigin` when downstream root auditing needs it.
5. Compare the transferred roots and guide shapes before generating dense
   hair. Adjust transfer deformation radius only when root-only transformation
   does not preserve the required guide shape.
6. Generate render curves with Hair Generate from the validated rest skin and
   guides. Set density, guide influence, segment count, thickness, and pruning
   from the shot requirement rather than from viewport convenience.
7. Add only the required skin and guide attributes to the transfer lists.
   Preserve `uv`, material or region identifiers, and render attributes through
   the cache only when a downstream consumer actually uses them.
8. Introduce skin animation or Vellum guide dynamics only after the rest output
   is repeatable. Cache the rest groom or capture data separately from animated
   results so a topology or UV change is detectable.

## Critical parameters and attributes

Guide Groom segment count controls editing and later solve cost; excess points
do not create better form by themselves. Guide groups and painted masks should
have stable semantic names. Guide Transfer `Match Method`, `UV Attribute`, and
`Attribute Class` form one correspondence contract. `guideorigin` records a
curve root position for downstream inspection. The transfer deformation blend
and radius determine whether a guide moves mostly from its root or follows a
broader point deformation.

Hair Generate density and seed determine root distribution. Influence Radius,
Maximum Guide Count, guide weighting, and skin-space blending determine how
sparse guides shape dense hair. Source Attribute Transfer distinguishes point,
vertex, primitive, and detail ownership; do not list `uv` under the wrong
owner. Segment count, thickness, pruning, and bounding limits affect both
memory and render cost. A rest attribute or separate rest inputs are required
when the chosen H21 deformation path depends on stable rest correspondence.

## Cache, version, and performance

Cache the static rest groom and any guide-capture data independently from
animated or simulated curves. Dense hair can dominate memory, so tune and
validate guides at low display density, then increase generation density only
for a representative render test. Avoid repeatedly rebuilding a skin VDB or
guide weights on every frame when the rest inputs have not changed. Record the
skin topology and UV version beside the groom cache; either can invalidate root
correspondence without producing an obvious node error.

Current SideFX pages may display Houdini 22 parameters. Houdini 22 introduced
the new Guide Reduce, redesigned Guide Deform SOP, and Configure Guide Deform
LOP workflow. Do not silently use those nodes as the Houdini 21 recipe. Resolve
the exact H21 node type and parameter surface from installed H21 help before
authoring a live graph.

## Validation

At the rest frame, display roots and verify that the first vertex of every
guide lies on the intended skin region. Inspect reversed, zero-length,
duplicated, penetrating, or isolated guides and compare segment counts by
group. For UV transfer, apply a directional checker to both skins, inspect UV
owner and value ranges, and compare source and target root locations around
seams and UDIM boundaries. Confirm that `guideorigin` and any selected
attributes exist on the expected class.

Generate a bounded sample of dense hair and verify root count, guide influence,
silhouette, clump continuity, thickness, and attribute values before scaling
up. Reload the rest cache and confirm identical roots and guide shapes. On an
animated test, inspect the first moving frame and a high-deformation frame for
sliding roots, flipped curves, and changing density. These checks are
documentation-backed; this card does not claim a live Houdini 21 groom or
render verification.

## Common failures and troubleshooting

A groom that slides usually has an animated input in the rest branch, missing
rest correspondence, or an invalid topology handoff. Hair growing inward often
has reversed guides, incorrect skin normals, or roots outside the intended
surface. UV transfer that jumps across the model usually means the UV name or
owner class differs, islands overlap ambiguously, or the source and target do
not share the promised UV parameterization. Direct transfer on changed topology
is not a tolerable approximation; switch to the UV method or rebuild the
correspondence.

Unexpected bald patches can come from guide influence limits, group masks,
minimum length, or density attributes rather than from insufficient global
density. Dense recooks commonly indicate that rest generation, skin VDB
construction, or capture is being recomputed per frame. If an H21 node lacks a
parameter shown online, treat that as a version mismatch instead of substituting
a similarly named H22 node.

## When not to use

Do not load this workflow for a single curve edit, a material-only hair request,
or a Vellum constraint problem on already valid guides. Use the existing UV
workflow for ordinary polygon unwrap and layout that has no groom transfer.
Use Vellum hair guidance for guide dynamics, pinning, bend, twist, and collision
behavior. Do not use UV transfer when the two skins lack a meaningful common UV
space; create an explicit correspondence or rebuild the groom instead.

## Houdini 21 notes

Guide Groom, Guide Transfer, and Hair Generate predate Houdini 21, and the H21
character-effects notes add grooming utilities such as Guide Clump Center,
Guide Find Strays, Guide Volume, Guide Surface, and Guide Fill. Those H21
additions may support diagnosis or guide coverage, but none is mandatory for
every groom. The core card deliberately stops at the stable rest-groom,
transfer, and generation contract.

Online node pages are currently served with Houdini 22 context. Their
long-standing inputs and `Since` metadata support this documented/static
summary, while exact H21 menus, node versions, and animation handoff remain to
be confirmed in installed H21 help. No H21 GUI, groom state, simulation, or
Karma render was run for this card.

## Official sources

- SideFX, [Creating and styling guide hair with Guide Groom](https://www.sidefx.com/docs/houdini/fur/groom.html), accessed 2026-07-27.
- SideFX, [Guide Transfer SOP](https://www.sidefx.com/docs/houdini/nodes/sop/guidetransfer.html), accessed 2026-07-27.
- SideFX, [Hair Generate SOP](https://www.sidefx.com/docs/houdini/nodes/sop/hairgen.html), accessed 2026-07-27.
- SideFX, [What's new: Muscles and character effects in Houdini 21](https://www.sidefx.com/docs/houdini/news/21/muscles.html), accessed 2026-07-27.
- SideFX, [What's new: Hair, fur, and feathers in Houdini 22](https://www.sidefx.com/docs/houdini/news/22/feathers.html), accessed 2026-07-27.
