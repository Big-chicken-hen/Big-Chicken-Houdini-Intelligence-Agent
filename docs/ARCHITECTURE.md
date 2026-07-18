# Architecture

## Runtime path

The product is a Codex client embedded in a Houdini Python Panel. Codex is the only intelligent component; the surrounding code transports requests, displays state, and executes deterministic Houdini operations.

```text
User → Houdini Panel → local Bridge → Codex app-server
     → one selected live backend
       ├─ HIA MCP V2 → hia_mcp_runtime → UI-thread HOM / hou
       └─ FXHoudiniMCP 1.3.0 compatibility fallback
     → current Houdini scene
```

The Panel sends text, local reference images, and optional selection context. The Bridge supervises the app-server and forwards authenticated loopback requests and protocol events. Codex interprets the request and uses exactly one backend selected by the launcher. HIA MCP V2 is the default perception, knowledge, execution, and validation layer; complex current-scene work normally becomes one `hia_execute_hom` batch. The fallback retains the third-party `execute_python` path. The two tool surfaces are never registered in the same app-server.

Local HTTP services bind only to `127.0.0.1` and use a fresh random token for each launcher session.

## Live and offline execution

Creation and modification requests target the currently open scene by default. Native `hython` is a separate helper used only for an explicit offline HIP, batch job, independent verification, long simulation, or background render. An unavailable live MCP connection is reported directly and never causes an implicit switch to an offline project.

## Conversation and automatic compaction

The Panel owns presentation only: responsive user and Codex cards, Markdown and code, attachments, selection context, and one tool-activity card per Turn. It does not summarize or maintain a second memory.

```text
Codex automatic compaction
  → thread/compacted or contextCompaction event
  → Bridge event stream
  → one concise Panel system message
```

Repeated notifications for the same automatic compaction are merged. There is no manual compact request, compact button, local threshold, or local summarizer.

## Local diagnostics

```text
Final runtime failure ─┐
                      ├→ deterministic report writer
User records a problem ┘   → <project-root>\.runtime\diagnostics\<report>.md
```

The writer derives the project root at runtime, merges evidence from the same Turn into one small Markdown file, and redacts credentials. It is not an Agent, Planner, memory system, monitoring service, database, cloud service, or uploader. Successful and satisfactory Turns produce no report.

Reports remain local and are ignored by Git through the existing `.runtime/` rule. The user decides whether to give a report to Codex or anyone else.

## Launcher preflight boundary

```text
scripts/hia-launcher.ps1
  → read-only discovery and bounded probes
  → one backend choice in .runtime/launcher/settings.json
  → scripts/launch-houdini.ps1
  → one Bridge/app-server/Houdini backend lifecycle
```

The launcher is a PowerShell/WPF shell with the standard Windows window frame, not another Agent or service. Its module derives the project root from the launcher location, enumerates Houdini without a version allowlist, requires explicit selection when more than one installation exists, and binds port probes only to `127.0.0.1`. `hia_v2` is the default; `fxhoudini` is an explicit fallback. `scripts/launch-houdini.ps1` remains the only lifecycle entry and injects only the selected backend's paths and environment. HIA V2 uses its own random port/token, `HIA_MCP_V2_*`, `/hia-mcp-v2/v1/*`, and `.runtime/hia-mcp-v2`; fallback keeps the locked third-party runtime without sharing those names.

Portable project configuration uses paths relative to the project or `$HIA_PROJECT_ROOT`, which the lifecycle script supplies only to child processes. Safe repair is deliberately limited to project-local runtime directories and those locked relative-path fields.
