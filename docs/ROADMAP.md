# Roadmap

This file records the current product status rather than historical phase gates.

## Current status

- Panel focus handling: attachment selection is non-native and non-modal, successful sends release input focus, streaming updates are batched, and Panel-owned timers and dialogs stop on close.
- Conversation UI: user and Codex cards resize with the Panel, Markdown and code remain readable, and each Turn keeps one tool-activity card with real failures expanded.
- Context handling: Codex automatic compaction is shown as one concise system message. There is no manual compaction control or local summarizer.
- Local diagnostics: final runtime failures and user-requested dissatisfaction reports are merged per Turn under `<project-root>\.runtime\diagnostics\`, with credential redaction and no upload.

## Remaining acceptance

Offline tests can verify event handling, layout rules, report writing, redaction, and lifecycle cleanup. They cannot prove keyboard focus behavior inside the Houdini host window.

**代码完成，等待真实 GUI 验收。**
