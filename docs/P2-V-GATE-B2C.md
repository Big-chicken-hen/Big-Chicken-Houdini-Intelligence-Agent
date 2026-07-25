# P2-V Gate B2C: real read-only MCP chain

## Status and boundary

Gate B2C is authorized only to connect the pinned Codex 0.144.3 app-server to the existing live Houdini read adapter through a project-local stdio MCP child and the authenticated loopback Bridge. Gate B2's baseline is complete. Gate B3 is not authorized.

The active HIA MCP tool surface is exactly:

1. `houdini_scene_info`
2. `houdini_node_type_info`

`houdini_graph_validate`, `houdini_graph_apply`, and `houdini_graph_verify` are explicitly disabled and unregistered. The frozen `schemas/houdini-mcp/0.1.0` graph contract remains unchanged. B2C uses the versioned read-only profile and does not infer permission from the dormant graph schemas.

No B2C component may create, modify, connect, delete, cook, render, save, cache, or publish Houdini data. The HIA MCP server publishes no Python, HScript, shell, `eval`, `exec`, filesystem, Computer Use, screen-takeover, or additional-Agent tool. Codex is the only reasoning, planning, dialogue, vision, and memory component.

The exact two-tool statement is scoped to the HIA MCP namespace. Pinned Codex 0.144.3 does not expose a stable project configuration or thread/turn parameter that proves its native shell, patch, and filesystem tools are removed from the model globally. B2C therefore does not make that broader claim. Instead, every new or resumed Thread is fixed to the stable `read-only` sandbox with approval policy `never`, and every Turn defensively reasserts the equivalent read-only, network-disabled policy. This prevents native writes and approval escalation but is not described as a native-tool visibility allowlist.

## Runtime chain

```text
Codex app-server 0.144.3
    -> owned stdio MCP child
houdini_intelligence (two read tools only)
    -> Bearer-authenticated HTTP on 127.0.0.1:<random-port>
existing Bridge status/scene routes
    -> bounded Panel polling
Houdini Panel read adapter
    -> bounded hou reads on the UI main thread
live Houdini session
```

The MCP child is not a network server. The Bridge remains the sole random-port loopback HTTP service. The Panel uses the existing standard-library HTTP workers and main-thread `QTimer`; QtNetwork is forbidden.

## Project-local Codex registration

The reviewed `.codex/config.toml` contains one `mcp_servers.houdini_intelligence` stdio table. Its static default command is `D:\Python_3.10\python.exe`, with arguments `-B -m hia_houdini_mcp.stdio`, project `cwd` `E:\houdini-intelligence-agent`, `required = false`, a 5-second startup timeout, and a 65-second tool timeout. This non-required repository default prevents an ordinary Codex Desktop project session, which has no live Houdini launcher environment, from being blocked by an unavailable MCP child. The Bridge supplies per-process Codex `-c` overrides for the already verified `sys.executable` and `required=true`, so the owned Houdini runtime still fails closed if its required MCP cannot initialize. Runtime code never rewrites the tracked TOML.

The file contains environment variable names only. The launcher briefly binds IPv4 loopback port zero to obtain an OS-selected ephemeral port, releases that probe, and places the resulting `HIA_BRIDGE_URL` only in the owned Bridge and Houdini child environments. The Bridge must bind that exact origin or fail closed; it never retries on another port. The `HIA_BRIDGE_TOKEN` is likewise supplied only through child environments. Bootstrap explicitly contains neither the URL nor either credential. These runtime values are not written to Git, TOML values, command arguments, logs, diagnostics, tool results, or this document. The independent `HIA_SCENE_EXECUTOR_TOKEN` is not inherited by Codex or the MCP child.

The offline configuration contract uses a Python 3.10 standard-library closed parser for this fixed source grammar and pins the exact reviewed field set, server name, command/arguments/cwd, timeouts, environment-name allowlist, two enabled tools, and three disabled tools. It rejects URL transport, literal environment maps, credential-bearing fields, extra server tables, unknown keys, and additional tools. A separate finite read-only `mcp get --json` check with the pinned Codex 0.144.3 executable supplies authoritative syntax evidence without starting the MCP server.

## Trusted status parameter facade

The ordinary-Bearer `GET /v1/scene/status` endpoint exposes only the safe first-read correlation context and the reviewed five-type availability summary. It never exposes the Houdini process nonce, publisher/observer identity, Panel executor credential, or full live parameter catalog. Reading status does not create or renew a live capability lease.

For each tool call, the Bridge-owned parameter facade supplies the trusted current HIP session and revision before validation and dispatch. A caller cannot make an attestation authoritative by supplying session, revision, catalog, Schema, process, or publisher fields. For `houdini_node_type_info`, a bounded list of one to five canonical allowlisted node types is the only scene-query intent supplied by the caller. Missing or stale live state fails with a bounded structured error rather than a wildcard, silent rebase, fixture fallback, or fabricated result.

## Offline acceptance

The complete standard-library test suite must pass with bytecode disabled and project-local temporary paths. B2C-specific tests must prove:

- the closed project configuration and secret-free environment-name allowlist;
- pinned MCP initialization and exact two-tool `tools/list`;
- fail-closed `read-only`/`never` policies on Thread start, Thread resume, and every Turn start, without rewriting the user prompt;
- successful fake `houdini_scene_info` and `houdini_node_type_info` calls;
- trusted status/session/revision injection and forged-field rejection;
- no executor credential in the Codex/MCP child environment;
- token redaction across errors and diagnostics;
- bounded timeout, cancellation, Panel disconnect, and child shutdown;
- no graph tool registration, dispatch, or frozen `0.1.0` modification.

Offline results do not prove real Houdini behavior. Development and tests do not start Houdini automatically.

## Manual real-GUI acceptance

After offline tests pass, the user performs the following finite acceptance:

1. Start Houdini through the project launcher and use an empty, unsaved HIP. Do not use Python Shell.
2. Record revision, dirty state, selection, node count, parameters, and connections.
3. Open Houdini Intelligence, create a new Thread, and ask Codex to call `houdini_scene_info`.
4. Verify that the returned build, HIP session, and revision are from the live session.
5. Ask Codex to call `houdini_node_type_info` for `Sop/transform`; verify the active build's reviewed transform-to-`xform` parameter mapping.
6. Confirm revision, dirty state, selection, nodes, parameters, connections, and on-disk HIP state are unchanged.
7. Close the Panel during a read, or call after its capability has gone, and confirm a bounded explicit failure rather than a hang or fabricated success.
8. Confirm the HIA MCP tool list contains exactly the two read tools and no HIA graph or execution tool. Do not interpret this as proof that Codex globally hides every native tool.

Manual screenshots and observations are user evidence. They are not an automated Houdini test and must not be reported as one.

## Gate closure

B2C remains unstaged and uncommitted until the user completes the manual acceptance and explicitly approves closure. A passing B2C proves only the two-tool real read chain. It does not authorize B3, a graph transaction, scene approval UI, a Houdini write, or any later P2-V capability.
