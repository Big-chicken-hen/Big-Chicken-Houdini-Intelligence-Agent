# HDA authoring, interfaces, and versioning

Pack version: 2.0.0

## Use for

Use a Houdini Digital Asset when a finished node network should become a reusable tool with a stable input/output contract, artist-facing controls, embedded help, and controlled evolution. This card covers creation, versioned type identity, and parameter-interface design. Use the companion library-debug card for installation precedence, embedded definitions, missing dependencies, and definition conflicts.

## Context and core data

An HDA definition is stored in an asset library, normally a `.hda` file, and creates instances of a node type. Its internal name follows `[namespace::]base_name[::version]`. The label is user-facing and may change; the internal name is the identity and cannot simply be renamed in place. Type Properties stores the parameter template, tools, help, scripts, metadata, and sections. Instances may be locked to the current definition or unlocked for editing. Multiple versions and multiple library definitions can coexist. SideFX-documented mechanics and the editorial release policy below are deliberately distinguished: Houdini defines the numeric version component, but it does not impose this card's semantic-versioning policy.

## Recommended data flow

Use:

`clean working subnet -> relative internal references -> explicit inputs and OUT_* -> Create Digital Asset -> namespaced and versioned type -> focused parameter promotion -> organized interface and help -> external library -> locked-instance tests -> versioned release`

Keep source construction understandable inside the asset. Expose controls that describe user intent, not every internal parameter. Treat the asset interface, geometry attributes, inputs, and outputs as a public contract.

## Step-by-step workflow

1. Finish and validate the internal network before assetization, removing abandoned branches and absolute references to external nodes.
2. Add explicit inputs, meaningful input labels, stable attribute expectations, and a clear final output.
3. Create the asset with a namespace, descriptive base name, numeric Houdini version component, and an external `.hda` library path. **Editorial recommendation:** a team may interpret that numeric component with semantic-versioning conventions, but that convention is project policy rather than a SideFX requirement.
4. Promote only high-value controls, then set defaults, ranges, units, menus, tooltips, and disable or hide rules.
5. Organize parameters into task-oriented folders and keep callbacks small, deterministic, and defensive.
6. Add embedded help that documents purpose, inputs, outputs, attributes, defaults, limitations, and version changes.
7. Save the definition, create a fresh locked instance from the Tab menu, and test normal and invalid inputs.
8. **Editorial recommendation:** release compatible additions as a minor version and behavior or interface breaks as a major version, preserving older definitions. Replace this convention with the project's documented migration policy when it differs.

## Critical parameters and attributes

- The internal name should include at least a unique namespace.
- The version component contains numbers and periods only.
- Type Properties changes require Apply or Accept before instances update.
- Promoted parameters create references from internal parameters to the asset interface.
- Menus may use static token/label pairs or scripts, but tokens should remain stable.
- Callback scripts should use `kwargs["node"]` and produce actionable errors.
- `opdef:` can reference sections embedded in an asset.
- `HOUDINI_OPNAMESPACE_HIERARCHY` influences ambiguous type-name resolution.
- Fully qualified type names provide deterministic scripting and migration.

## Cache, version, and performance

SideFX documents external libraries, embedded definitions, versioned names, and current-definition behavior. **Project policy:** prefer production libraries on a controlled Houdini path, usually keep one independently released asset per library, retain the numbered definitions required by completed scenes, and define cache invalidation and migration rules. Avoid callbacks that recook the entire asset while an artist drags a control. A changed output schema, parameter token, attribute contract, or result-changing default should enter the project's explicit compatibility process rather than silently replacing an established definition.

## Validation

Test a newly created locked instance, not only the unlocked development node. Verify default output, input labels, parameter ranges, menus, multiparms, callbacks, help, and error states. Use MMB and Asset Manager to confirm the active definition and library path. Open a scene using the previous version and confirm it remains stable. Test missing nested dependencies and unsupported inputs. For Houdini Engine use, test promoted controls and outputs in the intended host rather than assuming Houdini UI behavior transfers.

## Common failures and troubleshooting

- Only one instance changes: Edit Parameter Interface created spare parameters; edit the asset in Type Properties.
- Edits disappear or stay local: the instance is unlocked but the definition was not updated.
- An old implementation loads: inspect all definitions in Asset Manager and choose the intended current definition.
- A nested node is missing: install every dependent HDA library.
- A scene uses an embedded stale definition: inspect Asset Manager's preference for HIP definitions versus external libraries.
- Old scenes break after a parameter rename: preserve the old interface or release a new asset version with migration.
- Internal channel references fail after assetization: replace absolute paths with relative references.
- Tab menu resolves an unexpected type: use a fully qualified name and inspect namespace hierarchy.

## When not to use

Do not create an HDA solely to group a temporary handful of nodes or to add one spare control to one instance. Do not expose the entire internal parameter tree without user-oriented design. Avoid embedding production definitions in HIP files unless portability for a specific example is the explicit goal. Do not overwrite an existing production version with behavior that changes established output.

## Houdini 21 notes

H21 improves multiparms with dynamic instance labels and drag reordering. Older multiparms that do not use dynamic labels may show labels based on position rather than current values after reordering. Adding and removing instances is performed through the contextual menu on tabs or handles. HDA namespaces and versioned type names remain the primary compatibility mechanism; their core system predates H21.

## Official sources

- SideFX, [Creating and versioning digital assets](https://www.sidefx.com/docs/houdini/assets/create.html), accessed 2026-07-26.
- SideFX, [Digital asset versions and namespaces](https://www.sidefx.com/docs/houdini/assets/namespaces.html), accessed 2026-07-26.
- SideFX, [Edit an asset's user interface](https://www.sidefx.com/docs/houdini/assets/asset_ui.html), accessed 2026-07-26.
- SideFX, [Install and manage digital assets](https://www.sidefx.com/docs/houdini/assets/install.html), accessed 2026-07-26.
- SideFX, [Houdini 21 user interface and scripting changes](https://www.sidefx.com/docs/houdini/news/21/viewport.html), accessed 2026-07-26.
