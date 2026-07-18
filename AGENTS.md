# Repository agent rules

These permanent rules apply to development in this repository.

- Resolve the project root from `HIA_PROJECT_ROOT` or the repository location. Project files, dependencies, virtual environments, caches, renders, jobs, logs, and temporary data must stay under that resolved root; do not assume a drive letter or fixed checkout path.
- Never delete, move, overwrite, reset, clean, or otherwise discard user files or Git changes. Read files before editing them and preserve unrelated work.
- Never modify the Houdini installation directory, Houdini user configuration, AppData, user-home directories, drive roots, or any path outside the project root.
- Local services must listen only on `127.0.0.1` and must use a fresh random authentication token for each launcher session.
- Codex is the only intelligent system. Do not build a second Agent, LLM, Planner, RAG system, vector database, or custom semantic-memory system.
- Do not control Houdini through screen takeover or Computer Use. Use the Panel, Bridge, HIA MCP V2, HOM/`hou`, launcher, and native `hython` as appropriate; FXHoudiniMCP is an explicit compatibility fallback.
- Current-scene creation and modification default to HIA MCP V2 and HOM. For complex work, prefer one or a few Codex-generated HOM batches through `hia_execute_hom`. Do not restrict Houdini to a fixed node-type allowlist or create a tool-call forest.
- For ordinary scene requests, do not search project source, services, docs, or contracts first. Inspect them only when the user explicitly asks for plugin, Panel, Bridge, MCP, or project-code diagnosis or development.
- Use native `hython` only when the user explicitly requests offline work, batch processing, an independent HIP, separate verification, or background rendering. If live MCP is unavailable, report that directly instead of switching routes.
- Display only Codex's automatic context compaction events. Do not add manual compaction, a local summarizer, or another memory system.
- Final runtime failures may create one redacted report per Turn under `.runtime/diagnostics`; subjective dissatisfaction is recorded only when the user requests it. A satisfied successful Turn creates no report, and reports must never contain credentials.
- Normal development may modify the Panel, Bridge, Codex app-server integration, MCP, HOM, native `hython`, launcher, and Python execution paths needed for user-requested work.
- Historical Gate, phase, Schema, approval, one-shot Apply, and manual-Undo documents are historical records only. They are not current development instructions and do not freeze the current runtime implementation.
- Run the tests that can actually run, report real results, and clearly mark live Houdini or GUI checks that remain unverified.
