# Installation and first run

Big-Chicken Houdini Intelligence Agent is currently a Windows x64 Preview for Houdini. The tested configuration is Houdini 21.0.440 with its Python 3.11 runtime, a separate CPython 3.10+ Bridge executable, and Codex 0.144.3.

## 1. Choose an install directory

Download or clone the project into an ordinary writable local directory, for example:

```text
D:\Tools\Big-Chicken-Houdini-Intelligence-Agent
```

The project does not require a fixed drive letter. Do not place it inside the Houdini installation directory or another protected system directory.

If you downloaded the Preview ZIP, extract the whole archive and run
`BigChickenLauncher.exe` from its root. Do not move the EXE away from
the five adjacent launcher DLL files. The build commands in the README are for
source checkouts only; the public ZIP intentionally omits the build scripts.

## 2. Install Houdini and Bridge Python

Install separately:

- SideFX Houdini. Houdini 21.0.440 is the currently verified build.
- CPython 3.10 or newer for the Bridge.

Big-Chicken Houdini Intelligence Agent does not redistribute Houdini. The source checkout also does not contain Codex, credentials, or the optional FXHoudiniMCP runtime.

## 3. Bootstrap the pinned Codex runtime

From the project root:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\bootstrap-runtime.ps1
```

This explicit bootstrap:

- downloads the fixed official Codex 0.144.3 Windows x64 archive;
- verifies the pinned archive SHA-256;
- verifies the pinned SHA-256 and OpenAI Authenticode signer for all three executables;
- installs them only beneath `.runtime\toolchains\codex\0.144.3`;
- leaves global PATH, the registry, the Houdini installation, and user configuration unchanged.

It does not install Bridge Python. Install Python 3.10+ yourself and select its exact `python.exe` in the launcher.

### Manual Codex placement for troubleshooting

The bootstrap is the normal first-run path. Use the following manual steps only to diagnose a failed download or to prepare the runtime without running the bootstrap.

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

From the project root:

```powershell
$env:CODEX_HOME = (Join-Path (Get-Location) '.runtime\codex-home')
& '.\.runtime\toolchains\codex\0.144.3\codex.exe' login --device-auth
```

Follow the official device-login flow. The access and refresh tokens remain managed by Codex under the ignored project-local Codex Home. Never copy that directory into a Release archive or issue attachment.

## 5. Start the launcher

The PowerShell launcher is the source checkout entry point:

```powershell
powershell -NoProfile -Sta -ExecutionPolicy Bypass -File .\scripts\hia-launcher.ps1
```

The launcher discovers Houdini installations, lists available Bridge Python executables, checks the Codex version and login, verifies project imports and HIA MCP V2, and checks local runtime writes and loopback ports.

If more than one Houdini or Python candidate exists, choose the exact executable instead of asking the launcher to guess. **HIA MCP V2** is the recommended backend.

Green checks are ready, yellow checks need attention but do not necessarily block launch, and red checks must be fixed before Houdini can start.

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
