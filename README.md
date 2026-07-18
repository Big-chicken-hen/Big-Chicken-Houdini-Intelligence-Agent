# Houdini Intelligence Agent

Houdini Intelligence Agent embeds Codex in a Houdini Python Panel. The user describes work in natural language, optionally adds reference images and the current selection, and Codex operates the scene that is already open through HIA MCP V2 and HOM. FXHoudiniMCP 1.3.0 remains available only as an explicit compatibility fallback.

## Using the Panel

- Ask for creation, modification, connections, materials, animation, inspection, or validation without naming a tool or save path.
- Add files or clipboard images as references, and include the current Houdini selection when it matters.
- While Codex is working, send another message to append requirements to the active Turn.
- Current-scene work stays in the current Houdini session. Native `hython` is used only when the request explicitly calls for offline work, batch processing, an independent HIP, separate verification, or background rendering.
- If live Houdini MCP is unavailable, the Panel reports that condition instead of silently creating an offline project.

The conversation keeps user and Codex message cards, Markdown and code, attachments, and one tool-activity card per Turn. Codex may automatically organize older context; the Panel reports this once as `Codex 已自动整理较早的对话内容。` Start a new Thread when beginning a different task or when exact early details must remain immediately available.

## Scene status

- `场景版本` is the Panel's local count of observed scene changes. It is not a Git revision or HIP filename version.
- `未保存：是/否` reflects whether the current HIP has unsaved changes.

## Problem reports

Final runtime failures can produce one redacted Markdown report per Turn under `<project-root>\.runtime\diagnostics\`. If the result technically succeeds but is unsatisfactory, use `记录本次问题` in the Panel. Successful and satisfactory Turns do not create reports.

After a report is saved, copy its path and give the file to Codex when asking for diagnosis or a retry. Reports stay local, merge related failures from the same Turn, and redact credentials.

## Windows launcher and preflight

Run the dependency-free launcher from any checkout location; it derives the project root from its own path:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\hia-launcher.ps1
```

The Windows-only WPF window uses the standard system title bar with a DPI-aware dark Houdini-style shell, an original built-in vector HIA assistant mark, and no external UI assets or runtime. It scans `HFS`, `PATH`, read-only registry entries, and SideFX's usual Program Files directories. Every discovered Houdini version is shown with its full `houdini.exe` path. When several versions exist, choose one explicitly. The launcher also lets you choose Bridge Python and one mutually exclusive MCP backend: **HIA MCP V2** is recommended and selected by default; **FXHoudiniMCP 1.3.0** is the manual fallback. Changing any environment selection marks the result as stale and requires a rescan before launch.

Preflight validates the selected `houdini.exe` and sibling `hython.exe`, compares short read-only build probes, reports Houdini's Python major/minor and `import hou`, and checks Bridge imports, the project-local Codex executable/login, only the selected MCP backend, package/config files, `.runtime` writes, and loopback-only port allocation. Green is ready, yellow is actionable but non-blocking, and red prevents launch.

For console and CI-style checks, use:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\hia-launcher.ps1 -CheckOnly
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\hia-launcher.ps1 -CheckOnly -Json
```

Pass `-HoudiniExe`, `-BridgePython`, and optionally `-McpBackend hia_v2|fxhoudini` for exact noninteractive selection. The three choices are stored only in `<project-root>\.runtime\launcher\settings.json`; credentials, ports, and raw login output are never recorded. `-RepairSafeProject` only creates project-local runtime directories and normalizes the two locked project configs. It never installs global Python, changes Windows settings, or downloads dependencies.

Manual acceptance remains necessary for the real GUI: open the launcher, select **HIA MCP V2**, clear red checks, click **Launch Houdini**, and confirm the Panel shows `HIA MCP V2：可用`. Then exercise `hia_context`, dynamic node searches for box/vellum/mtlx/karma, selection-based `hia_inspect`, one batched `hia_execute_hom`, `hia_scene_diff`, and `hia_capture_viewport`. The session must not expose upstream `create_node`, `set_parameter`, or the rest of the 179-tool fallback surface. Automated tests parse the WPF asset and verify the offline transport contracts but never launch the real Houdini GUI.

## Offline verification

Run the standard-library test suite from the repository root:

```powershell
python -m unittest discover -s tests -t . -v
```
