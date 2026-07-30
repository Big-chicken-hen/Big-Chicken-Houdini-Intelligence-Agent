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

HIA MCP V2 exposes 18 tools. `hia_run_effect_experiment` is the only bounded baseline/candidate comparison executor; it temporarily applies scalar parameter deltas, advances/captures a short sequence, and returns factual evidence without scoring or creating an EffectSpec. `hia_local_help_search` preserves its lexical call shape while adding optional `lexical`, `vector`, and default `hybrid` retrieval. `hia_project_memory` is the only durable memory tool: only explicit `record`, `supersede`, and `delete` actions mutate memory; `search` and `list` are read-only. Supported memory types are `decision`, `preference`, `asset`, `lesson`, and `workflow`. Chat history, compaction events, and diagnostics are never copied into memory automatically.

The Panel's Knowledge and Memory page uses two separate, thin control paths:

```text
Panel project-memory page
  → authenticated Bridge POST /v1/project-memory
  → fixed hia_project_memory call through the selected HIA MCP V2 runtime
  → bounded list/search/mutation projection back to the Panel

Panel local-knowledge section
  → authenticated Bridge POST /v1/knowledge
  → fixed project-relative scripts/hia-knowledge.ps1 action
  → bounded status/source/job projection back to the Panel
```

Neither route accepts an arbitrary tool or command. The memory route reuses the live HIA MCP V2 input validator instead of duplicating the memory schema; the knowledge route exposes only the fixed status, source, repair, and index actions of the shared CLI. Their Panel request state is isolated from Thread, Goal, Stop, and conversation state. The WPF launcher is optional: `scripts/launch-houdini.ps1` supplies the same Bridge/runtime credentials, so both Panel paths remain available on the direct lifecycle path. No path opens SQLite directly in the Panel or creates a second memory store, importer, or installer.

The encoder worker is a persistent, serialized stdio child of HIA MCP, not a loopback server and not part of Houdini Python. It only encodes supplied text. It cannot generate memory bodies, answers, plans, or HOM.

## Live and offline execution

Creation and modification requests target the currently open scene by default. Native `hython` is a separate helper used only for an explicit offline HIP, batch job, independent verification, long simulation, or background render. An unavailable live MCP connection is reported directly and never causes an implicit switch to an offline project.

## Runtime cache and final outputs

AI previews, attachments, temporary files, and diagnostics remain under the project-local `.runtime/cache`, `.runtime/attachments`, or `.runtime/diagnostics` directories. HIA viewport/flipbook captures and AI Goal stage checkpoints are the narrow exception: each call re-reads the current HIP and, when it is a real saved file under an ordinary writable parent, uses `<hip-parent>/.hia/screenshots` or `<hip-parent>/.hia/checkpoints`. Untitled or unsafe scenes fall back to `.runtime/cache/screenshots` and the current `.runtime/launcher-sessions/<id>/checkpoints`. The launcher passes `HIA_RENDER_OUTPUT_DIR` to the Bridge, Codex app-server, selected MCP child, and Houdini; it defaults to `<project-root>/.runtime/cache` when the user has not selected a final-output directory.

The local knowledge database, indexed bodies, vectors, model files, managed
CPython base, uv, and caches remain below `.runtime`. The one user-visible
Bridge/local-knowledge/encoder environment is `<project-root>/.venv`. Both
`.runtime` and `.venv` are ignored by Git and excluded from every Release
archive.

A user-explicit final render, EXR, video, USD, simulation cache, or export may use an ordinary local directory outside the plugin repository. That output remains separate from both the HIP-local `.hia` automatic artifacts and project cache. The completed operation reports the actual final output path.

Cache deletion is implemented only by the project-relative
`scripts/hia-cache.ps1` command. The WPF **清理缓存** page calls that command and
does not own another deletion path. The fixed category allowlist is:

| Category | Exact project-relative root | Special boundary |
|---|---|---|
| `screenshots` | `.runtime/cache/screenshots` | unsaved/unsafe HIP fallback screenshots only |
| `previews` | `.runtime/cache/previews` | managed previews only |
| `tmp` | `.runtime/cache/tmp` | managed short-lived files only |
| `embedding-runtime` | `.runtime/cache/embedding` | excludes child `huggingface` |
| `embedding-downloads` | `.runtime/cache/embedding/huggingface` | model download cache only |
| `dotnet` | `.runtime/cache/dotnet` | project-local build cache only |

`-Action list` resolves each exact target from the command's own location,
enumerates size, and returns a content snapshot hash. `-Action clear` requires
both one or more explicit `-Category` values and the matching `-SnapshotHash`.
It revalidates the project root, directory chain, entry identity, reparse status,
and snapshot before deleting category contents, preserves the category roots,
and reports each category independently.

Discovery of any ordinary Houdini scene file (`.hip`, `.hiplc`, or `.hipnc`)
blocks its category and therefore the complete selected clear batch. Final
delivery output may use the `.runtime/cache` root but cannot resolve to or below
one of the clearable category roots.

There is no caller-supplied path, current-directory dependency, HOME-based root,
or wildcard deletion. Any reparse point, path escape, changed snapshot, unsafe
entry, or project-external target is rejected. The allowlist cannot reach
`.runtime/knowledge`, `.runtime/models`, `.runtime/toolchains`,
`.runtime/attachments`, `.runtime/launcher-sessions`, Codex Home/Threads,
checkpoints, HIP files, `.runtime/cache/renders`,
`.runtime/cache/research`, or a user-selected final-output directory.
It also never discovers or clears HIP-local `.hia` directories.

## Conversation and automatic compaction

The Panel owns conversation presentation plus thin user controls: responsive user and Codex cards, Markdown and code, attachments, selection context, one tool-activity card per Turn, task views, and explicit project-memory management. It does not summarize, implement retrieval, or maintain a second memory.

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

Full vector backfill is a separate one-shot process reached through
`powershell -File .\scripts\hia-knowledge.ps1 index-status` or
`powershell -File .\scripts\hia-knowledge.ps1 index-build`.
The PowerShell entry resolves the project root and canonical runtime, then calls
the existing `hia_mcp_runtime.knowledge_index_cli`; it does not duplicate index
logic. The inner CLI emits `hia-knowledge-index-jsonl/1`
start/progress/completed/error records, commits each bounded batch, and resumes
from missing or changed chunks after interruption. WPF consumes this same stream.
It is not an MCP tool or resident service.

Search, source import, rescan, and index status never trigger a dependency or
model download. Only an explicit environment or embedding installation action
may download. Missing dependencies, missing or damaged weights, load failure,
driver mismatch, and GPU/CPU memory failure cause an explicit fallback to an
already installed compatible profile when allowed, then a hard fallback to
FTS5. There is no quantization framework, reranker, third model, watcher,
scheduler, or second Agent.

## CLI-first local knowledge and environment

The WPF environment/local-knowledge page and Houdini Panel knowledge section
are presentation clients over the same project-relative commands:

```text
WPF or Panel local-knowledge UI ─┬→ scripts/hia-knowledge.ps1
direct PowerShell user ──────────┘       ├→ project-local environment probe/installer
                                        ├→ managed-source compatibility layer
                                        └→ existing HIA knowledge index CLI

WPF cache page ───────────┬→ scripts/hia-cache.ps1
direct PowerShell user ───┘       └→ fixed-category snapshot/list/clear core
```

The stable knowledge actions are `status`, `environment-status`,
`environment-install`, `environment-repair`, `import-file`, `import-folder`,
`list`, `delete`, `rescan`, `index-status`, and `index-build`. The thin
managed-source compatibility layer copies allowed documents into
`.runtime/knowledge/sources` and delegates parsing/indexing to the existing HIA
runtime. It does not reproduce the SQLite/FTS/vector implementation. Its format
allowlist is `.md`, `.txt`, `.html`, `.htm`, `.srt`, `.vtt`, and text-based
`.pdf`; video and scanned-document OCR are outside this contract. Deleting a
managed copy never deletes or edits the source file.

The base environment action owns one canonical runtime:
`<project-root>/.venv`. Project-local uv installs a managed CPython 3.10.11
beneath `.runtime/toolchains/python`, creates the staged venv, and installs
`pypdf` into it. The Bridge, parser, and optional embedding worker reuse this
one environment; no second HIA managed environment is created. Child Python
starts isolated, with user site-packages disabled and inherited `PYTHONHOME`,
`PYTHONPATH`, `VIRTUAL_ENV`, and Conda state removed. Final health validation
rejects a venv whose executable, prefix, base prefix, or import path escapes the
project. Neither global Python nor Houdini Python receives packages.

The former `.runtime/toolchains/hia-embedding/venv` path is a legacy migration
source only. Migration rebuilds a fresh staging environment under `.runtime`
from the managed CPython; it never moves or mutates the legacy venv. A
pre-existing root `.venv` without the HIA managed marker is refused. Before and
after atomic publication, validation covers Python 3.10.11/x64/no-user-site,
`sys.executable`, `sys.prefix`, a base prefix inside the managed CPython,
Bridge/MCP/parser/worker imports, reusable model integrity, and selected
Torch/CUDA behavior. Only a fully validated publication makes the exact legacy
source a cleanup candidate; a separate explicit cleanup confirmation is still
required. Failure preserves the legacy environment and
isolates the failed staging directory rather than leaving an active partial
`.venv`.

Houdini's embedded Python/hython remains selected-installation infrastructure,
and the FXHoudiniMCP fallback retains its independent compatibility environment.
Neither is activated from, merged into, or installed into `.venv`.

`environment-status` is the shared WPF/CLI environment probe. It reports the
resolved venv and Python paths, Python version/bitness, project-local uv, parser
version, PyTorch/CUDA build, actual `torch.cuda.is_available()` result, GPU name,
installed models, active profile, free disk space, and redacted proxy-presence
flags. The aggregate `status` action adds managed-source and index state, and
`index-status` exposes the dedicated resumable-index view. A missing model or
PyTorch means FTS5 and remains non-blocking for Houdini.

Environment repair is one shared plan. If the probe finds a verified installed
profile, `environment-repair` invokes the same installer with that profile and
the selected device, restoring parser, PyTorch, and the embedding worker in one
operation while reusing the model payload. If no profile is installed, it
repairs only the parser baseline and leaves FTS5 usable rather than downloading
a model implicitly. First-time optional embedding installation remains an
explicit action through `scripts/launcher/Install-HiaEmbedding.ps1`.
`auto` does not mark a CUDA configuration usable until the installed
project-local PyTorch probe succeeds and returns a device name. NVIDIA
driver/runtime mismatch therefore falls back to CPU embedding or FTS5; AMD,
integrated-graphics, no-discrete-GPU, and CPU-only hosts never become false
CUDA-ready states. CPU embedding trades indexing/query speed for semantic
retrieval; FTS5 remains the smallest, download-free lexical baseline.

Default paths are re-derived from the current script/repository location after a
project move. Profile and device choices are portable project settings rather
than captured development-machine paths. Install and repair use only the managed
Python under the current project's `.runtime`; an external Python can be selected
only as an explicit advanced Bridge runtime override. Advanced model-directory
overrides must resolve under the current project's `.runtime/models`; a
project-external absolute model path is rejected.

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
| Managed Python installations | `.runtime/toolchains/python` |
| Bridge/local-knowledge/embedding virtual environment | `.venv` |
| Canonical runtime Python | `.venv/Scripts/python.exe` |
| Windows activation script | `.venv/Scripts/Activate.ps1` |
| Legacy migration source only | `.runtime/toolchains/hia-embedding/venv` |
| Worker source | `services/hia_mcp_v2/embedding_worker` |
| Models root | `.runtime/models/qwen3-embedding` |
| Hugging Face cache | `.runtime/cache/embedding/huggingface` |
| Transformers cache | `.runtime/cache/embedding/transformers` |
| Torch cache | `.runtime/cache/embedding/torch` |
| Temporary files | `.runtime/cache/embedding/tmp` |
| SQLite database | `.runtime/knowledge/knowledge.sqlite3` |

The worker distribution/module/console entry point is `hia_embedding_worker`; the module launch is `python -m hia_embedding_worker`, and its protocol is `hia-embedding-stdio/1`.

Reserved launcher settings in `.runtime/launcher/settings.json` are `embedding_profile`, `embedding_dimension`, and `embedding_device`. Their automatic defaults contain no drive letter, username, or development-machine absolute model path. The managed Bridge path is recomputed from the current project root. The launcher is expected to export:

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

The distributable launcher is a thin self-contained .NET 8 WPF WinExe host. It derives the project root from `AppContext.BaseDirectory`, verifies project markers, and starts the existing PowerShell/WPF launcher; it does not duplicate discovery, preflight, repair, settings, reporting, or lifecycle rules. Local-knowledge and cache controls call `scripts/hia-knowledge.ps1` and `scripts/hia-cache.ps1`, so users who omit WPF keep the same operations. The managed payload is single-file while native WPF components remain as five sidecars beside the EXE, avoiding extraction outside the project. The project-local SDK, CLI home, NuGet caches, build intermediates, and publish directory all live below ignored `.runtime`; no global SDK, PATH, registry, or AppData mutation is required. `scripts/hia-launcher.ps1` remains the direct debugging entry and `scripts/launch-houdini.ps1` remains the direct lifecycle entry.

The command-line contract is intentionally the same composition rather than a
parallel implementation. `hia-launcher.ps1 -CheckOnly -Json` exposes discovery,
diagnostics, preflight and report paths; `launch-houdini.ps1` remains the single
GUI and CLI lifecycle entry; and `-PrintCodexLoginCommand` calls the same Core
helper used by the WPF clipboard action. Codex bootstrap, environment/model
repair, knowledge/index operations, and cache cleanup are the same
project-relative scripts invoked by WPF. Foreground index interruption is
bounded to that CLI process and returns 130; there is no launcher-owned
background stop service.

The launcher uses the standard Windows window frame, not another Agent or service. Its module derives the project root from the launcher location, enumerates Houdini without a version allowlist, requires explicit selection when more than one installation exists, and binds port probes only to `127.0.0.1`. `hia_v2` is the default; `fxhoudini` is an explicit fallback. `scripts/launch-houdini.ps1` remains the only lifecycle entry and injects only the selected backend's paths and environment. HIA V2 uses its own random port/token, `HIA_MCP_V2_*`, `/hia-mcp-v2/v1/*`, and `.runtime/hia-mcp-v2`; fallback keeps the locked third-party runtime without sharing those names.

Final delivery output is a separate launcher setting, not an internal cache or a Panel/session-history feature. A non-empty `render_output_dir` may point to any ordinary writable local absolute directory outside Windows and Houdini installations; an empty value resolves to project-local `.runtime/cache`. The WPF launcher validates and places only the resolved value in the child lifecycle process environment as `HIA_RENDER_OUTPUT_DIR`. The lifecycle validates it again, creates it without clearing contents, and injects it independently into the Bridge and Houdini child environments. Existing `HIA_CACHE_DIR` remains project-local and continues to own only internal screenshots, previews, and short-lived cache data; the variables are never assigned from one another.

Portable project configuration uses paths relative to the project or `$HIA_PROJECT_ROOT`, which the lifecycle script supplies only to child processes. The explicit final-output delivery directory is the sole launcher setting that may intentionally be an absolute path outside the project; leaving it empty retains fully portable project-local behavior. Safe repair is deliberately limited to project-local runtime directories and those locked relative-path fields.
