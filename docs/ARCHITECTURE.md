# Architecture

## Runtime path

The product is a Codex client embedded in a Houdini Python Panel. Codex is the only reasoning, planning, and content-generation component; the surrounding code transports requests, displays state, executes deterministic Houdini operations, and may run an optional project-local Qwen text encoder for retrieval only.

```text
User → Houdini Panel → local Bridge → Codex app-server
     → one selected live backend
       ├─ HIA MCP V2 ┬→ hia_mcp_runtime → UI-thread HOM / hou
       │              └→ persistent embedding stdio worker → project SQLite
       └─ FXHoudiniMCP 1.3.0 compatibility fallback
     → current Houdini scene
```

The Panel sends text, local reference images, and optional selection context. The Bridge supervises the app-server and forwards authenticated loopback requests and protocol events. Codex interprets the request and uses exactly one backend selected by the launcher. HIA MCP V2 is the default perception, knowledge, execution, and validation layer; complex current-scene work normally becomes one `hia_execute_hom` batch. The fallback retains the third-party `execute_python` path. The two tool surfaces are never registered in the same app-server.

Local HTTP services bind only to `127.0.0.1` and use a fresh random token for each launcher session.

HIA MCP V2 exposes 17 tools. `hia_local_help_search` preserves its lexical call shape while adding optional `lexical`, `vector`, and default `hybrid` retrieval. `hia_project_memory` is the only durable memory tool: only explicit `record`, `supersede`, and `delete` actions mutate memory; `search` and `list` are read-only. Supported memory types are `decision`, `preference`, `asset`, `lesson`, and `workflow`. Chat history, compaction events, and diagnostics are never copied into memory automatically.

The encoder worker is a persistent, serialized stdio child of HIA MCP, not a loopback server and not part of Houdini Python. It only encodes supplied text. It cannot generate memory bodies, answers, plans, or HOM.

## Live and offline execution

Creation and modification requests target the currently open scene by default. Native `hython` is a separate helper used only for an explicit offline HIP, batch job, independent verification, long simulation, or background render. An unavailable live MCP connection is reported directly and never causes an implicit switch to an offline project.

## Runtime cache and final outputs

Automatic screenshots, AI previews, attachments, temporary files, and diagnostics remain under the project-local `.runtime/cache`, `.runtime/attachments`, or `.runtime/diagnostics` directories. The launcher passes `HIA_RENDER_OUTPUT_DIR` to the Bridge, Codex app-server, selected MCP child, and Houdini; it defaults to `<project-root>/.runtime/cache` when the user has not selected a final-output directory.

The local knowledge database, indexed bodies, vectors, encoder virtual environment, model files, and model caches also remain below `.runtime`. They are internal runtime state, ignored by Git, and excluded from every Release archive.

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

Repeated notifications for the same automatic compaction are merged. There is no manual compact request, compact button, local threshold, local summarizer, or automatic chat-memory writer. Explicit `hia_project_memory` records are a separate user/project data path and are never produced by compaction.

## Local retrieval architecture

SQLite FTS5 is the hard baseline for local help and project-memory search. Hybrid search first obtains lexical candidates, then optionally blends vector candidates. An ordinary query adds vectors only for up to 32 exact lexical-candidate chunks and never consumes the general indexing backlog. While the vector index is partial, semantic ranking is confined to each query's own lexical candidate documents; a query with no trustworthy lexical candidate returns lexical/empty results instead of searching an ingestion-biased vector subset. Global vector ranking is enabled only when `complete=true`. Progress and scope are reported under `retrieval.vector.index`.

Source refresh is incremental by chunk SHA-256. A changed chunk invalidates only its corresponding vector; deleted sources remove their bodies, FTS rows, and vectors. Changing the selected model, model revision, or MRL dimension rebuilds only the vector layer: document bodies and FTS5 remain intact. Every result preserves source provenance and verification, and every semantic request reports the requested/active profile plus status and degradation reason.

Full vector backfill is a separate, launcher-owned one-shot process: Bridge Python runs `python -B -m hia_mcp_runtime.knowledge_index_cli --project-root <root> status|build`. The CLI emits `hia-knowledge-index-jsonl/1` start/progress/completed/error records, commits each bounded batch, and resumes from missing or changed chunks after interruption. It is not an MCP tool or resident service.

Missing dependencies, missing or damaged weights, load failure, and GPU/CPU memory failure never trigger a download. They cause an explicit fallback to an already installed compatible profile when allowed, then a hard fallback to FTS5. There is no quantization framework, reranker, third model, watcher, scheduler, or second Agent.

## Stable embedding launcher contract

`src/hia_core/embedding_contract.py` is the importable, standard-library-only authority. It performs no I/O. The launcher consumes this contract for selection, project-local installation/repair, preflight, and child-process environment; this wiring must not be interpreted as a bundled or installed model.

Profiles:

| Stable profile ID | Model directory | Default/max dimension | Selection behavior |
|---|---|---:|---|
| `qwen3-embedding-0.6b` | `.runtime/models/qwen3-embedding/qwen3-embedding-0.6b` | 1024/1024 | default; failure → FTS5 |
| `qwen3-embedding-8b` | `.runtime/models/qwen3-embedding/qwen3-embedding-8b` | 1024/4096 | higher quality; failure → installed 0.6B → FTS5 |

Both profiles are Apache-2.0, 32K-context, 100+ language, MRL- and query-instruction-capable official Qwen models. The 0.6B repository is about 1.21 GB. The 8B repository is about 15.2 GB with BF16 sharded weights and is not guaranteed to load reliably on a 16 GB GPU after runtime overhead. Only one model may be loaded at a time. The ordinary dimension is 1024 for both profiles; 4096 for 8B is an advanced environment override, not a launcher dimension control. The worker passes `truncate_dim=profile.dim` when constructing `SentenceTransformer` and normalizes at that truncated dimension; it does not slice a full Python vector afterward.

Exact runtime layout:

| Purpose | Project-relative path |
|---|---|
| Toolchain | `.runtime/toolchains/hia-embedding` |
| Virtual environment | `.runtime/toolchains/hia-embedding/venv` |
| Worker Python | `.runtime/toolchains/hia-embedding/venv/Scripts/python.exe` |
| Worker source | `services/hia_mcp_v2/embedding_worker` |
| Models root | `.runtime/models/qwen3-embedding` |
| Hugging Face cache | `.runtime/cache/embedding/huggingface` |
| Transformers cache | `.runtime/cache/embedding/transformers` |
| Torch cache | `.runtime/cache/embedding/torch` |
| Temporary files | `.runtime/cache/embedding/tmp` |
| SQLite database | `.runtime/knowledge/knowledge.sqlite3` |

The worker distribution/module/console entry point is `hia_embedding_worker`; the module launch is `python -m hia_embedding_worker`, and its protocol is `hia-embedding-stdio/1`.

Reserved launcher settings in `.runtime/launcher/settings.json` are `embedding_profile`, `embedding_dimension`, and `embedding_device`. The launcher is expected to export:

- `HIA_EMBEDDING_PROFILE`, `HIA_EMBEDDING_PYTHON`, `HIA_EMBEDDING_DIM`, and `HIA_EMBEDDING_DEVICE`;
- `HIA_EMBEDDING_MODEL_DIR_QWEN3_0_6B` and `HIA_EMBEDDING_MODEL_REVISION_QWEN3_0_6B`;
- `HIA_EMBEDDING_MODEL_DIR_QWEN3_8B` and `HIA_EMBEDDING_MODEL_REVISION_QWEN3_8B`.

The launcher derives these environment variables from its contract-backed selection; `HIA_EMBEDDING_DIM` remains the explicit advanced dimension override, and search/import remains download-free. The public health/degradation payload uses `contract_version`, `status`, `installed`, `ready`, `degraded`, `requested_profile`, `active_profile`, `model_id`, `model_revision`, `dim`, `normalized`, `initialized`, `loaded`, `fallback_reason`, and `repair`. Stable status values are `disabled`, `missing`, `installed`, `configured`, `loading`, `ready`, `degraded`, and `error`.

`installed` describes the active profile's project-local toolchain/model artifacts, not whether the originally requested profile exists or whether a model has loaded. `ready` means the active worker has initialized and loaded the exact revision/dimension. `degraded` means the requested profile or vector path was not used; `fallback_reason` explains why. Runtime `repair` identifies the affected profile/model and its configuration environment names without downloading anything; the canonical `install`, `repair`, and `repair_toolchain` action mapping lives in `launcher_contract()["repair_actions"]`, while exact expected paths come from `runtime_layout(project_root)`. Runtime `model_id` is the revision-qualified vector identity `<repository_model_id>@<revision>` and `model_revision` is also returned separately; profile-registry `model_id` remains the plain Hugging Face repository ID. A launcher integration must show the requested profile separately from the active fallback and must never claim 8B ready merely because files exist.

Official sources: [Qwen3-Embedding-0.6B model card](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B), [0.6B files](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B/tree/main), [Qwen3-Embedding-8B model card](https://huggingface.co/Qwen/Qwen3-Embedding-8B), and [8B files](https://huggingface.co/Qwen/Qwen3-Embedding-8B/tree/main).

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
