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

## Current P2-V Gate B2C real read-only MCP boundary

The corrected Gate B0 five-tool contract remains frozen as pre-release `0.1.0` and must not be edited in place. Gate B2 is complete at commit `02660b0d975e1c0016c77ff4fbbe14b8e9908942`. Gate B2C is authorized only to register and connect a project-scoped stdio MCP sidecar containing `houdini_scene_info` and `houdini_node_type_info`. It does not authorize B3 or any Houdini write.

`houdini_graph_validate`, `houdini_graph_apply`, and `houdini_graph_verify` remain unavailable and unregistered. All scene writes, including node creation, modification, wiring, deletion, and parameter changes, remain unavailable. Cook, render, save, cache, and HDA operations remain unavailable. Arbitrary Python, HScript, shell, `eval`, and `exec` product tools remain unavailable. The only permitted Codex registration is the reviewed repository file `E:\houdini-intelligence-agent\.codex\config.toml`; user/global configuration must not be changed. That tracked server stays `enabled = true` and `required = false` for ordinary Codex Desktop recovery, while the controlled Bridge launch must override only its owned app-server process to `required=true` so the Houdini runtime fails closed if MCP startup fails.

The only permitted live Houdini integration is a Python Panel adapter that performs bounded HOM reads on the Houdini UI main thread. Bridge, MCP, HTTP workers, and ordinary tests never import or call `hou`. The adapter may observe HIP lifecycle and scene-change events, read a bounded HIA-owned summary, and inspect the five allowlisted live node types. If session or revision observation cannot be proven reliable, capability publication fails closed with `HOUDINI_UNAVAILABLE`; it must never invent a revision.

Capability publication uses a Bridge-generated, launcher-delivered executor credential distinct from the ordinary Bridge Bearer token. Bridge-owned launch ID, generation, process nonce, and Schema digest are never accepted from tool arguments or an untrusted capability body. Raw HIP paths, environment variables, user parameters, scripts, callbacks, expressions, help text, file chooser contents, secrets, and unrestricted scene dumps must not cross the boundary.

First-read discovery uses only authenticated `GET /v1/scene/status`. Its response is an atomic, lease-checked safe correlation summary; it omits process/publisher/observer identities, executor credentials, full parameter metadata, paths, and environment data, and the read does not extend the lease.

Gate B2C remains read-only and must not call `createNode`, parameter setters, `setInput`, `destroy`, HIP save, cook, render, cache, HDA APIs, arbitrary Python/HScript/shell/eval/exec, or any graph tool. It must not start Houdini automatically, use QtNetwork, configure GitHub/network exposure, or enter B3. The project MCP configuration stores only fixed command metadata and environment-variable names; the random URL and Bearer token are inherited for one launcher lifecycle and are never persisted or logged. The independent Panel executor credential is not exposed to Codex or MCP. B2C changes remain unstaged and uncommitted until the user completes the separate real GUI acceptance and explicitly authorizes a commit.
