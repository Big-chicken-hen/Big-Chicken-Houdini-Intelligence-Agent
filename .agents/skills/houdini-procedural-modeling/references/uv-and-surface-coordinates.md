# UV and Surface Coordinates

Use this reference only when the delivery requires texture-space information. Treat coordinates as a modeling dependency and handoff contract, not as the material's identity; substrate, layers, optical response, textures, and Karma behavior remain LookDev responsibilities.

## Decide whether coordinates are required

Provide validated UVs or an explicit alternative coordinate system for a complete asset that needs image textures, decals, directional patterns, texture-space masks, baking, or export to another DCC, engine, or downstream package.

Conventional UVs may be omitted for an explicit blockout or proxy, a volume, a point or curve representation where they have no meaning, or an intentionally world-, object-, rest-, or procedural-space material whose delivery supports that choice. State the omission and the replacement coordinate contract.

Do not add this workflow to a simple primitive, one parameter edit, ordinary API failure, or material-only request unless the user explicitly asks to repair coordinates.

## Build coordinates with the component

- Generate coordinates while component topology, local axes, and construction semantics are known. Do not run one blind automatic flatten over the final combined mesh.
- Preserve valid coordinate sets outside an affected subsystem and rebuild only branches whose source topology changed.
- Generate source-level UVs before packing, copying, or instancing. Choose shared, offset, mirrored, or unique regions intentionally from downstream needs.
- Use stable component orientation for manufactured surfaces and directional patterns. Place seams at meaningful boundaries and keep relative texel scale deliberate.
- For sweeps, derive longitudinal coordinates from path length and transverse coordinates from the profile. Preserve path direction, frame, and seam continuity through bends.
- Use rest, object-space, path, or USD primvar coordinates when they better satisfy a compatible procedural delivery; name and document each role.

Do not replace existing authored UVs with triplanar or world-space mapping merely because it is easier. Avoid live world-space coordinates when asset motion would make the pattern swim.

## Keep UVs dependency-aware

After representative changes to dimensions, openings, counts, path length, profile scale, or repetition, verify that new faces receive coordinates, seams remain on intended boundaries, orientation remains meaningful, scale stays reasonable, and unrelated islands are not regenerated.

Do not bind UV or material regions to temporary primitive numbers or selection state. Preserve stable component and material semantics through named groups, attributes, USD paths, or equivalent existing identifiers.

## Validate before handoff

Use a checker or directional diagnostic pattern that exposes missing coordinates, invalid values, distortion, scale inconsistency, orientation errors, unintended overlap, seam problems, insufficient padding, mirrored text, path flips, and instance inconsistency. Do not judge UV quality only through a final weathered or low-contrast material.

Report the primary UV set, alternative coordinate sets and their purposes, relative texel policy, intentional overlap or mirroring, directional requirements, instance behavior, semantic groups or paths, and known limitations. Hand these stable inputs to `$houdini-material-lookdev`; do not duplicate its MaterialX or Karma workflow here.
