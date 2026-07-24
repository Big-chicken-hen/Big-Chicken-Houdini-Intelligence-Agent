# Big-Chicken Houdini Intelligence Agent

[![Tests](https://github.com/Big-chicken-hen/Big-Chicken-Houdini-Intelligence-Agent/actions/workflows/tests.yml/badge.svg)](https://github.com/Big-chicken-hen/Big-Chicken-Houdini-Intelligence-Agent/actions/workflows/tests.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Preview](https://img.shields.io/badge/release-v0.1.1--preview-orange.svg)](https://github.com/Big-chicken-hen/Big-Chicken-Houdini-Intelligence-Agent/releases/tag/v0.1.1-preview)

Build and revise editable Houdini node networks with Codex, natural language, reference images, and live scene context.

Big-Chicken Houdini Intelligence Agent is a Codex-powered Houdini plugin. It embeds a conversation panel inside Houdini while the compatible local runtime supplies the connection to the scene that is already open. Codex remains the reasoning system. The default HIA MCP V2 backend lets Codex inspect the scene, search the installed node catalog, execute batched HOM Python on Houdini's UI thread, validate results, and capture the viewport when visual feedback is needed.

> **Preview software:** Big-Chicken Houdini Intelligence Agent can run Codex-generated HOM/Python that modifies the current HIP. Save or version important work before use and review the result in Houdini.

## Highlights

- Create, inspect, connect, modify, materialize, animate, and validate editable Houdini networks from natural language.
- Attach local reference images or clipboard screenshots and optionally include the current node selection.
- Continue refining an active Turn without starting a separate conversation.
- Use Goal focus mode for long, multi-step work and launcher-assisted recovery after a confirmed Houdini crash.
- Search the live Houdini node catalog instead of relying on a fixed node whitelist.
- Keep screenshots, previews, attachments, diagnostics, and session data under the local project runtime directory.
- Choose a separate delivery directory for final renders, USD, exports, or simulation caches.

## Requirements and compatibility

| Component | Preview status |
|---|---|
| Operating system | Windows x64 only |
| Houdini | **21.0.440 with Python 3.11 is the tested configuration** |
| Other Houdini versions | The launcher can discover them, but they are not yet claimed as verified |
| Bridge Python | CPython 3.10 or newer; a normal python.org per-user install is supported |
| Codex | Project-pinned Codex CLI/app-server 0.144.3 |
| Account and network | A valid Codex/ChatGPT sign-in and access to the OpenAI service |
| Default backend | HIA MCP V2 |

Houdini and Bridge Python must be installed separately, and Houdini must be licensed. The Preview ZIP launcher can install the pinned Codex runtime inside its extracted directory.

## Recommended download: Preview ZIP

Download
[`Big-Chicken-Houdini-Intelligence-Agent-v0.1.1-preview-win-x64.zip`](https://github.com/Big-chicken-hen/Big-Chicken-Houdini-Intelligence-Agent/releases/download/v0.1.1-preview/Big-Chicken-Houdini-Intelligence-Agent-v0.1.1-preview-win-x64.zip)
and verify it with the adjacent
[`SHA256SUMS.txt`](https://github.com/Big-chicken-hen/Big-Chicken-Houdini-Intelligence-Agent/releases/download/v0.1.1-preview/SHA256SUMS.txt).

For most users, the ZIP is the simplest installation path:

1. Install and license Houdini, then install CPython 3.10 or newer for the Bridge. A regular 64-bit python.org **per-user** installation is supported; an administrator or system-wide Python installation is not required.
2. Use **Extract All** to unpack the complete ZIP into an ordinary writable directory. Do not run the launcher from inside the ZIP, and do not move `BigChickenLauncher.exe` away from its five adjacent DLL files.
3. Run `BigChickenLauncher.exe` from the extracted package root. The launcher is not currently code-signed, so Windows SmartScreen may show an unknown-publisher warning. Continue with **More info → Run anyway** only when the file came from this official Release and its SHA-256 matches `SHA256SUMS.txt`.
4. Select the Houdini executable, the Bridge `python.exe`, and **HIA MCP V2**, then run or refresh the checks.
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
$env:CODEX_HOME = (Join-Path (Get-Location) '.runtime\codex-home')
& '.\.runtime\toolchains\codex\0.144.3\codex.exe' login --device-auth
powershell -NoProfile -Sta -ExecutionPolicy Bypass -File .\scripts\hia-launcher.ps1
```

The bootstrap verifies the pinned archive SHA-256 and OpenAI Authenticode signatures and writes only below `.runtime`. Build commands later in this README are for source maintainers; they are not included in the Preview ZIP or required for normal use.

## Basic use

You can describe the result directly:

```text
在当前场景中新建一个可编辑的程序化资产。保留现有节点，不保存 HIP。
```

You do not need to name an MCP tool, a node whitelist, or an output directory. Current-scene work remains in the currently open Houdini session. Native `hython` is used only when the request explicitly asks for offline work, a separate HIP, batch processing, independent verification, a long simulation, or background rendering.

Reference images and the current selection can be included from the composer. While Codex is working, **追加指令** steers the active Turn. Starting a different task in a new Thread keeps the context smaller and easier to follow.

### Goal focus mode

Goal focus mode is optional. Enter a concise outcome, save it to the current Thread, and enable **目标专注模式** when Codex should continue a long task across multiple Turns. Pressing Stop pauses automatic continuation. Normal launcher starts still open with no conversation selected; only a launcher-confirmed crash recovery may restore the exact bound Thread and continue its active Goal.

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

Big-Chicken Houdini Intelligence Agent's local HTTP services bind to `127.0.0.1` and use fresh random credentials for each launcher session. Codex is the only AI component. Big-Chicken Houdini Intelligence Agent does not add another model, planner, RAG service, or screen-control system.

The optional FXHoudiniMCP 1.3.0 integration is a separately prepared compatibility fallback. It is not active alongside HIA MCP V2 and is not included in the source checkout or public Preview package.

## Local data and final outputs

Big-Chicken Houdini Intelligence Agent keeps internal runtime data beneath `<project-root>\.runtime\`:

- `cache\screenshots`, `cache\previews`, and `cache\tmp`
- `attachments`
- `diagnostics`
- launcher settings, Codex Home, and local toolchains

`.runtime` is ignored by Git and must never be included in a bug report or Release archive. Big-Chicken Houdini Intelligence Agent does not upload diagnostics or add its own telemetry. Codex itself communicates with the OpenAI service to provide the requested model response.

Final renders, images, video, USD, exports, and simulation caches may use the explicit output directory selected in the launcher. The manual screenshot-cache cleanup only targets Big-Chicken-generated top-level PNG files in the derived project screenshot directory; it does not recurse or clean the final output directory.

See [Runtime diagnostics](docs/DIAGNOSTICS.md) for report contents and redaction behavior.

## Known Preview limitations

- Only Windows x64 and Houdini 21.0.440/Python 3.11 have completed the current real-GUI acceptance path.
- Once a long HOM call has entered Houdini's UI thread, Stop can stop waiting and freeze Panel output but cannot safely force-kill that Python operation.
- Goal continuation and crash recovery are Preview features. Recovery requires a launcher-confirmed Houdini crash and a valid Thread/Goal binding.
- The public package does not include Houdini, Codex credentials, user HIP files, or the optional FXHoudiniMCP runtime.
- The launcher executable is not currently code-signed, so Windows may display a SmartScreen warning.
- Big-Chicken Houdini Intelligence Agent can modify the active scene. It does not automatically save the HIP before every change.

## Documentation

- [Installation and first run](docs/INSTALLATION.md)
- [Architecture](docs/ARCHITECTURE.md)
- [HIA MCP V2](docs/HIA-MCP-V2.md)
- [Runtime diagnostics](docs/DIAGNOSTICS.md)
- [Security policy](SECURITY.md)
- [Third-party notices](THIRD_PARTY_NOTICES.md)
- [Changelog](CHANGELOG.md)

## Development verification

Run the standard-library test suite from the repository root:

```powershell
python -m unittest discover -s tests -t . -v
```

Build the self-contained launcher locally:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\build-launcher.ps1 -InstallLocalSdk
.\.runtime\dist\launcher\BigChickenLauncher.exe
```

The build downloads the .NET 8 SDK only into the ignored project runtime, verifies the Microsoft archive, and does not install a global SDK. Public launcher builds use the built-in dark gradient and do not require external artwork.

Build the strict public Preview archive:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\build-release.ps1 `
  -Version 0.1.1-preview `
  -InstallLocalSdk
```

The archive and `SHA256SUMS.txt` are written to `.runtime\release`. The build
uses an explicit runtime allowlist, rebuilds the launcher, and runs
`scripts\check-public-release.py` before publishing the checksum. It excludes
project runtime state, credentials, tests, HIP files, renders, historical Gate
reports, and unlicensed artwork.

## Project status

This is an independent, unofficial project. It is not affiliated with, endorsed by, or sponsored by SideFX or OpenAI. Houdini, SideFX, OpenAI, Codex, and other product names belong to their respective owners.

## License

Big-Chicken Houdini Intelligence Agent is licensed under the [Apache License 2.0](LICENSE). Third-party components and interoperability targets retain their own licenses; see [Third-party notices](THIRD_PARTY_NOTICES.md).
