# HDA library installation and debugging

Pack version: 2.0.0

## Use for

Use this workflow when an HDA exists but the wrong definition loads, an asset is missing, a locked instance differs from its library, nested assets fail, or embedded and external definitions conflict. It focuses on library discovery, current-definition selection, dependency inspection, safe repair, and reproducible diagnosis. Use the authoring/versioning card for creating the asset and designing its parameter interface.

## Context and core data

An `.hda` library can contain one or more definitions. Houdini discovers libraries in `hda` and `otls` directories along `HOUDINI_PATH`, and can also install a library manually for the current session. A HIP file may embed definitions. Several definitions with the same internal type name may be available, but one becomes current. Versioned type names can coexist; multiple definitions of the exact same type name compete by library precedence and Asset Manager choices. A top-level HDA may contain instances of other HDAs whose libraries must also be installed.

## Recommended data flow

Use:

`failing instance -> record fully qualified type and node path -> MMB current library -> Asset Manager inventory -> inspect embedded/external precedence -> verify nested dependencies -> select or install intended definition -> match or migrate instance -> reopen clean session -> confirm`

Do not edit the contents first. Establish which type and definition Houdini is actually using, then decide whether the problem is installation, precedence, version compatibility, or an unlocked local modification.

## Step-by-step workflow

1. Record the failing node's fully qualified type name, current node path, error text, and scene location.
2. MMB the node or inspect its type definition to identify the current library and whether the instance matches it.
3. Open Asset Manager, jump to the current definition, and inventory every installed definition with the same base or exact type name.
4. Check whether the HIP contains embedded definitions and whether preferences favor HIP or external libraries.
5. Inspect nested HDA instances and install every missing dependency library before changing the top-level asset.
6. Select the intended current definition. **Project policy:** if existing instances must change versions, use the project's controlled migration and rollback procedure rather than overwriting an unrelated library.
7. Match a disposable test instance to the current definition and compare it with the failing instance before repairing user work.
8. Reopen a clean Houdini session with the intended path configuration and confirm that new and existing instances resolve identically.

## Critical parameters and attributes

- `hou.NodeType.nameWithCategory()` or the displayed fully qualified type distinguishes similarly labeled assets.
- `hou.NodeType.definition()` identifies the active definition.
- `hou.HDADefinition.libraryFilePath()` identifies the source library.
- `matchesCurrentDefinition()` distinguishes locked-current state from local divergence.
- `matchCurrentDefinition()` discards unlocked local contents and must be used only on an approved target.
- `HOUDINI_PATH` controls automatic library scanning.
- `HOUDINI_OPNAMESPACE_HIERARCHY` influences ambiguous type resolution.
- Asset Manager's “Use this definition” selects among exact-name definitions for the session.
- Preferences can favor definitions embedded in the HIP file.

## Cache, version, and performance

SideFX documents library discovery, manual installation, embedded definitions, and current-definition selection. **Project policy:** decide whether a library contains one independently released asset or a deliberately versioned bundle, install project libraries from a controlled path, record dependency versions, and test precedence changes before rollout. Avoid broad search paths containing obsolete duplicate definitions. External libraries are usually easier for a project to diff and deploy than unreported HIP-embedded copies, but that delivery choice is not a SideFX-mandated packaging rule.

## Validation

Create a fresh node from the Tab menu and compare its fully qualified type, library path, version, defaults, and locked state with the existing instance. Reopen the scene in a clean process, because session-only manual installation can conceal deployment gaps. Test with the project path alone and with intended site dependencies. Inspect every nested asset. Confirm that a previous-version scene continues to use its numbered definition and that new nodes use the expected current version.

## Common failures and troubleshooting

- Asset is available only until restart: its library was manually installed outside the Houdini path.
- Wrong implementation loads: two libraries define the same exact type; inspect Asset Manager precedence.
- New nodes use a different version: ambiguous references resolve to the highest version or namespace preference.
- Top-level HDA errors with unknown nodes: nested dependency libraries are missing.
- Local edits vanish after matching: the instance was unlocked; preserve evidence before any destructive match operation.
- External updates appear ignored: a HIP-embedded definition is preferred.
- Deleting the last embedded instance loses the definition: production assets should live in external libraries.
- A script creates the wrong node: pass a fully qualified type and request exact type resolution.

## When not to use

Do not diagnose a library conflict by immediately unlocking and editing the asset. Do not use Match Current Definition on user-modified instances without explicit approval. Avoid embedding production definitions solely to make installation problems disappear. Do not collapse distinct major versions into one exact internal name. Do not rely on a session-only “Use this definition” selection as a deployment solution.

## Houdini 21 notes

The HDA library, namespace, and exact-definition precedence mechanisms remain applicable in H21. H21's Engine plug-ins add or update host integrations, but host availability still depends on the correct HDA and nested libraries being deployed. Record the H21 production build alongside library versions when troubleshooting cross-machine differences.

## Official sources

- SideFX, [Install and manage digital assets](https://www.sidefx.com/docs/houdini/assets/install.html), accessed 2026-07-26.
- SideFX, [Digital asset versions and namespaces](https://www.sidefx.com/docs/houdini/assets/namespaces.html), accessed 2026-07-26.
- SideFX, [hou.HDADefinition](https://www.sidefx.com/docs/houdini/hom/hou/HDADefinition.html), accessed 2026-07-26.
- SideFX, [Creating and versioning digital assets](https://www.sidefx.com/docs/houdini/assets/create.html), accessed 2026-07-26.
- SideFX, [What's new: Houdini Engine, APIs, and plug-ins in H21](https://www.sidefx.com/docs/houdini/news/21/engine.html), accessed 2026-07-26.
