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
- Do not limit architecture to SOP; add context-specific adapters for OBJ, SOP, DOP, LOP, VOP, MaterialX, COP, TOP, KineFX, and APEX as their phases are authorized.
- Never automatically save or overwrite HIP files, modify HDA definitions, delete nodes, or scan arbitrary filesystem locations.

## Current phase: P2-V Gate B1 general offline implementation

P1-V is complete and the corrected P2-V Gate B0 five-tool general graph contract is frozen as pre-release `0.1.0`. Gate B1 may preserve and adapt the existing project-local drafts only for the exact five-tool deny-by-default contract, deterministic relational validation, authenticated bounded Bridge scene queue, structured results, and a pure-Python fake executor shared by the table and stairs fixtures. `houdini_graph_apply` is the only write tool; B1 remains fake-only and live-disabled. Do not import or call `hou`, start Houdini, modify a scene, write `.codex/config.toml`, register or start a real MCP service, use QtNetwork, expose arbitrary Python/HScript/shell/eval, or claim live main-thread, cook, rollback, or Undo verification. Keep B1 changes uncommitted, run the complete offline suite, stop before B2, and wait for separate approval.
