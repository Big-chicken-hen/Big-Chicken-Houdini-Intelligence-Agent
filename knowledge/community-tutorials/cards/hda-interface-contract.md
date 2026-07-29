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

## Data flow, cache, version, and performance

Topology-affecting parameters invalidate downstream caches and should be labeled. Heavy internal stages need an intentional cache/bypass quality strategy, but an HDA must not write implicit uncontrolled files. Relative references survive asset relocation; absolute paths often do not. Versioned definitions enable upgrade tests, while editing a shared definition in place can break existing scenes.

## Common failures and repairs

- **Fresh instance differs:** hidden scene dependency, absolute path, unsaved definition, or non-default internal parameter.
- **Promoted parameter has no effect:** reference points to wrong node/tuple or internal expression overrides it.
- **Definition conflict:** same type/version installed from multiple libraries; inspect definition path and namespace.
- **Asset recooks constantly:** callback or time-dependent expression dirties broad stages; isolate time dependence and profile.
- **Upgrade loses values:** parameter names/types changed without migration; preserve stable names or provide explicit conversion.

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
