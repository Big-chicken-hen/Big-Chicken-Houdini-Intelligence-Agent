# P2-V architecture design review

## Review status and authorization boundary

This document is a design-review artifact for Gate B. It does not authorize a live Houdini connection, creation or modification of Houdini nodes, startup of the Houdini GUI, creation of project MCP configuration, or implementation of the tool host, Bridge queue, Panel executor, or `hou` calls.

P2-V begins implementation only after the architecture, versioned tool Schemas, threat model, and test plan have been reviewed. A later, separately approved live-write sub-gate is required before `houdini_graph_apply` may mutate a real HIP session.

Codex remains the sole intelligent agent, planner, dialogue owner, and memory source. The MCP adapter, Bridge, Panel executor, and Houdini runtime perform only deterministic validation, transport, execution, and verification. They do not add an Agent, LLM, planner, RAG system, semantic memory, prompt rewriting, or screen control.

## P2-V scope

The first visible scene-operation slice accepts a request such as “生成一张简单、可编辑的四条腿桌子。” and allows Codex to construct one editable procedural table in the current Houdini session through four reviewed tools:

1. `houdini_scene_info`
2. `houdini_node_type_info`
3. `houdini_graph_apply`
4. `houdini_graph_verify`

P2-V does not provide arbitrary Python, HScript, expressions, `eval`, shell execution, filesystem access, HIP save, rendering, caching, HDA creation or publishing, node deletion as a public tool, modification of an existing user node, or access outside the approved `/obj/HIA_Table_*` container scope.

## Trust and transport path

```text
Codex app-server (pinned 0.144.3; reasoning and tool selection)
    -> project-local MCP subprocess over stdio
Project-local Houdini MCP adapter (four deterministic tools only)
    -> authenticated HTTP to 127.0.0.1:<random-port>
Existing Bearer-authenticated loopback Bridge (validation and bounded queue)
    <- nonblocking authenticated polling / result posting
Houdini Intelligence Panel main-thread executor (single live-HIP writer)
    -> hou API on the Houdini UI thread only
Live Houdini session
```

The app-server remains stdio-only and is never exposed on a network socket. The MCP adapter is a project-local stdio child process, not an HTTP server. The Bridge retains its random loopback port and random session Bearer token; neither the token nor the scene gateway is exposed to a LAN or the internet. The Panel does not block its UI thread while waiting for Bridge work.

No component in this path uses Computer Use or screen takeover. The only scene mutation path is the reviewed declarative request reaching the main-thread `hou` executor.

## Component responsibilities

### Codex app-server and Codex

- Codex interprets the user request and selects one of the four tools.
- Codex supplies declarative graph intent; it does not send executable code.
- The pinned Codex 0.144.3 capability and generated stable protocol baseline remain authoritative for this project.
- Existing exclusions remain in force: no experimental Schema, `dynamicTools`, process API, `thread/shellCommand`, or app-server WebSocket.

### Project-local stdio MCP adapter

- Publishes exactly the four P2-V tools with versioned JSON Schema.
- Denies every unregistered tool and rejects unknown fields where the versioned Schema requires a closed object.
- Performs structural, size, count, string, enum, and deadline validation before forwarding a request.
- Converts schema-admitted execution failures into structured MCP tool results without leaking tokens, tracebacks, or internal paths; cancellation, queue, and shutdown failures remain bounded adapter errors.
- Does not import `hou`, inspect the filesystem, make planning decisions, rewrite prompts, or execute code.
- Uses a bounded wait and cancellation path rather than keeping an unbounded tool call alive.

### Authenticated loopback Bridge

- Authenticates every adapter and Panel request using the existing session Bearer token.
- Maintains a bounded in-memory request queue and one result slot per request; it is not a second chat store.
- Correlates `request_id`, Codex Thread/Turn, HIP session, revision, idempotency key, deadline, and result.
- Allows only one queued or executing write against the live HIP at a time.
- Rejects replay, expired requests, queue overflow, unknown tools, malformed results, and session mismatch with structured errors.
- Does not call `hou`, approve a change, synthesize a graph, or broaden the declared scope.

### Panel main-thread executor

- Reuses the P1 transport model: standard-library HTTP runs only in daemon workers, with one worker reserved for event polling and at most two control-request workers. Workers place plain Python dictionaries into a bounded thread-safe queue and never touch a widget or emit a Qt signal.
- Uses one main-thread `QTimer` to drain that queue. Every request has a finite monotonic deadline and generation; stale or post-disposal results are discarded without blocking the Houdini UI.
- Dispatches accepted work from that main-thread timer drain through an equivalent Houdini-supported main-thread scheduling mechanism; no worker emits a Qt signal carrying work.
- Is the sole writer to the live HIP. Network callbacks and Bridge worker threads never call `hou`.
- Revalidates session, revision, approval proof, idempotency, node types, names, parameters, connections, and scope immediately before execution.
- Posts a structured result to the Bridge and does not retain duplicate conversation history.

### Houdini runtime adapter

- Discovers live capabilities from `hou` at runtime.
- Applies only reviewed, declarative OBJ/SOP operations.
- Wraps one approved graph application in one `hou.undos.group`.
- Verifies the resulting graph using live node and parameter state rather than assuming a cook succeeded.

## Runtime discovery and live authority

No P2 component hard-codes a Houdini installation directory, Houdini version, Python version, or the existence and parameter layout of a node type.

The launcher or process supervisor discovers candidate Houdini installations only within separately authorized, bounded locations. Once the Panel is loaded, a trusted internal capability attestation reports the live process nonce, build, platform, Python ABI, HIP identity, available contexts, node-type catalog digest, and the eight reviewed schema hashes. This attestation is transport state, not an untrusted tool argument or a public `houdini_scene_info` response. `houdini_node_type_info` returns only the bounded node metadata defined by its output schema.

Static recipes may name reviewed logical types such as an OBJ geometry container, SOP box, SOP merge, and SOP output/null, but the live schema is authoritative. A missing or unattested live process returns `HOUDINI_UNAVAILABLE`. A build, catalog, schema-hash, type, parameter, tuple, or context mismatch returns `CAPABILITY_MISMATCH`. The executor does not guess a substitute or fall back to Python.

The public schemas require an exact `hip_session_id` and `base_scene_revision`, including for `houdini_scene_info`. This is not satisfied with a `"current"` wildcard. The trusted Bridge session snapshot injects those exact values into Codex-visible connection state before a tool request is formed; if no current snapshot exists, dispatch fails with `HOUDINI_UNAVAILABLE`. A changed session or older revision still fails with `HIP_SESSION_MISMATCH` or `SCENE_CONFLICT`.

## Tool surface

| Tool | Access | P2-V purpose | Mutation |
|---|---|---|---|
| `houdini_scene_info` | Read-only | Echo the exact HIP session/revision envelope and return the bounded fingerprint, frame, FPS, dirty flag, HIA-owned container list, and active HIA container defined by the output schema. | None |
| `houdini_node_type_info` | Read-only | Return a bounded, sanitized description of specifically requested reviewed OBJ/SOP types and parameters from the live catalog. | None |
| `houdini_graph_apply` | Exact approval required | Apply one declarative graph inside a new `/obj/HIA_Table_*` container under revision and idempotency controls. | Creates only the request-owned container and its reviewed children. |
| `houdini_graph_verify` | Read-only | Verify names, types, parameters, wiring, flags, errors, scope, and revision for an HIA-owned graph. | None |

The read-only tools do not return arbitrary scene dumps, scripts, parameter expressions, secrets, environment variables, or filesystem paths. Requests and responses are bounded by explicit maximum counts and string sizes in the versioned Schemas.

## Scene command envelope

The public input schema contains the correlation fields below. The trusted adapter-to-Bridge envelope additionally binds the exact `tool_name`, schema version, canonical `arguments` bytes, absolute monotonic deadline, and capability-attestation digest; callers cannot override those trusted fields.

Every tool input carries at least:

- `request_id`
- `thread_id` and `turn_id`
- `hip_session_id`
- `base_scene_revision`
- `idempotency_key`
- `deadline_ms`
- `permission_level`

Every result is JSON-serializable and contains:

- `ok`
- `request_id`
- `hip_session_id`
- `base_scene_revision` and resulting `scene_revision`
- `idempotency_key`
- `created_nodes` and `changed_nodes`
- `artifacts` (always empty in P2-V)
- `warnings`
- `structured_error`
- `job_id` (always absent/null in P2-V)

User-facing failures have a stable code, safe message, retryability flag, and bounded details. A traceback may be recorded in a project-local diagnostic channel in a later authorized implementation, but it is never the only tool result and is never returned as uncontrolled user-facing text.

Queue cancellation, queue capacity, and Bridge shutdown are adapter/transport outcomes rather than tool output objects. B1 maps them to bounded JSON-RPC/MCP errors with codes `CANCELLED`, `QUEUE_FULL`, and `SHUTTING_DOWN`; it does not inject codes that are absent from a tool's reviewed output schema. Authentication and tool execution failures remain schema-valid structured tool results.

## Deterministic cross-field validation

JSON Schema closes and bounds each accepted value, but B1 must also run deterministic validators for relations that the eight schemas do not express completely:

- result correlation must echo the submitted request, session, base revision, and idempotency key, and the resulting revision may not regress;
- an apply replay record must be keyed by the same idempotency key and canonical digest as the top-level request;
- `container_path`, every created path, and every residual path must agree with the exact request-owned container;
- node-type results must be unique and correspond exactly to the requested query set;
- verification check status, issues, overall validity, and graph digest must be mutually consistent;
- success and failure branches must agree with `ok`, `structured_error`, created/changed paths, replay state, and revision advancement.

Any mismatch is rejected before it can complete another request. No validator evaluates strings, imports `hou`, or broadens an enum.

## Request queue and execution sequence

1. Codex invokes one of the four MCP tools.
2. The adapter validates the versioned Schema, attaches transport correlation, and submits the request to the authenticated Bridge.
3. For `houdini_graph_apply`, the request remains non-executable until an exact approval proof is attached to the canonical request hash.
4. The Panel obtains work through bounded, authenticated, nonblocking polling. At most one live-HIP write enters `starting` or `inProgress` state.
5. The Panel main-thread executor performs all precondition checks against current live state. It rejects a changed HIP session, stale revision, expired deadline, reused idempotency key with different content, absent approval, unapproved type or parameter, existing target name, or out-of-scope path before mutation.
6. A read-only tool executes directly on the main thread. A write executes as the transaction described below.
7. The executor verifies the resulting state and increments the scene revision only for a successful committed mutation.
8. The Panel posts the structured result to the Bridge; the Bridge resolves the waiting adapter call once.
9. Retries using the same idempotency key and identical canonical request return the recorded result without creating a second graph. Reuse with different content returns `IDEMPOTENCY_CONFLICT`.

Disconnects never imply success. An uncertain write result is reconciled by `idempotency_key`, HIP session, revision, and the exact owned container before any retry is considered.

## `houdini_graph_apply` transaction

### Exact approval

Approval is per invocation and binds the canonical request hash, Thread, Turn, HIP session, base revision, idempotency key, target container, node/connection counts, parameter summary, and stated side effects. It is invalid after any bound value changes. P2-V provides no permanent approval and no wildcard approval for future graphs.

The approval UI must explicitly state that one new procedural table container will be created in the current scene and that the operation does not save the HIP. Denial or dismissal returns `APPROVAL_DENIED` without calling a mutating `hou` API.

### Preconditions

- `hip_session_id` equals the live session.
- `base_scene_revision` equals the current revision; last-write-wins is forbidden.
- The deadline has not expired.
- The idempotency record is new or is an exact replay with a known result.
- The target is one direct child of `/obj` and matches the reviewed `HIA_Table_<id>` naming grammar.
- The target name does not already exist.
- Every node type, node name, parameter, value, and connection passes the live-schema and static-policy checks.
- The canonical request has a matching, unexpired approval proof.

### Allowed mutation

The executor creates one new OBJ geometry container and only the reviewed SOP children declared inside it. It cannot traverse to or reference an existing user node, alter the current selection as a required side effect, or modify `/obj` children outside the new container.

The minimum accepted table graph is:

```text
/obj/HIA_Table_<id>
  tabletop_box
  leg_front_left
  leg_front_right
  leg_back_left
  leg_back_right
  merge_table
  OUT_TABLE
```

The tabletop and four legs expose ordinary editable size and position parameters. The four legs and tabletop connect to `merge_table`; `OUT_TABLE` receives the merged result and receives the display flag. Node types and actual parameter names are resolved against the live catalog before mutation.

The entire creation executes inside one `hou.undos.group`, so one user Undo removes the complete request-owned table. The tool never invokes Undo on the user's behalf after a successful commit.

### Failure containment

The executor records the newly created container object as request-owned before creating children. If a later operation fails, rollback may destroy only that exact container created by the current request, while still inside the controlled transaction. It must first prove object identity, parent `/obj`, expected HIA name, current request ownership, and absence before this request. It never searches for similarly named nodes and never deletes or repairs pre-existing content.

If those proofs are unavailable, the executor stops and returns a high-severity structured error rather than guessing. No public delete tool is exposed.

### Postconditions

- The container and required seven children exist with the expected live types.
- Connections match the approved declaration.
- Parameter values round-trip to the accepted values.
- `OUT_TABLE` has the display flag and no unapproved node has a display flag.
- Cook and node errors are returned as bounded warnings/errors.
- Every created path is below the exact request-owned container.
- `created_nodes` is complete, `changed_nodes` contains no pre-existing path, and the successful revision advances exactly once.

Postcondition failure triggers the same request-scoped rollback before a result is committed.

## Revision, HIP session, and idempotency

`hip_session_id` changes whenever the active HIP identity is replaced, cleared, or reloaded. A monotonic in-session `scene_revision` advances for accepted tool writes and for observed manual scene changes relevant to the protected scope. The exact Houdini event callbacks used to observe manual changes must be capability-tested in the live build; missing reliable observation blocks writes rather than permitting stale updates.

A request based on an older revision returns `SCENE_CONFLICT` with the safe current revision and requires Codex to inspect again. A request from another HIP session returns `HIP_SESSION_MISMATCH`. Neither path performs a mutation.

Idempotency state stores only technical correlation and a canonical request/result digest. It does not contain chat history or semantic memory. Retention is bounded to the current Bridge/HIP session for P2-V unless a reviewed persistence design is approved later.

## Project-scoped Codex MCP configuration plan

A later reviewed implementation will create project-scoped `.codex` configuration only if separately authorized. It will register one local stdio MCP server using an absolute project-local command, an explicit project `cwd`, and argument arrays. The server itself exposes exactly the four P2-V tools, and the Codex configuration additionally restricts it with `enabled_tools` to those same four names.

Per-tool approval policy will set `houdini_graph_apply` to prompt for each invocation. Read-only tool approval policy will be selected explicitly during configuration review and will not broaden the write permission. The configuration will not contain API keys, login material, a shell command string, a network listener, or any additional MCP tool.

The official Codex configuration reference documents stdio server `command`/`cwd`, `enabled_tools`, and per-tool `approval_mode`. That current documentation is design input only: the pinned Codex 0.144.3 executable, its generated stable Schema, and a finite project-local capability probe remain authoritative. Exact TOML keys and approval behavior must be proven against 0.144.3 before the project file is created. This review round creates no `.codex/config.toml` and does not start an MCP server.

## Security invariants

- Only `127.0.0.1` with a random port and random Bearer token is used for Bridge control traffic.
- The token is never logged, returned in tool results, persisted in chat, or placed in command-line arguments.
- The adapter has no arbitrary URL, command, environment, or path parameter.
- The four-tool allowlist is enforced independently by Codex configuration, MCP registration, Bridge routing, and Panel dispatch.
- A read tool cannot be upgraded into a write by a request field.
- All `hou` calls execute on the Houdini UI thread; the live HIP has one writer.
- No request can create outside one new `/obj/HIA_Table_*` container or refer to existing user nodes.
- No save, file write, render, HDA, Python, HScript, expression, `eval`, shell, or public delete surface exists.
- Manual edits cause conflict rather than last-write-wins.
- Unknown fields, types, methods, node types, parameters, paths, and states fail closed.
- The live Houdini schema overrides static assumptions; no fallback executes arbitrary code.

## Gate B0 decisions frozen for B1

B1 uses these conservative defaults; changing one requires a versioned contract review:

1. Bridge routing is limited to `POST /v1/scene/requests`, `GET /v1/scene/requests/{request_id}/result`, `GET /v1/scene/requests/next`, `POST /v1/scene/requests/{request_id}/approval`, `POST /v1/scene/requests/{request_id}/result`, and `POST /v1/scene/requests/{request_id}/cancel`. `POST` accepts atomically and returns HTTP 202 with the request ID, canonical digest, and pending state, or HTTP 200 for an already terminal exact replay. The adapter obtains a terminal HTTP 200 result through result long-polls of at most 1,000 ms until the original absolute deadline; 202 remains pending and never means scene success. No unbounded HTTP request exists.
2. The typed scene-approval route is independent of P1's app-server `/v1/approval` route and cannot answer one of its requests. Its closed body contains only `decision=allow|deny`, the exact `request_digest`, Bridge `launch_id`, and `generation`. The authenticated Bridge stamps receipt time and, only for an exact pending apply, creates an internal one-use proof expiring at the earlier of the absolute request deadline or 60 seconds. The proof is never returned to MCP arguments; digest/generation mismatch, duplicate decision, expiry, or disconnect denies execution.
3. The queue holds at most 32 requests, an event/result poll waits at most 1,000 ms, and an HTTP JSON body is at most 262,144 bytes. The schema's 100–60,000 ms budget is converted once to an absolute monotonic deadline that no retry or poll can reset.
4. The stdio adapter admits only JSON-RPC `initialize`, `notifications/initialized`, `ping`, `tools/list`, `tools/call`, and `notifications/cancelled`. It advertises only MCP protocol `2024-11-05` for the offline baseline. A UTF-8 JSONL line is at most 262,144 bytes with nesting at most 32. An unknown request receives JSON-RPC method-not-found; an unknown notification is safely recorded and ignored because notifications have no response; malformed, duplicate-key, non-finite, oversized, or over-deep input terminates that protocol session and never replays a write. Compatibility with pinned Codex 0.144.3 remains unverified until the separately approved finite registration probe.
5. Canonical bytes are UTF-8 JSON with recursively sorted object keys, no insignificant whitespace, preserved Unicode, rejected duplicate keys, and rejected NaN/infinity. SHA-256 binds contract version, tool name, request/Thread/Turn, HIP session/fingerprint, base revision, idempotency key, permission, trusted absolute monotonic deadline, and complete arguments.
6. Identifiers, names, node/connection counts, numeric limits, and strings are exactly those in the versioned schemas: one `HIA_Table_<id>` container, seven child roles, and six fixed connections. B1 adds no aliases.
7. B1 accepts only a fixed fake capability attestation whose process/session/catalog/schema digests match its fixture. The trusted session snapshot is bound to one Bridge launch identifier and monotonically increasing generation; Panel disconnect, Bridge restart, HIP replacement, or generation change invalidates it. Without a current attestation, all four tools fail with `HOUDINI_UNAVAILABLE`; a mismatch fails with `CAPABILITY_MISMATCH`. No live dispatch exists in B1.
8. Approval is external to untrusted MCP arguments, one-use, and bound to the canonical digest. It expires at the earlier of the trusted absolute request deadline or 60 seconds after grant. Denial, dismissal, disconnect, reuse, or any changed bound field fails without queueing a write.
9. Cancellation before executor claim is terminal. Cancellation after claim is only a request to the deterministic executor and cannot be reported as successful until a terminal result exists. Queue overflow and shutdown reject new work; shutdown resolves every pending waiter once.
10. Idempotency records are in-memory, scoped to the current Bridge/HIP session, and capped at 256 terminal entries. Exact same-key/same-digest replays return the recorded result; changed digests return `IDEMPOTENCY_CONFLICT`. B1 never assumes success across a restart or automatically retries an indeterminate write.
11. The public read summaries are exactly the bounded output-schema fields. Process/build/catalog data stays in the trusted attestation; no selection dump, arbitrary scene tree, environment, user filesystem path, or raw parameter data is returned.
12. Apply must perform mandatory internal postcondition validation. A later Codex call to `houdini_graph_verify` is an additional read, not a substitute. B1 proves both only with fake state.
13. Houdini callbacks, live type/parameter meanings, main-thread scheduling, Undo, cook, rollback, exact project MCP TOML, and runtime approval UI remain unresolved live questions. They fail closed and are deferred to separately approved B2–B4 gates; B1 must not simulate them as verified Houdini facts.

No unresolved live item may be filled by a permissive default.

## Gate B sub-gates

### B0 — Design review

Produce and review architecture, four versioned tool Schemas, threat model, and test plan. No MCP configuration, service startup, Houdini process, or `hou` write is authorized.

### B1 — Offline transport and contract implementation

Implement the stdio MCP adapter, four-tool deny-by-default registration, authenticated Bridge queue, structured results, and a fake Panel executor. B1 is fake-only and live-disabled: validate all Schema, approval, revision, idempotency, Unicode, timeout, authentication, replay, and shutdown behavior without importing `hou`, starting Houdini, or dispatching to a live process.

### B2 — Read-only Houdini capability slice

After separate approval, load only the Panel-side read adapter in a manually started Houdini session. Before any dispatch, require an attestation bound to the current Houdini process nonce, build, HIP session/fingerprint, revision, catalog digest, and reviewed schema hashes. At most `houdini_scene_info` and `houdini_node_type_info` may then be enabled; `houdini_graph_apply` and `houdini_graph_verify` remain disabled. Missing attestation returns `HOUDINI_UNAVAILABLE`; mismatch returns `CAPABILITY_MISMATCH`. Do not mutate the scene.

### B3 — Write-path simulation and approval acceptance

Use a pure-Python fake scene graph and executor, with no `hou` module, to prove exact approval binding, single-writer serialization, transaction boundaries, modeled one-step Undo semantics, conflict rejection, idempotent replay, rollback confinement, and postcondition verification. No real HIP mutation is authorized and fake behavior is not evidence about Houdini.

### B4 — Separately approved live table write

Only after B0–B3 evidence is reviewed, request explicit approval for one live `houdini_graph_apply` against the displayed HIP session, base revision, canonical graph, and target container. Verify the minimum editable table, response structure, replay behavior, and one user Undo. Do not save the HIP.

### B5 — P2-V closeout

Run the complete offline suite and the approved finite live acceptance matrix, report all unverified behavior and warnings, inspect the Git diff, and wait for explicit submission approval. Do not enter rendering, HDA, arbitrary Python, wider DCC contexts, or later phases.
