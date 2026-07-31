# Installation and first run

Big-Chicken Houdini Intelligence Agent is currently a Windows x64 Preview for Houdini. The launcher discovers the selected Houdini installation dynamically rather than pinning a Houdini major version. The source package includes UI startup adapters for Python 3.10, 3.11, and 3.13, covering Houdini 22's standard Python 3.13 build and its separate Python 3.11 build. Houdini 21.0.440/Python 3.11 remains the configuration with completed live-GUI evidence on this machine. The Bridge/local-knowledge environment uses project-managed CPython 3.10.11 and Codex 0.144.3. The published v0.1.1 Preview ZIP is the historical 2026-07-24 snapshot and does not represent later source changes. Use that ZIP only to reproduce its published version; use a source checkout to inspect current development until a newer Preview is published.

## 1. Download and fully extract the historical Preview ZIP

Download the
[`Big-Chicken-Houdini-Intelligence-Agent-v0.1.1-preview-win-x64.zip`](https://github.com/Big-chicken-hen/Big-Chicken-Houdini-Intelligence-Agent/releases/download/v0.1.1-preview/Big-Chicken-Houdini-Intelligence-Agent-v0.1.1-preview-win-x64.zip)
and its adjacent
[`SHA256SUMS.txt`](https://github.com/Big-chicken-hen/Big-Chicken-Houdini-Intelligence-Agent/releases/download/v0.1.1-preview/SHA256SUMS.txt).
Do not reuse this archive or checksum as the candidate output for a later
release.
Use **Extract All** to unpack the complete archive into an ordinary writable local directory, for example:

```text
<writable-local-directory>\Big-Chicken-Houdini-Intelligence-Agent
```

The project does not require a fixed drive letter. Do not run it from inside the ZIP, and do not place it inside the Houdini installation directory or another protected system directory.

Run `BigChickenLauncher.exe` from the extracted package root. Do not move the
EXE away from its five adjacent launcher DLL files. The launcher is not currently
code-signed, so Windows SmartScreen may report an unknown publisher. Continue
with **More info → Run anyway** only if the ZIP came from the official Release
above and its SHA-256 matches `SHA256SUMS.txt`.

The build commands in the README are for source checkouts only; the public ZIP
intentionally omits those maintainer scripts.

## 2. Install Houdini

Install separately:

- SideFX Houdini. Installed builds are discovered dynamically; Houdini 21.0.440 is the currently live-verified build, while the bundled Python 3.13 adapter covers the standard Houdini 22 startup path without treating version metadata as a gate.
- A valid Codex/ChatGPT sign-in and network access to the OpenAI service.

Normal installation does not require global Python, a PATH Python, administrator
site-packages, or a package installed into Houdini. HIA prepares one managed
CPython base beneath the extracted project's `.runtime` directory and one
user-visible virtual environment at:

```text
<project-root>\.venv
```

The same `.venv` runs the Bridge, local document parsing, and optional embedding
worker. Activate it manually, when desired, with:

```powershell
.\.venv\Scripts\Activate.ps1
```

The project-local uv executable, CPython base, download caches, models, and
knowledge data remain internal under `.runtime`.

The base installation includes the PDF parser but not PyTorch or a Qwen model.
Those larger optional dependencies are installed only after the user explicitly
chooses embedding. Houdini's embedded Python/HOM runtime continues to follow the
selected Houdini installation and is never modified or redirected through
`.venv`. The separately installed FXHoudiniMCP fallback also keeps its own
environment; it is not merged into HIA's `.venv`.

Big-Chicken Houdini Intelligence Agent does not redistribute Houdini. Houdini
must already be installed and licensed. Other Houdini versions may be discovered,
but only Houdini 21.0.440 with Python 3.11 has completed the current real-GUI
acceptance path. Houdini 22's Python 3.13 and separate Python 3.11 startup
paths are bundled and are reported as unverified rather than rejected when no
matching live-GUI evidence is available.

## 3. Install or repair the project-local runtimes

Start `BigChickenLauncher.exe`, select the Houdini executable and **HIA MCP V2**,
and keep the recommended project-managed Python mode. Run or refresh the checks.
If the main action says **安装/修复 Codex**, click it. In **本地知识**, a missing,
legacy, unsafe, or incomplete environment shows the detected reason and an
**安装本地知识环境** or **修复本地知识环境** button. Click it once; the launcher
runs the same project-relative CLI asynchronously, shows the current stage and
project-local log, and offers a retry if verification fails. A WPF user does
not need to open PowerShell for this repair.

This normal first-run action:

- downloads the fixed official Codex 0.144.3 Windows x64 archive;
- verifies the pinned archive SHA-256;
- verifies the pinned SHA-256 and OpenAI Authenticode signer for all three executables;
- installs them only beneath `.runtime\toolchains\codex\0.144.3`;
- leaves global PATH, the registry, the Houdini installation, and user configuration unchanged.

The local-knowledge action always installs or repairs project-managed Python
3.10.11, project-local uv, the single `<project-root>\.venv`, and `pypdf`. When no verified model is
installed, it stops at that parser baseline and does not install PyTorch or
download a Qwen model. Failure never blocks basic Houdini launch: retrieval
remains available through SQLite FTS5.

The former `.runtime\toolchains\hia-embedding\venv` path is accepted only as a
legacy migration source. Explicit repair uses project-local managed CPython to
build a fresh staging venv under `.runtime`; it never moves or rewrites the old
venv in place. An existing top-level `.venv` without a valid HIA managed marker
is refused rather than taken over.

Before publishing, repair verifies Python 3.10.11 x64, disabled user
site-packages, project-local `sys.executable`/`sys.prefix`/base prefix,
Bridge/MCP/parser/worker imports, the reusable model payload, and the selected
PyTorch/CUDA runtime. It validates the published `.venv` again before the exact
legacy source can be reported as a cleanup candidate. Cleanup requires a
separate explicit confirmation; repair never removes it automatically. Any failure preserves the old
environment and does not leave a half-published `.venv`. The knowledge database,
model directories, and project-local uv/Hugging Face caches are not deleted.
The vector index is not started automatically.

### Command-line access without WPF

The launcher is the normal repair path. Users who deliberately run without WPF
have the same capabilities through project-relative commands:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\bootstrap-runtime.ps1
powershell -File .\scripts\hia-knowledge.ps1 environment-install
powershell -File .\scripts\hia-launcher.ps1 -PrintCodexLoginCommand
```

### Cold start from Houdini or Houdini plus Codex

The minimum supported starting point is a licensed Houdini installation. If the
project-pinned Codex runtime is missing or damaged, preflight reports
`codex.executable` as red and the GUI offers **安装/修复 Codex**. The equivalent
CLI is `scripts\bootstrap-runtime.ps1`; it downloads only the fixed official
archive, verifies its hash and signatures, and installs it below this project's
`.runtime`. It never borrows an unrelated PATH Codex as the runtime contract.
After that step, use `-PrintCodexLoginCommand` and complete the official device
login yourself. The launcher neither stores nor bypasses account credentials.

If the pinned project-local Codex runtime is already verified, that download is
skipped. Preflight then proceeds to the project-root `.venv`, Bridge/MCP imports,
the knowledge parser/index, and the optional selected embedding runtime. A fresh
user installs the parser baseline with `environment-install`; an existing
incomplete environment uses `environment-repair`. Optional model installation
is always a separate explicit click or CLI command. Missing CUDA or model files
degrade embedding to CPU or FTS5 and do not make a usable Houdini installation
look broken.

### Manual Codex placement for troubleshooting

The launcher action and bootstrap script are the normal first-run paths. Use the following manual steps only to diagnose a failed download or to prepare the runtime without running the bootstrap.

Obtain the official Windows x64 Codex 0.144.3 archive from the
[OpenAI Codex 0.144.3 release](https://github.com/openai/codex/releases/tag/rust-v0.144.3).
The reviewed Windows archive is `codex-x86_64-pc-windows-msvc.exe.zip`, with SHA-256:

```text
5490114d8684b30f91e6e6f7b1238b2544fa3b957e42c9836aa959e8f563c01f
```

Verify the downloaded archive before extracting it:

```powershell
Get-FileHash -Algorithm SHA256 .\codex-x86_64-pc-windows-msvc.exe.zip
```

After extraction, rename `codex-x86_64-pc-windows-msvc.exe` to `codex.exe` and verify:

```powershell
.\codex.exe --version
```

The result must report `0.144.3`. From the project root, create:

```text
.runtime\toolchains\codex\0.144.3\
```

Place all three official executables together:

```text
.runtime\toolchains\codex\0.144.3\codex.exe
.runtime\toolchains\codex\0.144.3\codex-command-runner.exe
.runtime\toolchains\codex\0.144.3\codex-windows-sandbox-setup.exe
```

Do not commit `.runtime`; it is the local state directory.

## 4. Complete the project-local Codex login

After Codex installation and the automatic check refresh, a missing login changes
the action button to **复制登录命令**. Click it, open PowerShell, paste and run
the copied command, and complete the official device-login flow in your browser.
The copied command contains paths but no credentials. Return to the launcher and
click **重新扫描** when login finishes.

If the clipboard action is unavailable, open PowerShell in the extracted package
root and run:

```powershell
$env:CODEX_HOME = (Join-Path (Get-Location) '.runtime\codex-home')
& '.\.runtime\toolchains\codex\0.144.3\codex.exe' login --device-auth
```

The access and refresh tokens remain managed by Codex under the ignored
project-local Codex Home. Never copy that directory into a Release archive or
issue attachment.

## 5. Select the environment and launch Houdini

Preview ZIP users should return to the already-open `BigChickenLauncher.exe`.
For a source checkout, start the PowerShell launcher:

```powershell
powershell -NoProfile -Sta -ExecutionPolicy Bypass -File .\scripts\hia-launcher.ps1
```

The launcher discovers Houdini installations, checks the project-managed
Bridge/local-knowledge environment, checks the Codex version and login, verifies
project imports and HIA MCP V2, and checks local runtime writes and loopback ports.

If more than one Houdini candidate exists, choose the exact executable instead of
asking the launcher to guess. **HIA MCP V2** is the recommended backend. An
external Python is available only as an explicit advanced Bridge override. The
normal install and repair path always prepares and uses the project-managed
Python and shared `.venv`.

Green checks are ready, yellow checks need attention but do not necessarily block launch, and red checks must be fixed before Houdini can start.

When no red checks remain, click **Launch Houdini**.

### Optional launcher EXE (source checkout only)

Build the thin self-contained WPF launcher:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\build-launcher.ps1 -InstallLocalSdk
.\.runtime\dist\launcher\BigChickenLauncher.exe
```

`-InstallLocalSdk` downloads and verifies a Microsoft .NET 8 SDK under `.runtime`; it does not install a global SDK. The published EXE and its native sidecars must remain together. Release archives include the project-owned launcher illustration at `assets\launcher\launcher-hero.png`. If that file is missing or cannot be decoded, the launcher falls back to its built-in dark gradient without blocking preflight or Houdini launch.

## Use local knowledge without WPF

The WPF **本地知识** page is a client of the same project-relative commands
available to users who run `scripts\launch-houdini.ps1` directly. It does not
contain an exclusive importer, source database, parser, or indexer. Open
PowerShell in the project root and run the required action:

```powershell
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
powershell -File .\scripts\hia-knowledge.ps1 assets capabilities
powershell -File .\scripts\hia-knowledge.ps1 assets repair
powershell -File .\scripts\hia-knowledge.ps1 assets import -Path "<path-to-document-or-media>"
powershell -File .\scripts\hia-knowledge.ps1 assets status -AssetId "<asset-id>"
powershell -File .\scripts\hia-knowledge.ps1 assets resume -AssetId "<asset-id>"
```

`index-build` commits bounded batches, so the same command continues missing or
changed chunks after interruption. Ordinary source import supports `.md`,
`.txt`, `.html`, `.htm`, `.srt`, `.vtt`, and text-based `.pdf`. The asset
pipeline adds deterministic CSV, DOCX, PPTX, XLSX, image OCR, scanned-PDF page
OCR, and local audio/video transcription adapters. Run `assets capabilities`
before import: it reports each adapter as available, dependency-missing, or
not-configured without downloading anything. `assets repair` is the explicit
project-local dependency/model/FFmpeg repair action; `assets import` creates
the managed asset and initial extraction checkpoint; `assets status` inspects
one asset; and `assets resume` continues bounded OCR/transcription work.
Extracted `user_document` and `user_transcript` fragments enter the same SQLite
FTS5 and optional Qwen vector index rather than a second knowledge store.

Current source-tree acceptance is intentionally explicit: the clean-clone
fixture reports RapidOCR/ONNX Runtime image OCR and scanned-PDF page OCR ready,
and a real Markdown asset reaches the shared FTS5/Qwen index. The
media-transcription code path is present, but this machine could not finish the
external faster-whisper model or FFmpeg downloads because both endpoints
returned `WinError 10060`; no real no-sidecar video-ASR acceptance is claimed
yet. Ordinary imports are managed under `.runtime\knowledge\sources`, asset
state stays under `.runtime\knowledge\assets`, and deletion never changes the
original selected file.

The WPF page maps these actions to **导入文件**, **导入文件夹**, managed-source
rows showing origin/format/size/index state, **删除所选**, **重新扫描**, and
**构建/继续索引**. The deletion prompt explicitly identifies the managed copy
and repeats that the original file is outside the deletion target.

`environment-status` is the canonical environment troubleshooting report. It
includes the full resolved venv/Python paths, Python version and bitness,
project-local uv, `pypdf`, PyTorch version, CUDA build/availability and GPU name,
model/profile status, free space, and redacted proxy-presence flags. `status`
adds managed-source and index state, while `index-status` returns the dedicated
index view. The WPF page uses these same probes, groups them as environment,
parser, model, and index status with repair/log actions, and may shorten a
visible path while retaining the complete path in its tooltip/report.

## GUI and command-line parity

WPF is a thin client over the same PowerShell module and project-relative
commands. GUI-only conveniences such as a file picker, clipboard copy, report
opening, and confirmation dialogs do not own discovery, installation, indexing,
cleanup, or lifecycle rules.

| Existing GUI capability | Equivalent project-root command | Output and exit contract |
| --- | --- | --- |
| Discover Houdini, diagnose, preflight, refresh status, write report | `powershell -File .\scripts\hia-launcher.ps1 -CheckOnly -Json` | Structured preflight JSON including candidates/report paths; `0` green/yellow, `2` red |
| Repair safe project-local directories/config | `powershell -File .\scripts\hia-launcher.ps1 -RepairSafeProject -CheckOnly -Json` | Same post-repair preflight JSON; `0` or `2` |
| Install/verify pinned Codex | `powershell -File .\scripts\bootstrap-runtime.ps1` | Clear stage text; `0` verified, nonzero failure |
| Obtain the official login command | `powershell -File .\scripts\hia-launcher.ps1 -PrintCodexLoginCommand -Json` | `hia-launcher-cli/1`; `0` success. Login itself remains interactive |
| Inspect/install/repair `.venv`, Python, uv and parser | `powershell -File .\scripts\hia-knowledge.ps1 environment-status`, `environment-install`, or `environment-repair` | `hia-knowledge-launcher-cli/1` JSON; `0` success/status, nonzero diagnosed failure |
| Install/verify selected Qwen model and CPU/CUDA worker | `powershell -File .\scripts\launcher\Install-HiaEmbedding.ps1 -ProjectRoot (Resolve-Path .) -Profile <profile> -Device <auto\|cuda\|cpu>` | Final JSON plus project-local log; `0` verified, `1` failure |
| Import/list/delete/rescan knowledge and build/status index | `scripts\hia-knowledge.ps1` actions shown above | JSON for source actions; index JSON/JSONL; `0` success, `130` interrupted inner index CLI |
| Preview/clear managed cache | `powershell -File .\scripts\hia-cache.ps1 -Action <list\|clear> ...` | `hia-cache-json/1`; `0` success, `2` command error, `3` safety block, `4` stale snapshot, `5` partial file failure |
| Start Houdini after passing preflight | `powershell -File .\scripts\launch-houdini.ps1 -HoudiniExe "<path>" -BridgePython ".\.venv\Scripts\python.exe"` | The same sole lifecycle entry used by WPF; returns Houdini's confirmed exit code or nonzero startup/lifecycle failure |
| Cancel an active index build | Press `Ctrl+C` in its foreground index command | Inner index CLI returns `130`; committed batches remain and the next build continues |
| View/copy latest report | Read `report.json_path` / `report.log_path` from `-CheckOnly -Json` | No second report implementation |
| Release source preflight/build | `scripts\build-release.ps1 -PreflightOnly` / full `build-release.ps1` | Clear text; `0` pass, nonzero rejection. `check-public-release.py` uses `0` pass, `1` policy rejection, `2` invocation/I/O failure |

There is intentionally no separate background Houdini stop service. Close
Houdini normally; the foreground `scripts\launch-houdini.ps1` lifecycle then
returns and cleans up only its verified Bridge process. The WPF launcher does
not expose a different Houdini-stop action. Uninstall likewise needs no helper:
after closing Houdini and the launcher, delete this exact extracted project
directory as described below.

### Optional embedding without WPF

Install the base environment first. To opt into the default embedding model
without WPF, invoke the same installer used by the launcher:

```powershell
$projectRoot = (Resolve-Path -LiteralPath .).Path
powershell -File .\scripts\launcher\Install-HiaEmbedding.ps1 `
  -ProjectRoot $projectRoot `
  -Profile qwen3-embedding-0.6b `
  -Device auto
```

Before downloading, the GUI and install log identify the official Hugging Face
model ID/revision, the resolved project-local target, and the official model
file size (about 1.21 GB for 0.6B or 15.2 GB for 8B). The running GUI uses an
indeterminate stage indicator and an expandable project-local log rather than
inventing a byte percentage that the bounded child does not publish. A failed
or interrupted retry reuses the Hugging Face and uv caches under `.runtime`;
it does not restart from an unrelated global cache.

`auto` accepts CUDA only after the project-local PyTorch runtime actually reports
`torch.cuda.is_available()` and a GPU name. A visible NVIDIA adapter alone is not
success. Driver/runtime mismatch falls back to CPU embedding when usable, or to
FTS5; AMD, integrated-graphics, no-discrete-GPU, and deliberately CPU-only
systems can use `-Device cpu`. CPU embedding is slower for large indexing jobs,
while FTS5 is lexical-only and requires no model download. Neither mode blocks
Houdini.

Project-local uv is bootstrapped under `.runtime\toolchains` when missing and
never modifies system PATH. Proxy values are inherited only by bounded installer
children and are redacted from status/log output. Advanced custom model
directories must resolve beneath the current project's `.runtime\models`
boundary. Defaults never store a development-machine drive or username, and a
moved project resolves its toolchains and models again from the new root.

## Inspect or clear managed cache without WPF

List first, note the returned snapshot hash, then clear only an explicit
allowlisted category:

```powershell
powershell -File .\scripts\hia-cache.ps1 -Action list
powershell -File .\scripts\hia-cache.ps1 -Action list -Category screenshots
powershell -File .\scripts\hia-cache.ps1 -Action clear -Category screenshots -SnapshotHash "<hash-from-list>"
```

Available categories are `screenshots`, `previews`, `tmp`,
`embedding-runtime`, `embedding-downloads`, and `dotnet`. The list result shows
each exact resolved target and estimated size; clear reports each category's
outcome. The command derives the project root from its own location, rejects
reparse points, path escape, changed snapshots, and every project-external
target, and preserves category roots.

An ordinary `.hip`, `.hiplc`, or `.hipnc` file anywhere in a selected category
blocks that category and makes the entire selected clear batch perform zero
writes. The default final-output root `.runtime\cache` remains valid, but a
final-output path cannot equal or sit below any clearable category root.

There is no arbitrary-path or wildcard cleanup mode. The command never targets
`.runtime\knowledge`, `.runtime\models`, `.runtime\toolchains`,
`.runtime\attachments`, `.runtime\launcher-sessions`, Codex Home/Threads,
checkpoints, HIP files, `.runtime\cache\renders`,
`.runtime\cache\research`, or a user-selected final-output directory.

## 6. Open the Houdini Panel

After launching Houdini:

1. Open a pane's **New Pane Tab Type** menu.
2. Select **Python Panel**.
3. Select **Big-Chicken Houdini Intelligence Agent**.
4. Confirm the status row reports Codex and Houdini connected and `HIA MCP V2：可用`.
5. Create a new Thread and send a small inspection or creation request.

The default request targets the current HIP. Big-Chicken Houdini Intelligence Agent does not silently switch to a separate offline file when the live connection is unavailable.

## 7. Verify the installation

Try a read-only request first:

```text
读取当前 Houdini 场景和选择，只报告上下文，不修改场景。
```

Then try a disposable edit in a new HIP:

```text
在当前场景中新建一个可编辑的 Box 网络，不清空、不加载、不保存 HIP。
```

Confirm that the nodes appear in the active scene and remain editable.

## Common first-run problems

### Codex executable is missing

Rerun the bootstrap command first. It accepts an already verified installation, refuses to overwrite a partial or unexpected runtime, and reports the exact invalid path.

The launcher requires exactly one Codex executable whose directory version matches a contract under `contracts\codex-app-server`. For this Preview, the resulting path is:

```text
.runtime\toolchains\codex\0.144.3\codex.exe
```

### Codex login is missing or revoked

Repeat the project-local login command above. Do not use or copy another user's Codex Home.

### Project-local Python or local knowledge is red

In WPF, open **本地知识**, read the reason beside the environment status, and
click **安装本地知识环境**, **修复本地知识环境**, or the retry action shown there.
The expandable log identifies the failed managed-Python, uv, venv, parser,
network/proxy, disk-space, or verification stage. If a verified model is
already installed, the same repair also restores its PyTorch/worker runtime for
the current CPU/CUDA selection; no second repair click is required.

Without WPF, run the same underlying commands:

```powershell
powershell -File .\scripts\hia-knowledge.ps1 environment-status
powershell -File .\scripts\hia-knowledge.ps1 environment-repair
```

The repair action stays beneath the current project root and repairs the same
`.venv` used by the Bridge and local knowledge. Do not fix this with global `pip`,
user site-packages, a PATH change, or packages copied into the Houdini
installation. A failed or interrupted repair can be retried; the project-local
install lock prevents a second concurrent run, and FTS5 remains available while
the optional embedding runtime is incomplete.

### CUDA or a GPU is unavailable

Use the reported PyTorch/CUDA build, `cuda_available`, and GPU name rather than
the adapter name alone. A missing or incompatible NVIDIA driver is not accepted
as CUDA-ready. Choose CPU embedding or remain on FTS5; basic Houdini startup
continues normally on AMD and GPU-less systems.

### Environment download fails or disk space is low

`environment-status` reports available project-volume space and whether HTTP,
HTTPS, or no-proxy variables are present without exposing their values. Free
space first, verify the proxy outside HIA if required, then rerun the same
project-local repair. Read the project-local log path returned by the command;
do not switch to global `pip`.

### The project was moved

Close the launcher and Houdini, move the complete extracted directory, then run
`environment-status` again from the new root. Managed toolchain and model paths
and the top-level `.venv` are re-derived from the script location. A legacy venv
whose base Python still points outside the project is reported as
repair-required instead of silently borrowing that machine's installation.

### Cache snapshot changed

Run `hia-cache.ps1 -Action list` again and use the new hash. The clear command
intentionally refuses a stale preview rather than deleting files that appeared
or changed after confirmation.

### Several Houdini versions were found

Select the desired `houdini.exe` explicitly. Discovery is dynamic and is not itself a compatibility guarantee; Houdini 21.0.440 is the current live-verified build, and Houdini 22's Python 3.13/3.11 UI startup paths are included pending a live H22 run.

### HIA MCP V2 is unavailable

Close all Houdini instances started by an older Big-Chicken Launcher, start again through the current launcher, and rerun preflight. Do not start a separate MCP server manually.

### FXHoudiniMCP fallback is unavailable

The optional fallback is not included in the public source or Preview package. Use HIA MCP V2 unless you have separately installed the exact compatible fallback runtime.

## Uninstall

Close Houdini and the launcher, then remove the extracted project directory. Big-Chicken Houdini Intelligence Agent does not require a global service, global Python package, PATH change, or Houdini installation-directory modification. The ignored `.runtime` directory contains local settings, caches, attachments, diagnostics, Codex Home, and credentials; remove it only as part of deleting this exact project copy.
