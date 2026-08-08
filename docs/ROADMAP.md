# Roadmap

This file records the current product status rather than historical phase gates.

## Current status

- Product scope is one ordinary Codex Thread at a time, with ordinary history, attachments, Native Goal, Focus, Stop/Continue, diagnostics, HIA MCP V2/HOM execution, and third-compaction Thread rotation.
- Native model subagents may be displayed for bounded read-only assistance, but the plugin does not create fixed roles or manage a second lifecycle.
- Panel focus handling: attachment selection is non-native and non-modal, successful sends release input focus, streaming updates are batched, and Panel-owned timers and dialogs stop on close.
- Conversation UI: user and Codex cards resize with the Panel, Markdown and code remain readable, and each Turn keeps one tool-activity card with real failures expanded.
- Context handling: Codex automatic compaction is shown as one concise system message. There is no manual compaction control or local summarizer.
- Local diagnostics: final runtime failures and user-requested dissatisfaction reports are merged per Turn under `<project-root>\.runtime\diagnostics\`, with credential redaction and no upload.

The experimental fixed five-role project-team mode was removed before v0.2.0 because it destabilized ordinary operation and had not completed embedded Houdini acceptance.

## Remaining acceptance

Offline tests can verify ordinary event handling, layout rules, report writing, redaction,
Thread rotation, and lifecycle cleanup. They cannot prove keyboard focus behavior,
Panel embedding, or real scene writes inside the Houdini host window.

**代码完成，等待真实 GUI 验收。**
