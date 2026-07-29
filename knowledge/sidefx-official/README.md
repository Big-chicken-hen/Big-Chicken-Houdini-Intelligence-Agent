# SideFX official workflow knowledge pack

Pack version: 2.0.0

This distributable corpus contains original Houdini 21 workflow summaries
written by Big-Chicken contributors after consulting the SideFX primary sources
registered in `sources.json`. The cards explain how to complete real tasks; they
are not a node dictionary or a replacement for the installed Houdini help.
`manifest.json` inventories the cards, while `coverage.json` records the covered
domains, evidence sources, and remaining gaps.

## Copyright boundary

The repository distributes only original summaries under Apache-2.0. It does
not redistribute SideFX documentation pages, tutorial or masterclass
transcripts, images, videos, example projects, or installed help archives.
Linked upstream material remains subject to SideFX's terms. Keep quotations
short and necessary, and express workflows in original language rather than
reconstructing a source page.

## Required card structure

Every workflow card must contain these exact second-level sections:

1. `Use for`
2. `Context and core data`
3. `Recommended data flow`
4. `Step-by-step workflow`
5. `Critical parameters and attributes`
6. `Cache, version, and performance`
7. `Validation`
8. `Common failures and troubleshooting`
9. `When not to use`
10. `Houdini 21 notes`
11. `Official sources`

Each card must provide a substantive ordered workflow, task-relevant validation
and troubleshooting, version and performance cautions, and at least two
directly relevant SideFX documentation, tutorial, learning-path, or masterclass
links with their access date. Definitions alone are not a workflow.

## Automated quality gate

`tests/unit/test_sidefx_official_knowledge.py` rejects a card shorter than
2,500 characters or 450 words, a card with fewer than five numbered workflow
steps, missing required sections, missing access dates, fewer than two SideFX
links, unregistered sources, near-duplicate prose, forbidden fixed examples,
replacement-character corruption, or inconsistent pack metadata. It also
requires the manifest to match every card on disk and the coverage matrix to
resolve at least 36 independently named workflows to real cards and sources.
These are minimum rejection thresholds, not targets to pad with generic prose.

## Version and verification boundary

Houdini nodes, parameters, supported renderer features, and recommended
workflows can change between builds. Confirm version-sensitive details against
the active installation's local help before implementation. A card is planning
evidence, not proof that a procedure works in the current scene; completion
claims still require appropriate validation in the real Houdini build.

The pack uses three evidence labels:

- `documented/static` means the summary, links, structure, and cross-references
  passed repository checks against the cited SideFX material.
- `change-note-supported` means a Houdini 21 change page directly supports the
  stated release difference; it still is not a runtime result.
- `live-verified` means the procedure was actually exercised in the named
  Houdini build and its expected output was observed. No card in this rebuild
  is marked live-verified.

The `Validation` sections are test recipes and expected evidence, not records
that those tests already passed. Likewise, `covered` in `coverage.json` means
the documentation workflow is substantively represented; it never means a
renderer, solver, GUI, or scene was live-verified.

Installed Houdini help archives may be indexed from the user's own installation
at runtime. They are never copied into this repository or a release archive.

## Retrieval scope

For a complex, unfamiliar, reference-driven, or version-sensitive task, retrieve
the relevant workflow cards before planning and continue to current SideFX or
original-source research when the local evidence is incomplete. Retrieve only
the domains needed for the task. A simple known action does not need to load or
search the entire corpus, and the presence of this pack does not justify
unrelated research.
