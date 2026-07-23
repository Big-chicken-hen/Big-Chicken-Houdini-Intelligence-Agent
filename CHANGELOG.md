# Changelog

All notable user-facing changes to Big-Chicken Houdini Intelligence Agent are recorded here.

## 0.1.0-preview - 2026-07-23

### Added

- Houdini Python Panel with Codex conversation history, Markdown, streaming messages, reference images, clipboard screenshots, and optional current-selection context.
- HIA MCP V2 as the default live Houdini backend, with dynamic node discovery, scene inspection, batched UI-thread HOM execution, validation, scene diff, and viewport capture.
- Windows launcher with Houdini and Bridge Python discovery, environment preflight, backend selection, final-output selection, and project-local runtime state.
- Optional Goal focus mode for automatic continuation of long multi-step work.
- Launcher-confirmed Houdini crash recovery that can restore the exact bound Thread and continue an active Goal.
- Local redacted runtime diagnostics and manual recording of unsatisfactory results.
- Native `hython` path for explicitly requested offline HIP work, batch processing, independent verification, simulation, and background rendering.
- Optional, separately installed FXHoudiniMCP 1.3.0 compatibility fallback.

### Changed

- Current-scene HIA MCP V2/HOM execution is the default; unavailable live Houdini connections no longer silently switch to an offline project.
- Complex scene changes prefer one or a few batched HOM operations instead of a large sequence of fine-grained tool calls.
- Launcher and project paths are derived from the project location instead of a fixed drive.
- Internal screenshots, previews, attachments, diagnostics, and temporary files use the ignored project `.runtime` tree.
- Public launcher presentation uses the built-in dark gradient and does not require third-party artwork.

### Known Preview limitations

- Windows x64 only.
- Houdini 21.0.440 with Python 3.11 is the only currently verified real-GUI configuration.
- An HOM call already executing on Houdini's UI thread cannot be force-killed safely.
- Goal continuation and crash recovery remain Preview features.
- Codex 0.144.3 and its project-local login must be prepared before first launch.
- The launcher executable is not code-signed.
