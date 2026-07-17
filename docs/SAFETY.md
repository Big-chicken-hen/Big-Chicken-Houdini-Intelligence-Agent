# Safety policy

This policy has the highest priority. When safety cannot be proven, stop and ask; never guess.

## Filesystem rules

- The only project root is `E:\houdini-intelligence-agent`.
- Project source, environments, caches, temporary data, renders, jobs, logs, checkpoints, quarantine records, and published artifacts must not be created on `C:`.
- Never modify or clean `C:\Users`, AppData, a user home directory, `CODEX_HOME`, Houdini user configuration, a drive root, or any path outside the project root without separate authorization for one exact absolute path.
- Reject empty paths, drive roots, UNC paths, Windows device paths, alternate data streams, outside-root paths, traversal that escapes the root, and every junction, symlink, mount point, or other reparse point.
- Apply Windows case-insensitive path semantics. A path is writable only when its normalized absolute form is a strict ordinary child of the approved project root.
- Before a future delete or move: inventory read-only; canonicalize the root and every target; check junctions, symlinks, UNC/device syntax, ADS, and traversal; display exact targets and scope; then obtain separate explicit approval.
- Never recursively delete a computed, expanded, uncertain, or unverified path. Prefer moving approved cleanup candidates to a project-local quarantine with a manifest when that phase is authorized.
- Use one native PowerShell/Windows chain with literal paths and argument arrays. Never hand enumerated paths to another shell.
- Never use destructive Git commands or overwrite unrelated user work.

`src/hia_core/path_policy.py` is validation-only. It must not delete, move, quarantine, clean, create, or modify a target.

## Houdini and scene rules

- Never claim a Houdini connection, scene state, schema, node operation, cook, render, image interpretation, HIP/HDA understanding, or test result without real evidence.
- Never modify a scene without approval matching that exact operation and risk level.
- Every high-risk operation requires explicit approval and a secret-free audit record.
- Do not automatically save or overwrite HIP files, alter HDA definitions, delete nodes, disconnect existing wiring, or scan arbitrary filesystem locations.
- The current live Houdini schema overrides static documentation, examples, skills, and model knowledge; report conflicts.
- All live HOM/UI calls run on Houdini's main thread. RPC I/O only validates and queues.
- Writes use scene revision, HIP fingerprint, idempotency, bounded scope, structured errors, and one undo group. Stale writes fail with `SCENE_CONFLICT`; last-write-wins is forbidden.
- Future services bind to `127.0.0.1` by default and use authentication. Never expose the app-server or Scene Gateway directly.

## Intelligence and data rules

- Codex is the only intelligent subject. Do not create another Agent, LLM, planner, RAG system, vector database, semantic-memory service, or external vision model.
- Houdini and sidecars provide deterministic execution only. Do not operate Houdini via screen takeover or Computer Use.
- Codex-native subagents are phase-gated, proposal-first, and never independent live-HIP writers.
- Technical databases may store identifiers, revisions, jobs, artifacts, idempotency, and audit data, but not duplicate chats, embeddings, semantic summaries, or custom memory.
- Retrieval and preference storage do not train model weights and must never be described as doing so.
- User-inspectable knowledge, cases, rules, memory, and preferences must remain exportable and deletable.

## Execution and reporting rules

- Never expose unrestricted arbitrary Python, shell execution, `exec`, or `eval` as product tools.
- Any future Python capability must follow the isolated, clone, and explicitly unsafe-live tiers described in the architecture.
- Keep credentials, tokens, and secrets out of audit records and user-facing errors.
- Return structured JSON-serializable errors. Diagnostic tracebacks may be logged but are not sufficient user-facing output.
- Run all available tests and report their actual results. Mark unavailable live checks as unverified and explain why.
- Stay within the currently authorized phase and stop at its boundary.

## Current P2-V Gate B4B one-shot live acceptance boundary

Gate B4A completed at commit `0b3b5a51c0ad95aee382794d74852d2d541bf880`. The corrected five-tool `schemas/houdini-mcp/0.1.0` contract, the two-tool `schemas/houdini-mcp/0.2.0` read-only contract, and `tests/fixtures/p2_v/stairs_graph.json` are frozen and must not be edited in place.

Production remains strictly read-only. `.codex/config.toml`, the launcher, production app-server command, `stdio.main()`, Bridge, normal Panel dispatch, and live MCP path continue to expose only `houdini_scene_info` and `houdini_node_type_info`. The three graph tools remain unregistered and uncallable. Gate B4B adds only a separately opened local acceptance Panel; no environment variable, configuration, command-line argument, network request, or ordinary Panel action may enable it.

The dedicated Panel injects the already running Houdini `hou` module into the existing generic writer on the Houdini UI main thread. It may read only the frozen stairs fixture, build one exact in-process `SceneQueue` approval/claim/binding, and present the full normalized graph, digests, target, side effects, build, HIP session, revision, dirty state, `/obj` fingerprint, existing nodes, selection, and current node. The local B1 `FakeCapabilityAttestation` is only the existing queue's approval-ledger envelope; it is never presented as live capability evidence.

The one process-local Apply latch is consumed before the first attempt and cannot be reset by closing or reopening the pane. The approved target is exactly `/obj/HIA_Graph_stairs_demo` in one blank, new, unsaved, disposable HIP. No retry, second fixture, Redo, fault injection, automatic launch, or second Apply is authorized. The user must review the full approval JSON and explicitly confirm before the existing claim authority and `HoudiniWriteAdapter` can consume the request.

All existing writer guards remain mandatory: exact session/revision/fingerprint/catalog and target-absence checks, one `hou.undos.group("HIA: Apply Graph")`, exact created identities, catalog-driven types, typed safe parameters, owned connections, declared flags, bounded observed-state postconditions, unchanged baseline content, and confined rollback only when ownership is proven. Never save, force cook, render, cache, publish or modify an HDA, execute request-supplied Python/HScript/shell, or write an external file.

Strict B4B observers must be installed and read back before the apply and on each exact new node before its next mutation. Every exercised create, ownership metadata, parameter, connection, and changed flag call requires a bounded matching main-thread event and callback source. A true flag no-op is recorded as a no-op. Zero events, an unknown source/subject, a late or off-main event, overflow, or observer installation failure is fail-closed. No extra real write may be added merely to probe callback behavior.

After the single apply, independently verify the graph's exact types, names, parameters, connections, flags, output, digest, errors, absence of extras/external edges, unchanged baseline state, and exactly one revision advance. If the exact owned root remains, the controller must expose `WAIT_MANUAL_UNDO` and `manual_undo_required=true` even when strict acceptance or ledger evidence is incomplete and `ok=false`. Then the code stops: only the user may press Ctrl+Z once. The verifier never calls Undo or Redo. Its read-only post-Undo proof requires the exact root and declared child paths to be absent, the baseline `/obj` identities/fingerprint, selection, current node, and new unsaved HIP state to remain valid, and the observer journal to contain the exact owned deletion without unrelated scene events. Restored cleanup after a failed acceptance remains a failed Gate result; it is not evidence that the apply passed.

This one successful apply plus manual Undo does not prove the writer's real `root.destroy()` rollback path, failure containment, or wider version compatibility. Report those as unverified. Gate B4B adds no node types, contexts, dependency, service, database, framework, protocol, Schema version, or later-gate feature. All B4B changes stay unstaged and uncommitted until separate review and approval.
