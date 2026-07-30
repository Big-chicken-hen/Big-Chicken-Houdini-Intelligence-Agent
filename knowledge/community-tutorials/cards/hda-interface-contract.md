# HDA interface, versioning, and dependency checks

Source kind: `community_tutorial`
Verification: `community_unverified`
Version: sources include H17 and evolving HDA notes; verify current definition and parameter-template APIs.

## Use, prerequisites, and target

Use when turning a proven SOP/LOP/OBJ network into a reusable Houdini Digital Asset or repairing an existing asset interface. Begin with a clean network that has explicit inputs, outputs, and no exploratory branches. The target is a versioned HDA whose parameter interface, internal references, dependencies, outputs, and upgrade behavior can be tested without opening its internals.

## Semantic network stages

External inputs → named `IN_*` nodes → validation/default stage → core procedural stages → optional debug branch → named `OUT_*` nodes → HDA interface/definition → clean-instance test.

## Ordered workflow

1. Stabilize the internal network before creating the asset. Name responsibilities, expose explicit `IN_*`/`OUT_*`, remove unused branches, and record required input count/type plus output contract.
2. Create the digital asset definition with a portable namespace/name and semantic version policy. Choose the library location under the project/package rules. Do not overwrite an unrelated installed definition.
3. Design parameter folders by user task, not internal node order. Promote only controls that form a supported interface. For each parameter record name, label, type, default, range/menu, units, expression language, and whether changing it affects topology or cache identity.
4. Wire promoted parameters with channel references or parameter expressions that are relative to the asset, not fragile absolute node paths. Use multiparms/ramps only when their data model is required. Keep internal implementation parameters hidden rather than accidentally public.
5. Add input validation and safe defaults. Check missing geometry, attribute schema, prim paths, scale, and optional inputs close to `IN_*`. Produce useful warnings/errors rather than obscure downstream failures.
6. Configure output labels, cook controls, asset icon/help, and any scripts/callbacks. Keep callbacks small, deterministic, and UI-safe; core geometry logic belongs in the node network. Python modules need explicit APIs and no hidden network/service behavior.
7. Save the definition, instantiate a fresh node in a clean test network, and exercise defaults, min/max, menus, input absence, presets, copy/paste, and reload. Locking must not hide missing external dependencies.
8. For a new version, duplicate/update the definition intentionally, document parameter migration, and test an old instance. End with a published `OUT_HDA` behavior and a developer debug output only if it has real diagnostic value.

## Key responsibilities and fields

The definition owns type name/version and sections; parameter templates own the public interface; channel references map public controls to internal responsibilities; `IN_*` validation owns preconditions; `OUT_*` owns result contract. Node paths, file paths, operator type names, spare parameters, and multiparm instance names are dependency fields that must remain portable.

## Executable parameter and connection contract

1. Convert only a validated subnet with named `IN_*`/`OUT_*` boundaries. In **Create Digital Asset**, use a namespaced internal type such as `org::tool_name::1.0`, a distinct label, correct minimum/maximum inputs, and a project-contained **Save To** library path. Do not reuse an installed unrelated type name.
2. In **Operator Type Properties ▸ Parameters**, define stable internal parameter names. For an example quality control use integer `quality`, default `1`, hard range `0..2`, and menu tokens `preview`, `work`, `final`; internal networks consume tokens/values rather than UI labels. Mark topology-affecting controls in help and the cache identity.
3. Promote numeric controls with explicit type, tuple size, default, unit, soft/hard range, and disable/hide conditions. Use relative channel references such as `ch("../quality")` from internal nodes. Search the asset for absolute `/obj/...`, `/stage/...`, and local disk references before saving.
4. Put input validation immediately after each `IN_*`: required geometry type, non-empty group, attribute class/type, unit/scale, and USD prim-path syntax. An optional input receives a documented internal default; a required missing input raises one actionable error.
5. Keep callbacks limited to interface synchronization. Geometry, USD authoring, and file production remain in nodes. A callback must be deterministic, bounded, and safe when the node is copied, deleted, or running without a visible desktop.
6. Save the definition, create a new instance, and test default, hard minimum/maximum, every menu token, missing/invalid input, copy/paste, asset reload, and a renamed parent network. Compare output schema/counts/bounds to the declared contract.
7. For an upgrade use **Increase Minor Version** for compatible additions or **Increase Major Version** for a breaking contract. Keep the previous definition installed for migration tests; map renamed/removed parameters explicitly and compare one old scene instance.

## Data flow, cache, version, and performance

Topology-affecting parameters invalidate downstream caches and should be labeled. Heavy internal stages need an intentional cache/bypass quality strategy, but an HDA must not write implicit uncontrolled files. Relative references survive asset relocation; absolute paths often do not. Versioned definitions enable upgrade tests, while editing a shared definition in place can break existing scenes.

## Common failures and repairs

- **Fresh instance differs:** hidden scene dependency, absolute path, unsaved definition, or non-default internal parameter.
- **Promoted parameter has no effect:** reference points to wrong node/tuple or internal expression overrides it.
- **Definition conflict:** same type/version installed from multiple libraries; inspect definition path and namespace.
- **Asset recooks constantly:** callback or time-dependent expression dirties broad stages; isolate time dependence and profile.
- **Upgrade loses values:** parameter names/types changed without migration; preserve stable names or provide explicit conversion.

## Checkpoints and observable evidence

- **H0 — definition:** active type name, namespace/version, library path, input limits, and output labels match the intended definition exactly.
- **H1 — interface:** a machine-readable inventory of parameter name/type/default/range/menu token shows no duplicate names or label-as-token dependencies.
- **H2 — references:** all promoted parameters affect one documented internal responsibility; absolute scene/file dependency scan is empty except explicit supported inputs.
- **H3 — clean instance:** a newly created default instance cooks from declared inputs, invalid input emits the intended message, and copy/paste/reload preserves values.
- **H4 — upgrade:** old and new definitions coexist, supported parameters migrate, and output schema remains compatible or the breaking delta is explicitly versioned.

## When not to use this workflow

Do not make an HDA from an exploratory graph whose inputs, outputs, and supported controls are still changing. Avoid an asset for a one-scene one-off subnet with no reuse boundary, and do not hide uncontrolled filesystem writes, absolute scene dependencies, or core geometry generation inside callbacks.

## Provenance boundary

Matt Estela's HDA notes and Delano Athias's rigging HDA seminar motivate practical packaging. The interface contract, clean-instance gate, and migration checks are original synthesis. Current HOM/template APIs are unverified.

## Semantic expectations and verification checklist

- Presence: declared inputs/outputs and every public parameter exist with intended type/default.
- Mapping: each public parameter drives its documented internal responsibility; relative references resolve after relocation.
- Range/interface: menus, min/max, tuple sizes, multiparms, and disabled/hidden states behave at boundaries.
- Cook evidence: a fresh default instance cooks with valid inputs and emits actionable validation for invalid ones.
- Definition evidence: saved library path, type name, version, and active definition match the intended package.
- Regression evidence: reload/copy/paste and one old-version instance preserve supported values and output contract.

## Sources

- Matt Estela, [HDA's](https://www.tokeru.com/cgwiki/HoudiniHDA.html).
- Delano Athias, [SpaceGirl Rigging | Seminar 1 | HDA](https://www.sidefx.com/tutorials/spacegirl-rigging-part-1/).
