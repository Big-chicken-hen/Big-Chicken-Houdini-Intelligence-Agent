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

## Runtime cache and final outputs

Automatic screenshots, AI previews, attachments, temporary files, and diagnostics remain under the project-local `.runtime/cache`, `.runtime/attachments`, or `.runtime/diagnostics` directories. The launcher passes `HIA_RENDER_OUTPUT_DIR` to the Bridge, Codex app-server, selected MCP child, and Houdini; it defaults to `<project-root>/.runtime/cache` when the user has not selected a final-output directory.

A user-explicit final render, EXR, video, USD, simulation cache, or export may use an ordinary local directory outside the plugin repository. That exception does not change the project-local cache boundary of `hia_capture_viewport`. The completed operation reports the actual final output path.

The launcher's only cache-deletion operation is the user-invoked screenshot cleanup. It recomputes `<project-root>/.runtime/cache/screenshots`, requires an exact case-insensitive path match, rejects any reparse point in the project-root/runtime/cache/screenshots chain, previews the fixed candidate set, and requests one confirmation. Deletion is limited to unchanged, ordinary, first-level PNG files from that preview; subdirectories and every other runtime or delivery location remain outside its scope.

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
.runtime/dist/launcher/BigChickenLauncher.exe
  → locate the project root and invoke scripts/hia-launcher.ps1
or scripts/hia-launcher.ps1 directly
  → read-only discovery and bounded probes
  → one backend choice and optional final-output directory in .runtime/launcher/settings.json
  → scripts/launch-houdini.ps1
  → one Bridge/app-server/Houdini backend lifecycle
```

The distributable launcher is a thin self-contained .NET 8 WPF WinExe host. It derives the project root from `AppContext.BaseDirectory`, verifies project markers, and starts the existing PowerShell/WPF launcher; it does not duplicate discovery, preflight, repair, settings, reporting, or lifecycle rules. The managed payload is single-file while native WPF components remain as five sidecars beside the EXE, avoiding extraction outside the project. The project-local SDK, CLI home, NuGet caches, build intermediates, and publish directory all live below ignored `.runtime`; no global SDK, PATH, registry, or AppData mutation is required. `scripts/hia-launcher.ps1` remains the direct debugging and CLI entry.

The launcher uses the standard Windows window frame, not another Agent or service. Its module derives the project root from the launcher location, enumerates Houdini without a version allowlist, requires explicit selection when more than one installation exists, and binds port probes only to `127.0.0.1`. `hia_v2` is the default; `fxhoudini` is an explicit fallback. `scripts/launch-houdini.ps1` remains the only lifecycle entry and injects only the selected backend's paths and environment. HIA V2 uses its own random port/token, `HIA_MCP_V2_*`, `/hia-mcp-v2/v1/*`, and `.runtime/hia-mcp-v2`; fallback keeps the locked third-party runtime without sharing those names.

Final delivery output is a separate launcher setting, not an internal cache or a Panel/session-history feature. A non-empty `render_output_dir` may point to any ordinary writable local absolute directory outside Windows and Houdini installations; an empty value resolves to project-local `.runtime/cache`. The WPF launcher validates and places only the resolved value in the child lifecycle process environment as `HIA_RENDER_OUTPUT_DIR`. The lifecycle validates it again, creates it without clearing contents, and injects it independently into the Bridge and Houdini child environments. Existing `HIA_CACHE_DIR` remains project-local and continues to own only internal screenshots, previews, and short-lived cache data; the variables are never assigned from one another.

Portable project configuration uses paths relative to the project or `$HIA_PROJECT_ROOT`, which the lifecycle script supplies only to child processes. The explicit final-output delivery directory is the sole launcher setting that may intentionally be an absolute path outside the project; leaving it empty retains fully portable project-local behavior. Safe repair is deliberately limited to project-local runtime directories and those locked relative-path fields.
