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

## Current P2-V Gate B3 pure-Python fake-only boundary

Gate B2C completed at commit `edf7f3a`; its HTTPS transport hotfix completed at commit `3213625`. The corrected five-tool `schemas/houdini-mcp/0.1.0` contract and two-tool `schemas/houdini-mcp/0.2.0` read-only contract are frozen and must not be edited in place. Gate B3 authorizes only a pure-Python offline transaction simulator and tests. B4, live `hou`, and every real Houdini write remain unauthorized.

Production remains strictly read-only. `.codex/config.toml`, the launcher, production app-server command, `stdio.main()`, Bridge, Panel, and live capability path must continue to expose only `houdini_scene_info` and `houdini_node_type_info`; `houdini_graph_validate`, `houdini_graph_apply`, and `houdini_graph_verify` remain unregistered and uncallable there. A fake graph profile may be constructed only by tests or an explicitly named offline harness, never by a request, environment variable, default runtime path, or network service.

The tracked project server remains `enabled = true` and `required = false`; only the Bridge-owned app-server child may override it to `required=true` through a per-process argument. Only the Panel adapter may import `hou`, every live HOM read stays on the Houdini UI main thread, and the B2C attestation, safe status facade, lease, secret-redaction, and independent executor-credential controls remain mandatory and unchanged.

The fake scene must store declared intent separately from observed nodes, types, typed parameters, connections, flags, layout, and ownership. Every run starts with a non-HIA sentinel whose full observed state is compared after success, failure, rollback, and simulated Undo. Verification reconstructs a canonical graph from observed state; it must not trust a saved request graph. Deliberate observed-state tampering must fail verification.

Each fake mutation boundary is deterministic and fault-injectable. Rollback may act only on the exact object identity of the root created and owned by the current request; prefix scans, name guesses, and changes to pre-existing objects are forbidden. Failure to prove or complete rollback returns `ROLLBACK_FAILED` or `SCENE_STATE_INDETERMINATE`, freezes later fake writes, and never triggers an automatic retry, save, reload, or success assumption. Mandatory internal postcondition verification runs before commit and cannot be replaced by a later external verify call.

One re-entrant lock is the fake scene transaction and snapshot boundary for apply, simulated Undo, scene info, validation, verification, and capability snapshots. A separate content-blind arbiter performs each control-state check and its following mutation as one step; cancel, deadline, shutdown, and final commit therefore have one authoritative ordering. Any ordinary exception after publication enters confined rollback. A non-ordinary interruption performs the same best-effort containment and is then re-raised. Exact rollback proof covers key, path, object identity, transaction, ownership, target, Undo, and audit identity; any ambiguity freezes writes rather than deleting by name. Verification rejects unrepresentable observed values with a schema-valid structured failure.

A successful fake apply records exactly one test-only simulated Undo transaction. One simulated Undo restores the complete pre-transaction observed fake state and leaves the sentinel unchanged. This is not evidence that real `hou.undos.group`, live rollback, cooking, or main-thread scheduling works; those claims remain for a separately authorized B4 acceptance.

Gate B3 must not import `hou`, start Houdini, hython, MCP, Bridge, app-server, or another network service, or modify a real HIP. It must not provide create/set/connect/destroy/save/cook/render/cache/HDA, Python SOP, HScript, expression, callback, arbitrary Python/shell/`eval`/`exec`, filesystem, or secret-handling product capabilities. It must not modify `.codex/config.toml`, launcher/runtime configuration, Codex login, proxy, user/global configuration, or frozen Schema. All B3 changes remain unstaged and uncommitted pending separate user approval.
