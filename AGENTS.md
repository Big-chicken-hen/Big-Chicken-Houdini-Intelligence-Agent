# Repository agent rules

These rules have the highest priority for every development round in this repository.

## Safety boundary

1. The only project root is `E:\houdini-intelligence-agent`.
2. Never create project files, virtual environments, caches, renders, HDAs, logs, checkpoints, jobs, quarantine data, or temporary files on `C:`.
3. Never modify `C:\Users`, AppData, a user home directory, `CODEX_HOME`, Houdini user configuration, or any drive root.
4. Before any future delete or move, perform a read-only inventory, canonicalize every exact target, prove it is strictly inside the project root, reject UNC/device/ADS/path-escape/reparse-point targets, show the exact scope, and obtain separate explicit user approval.
5. Never recursively delete a computed, expanded, uncertain, or unverified path. Do not provide a general recursive-delete product tool.
6. On Windows, keep filesystem operations in one native PowerShell/Windows call chain, use literal paths and argument arrays, and never pass PowerShell-enumerated paths to another shell.
7. Do not use `git reset --hard`, `git checkout --`, force-overwrite, or silently discard user work.
8. Read existing files before modifying them. Preserve unrelated user changes.
9. Do not install dependencies or create a virtual environment unless the user explicitly authorizes the implementation stage. Keep any later approved runtime data under the project `.runtime` directory.
10. Bind every future local control service to `127.0.0.1` and authenticate it. Never expose the Houdini gateway or Codex app-server directly to a LAN or the internet.
11. Never fabricate test results, Houdini connections, node operations, screenshots, renders, schema discovery, or HIP/HDA understanding.
12. Run all tests that can run, report actual results, and label tests that cannot run as unverified with the reason.

## Product boundary

- Codex is the only intelligent agent. Do not build a second Agent, LLM, planner, RAG system, vector database, semantic-memory service, or external vision model.
- Houdini supplies UI, deterministic tools, scene execution, and artifacts. Do not control Houdini through screen takeover or Computer Use.
- Codex-native subagents may be introduced only in the authorized phase. The live HIP remains single-writer, and subagents return proposals by default.
- SQLite may eventually store technical state and audit metadata, but not a duplicate chat history, embeddings, semantic summaries, or a custom memory system.
- Knowledge retrieval and preference storage do not train or alter model weights; describe them accurately.

## Development boundary

- Follow the active phase only. Never begin a later phase implicitly.
- Keep protocol bridge, MCP transport, Houdini gateway, context adapters, tools, jobs, validation, approvals, audit, and UI in separate modules.
- Return structured, JSON-serializable user-facing errors; tracebacks may be diagnostic logs but never the only user-facing response.
- Never expose arbitrary shell execution, `exec`, or `eval` as an unrestricted product tool.
- Never modify a Houdini scene without the approval required for that exact operation and risk level.
- Discover the active Houdini installation and live schema at runtime; do not hard-code a Houdini version or install path. The live schema is authoritative when it conflicts with static material.
- Do not permanently limit the Houdini graph engine to the first certified catalog. Admit later node types or Houdini contexts only through separately reviewed catalog/protocol versions and context-specific risk policy; do not add per-node handlers or a general DCC abstraction.
- Never automatically save or overwrite HIP files, modify HDA definitions, delete nodes, or scan arbitrary filesystem locations.

## Current phase: P2-V Gate B4B one-shot local Houdini acceptance

P1-V and Gates B1 through B4A are complete. Gate B4A was closed at commit `0b3b5a51c0ad95aee382794d74852d2d541bf880`. The five-tool general graph contract in `schemas/houdini-mcp/0.1.0`, the two-tool read-only contract in `schemas/houdini-mcp/0.2.0`, and every existing fixture are frozen and must not be edited in place.

Gate B4B authorizes only a dormant local acceptance entry that injects the active Houdini `hou` module into the existing catalog-driven `HoudiniWriteAdapter`. It may be opened manually as a dedicated Python Panel inside Houdini, but it must not be imported by the normal Panel, Bridge, MCP server, app-server, launcher, or project configuration. Production remains strictly read-only and continues to expose exactly `houdini_scene_info` and `houdini_node_type_info`; all three graph MCP tools remain unregistered and uncallable.

The only live acceptance input is `tests/fixtures/p2_v/stairs_graph.json`. The entry may operate only on a blank, new, unsaved, disposable HIP; only on Houdini's UI main thread; and only after it displays the complete normalized graph, graph digest, approval payload and binding, build, session, revision, dirty state, `/obj` fingerprint, existing nodes, selection, current node, exact target `/obj/HIA_Graph_stairs_demo`, and the one-shot warning. A process-local latch is consumed by the first apply attempt, including a failed attempt; closing or reopening the Panel must not make a second apply possible.

The local entry must use the existing `SceneQueue` request, approval presentation, approval decision, claim, approval-binding digest, one-use claim authority, writer transaction, and queue completion path. No caller boolean or Panel control grants write authority by itself. The B1 queue profile may serve only as the local in-process approval ledger; its `FakeCapabilityAttestation` is not live evidence. Every live authority decision must still be rechecked against the injected read adapter, active HIP session/revision/fingerprint, exact `/obj` state, and current catalog immediately before mutation.

The B4A writer invariants remain mandatory: one `hou.undos.group("HIA: Apply Graph")`, one new validated HIA-owned Object/geo root, common catalog-resolved creation for all declared children, typed safe parameters, request-owned connections, declared flags, exact identity/path/type proofs, bounded postcondition verification, and confined rollback only when exact ownership is provable. Never search for or modify pre-existing content. Never use arbitrary Python, HScript, expressions, callbacks supplied by a request, shell, subprocess, save, forced cook, render, cache, HDA operations, or external file writes.

Before the one live apply, the entry must prove the current build provides the already certified `Object/geo`, `Sop/box`, `Sop/transform` resolving to `xform`, `Sop/merge`, and `Sop/null` catalog and only the fixture-required safe parameters. This is a bounded acceptance catalog, not a permanent engine allowlist. B4B adds no node type, context, Schema, protocol version, service, database, dependency, plugin system, or generalized framework.

Strict event evidence is opt-in only for B4B. It must verify callback registration by readback, attach observers to each exact newly returned node before its next mutation, and require bounded main-thread evidence for every exercised create, ownership user-data, typed parameter, connection, and changed flag operation. The manual Undo must provide exact owned-root destruction evidence. A missing, mismatched, external, late, off-main-thread, or overflowing event journal fails closed. Do not perform extra writes merely to probe callbacks. A declared flag already at the desired value is an observed no-op, not fabricated callback evidence.

After apply, verify the exact declared types, names, parameters, connections, flags, output, graph digest, absence of extra children or external edges, unchanged baseline content/selection/current node, no ordinary node errors, and exactly one revision advance. Do not force cook. If the exact owned root remains after the one-shot attempt, stop in `WAIT_MANUAL_UNDO` with `manual_undo_required=true` even when strict acceptance evidence or queue completion failed. The user must then press Ctrl+Z exactly once and run the read-only verifier; the verifier itself must never call Undo or Redo. After that manual Undo, verify the entire HIA-owned root is absent; the original `/obj` identities/fingerprint, selection, current node, new/unsaved HIP state, and dirty baseline are restored; and no render, cache, HDA, or external file was produced. Cleanup restoration after an `ok=false` apply must remain a failed Gate result and must never be relabeled as successful acceptance.

The live acceptance permits no retry, second apply, Redo, fault injection, second fixture, automatic Houdini launch, production graph registration, or later Gate work. Real rollback-through-`root.destroy()` remains unverified by a successful apply followed by manual Undo and must be reported as such. Complete the offline implementation and full standard-library test suite first, leave every B4B change unstaged and uncommitted, and stop for the user's final live GUI confirmation.
