# Installation and first run

Big-Chicken Houdini Intelligence Agent is currently a Windows x64 Preview for Houdini. The tested configuration is Houdini 21.0.440 with its Python 3.11 runtime, a separate CPython 3.10+ Bridge executable, and Codex 0.144.3. For most users, the published Preview ZIP is the recommended installation; clone the source only when you plan to develop or inspect the project.

## 1. Download and fully extract the Preview ZIP

Download the
[`Big-Chicken-Houdini-Intelligence-Agent-v0.1.1-preview-win-x64.zip`](https://github.com/Big-chicken-hen/Big-Chicken-Houdini-Intelligence-Agent/releases/download/v0.1.1-preview/Big-Chicken-Houdini-Intelligence-Agent-v0.1.1-preview-win-x64.zip)
and its adjacent
[`SHA256SUMS.txt`](https://github.com/Big-chicken-hen/Big-Chicken-Houdini-Intelligence-Agent/releases/download/v0.1.1-preview/SHA256SUMS.txt).
Use **Extract All** to unpack the complete archive into an ordinary writable local directory, for example:

```text
D:\Tools\Big-Chicken-Houdini-Intelligence-Agent
```

The project does not require a fixed drive letter. Do not run it from inside the ZIP, and do not place it inside the Houdini installation directory or another protected system directory.

Run `BigChickenLauncher.exe` from the extracted package root. Do not move the
EXE away from its five adjacent launcher DLL files. The launcher is not currently
code-signed, so Windows SmartScreen may report an unknown publisher. Continue
with **More info → Run anyway** only if the ZIP came from the official Release
above and its SHA-256 matches `SHA256SUMS.txt`.

The build commands in the README are for source checkouts only; the public ZIP
intentionally omits those maintainer scripts.

## 2. Install Houdini and Bridge Python

Install separately:

- SideFX Houdini. Houdini 21.0.440 is the currently verified build.
- CPython 3.10 or newer for the Bridge.
- A valid Codex/ChatGPT sign-in and network access to the OpenAI service.

A normal 64-bit Python from [python.org](https://www.python.org/downloads/windows/)
installed **for the current user** is supported; administrator access, a
system-wide Python installation, and a global PATH change are not required.
The launcher can discover a per-user installation, or you can select its exact
`python.exe` manually. Bridge Python is separate from Houdini's embedded Python.

Big-Chicken Houdini Intelligence Agent does not redistribute Houdini. Houdini
must already be installed and licensed. Other Houdini versions may be discovered,
but only Houdini 21.0.440 with Python 3.11 has completed the current real-GUI
acceptance path.

## 3. Install or repair the pinned Codex runtime

Start `BigChickenLauncher.exe`, select the Houdini executable, the Bridge
`python.exe`, and **HIA MCP V2**, then run or refresh the checks. If the action
button says **安装/修复 Codex**, click it. The launcher downloads and verifies
the pinned Codex runtime, then refreshes the checks automatically.

This normal first-run action:

- downloads the fixed official Codex 0.144.3 Windows x64 archive;
- verifies the pinned archive SHA-256;
- verifies the pinned SHA-256 and OpenAI Authenticode signer for all three executables;
- installs them only beneath `.runtime\toolchains\codex\0.144.3`;
- leaves global PATH, the registry, the Houdini installation, and user configuration unchanged.

It does not install Bridge Python.

### Command-line fallback

If the launcher cannot complete the download, open PowerShell in the extracted
package root and run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\bootstrap-runtime.ps1
```

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

The launcher discovers Houdini installations, lists available Bridge Python executables, checks the Codex version and login, verifies project imports and HIA MCP V2, and checks local runtime writes and loopback ports.

If more than one Houdini or Python candidate exists, choose the exact executable instead of asking the launcher to guess. **HIA MCP V2** is the recommended backend.

Green checks are ready, yellow checks need attention but do not necessarily block launch, and red checks must be fixed before Houdini can start.

When no red checks remain, click **Launch Houdini**.

### Optional launcher EXE (source checkout only)

Build the thin self-contained WPF launcher:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\build-launcher.ps1 -InstallLocalSdk
.\.runtime\dist\launcher\BigChickenLauncher.exe
```

`-InstallLocalSdk` downloads and verifies a Microsoft .NET 8 SDK under `.runtime`; it does not install a global SDK. The published EXE and its native sidecars must remain together. The launcher uses its built-in dark gradient and has no required external artwork.

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

### Bridge Python is red

Choose an exact CPython 3.10+ `python.exe`. Big-Chicken Houdini Intelligence Agent's Bridge and HIA MCP V2 use the Python standard library and project source paths; they do not require Houdini's embedded Python as the Bridge process.

### Several Houdini versions were found

Select the desired `houdini.exe` explicitly. Discovery of a version is not a compatibility guarantee; Houdini 21.0.440 is the current verified build.

### HIA MCP V2 is unavailable

Close all Houdini instances started by an older Big-Chicken Launcher, start again through the current launcher, and rerun preflight. Do not start a separate MCP server manually.

### FXHoudiniMCP fallback is unavailable

The optional fallback is not included in the public source or Preview package. Use HIA MCP V2 unless you have separately installed the exact compatible fallback runtime.

## Uninstall

Close Houdini and the launcher, then remove the extracted project directory. Big-Chicken Houdini Intelligence Agent does not require a global service, global Python package, PATH change, or Houdini installation-directory modification. The ignored `.runtime` directory contains local settings, caches, attachments, diagnostics, Codex Home, and credentials; remove it only as part of deleting this exact project copy.
