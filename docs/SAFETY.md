# Safety

Runtime problem reports are local diagnostic aids, not telemetry or incident tracking.

- Write reports only beneath `<project-root>\.runtime\diagnostics\`; derive the project root from the running project instead of hard-coding a drive path.
- Include only evidence relevant to the current Turn and affected Houdini scene state. Do not read unrelated source, chat history, private files, user directories, Houdini configuration, or paths outside the project to enrich a report.
- Never record bearer tokens, authorization headers, cookies, API keys, login data, refresh tokens, access tokens, or other credentials. Replace detected values with `[REDACTED]`.
- Redact irrelevant absolute paths and unrelated traceback content. Mark uncertain conclusions as `待验证假设` instead of presenting them as facts.
- Do not upload reports, send them over the network, open Explorer automatically, or create a GitHub issue.
- The user decides whether and with whom to share a report. Saving a report does not grant permission to inspect other files or contact an external service.
- Do not operate Houdini through screen takeover or Computer Use. Live work defaults to HIA MCP V2 and HOM in the current session; FXHoudiniMCP is an explicit compatibility fallback.
