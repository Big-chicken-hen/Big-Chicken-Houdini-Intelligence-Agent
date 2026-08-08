# Big-Chicken Houdini Intelligence Agent

[![Tests](https://github.com/Big-chicken-hen/Big-Chicken-Houdini-Intelligence-Agent/actions/workflows/tests.yml/badge.svg)](https://github.com/Big-chicken-hen/Big-Chicken-Houdini-Intelligence-Agent/actions/workflows/tests.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Published Preview](https://img.shields.io/badge/published-v0.1.1--preview-orange.svg)](https://github.com/Big-chicken-hen/Big-Chicken-Houdini-Intelligence-Agent/releases/tag/v0.1.1-preview)

Build and revise editable Houdini node networks with Codex, natural language, reference images, and live scene context.

Big-Chicken Houdini Intelligence Agent is a Codex-powered Houdini plugin. It embeds a conversation panel inside Houdini while the compatible local runtime supplies the connection to the scene that is already open. Codex remains the reasoning system. The default HIA MCP V2 backend lets Codex inspect the scene, search the installed node catalog, execute batched HOM Python on Houdini's UI thread, validate results, and capture the viewport when visual feedback is needed.

The workflow badge above reports the repository's default branch. It is not
evidence for an unmerged feature branch; use that pull request's own Checks view
for branch-specific CI results.

> **Preview software:** Big-Chicken Houdini Intelligence Agent can run Codex-generated HOM/Python that modifies the current HIP. Save or version important work before use and review the result in Houdini.

## Highlights

- Create, inspect, connect, modify, materialize, animate, and validate editable Houdini networks from natural language.
- Attach local reference images or clipboard screenshots and optionally include the current node selection.
- Continue refining an active Turn without starting a separate conversation.
- Use Goal focus mode for long, multi-step work without automatic scene recovery.
- Search the live Houdini node catalog instead of relying on a fixed node whitelist.
- Search local help and explicitly recorded project memory through SQLite FTS5, with an optional project-local Qwen text encoder for hybrid retrieval.
- Keep internal data and HIA captures project-local under `.runtime`; HIA does not create scene checkpoints beside a HIP.
- Choose a separate delivery directory for final renders, USD, exports, or simulation caches.

## Requirements and compatibility

| Component | Preview status |
|---|---|
| Operating system | Windows x64 only |
| Houdini | **21.0.440 with Python 3.11 is the tested configuration** |
| Houdini 22 / Python 3.13 | A matching source UI-ready hook and CI compile/test lane are present; embedded Houdini acceptance is not yet verified |
| Other Houdini versions | The launcher may discover them, but they are not claimed as verified |
| Bridge and local knowledge Python | One project-managed CPython 3.10.11 virtual environment at `<project-root>\.venv` |
| Codex | Project-pinned Codex CLI/app-server 0.144.3 |
| Account and network | A valid Codex/ChatGPT sign-in and access to the OpenAI service |
| Default backend | HIA MCP V2 |

Houdini must be installed and licensed separately. The Preview ZIP can prepare the pinned Codex runtime, project-managed Python, local knowledge parser, and optional embedding runtime inside its extracted project directory. It never installs packages into global Python, Houdini Python, or the user site-packages directory.

## Published historical Preview ZIP

Download
[`Big-Chicken-Houdini-Intelligence-Agent-v0.1.1-preview-win-x64.zip`](https://github.com/Big-chicken-hen/Big-Chicken-Houdini-Intelligence-Agent/releases/download/v0.1.1-preview/Big-Chicken-Houdini-Intelligence-Agent-v0.1.1-preview-win-x64.zip)
and verify it with the adjacent
[`SHA256SUMS.txt`](https://github.com/Big-chicken-hen/Big-Chicken-Houdini-Intelligence-Agent/releases/download/v0.1.1-preview/SHA256SUMS.txt).

This is the published 2026-07-24 snapshot. It does not represent later source
changes and must not be reused as a candidate archive for a newer release.

For users reproducing that published snapshot, the ZIP is the simplest installation path:

1. Install and license Houdini. A separate global or PATH Python installation is not required for normal use.
2. Use **Extract All** to unpack the complete ZIP into an ordinary writable directory. Do not run the launcher from inside the ZIP, and do not move `BigChickenLauncher.exe` away from its five adjacent DLL files.
3. Run `BigChickenLauncher.exe` from the extracted package root. The launcher is not currently code-signed, so Windows SmartScreen may show an unknown-publisher warning. Continue with **More info → Run anyway** only when the file came from this official Release and its SHA-256 matches `SHA256SUMS.txt`.
4. Select the Houdini executable and **HIA MCP V2**, keep the recommended project-managed Python mode, then run or refresh the checks. If the local knowledge environment is missing, use its project-local install/repair action.
5. If the action button says **安装/修复 Codex**, click it. The launcher downloads and verifies the pinned official Codex runtime only under the extracted package's `.runtime` directory.
6. After the checks refresh, if the action button says **复制登录命令**, click it, paste the copied command into PowerShell, run it, and complete the official device-login flow. Return to the launcher and click **重新扫描**.
7. When no red checks remain, click **Launch Houdini**. In Houdini, open **New Pane Tab Type → Python Panel → Big-Chicken Houdini Intelligence Agent**.
8. Confirm that the Panel reports Codex, Houdini, and HIA MCP V2 as available. Start with the read-only verification request in [Installation](docs/INSTALLATION.md) before editing an important HIP.

Only Windows x64 and Houdini 21.0.440 with Python 3.11 have completed the current real-GUI acceptance path. Other Houdini versions may be discovered by the launcher but are not yet claimed as verified.

See [Installation and first run](docs/INSTALLATION.md) for the expanded walkthrough and troubleshooting.

## Source checkout

Cloning the source is intended for development. From the project root, install the project-local Codex runtime, complete login, and start the PowerShell launcher:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\bootstrap-runtime.ps1
powershell -File .\scripts\hia-knowledge.ps1 environment-install
powershell -File .\scripts\hia-launcher.ps1 -PrintCodexLoginCommand
# Run the printed official device-login command once; the launcher never handles the credential.
powershell -NoProfile -Sta -ExecutionPolicy Bypass -File .\scripts\hia-launcher.ps1
```

The bootstrap verifies the pinned archive SHA-256 and OpenAI Authenticode signatures and writes only below `.runtime`. Build commands later in this README are for source maintainers; they are not included in the Preview ZIP or required for normal use.

Two cold-start paths use the same checks. With only Houdini installed, run the
Codex bootstrap, complete the explicit device login, then install the base
knowledge environment. If the pinned project-local Codex runtime is already
verified and logged in, skip its bootstrap and continue with
`hia-knowledge.ps1 environment-install`. A global/PATH Codex or Python is not
silently adopted as a project default. Missing optional embedding does not block
Houdini or explicit lexical FTS5 search. Explicit hybrid/vector requests fail clearly
until the selected project-local encoder and matching vector signature are available;
they never fall back to lexical.

Advanced users can run the same launcher flow without WPF:

```powershell
powershell -File .\scripts\hia-launcher.ps1 -CheckOnly -Json
powershell -File .\scripts\hia-launcher.ps1 -RepairSafeProject -CheckOnly -Json
powershell -File .\scripts\hia-launcher.ps1 -PrintCodexLoginCommand -Json
powershell -File .\scripts\launch-houdini.ps1 `
  -HoudiniExe "<full-path-to-houdini.exe>" `
  -BridgePython ".\.venv\Scripts\python.exe"
```

`-CheckOnly` performs discovery, diagnostics, preflight, and report generation;
its JSON includes all Houdini candidates and the report paths. Exit `0` means
green or non-blocking yellow, while exit `2` means launch-blocking red.
After a passing check, advanced users start the same sole lifecycle entry used
by WPF: `scripts\launch-houdini.ps1`. Its exit code is Houdini's confirmed exit
code, or nonzero when startup/lifecycle validation fails. The full GUI/CLI
mapping is in
[Installation](docs/INSTALLATION.md#gui-and-command-line-parity).

## Basic use

You can describe the result directly:

```text
在当前场景中新建一个可编辑的程序化资产。保留现有节点，不保存 HIP。
```

You do not need to name an MCP tool, a node whitelist, or an output directory. Current-scene work remains in the currently open Houdini session. Native `hython` is used only when the request explicitly asks for offline work, a separate HIP, batch processing, independent verification, a long simulation, or background rendering.

Reference images and the current selection can be included from the composer. While Codex is working, **追加指令** steers the active Turn. Starting a different task in a new Thread keeps the context smaller and easier to follow.

For a Houdini scene submission, **单个 AI** keeps the work in the current Thread.
**项目团队** creates one project with exactly five native Codex Threads: Supervisor,
Planning, Execution, Visual Review, and Technical Review. Execution is the only HIP
writer; the other four roles are read-only and have no HIA/HOM inventory. If their
selected model actually exposes native subagent tools, those four roles may use them
only for bounded read-only research or review. Execution never delegates, and project
correctness never depends on subagents being available.

The history list also supports permanent Thread deletion. Select one idle Thread and click **Delete** twice within five seconds. An active Turn must be stopped and allowed to finish first. Deleting the currently open Thread returns the Panel to a blank state and releases its local UI references; attachment files are not deleted.

### Goal focus mode

Goal focus mode is optional. Enter a concise outcome, save it to the current Thread, and enable **目标专注模式** when Codex should continue a long task across multiple Turns. Pressing Stop pauses automatic continuation. Launcher starts open with no conversation selected; HIA does not automatically restore a Thread, Goal, or recovery HIP.

## How execution works

```text
User
  → Big-Chicken Houdini Intelligence Agent Panel
  → local authenticated Bridge
  → Codex app-server
  → HIA MCP V2
  → Houdini UI-thread HOM / hou
  → current HIP
```

Big-Chicken Houdini Intelligence Agent's local HTTP services bind to `127.0.0.1` and use fresh random credentials for each launcher session. Codex remains the only reasoning and planning component. An optional local Qwen encoder can deterministically encode text for retrieval, but it does not generate answers, write memory, plan, or act on Houdini.

The optional FXHoudiniMCP 1.3.0 integration is a separately prepared compatibility fallback. It is not active alongside HIA MCP V2 and is not included in the source checkout or public Preview package.

## Local data and final outputs

Big-Chicken Houdini Intelligence Agent keeps internal runtime data beneath `<project-root>\.runtime\`:

- `cache\screenshots`, `cache\previews`, `cache\tmp`, and embedding/build caches
- `knowledge\sources` managed knowledge copies and `knowledge\knowledge.sqlite3`
- `models` and internal toolchains, including the managed CPython base and project-local uv
- `attachments`, `launcher-sessions`, `diagnostics`, launcher settings, and Codex Home

The one user-visible HIA environment is `<project-root>\.venv`; activate it on
Windows with `.\.venv\Scripts\Activate.ps1`. Its managed CPython base, uv
executable, download caches, models, and knowledge data remain under
`.runtime`. Neither `.venv` nor `.runtime` is included in Git or a Release
archive.

Viewport and flipbook captures always use
`<project-root>\.runtime\cache\screenshots\`. HIA does not create or
automatically open scene-recovery copies beside a HIP; opening and saving a HIP
remain explicit user actions. Attachments, previews, knowledge, models,
temporary data, and final deliverables remain in their documented locations.

`.runtime` is ignored by Git and must never be included in a bug report or Release archive. Big-Chicken Houdini Intelligence Agent does not upload diagnostics or add its own telemetry. Codex itself communicates with the OpenAI service to provide the requested model response.

Final renders, images, video, USD, exports, and simulation caches may use the explicit output directory selected in the launcher. They are user deliverables, not managed cache categories.

The WPF **清理缓存** page and the project-relative CLI use the same allowlisted implementation:

```powershell
powershell -File .\scripts\hia-cache.ps1 -Action list
powershell -File .\scripts\hia-cache.ps1 -Action list -Category screenshots
powershell -File .\scripts\hia-cache.ps1 -Action clear -Category screenshots -SnapshotHash "<hash-from-list>"
```

The fixed categories are `screenshots`, `previews`, `tmp`, `embedding-runtime`, `embedding-downloads`, and `dotnet`. `list` reports the exact resolved target and estimated bytes. `clear` requires an explicit category and the matching snapshot hash, then reports each category separately. It derives the project root from the script location and rejects reparse points, path escape, changed snapshots, and project-external targets. It never targets `.runtime\knowledge`, `.runtime\models`, `.runtime\toolchains`, `.runtime\attachments`, `.runtime\launcher-sessions`, Codex task data, HIP files, `.runtime\cache\renders`, `.runtime\cache\research`, or a user-selected final-output directory.

If any selected category contains an ordinary `.hip`, `.hiplc`, or `.hipnc` file, that category is blocked and the whole selected batch performs zero writes. A final-output directory may be the `.runtime\cache` root, but it cannot equal or sit below any clearable category root.

See [Runtime diagnostics](docs/DIAGNOSTICS.md) for report contents and redaction behavior.

## Optional local retrieval and project memory

HIA MCP V2 exposes 17 tools. Its capability catalog is generated from the same `TOOL_SPECS` registry used by `tools/list`, so tool names, domains, descriptions, parameters, and discovery aliases cannot drift into a second handwritten list. `hia_execute_hom` executes one direct HOM batch inside one ordinary Houdini Undo group; scene diffs, validation, and viewport capture remain explicit independent calls. Existing `hia_local_help_search` calls remain compatible and support lexical, vector, and hybrid retrieval: SQLite FTS5 provides lexical search once the corpus is initialized, while an explicitly installed local Qwen encoder provides vector matches. A new project requires one explicit refresh or CLI index initialization; a strictly read-only search never creates the database. Vector or hybrid requests fail clearly when the selected encoder cannot load instead of silently changing retrieval mode; searching never downloads a model or dependency.

`hia_local_help_search` defaults to compact output under a byte budget. A batch shares public retrieval/index state once instead of repeating it beside every query; each query has its own `next_offset`, and a top-level cursor is returned only when the unfinished query cursors agree. Full responses paginate by card: a byte-budget truncation can shorten the card tail but does not expose a within-card continuation cursor. Encoder runtime state and corpus-index state are separate, so an available model does not imply complete global semantic recall. While the corpus is partial, `ranking_scope=lexical_candidates` means vectors only rerank each query's lexical candidates; the single AND-to-OR relaxation helps long lexical queries but cannot provide zero-overlap global semantic recall. `refresh=false` makes the corpus index strictly read-only—no source scan, SQLite write, vector backlog fill, or vector-layer switch—but a first optional embedding-worker startup may still create its own project-local runtime cache. Use `refresh=true` for one explicit incremental refresh. Full vector completion remains a separate CLI operation.

The Release includes a versioned built-in official-workflow pack: its manifest, coverage matrix, source registry, and every declared original card. An explicit first bootstrap validates and copies that pack to `.runtime\knowledge\builtin\<pack-id>\<version>-<digest>`, then indexes every card's full workflow body, steps, important nodes and parameters, validation, troubleshooting, and all registered SideFX source URLs. Updating the built-in pack never overwrites managed user sources or explicit project memory. Compact results stay small; `response_format=full` reconstructs the selected full card within the request byte budget.

The only durable memory tool is `hia_project_memory`. It supports explicit `record`, `search`, `list`, `delete`, and `supersede` actions for `decision`, `preference`, `asset`, `lesson`, and `workflow` records. Nothing copies chat history or writes a summary automatically: Codex supplies the final durable text only when it deliberately invokes a write action.

The Panel's **Knowledge and Memory** tab exposes project memory in a compact, non-modal view. It can search and list records, show the type, summary, tags, scope, replacement state, and stable ID, and explicitly record, supersede, or delete one selected memory. Delete uses an exact stable ID and a second click; there is no delete-all action. These operations do not delete Threads, indexed knowledge sources, attachments, or project files.

The Panel calls the authenticated Bridge `/v1/project-memory` route, which forwards only the fixed `hia_project_memory` tool and returns a bounded display projection. This path works when the lifecycle is started directly with `scripts/launch-houdini.ps1`; the WPF launcher is not a dependency for memory management.

The same tab exposes local-knowledge status and explicit administration through the fixed Bridge `/v1/knowledge` route. It distinguishes environment readiness, lexical/vector availability, and index completion; shows the built-in pack, managed user-source count, and vector progress; and supports non-modal file/folder import, exact managed-copy deletion, repair, and resumable index build. The Bridge is only a bounded adapter over `scripts/hia-knowledge.ps1`: it does not open SQLite or copy parser, installer, or index logic into the Panel. This route also works with direct `scripts/launch-houdini.ps1`.

The public package includes original Big-Chicken workflow cards linked to
SideFX primary sources, together with a versioned manifest, coverage matrix,
and source registry. When Houdini is installed, HIA also indexes selected
text help archives directly from that local installation, including node, HOM,
VEX, Solaris, Pyro, Vellum, FLIP, PDG, modeling, animation, shading, rendering,
and version notes. SideFX documentation bodies and archives are never copied
into this repository or the release; only the original workflow cards and
their source/coverage metadata are distributed.

The stable model profiles are:

| Profile ID | Intended use | Repository size | Dimensions |
|---|---|---:|---:|
| `qwen3-embedding-0.6b` | Default | about 1.21 GB | default/max 1024 |
| `qwen3-embedding-8b` | Higher quality; BF16 shards | about 15.2 GB | default 1024, advanced MRL max 4096 |

Both official Qwen3 Embedding profiles are Apache-2.0, support a 32K context, 100+ languages, MRL dimensions, and query instructions. Only one model is loaded at a time. The 8B BF16 model is not guaranteed to fit or run reliably on a 16 GB GPU once runtime overhead is included; if the selected profile cannot load, vector/hybrid retrieval reports that error until the user explicitly selects another installed profile or lexical mode. No quantization framework, reranker, or third model is introduced.

The canonical Bridge/local-knowledge/encoder environment is the project-root
`.venv`; models, caches, managed CPython base, uv, SQLite database, indexed
bodies, and vectors remain below `.runtime`. Both locations are excluded from
source and Release archives. The launcher consumes the stable contract for
profile selection, project-local installation/repair, preflight, and
child-process environment; neither profile is bundled or presumed installed.
The settings, environment, directory, health, degradation, and repair contract
is documented in [Architecture](docs/ARCHITECTURE.md) and defined by
`src/hia_core/embedding_contract.py`.

The local knowledge environment, managed user sources, and index can be administered without WPF, the Panel, or Houdini. From the project root, use the stable project-relative CLI. If `.venv` is missing, first run `powershell -File .\scripts\hia-knowledge.ps1 environment-install`:

```powershell
.\.venv\Scripts\python.exe -B .\houdini_package\python_libs\hia_mcp_runtime\knowledge_index_cli.py --project-root . --format jsonl bootstrap
powershell -File .\scripts\hia-knowledge.ps1 status
powershell -File .\scripts\hia-knowledge.ps1 environment-status
powershell -File .\scripts\hia-knowledge.ps1 environment-install
powershell -File .\scripts\hia-knowledge.ps1 environment-repair
powershell -File .\scripts\hia-knowledge.ps1 import-file -Path "<path-to-document>"
powershell -File .\scripts\hia-knowledge.ps1 import-folder -Path "<path-to-folder>"
powershell -File .\scripts\hia-knowledge.ps1 list
powershell -File .\scripts\hia-knowledge.ps1 delete -SourceId "<source-id>"
powershell -File .\scripts\hia-knowledge.ps1 rescan
powershell -File .\scripts\hia-knowledge.ps1 index-status
powershell -File .\scripts\hia-knowledge.ps1 index-build -BatchSize 32
```

`bootstrap` is the launcher-independent, offline first-run contract. It installs the shipped pack and builds its FTS5 layer; `index-build` can then resume optional vector batches. `status` remains strictly read-only: if no database exists it reports `not_initialized` without creating `.runtime`, WAL files, or schema migrations.

The WPF local-knowledge page and the Houdini Panel's **Knowledge and Memory** tab call this same CLI; neither owns a second importer, source registry, parser, installer, or indexer. They expose file/folder import, a managed-source list with origin, format, size, and index state, exact delete-selected, repair, and build/continue-index actions. Users who start the lifecycle directly through `scripts\launch-houdini.ps1` retain the Panel and CLI paths without WPF. The interfaces support only Markdown, TXT, HTML/HTM, SRT, VTT, and text-based PDF in this first version; they do not claim video ingestion, scanned-document OCR, or image OCR. Imported files are managed copies under `.runtime\knowledge\sources`. Deleting one managed copy never deletes or edits its original file. Folder import applies the same format allowlist.

`environment-status` reports the resolved venv path, Python version and bitness, project-local uv, `pypdf`, PyTorch version, CUDA build/availability and GPU name, installed model profiles, free-space hint, and proxy presence with values redacted. `status` adds managed-source and index state, while `index-status` returns the dedicated index view. WPF groups these facts into environment, parser, model, and index status. When the environment is missing, legacy, unsafe, or incomplete, the page shows the detected reason and a one-click install/repair action. The command runs asynchronously, exposes its current stage and project-local log, and becomes a retry action after failure; GUI users do not need to copy a PowerShell command. The full absolute paths remain available in structured output and WPF tooltips/reports even when the visible label is shortened.

`environment-install` and `environment-repair` use one shared install plan. They
always repair the project-managed CPython base, the single
`<project-root>\.venv`, project-local uv, and `pypdf`. When a verified profile is
already installed, the same one-click repair also restores its
PyTorch/embedding worker for the selected CPU/CUDA device and reuses the
existing model payload; the user is not sent to a second repair control. With
no installed profile, the action prepares only the parser baseline and does not
silently download a Qwen model.

An old `.runtime\toolchains\hia-embedding\venv` is recognized only as a legacy
migration source. Repair rebuilds a staged environment from the managed CPython
instead of moving or rewriting the old venv. An existing top-level `.venv`
without the HIA managed marker is refused rather than overwritten. The staged
environment must pass the Python 3.10.11/x64/no-user-site, prefix/base-prefix,
Bridge/MCP/parser/worker import, model, and selected Torch/CUDA checks before it
is atomically published and checked again. Only then is the exact legacy
source reported as a cleanup candidate; it is never removed without a separate
explicit confirmation. Failure preserves it and leaves no half-published `.venv`.
FTS5 remains available and Houdini startup is not blocked throughout.

Optional embedding remains an explicit, user-initiated action. The WPF button invokes the same project-relative installer that non-WPF users can run:

```powershell
$projectRoot = (Resolve-Path -LiteralPath .).Path
powershell -File .\scripts\launcher\Install-HiaEmbedding.ps1 `
  -ProjectRoot $projectRoot `
  -Profile qwen3-embedding-0.6b `
  -Device auto
```

The model selector and install log show the official Hugging Face model ID,
revision, target directory, and official file size (about 1.21 GB for 0.6B or
15.2 GB for 8B). Hugging Face and uv caches stay under `.runtime`; retrying an
interrupted install reuses those project-local caches. Python packages are
installed only into the project-root `.venv`. No model is downloaded on import,
search, or ordinary Houdini launch.

Use `-Device cpu` on AMD, integrated-graphics, no-discrete-GPU, or deliberately
CPU-only systems. The command prepares and uses only the project-managed Python
and `.venv`. User site-packages are disabled. Houdini's embedded Python and the
separate FXHoudiniMCP compatibility environment are never activated, merged, or
modified by this workflow.

The launcher exposes `Automatic`, `NVIDIA GPU (CUDA)`, and `CPU` choices beside
the embedding model. `auto` may attempt a CUDA setup only for an NVIDIA candidate,
but it accepts CUDA only after the project venv's actual
`torch.cuda.is_available()` probe succeeds and returns a device name. A missing
driver, incompatible CUDA runtime, AMD GPU, or absent discrete GPU therefore never
becomes a false GPU-ready state. A user-selected CPU embedding is slower for large
backfills but preserves semantic retrieval. Otherwise vector/hybrid requests report
the unavailable encoder; lexical retrieval remains available only when explicitly
selected. Encoder failure does not block basic Houdini launch.

Automatic settings are resolved from the current project location rather than a
developer-machine drive or username. Moving the project causes paths to be
resolved again. Advanced model-directory overrides must remain beneath the current
project's `.runtime\models` boundary; project-external absolute model paths are
rejected rather than saved as defaults.

Official sources: [Qwen3-Embedding-0.6B model card](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B), [0.6B files](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B/tree/main), [Qwen3-Embedding-8B model card](https://huggingface.co/Qwen/Qwen3-Embedding-8B), and [8B files](https://huggingface.co/Qwen/Qwen3-Embedding-8B/tree/main).

## Known Preview limitations

- Only Windows x64 and Houdini 21.0.440/Python 3.11 have completed the current real-GUI acceptance path.
- Once a long HOM call has entered Houdini's UI thread, Stop can stop waiting and freeze Panel output but cannot safely force-kill that Python operation.
- Goal continuation is a Preview feature. HIA does not automatically create, open, or resume scene-recovery copies.
- The public package does not include Houdini, Codex credentials, user HIP files, the optional FXHoudiniMCP runtime, Qwen model weights, an embedding virtual environment, the knowledge database, indexed bodies, or vectors.
- The launcher executable is not currently code-signed, so Windows may display a SmartScreen warning.
- Big-Chicken Houdini Intelligence Agent can modify the active scene. It does not automatically save the HIP before every change.

## Documentation

- [Installation and first run](docs/INSTALLATION.md)
- [Architecture](docs/ARCHITECTURE.md)
- [HIA MCP V2](docs/HIA-MCP-V2.md)
- [Runtime diagnostics](docs/DIAGNOSTICS.md)
- [Project-team live acceptance](docs/PROJECT_TEAM_LIVE_ACCEPTANCE.md)
- [Security policy](SECURITY.md)
- [Third-party notices](THIRD_PARTY_NOTICES.md)
- [Changelog](CHANGELOG.md)

## Development verification

### Evidence levels

These labels are deliberately not interchangeable:

| Label | What it proves |
|---|---|
| `unit-tested` | Deterministic Python tests passed in the named interpreter; no real app-server or Houdini process is implied |
| `app-server smoke-tested` | A real Codex app-server exercised native Thread/Turn provisioning and cleanup; no Houdini scene integration is implied unless separately recorded |
| `standalone Qt-tested` | The Panel ran under a standalone Qt harness; embedding, Houdini callbacks, and real scene writes are not implied |
| `embedded Houdini-tested` | The exact Houdini/Python build completed the documented live Panel, HIA/HOM, evidence, review, repair, recovery, and path cases |
| `unverified` | The named layer or target has not produced the required direct evidence |

The current branch's status must be reported from its own test output and pull
request checks, not inferred from the default-branch badge. Houdini 22/Python
3.13 remains embedded-unverified until it completes
[Project-team live acceptance](docs/PROJECT_TEAM_LIVE_ACCEPTANCE.md).

Run the standard-library test suite from the repository root:

```powershell
python -m unittest discover -s tests -t . -v
```

Build the self-contained launcher locally:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\build-launcher.ps1 -InstallLocalSdk
.\.runtime\dist\launcher\BigChickenLauncher.exe
```

The build downloads the .NET 8 SDK only into the ignored project runtime, verifies the Microsoft archive, and does not install a global SDK. Public launcher builds include the project launcher illustration at `assets\launcher\launcher-hero.png`, so a fresh clone or Release archive shows the same startup artwork without relying on `.runtime`.

Build the strict public Preview archive:

```powershell
$ReleaseVersion = '<approved-preview-version>'
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\build-release.ps1 `
  -Version $ReleaseVersion `
  -PreflightOnly
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\build-release.ps1 `
  -Version $ReleaseVersion `
  -InstallLocalSdk
```

The archive and version-bound `SHA256SUMS-v<version>.txt` are written to
`.runtime\release`. The build
uses an explicit runtime allowlist, rebuilds the launcher, and runs
`scripts\check-public-release.py` before publishing the checksum. It excludes
project runtime state, credentials, tests, HIP files, renders, historical Gate
reports, and unlicensed artwork. The project-owned launcher illustration is
included explicitly. `-PreflightOnly` is read-only: it requires the canonical
project `.venv`, rejects missing or untracked release/build inputs, and verifies
the built-in knowledge manifests before the .NET build or archive staging
begins. The final archive scan also rejects the current checkout's absolute path.

## Project status

This is an independent, unofficial project. It is not affiliated with, endorsed by, or sponsored by SideFX or OpenAI. Houdini, SideFX, OpenAI, Codex, and other product names belong to their respective owners.

## License

Big-Chicken Houdini Intelligence Agent is licensed under the [Apache License 2.0](LICENSE). Third-party components and interoperability targets retain their own licenses; see [Third-party notices](THIRD_PARTY_NOTICES.md).
