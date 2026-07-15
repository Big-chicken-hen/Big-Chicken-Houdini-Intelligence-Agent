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

## Current P2-V design-review boundary

This sub-gate creates only architecture, versioned schemas, a threat model, offline contract tests, and a test plan for four named Houdini tools. It starts no MCP adapter, Bridge scene endpoint, Houdini process, network listener, or scene operation; it imports no `hou`; it writes no `.codex/config.toml`; and it implements no tool handler. Live scene work requires a later, separate approval after the design package is reviewed.
