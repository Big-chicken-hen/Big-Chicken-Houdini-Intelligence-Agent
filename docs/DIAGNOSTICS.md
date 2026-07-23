# Runtime diagnostics

Runtime diagnostics are small local Markdown reports intended to help reproduce and fix a failed or unsatisfactory Houdini Turn. They are not telemetry, monitoring, or a second memory system.

## When a report is created

An automatic report is created only when a Turn ends with a meaningful failure, such as a final MCP, HOM, `execute_python`, Bridge, node, selection, render, image-copy, or scene-validation failure. A transient warning that is still retrying does not create a report by itself.

Use the Panel action `记录本次问题` when execution succeeds but the result is visually or procedurally unsatisfactory. The software does not try to judge subjective quality automatically. A successful and satisfactory Turn creates no report because there is no problem to diagnose.

All evidence for the same Turn is merged into one report, including retries, recovery attempts, warnings, traceback excerpts, and optional user feedback.

## Location and contents

Reports are written beneath:

```text
<project-root>\.runtime\diagnostics\<timestamp>-<short-slug>.md
```

The writer derives `<project-root>` at runtime. A report may include:

- time, final status, Houdini and Python versions, safe plugin or Git revision, model, and effort;
- safe Thread and Turn identifiers, user-goal summary, expected result, actual result, and execution stage;
- tools and call order, error code and text, redacted traceback, retries, recovery, and workaround;
- relevant nodes, selection, scene revision, unsaved state, whether the scene changed, and a created root path;
- attachments by safe name, manual checks, undo guidance, reproduction steps, impact, and next action.

Anything inferred rather than observed is labeled `待验证假设`.

## Redaction and sharing

Bearer tokens, authorization data, cookies, API keys, login data, refresh tokens, access tokens, and similar credentials are replaced with `[REDACTED]`. Reports omit irrelevant chat, source, private files, and unrelated absolute paths.

Reports remain local. The writer does not upload them, open Explorer, contact a service, or create a GitHub issue. To request help, copy the saved path and give the report to Codex. The user decides whether to share it elsewhere.

Before sharing a report publicly, read it in a text editor and remove any project name, asset path, Thread summary, or scene detail that should remain private. Never attach the surrounding `.runtime` directory, Codex Home, launcher settings, reference images, screenshots, HIP files, or final outputs to an issue by default.

For a public bug report, pair the smallest relevant redacted excerpt with Big-Chicken Houdini Intelligence Agent, Windows, Houdini, Houdini Python, Bridge Python, Codex, and selected MCP backend versions. Security-sensitive reports should follow [SECURITY.md](../SECURITY.md) instead of a public issue.

## Fictional example

```markdown
# Runtime problem report

- Time: 2030-01-01 00:00:00
- Status: final failure
- User goal: update the selected geometry
- Expected: the editable node network is updated
- Actual: the operation stopped before the final scene check
- Stage: HOM execution
- Retries: 1
- Scene modified: unknown
- Workaround: inspect the reported node state before retrying
- Next step: give this report to Codex with the original request
- 待验证假设: a node parameter may be incompatible with the active Houdini build
```
