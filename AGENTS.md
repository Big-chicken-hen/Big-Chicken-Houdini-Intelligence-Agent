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

## Current phase: P2-V Gate B2 read-only Houdini capability slice

P1-V and Gate B1 are complete. The five-tool general graph contract remains frozen as pre-release `0.1.0`; never alter that frozen directory in place. Gate B2A's offline implementation is complete and adds a versioned read-only protocol profile and a Houdini Python Panel adapter for exactly `houdini_scene_info` and `houdini_node_type_info`. Gate B2B's real Houdini GUI acceptance was completed manually by the user. Gate B2 is awaiting the user's explicit authorization for its final commit; B2C and B3 are not authorized.

The three graph tools (`houdini_graph_validate`, `houdini_graph_apply`, and `houdini_graph_verify`) remain disabled. All scene writes, including node creation, modification, wiring, deletion, and parameter changes, remain disabled. Cook, render, save, cache, and HDA operations remain disabled. Arbitrary Python, HScript, shell, `eval`, and `exec` execution remains disabled. Do not create `.codex/config.toml`, register or start a real MCP service, or enter B2C or B3.

Only the Panel-side adapter may import `hou`, and every HOM read must occur on Houdini's UI main thread. Bridge, MCP, workers, and ordinary Python tests must remain importable without Houdini. Use the existing standard-library HTTP workers and the Panel's bounded main-thread `QTimer`; QtNetwork is forbidden. Live capability attestation must be internally bound to the Bridge launch/generation, Houdini process nonce and build, Python/PySide versions, HIP session/fingerprint/revision, live OBJ/SOP catalog digest, and versioned Schema digest. Tool arguments are never a trusted attestation source.

The authenticated ordinary-Bearer `GET /v1/scene/status` endpoint may expose only the current safe first-read correlation context and five-type availability summary. It never exposes the process nonce, publisher/observer identity, executor credential, or full parameter catalog, and reading it must not renew the live capability lease.

Gate B2 remains read-only. Do not call any prohibited operation listed above and do not start Houdini automatically. The user's B2B result is evidence from a real manual GUI acceptance, not an automated Houdini test; do not fabricate screenshots, paths, or automated evidence. Keep all B2 changes uncommitted until the user explicitly authorizes the final Gate B2 commit, rerun the complete offline suite for final closure, and stop without entering B2C or B3.
