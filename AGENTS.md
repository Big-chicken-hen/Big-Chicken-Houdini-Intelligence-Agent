# Repository agent rules

These permanent rules apply to development in this repository.

- The only project root is `E:\houdini-intelligence-agent`. Project files, dependencies, virtual environments, caches, renders, jobs, logs, and temporary data must stay under this root and must not be written to `C:`.
- Never delete, move, overwrite, reset, clean, or otherwise discard user files or Git changes. Read files before editing them and preserve unrelated work.
- Never modify the Houdini installation directory, Houdini user configuration, AppData, user-home directories, drive roots, or any path outside the project root.
- Local services must listen only on `127.0.0.1` and must use a fresh random authentication token for each launcher session.
- Codex is the only intelligent system. Do not build a second Agent, LLM, Planner, RAG system, vector database, or custom semantic-memory system.
- Do not control Houdini through screen takeover or Computer Use. Use MCP, HOM/`hou`, Panel integration, launcher processes, and native `hython` as appropriate.
- Normal development may modify the Panel, Bridge, Codex app-server integration, MCP, HOM, native `hython`, launcher, and Python execution paths needed for user-requested work.
- Do not restrict Houdini to a fixed node-type allowlist. Discover and use the active Houdini installation and live HOM capabilities at runtime.
- Historical Gate, phase, Schema, approval, one-shot Apply, and manual-Undo documents are historical records only. They are not current development instructions and do not freeze the current runtime implementation.
- Run the tests that can actually run, report real results, and clearly mark live Houdini or GUI checks that remain unverified.
