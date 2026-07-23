# Security policy

Big-Chicken Houdini Intelligence Agent is a local creative-tool integration that can execute Codex-generated HOM/Python and modify the current Houdini scene. Treat a request to the plugin with the same care as a script you intend to run in Houdini.

## Supported version

Security fixes currently target the latest `0.1.x-preview` source and Preview release. Older development commits and historical phase branches are not supported release channels.

## Reporting a vulnerability

Use GitHub's private vulnerability reporting for this repository when it is available. Do not post credentials, Codex Home contents, private HIP files, proprietary assets, access tokens, or an unredacted diagnostic in a public issue.

If private reporting is unavailable, open a minimal public issue that contains no sensitive reproduction data and asks the maintainer to establish a private contact path.

For ordinary runtime bugs, include:

- Big-Chicken Houdini Intelligence Agent version or Git commit;
- Windows, Houdini, Houdini Python, Bridge Python, and Codex versions;
- selected MCP backend;
- a concise reproduction using a disposable HIP;
- the smallest relevant excerpt from a redacted Big-Chicken Houdini Intelligence Agent diagnostic.

## Runtime boundary

- Big-Chicken Houdini Intelligence Agent's local HTTP services bind to `127.0.0.1` and use fresh random credentials for each launcher session.
- Runtime credentials are passed only to owned child processes and must not be written to logs, configuration values, diagnostics, command arguments, or reports.
- HIA MCP V2 executes HOM on Houdini's UI thread. A request can create, modify, connect, cook, render, or delete scene content according to the generated script.
- Stop can cancel before execution or stop waiting for a result. It cannot safely force-kill Python already running in Houdini's UI thread.
- Big-Chicken Houdini Intelligence Agent does not use screen takeover and does not add its own cloud telemetry.
- Codex communicates with the OpenAI service and is governed by the user's Codex/OpenAI account and applicable terms.

## Safe use

- Save or version important HIP files before asking Codex to make broad changes.
- Use a disposable HIP when evaluating a new release or unfamiliar workflow.
- Review reference files and natural-language instructions before sending them.
- Keep the project in an ordinary user-writable directory, not inside a Houdini or Windows installation directory.
- Do not expose Big-Chicken Houdini Intelligence Agent's loopback ports through a proxy, port forward, or firewall rule.
- Do not share `.runtime`, especially `codex-home`, launcher settings, attachments, diagnostics, or cached screenshots.
- Do not package or publish a user's HIP, render, asset, or diagnostic without explicit permission.

## Local diagnostics

Big-Chicken Houdini Intelligence Agent diagnostics are local Markdown files beneath `<project-root>\.runtime\diagnostics`. The writer redacts common credential forms, but a user should still review every report before sharing it. Big-Chicken Houdini Intelligence Agent does not automatically upload reports or create issues.

## Third-party components

Houdini, Codex, .NET, and the optional FXHoudiniMCP fallback have their own security and release processes. See [Third-party notices](THIRD_PARTY_NOTICES.md). Report upstream vulnerabilities to the appropriate upstream maintainer when the issue is not caused by Big-Chicken Houdini Intelligence Agent.
