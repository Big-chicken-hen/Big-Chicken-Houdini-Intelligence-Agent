# Solaris and USD composition workflow

Pack version: 2.0.0

## Use for

Use this card when converting Houdini geometry into a USD asset, assembling a
shot in Solaris, deciding between references and payloads, designing variants
or instances, or debugging an opinion that does not appear on the composed
stage. It is a workflow for layer ownership and composition, not an exhaustive
USD specification. The central question is which layer should author each
decision without copying or destructively flattening reusable data.

## Context and core data

Solaris LOPs author and compose a USD stage. The core structures are prim paths,
schemas and typed attributes, relationships, metadata, layers and their
strength, references, payloads, variants, instances, collections, primvars,
material bindings, and time samples. A stage is the composed result of opinions
from layers; editing the visible result without identifying the owning layer
can create a stronger override while leaving the source unchanged.

Stable prim paths are API and delivery contracts. A reusable asset normally
needs a predictable root, geometry and material scope, optional variants, and
an external layer or file. A shot or scene assembly composes those assets,
transforms, look overrides, lights, cameras, and render settings without
duplicating their internal geometry. For native USD instancing, distinguish the
instanceable root from its descendant instance proxies. The root may carry a
per-instance transform and constant primvars used for shading; descendant
instance proxies cannot be edited directly while the root remains instanceable.

## Recommended data flow

Use:

`SOP source and semantic attributes -> Component Builder or deliberate SOP
Import -> versioned asset layer -> optional payload/reference and variants ->
shot assembly layer -> look/material overrides -> lighting/camera layer ->
render settings/products -> composed-stage inspection -> USD/export validation`.

References compose reusable content immediately. Payloads add deferred loading
for content that need not be populated in every task. Variants encode supported
alternatives under one asset interface. Instances preserve shared prototypes
for repeated structure. Author per-instance placement and shading controls on
the instanceable root, and author structural or descendant changes in the
source/prototype, a supported variant, or a deliberately de-instanced hero
copy. Use the weakest sufficient opinion and separate asset, shot, look, and
render ownership.

## Step-by-step workflow

1. Define the delivery root prim, path policy, world units, up axis, frame rate,
   asset or shot boundary, version, and which downstream departments must be
   able to override which decisions.
2. Prepare SOP geometry with stable names, normals, topology, UVs or other
   primvars, material subsets, and explicit outputs. Decide which attributes
   should become USD primvars and verify their owner and interpolation.
3. Author the asset through Component Builder or a deliberate SOP Import
   network. Inspect the generated prim hierarchy and schemas instead of relying
   on default paths. Keep geometry and materials under understandable scopes.
4. Write or reference the asset layer. Choose a payload only when deferred
   loading is useful, and verify both loaded and unloaded behavior. Do not use
   payloads as an undocumented workaround for a slow or tangled asset.
5. Create variants only for intentional alternatives with a stable selection
   contract. Preserve instancing for repeated assets. Put unique transforms
   and constant shading primvars on the instanceable root; never target a
   descendant instance proxy for a direct per-instance geometry or property
   edit.
6. Assemble the shot with transforms, collections, material overrides, lights,
   cameras, and render settings in appropriately owned layers. Confirm the
   current edit target before authoring an override.
7. Inspect the composed stage, layer stack, prim stack, variant selections,
   payload state, instance/prototype relationships, material bindings, and
   time samples at representative frames.
8. Save the requested USD and reopen it or reference it into a clean stage.
   Verify prim paths, dependencies, units, variants, payloads, materials, frame
   range, and portability from the written deliverable.

## Critical parameters and attributes

Track default prim, root prim path, kind, purpose, visibility, active state,
instanceable state, payload and reference asset paths, variant set and
selection, layer identifier, sublayer order, edit target, meters-per-unit,
up-axis, FPS/time-codes-per-second, start/end time codes, transform operation
order, and primvar type/interpolation. For an instanceable branch, record which
prim is the root, confirm that per-instance transforms and constant shading
primvars are authored there, and treat descendants shown as instance proxies as
read-only. For SOP import, verify path, name, and material attributes
deliberately. A primvar with the right name but wrong type or interpolation can
produce plausible yet incorrect shading.

## Cache, version, and performance

Version assets and USD layers explicitly while keeping logical prim paths stable
when compatibility is required. Use relative or resolver-managed asset paths
appropriate to the project. Payload large reusable content that is not always
needed, and preserve instancing for repeated geometry. Avoid thousands of tiny
authoring layers or broad stage rewrites for local changes. Cache expensive SOP
generation before repeated Solaris work, but do not flatten the USD layer stack
merely to conceal ownership problems. Test package portability with dependencies
available in the same conditions as delivery.

## Validation

Use the Scene Graph Tree and Details/Layer tools to inspect the composed prim
and its authored opinions. Check prim path, type, kind, purpose, visibility,
active and loaded state, variant selection, instance status, material binding,
primvars, transform, and time samples. Reload saved USD in a clean context.
Test payload-loaded and unloaded states, each supported variant, one repeated
instance, and representative animated frames. On that instance, confirm the
root transform and root constant primvar vary as intended, the shader consumes
the primvar, and no authored opinion targets a descendant instance proxy. A
valid file that resolves only because of an unreported absolute local path is
not a portable deliverable. These checks are derived from SideFX documentation;
this pack did not live-test them in Houdini 21 or Karma.

## Common failures and troubleshooting

- An edit that “does nothing” may target the wrong prim path, a weaker layer,
  an inactive prim, a muted layer, or an unloaded payload.
- An edit that cannot be found in the source may be a stronger session-layer or
  downstream opinion. Inspect the prim and layer stacks before deleting data.
- A descendant edit fails or appears to do nothing when the target is an
  instance proxy. Move a shared change to the source/prototype, expose it as a
  variant, or deliberately de-instance only the required hero branch.
- A unique root transform or constant shading primvar is legal and does not by
  itself require breaking native instancing. If the look does not vary, inspect
  primvar name, type, interpolation, inheritance, and shader consumption.
- Missing materials often result from changed prim paths, collection membership,
  subsets, or binding relationships rather than shader construction.
- A payload that never appears may have an invalid asset/default prim path or
  simply be unloaded.
- Scale and animation mismatches require checking meters per unit, up axis,
  FPS, time-codes-per-second, and transform order.

## When not to use

Do not introduce USD composition for a simple SOP-only operation whose delivery
does not require Solaris or USD. Do not use variants for arbitrary history or
payloads for small always-needed data. Do not flatten a stage by default when
the user needs editable composition and provenance.

## Houdini 21 notes

This card targets Houdini 21. Solaris and USD concepts are stable, but LOP node
versions and SideFX’s current online documentation can advance beyond H21.
Confirm H21 node types and parameters in installed help. Prefer current
Component Builder and Solaris workflows for new assets while preserving an
existing compatible layer structure when modification is the task. The
instanceable-root and instance-proxy rules above are documented by SideFX but
were not reproduced in a live H21 stage during this corpus rebuild.

## Official sources

- [USD in Solaris](https://www.sidefx.com/docs/houdini/solaris/usd.html) — SideFX composition concepts and direct instanceable-root/instance-proxy rules; accessed 2026-07-26.
- [Configure Primitives LOP](https://www.sidefx.com/docs/houdini/nodes/lop/configureprimitive.html) — SideFX confirmation that an instanceable root can carry attributes while its descendants cannot be edited; accessed 2026-07-26.
- [Solaris](https://www.sidefx.com/docs/houdini/solaris/index.html) — SideFX scene-building documentation; accessed 2026-07-26.
- [Solaris Essentials](https://www.sidefx.com/tutorials/solaris-essentials/) — SideFX learning series; accessed 2026-07-26.
- [Houdini 21 Solaris changes](https://www.sidefx.com/docs/houdini/news/21/solaris.html) — SideFX version notes; accessed 2026-07-26.
