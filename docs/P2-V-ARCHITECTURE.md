# P2-V architecture design review

## Review status and authorization boundary

Gate B0 and Gate B1 are complete, and the Gate B2 read-only capability slice has passed its separately performed manual Houdini GUI acceptance. Gate B2C is now authorized only to connect the pinned Codex 0.144.3 app-server to the existing authenticated loopback Bridge and live Panel through one project-scoped stdio MCP sidecar. The active B2C profile exposes exactly `houdini_scene_info` and `houdini_node_type_info`.

Gate B2C does not authorize Gate B3, a graph tool, a scene write, a cook, a render, a save, an HDA operation, or arbitrary code. `houdini_graph_validate`, `houdini_graph_apply`, and `houdini_graph_verify` remain disabled and unregistered in the active Codex profile.

Schema version `0.1.0` is frozen pre-release. This five-tool contract is an in-place correction of a rejected, unapproved four-tool draft that was never published or enabled; Git history preserves that draft for audit. Any future breaking change requires a new schema version rather than reusing `0.1.0`.

Codex remains the sole intelligent agent, planner, dialogue owner, and memory source. The MCP adapter, Bridge, Panel executor, and Houdini runtime perform only deterministic validation, transport, execution, and verification. They do not add an Agent, LLM, planner, RAG system, semantic memory, prompt rewriting, or screen control.

## P2-V scope

P2-V defines one general declarative graph surface. A table is the first acceptance fixture, not a tool, product boundary, fixed topology, or naming convention. The same contract must represent structurally different assets such as a stool, cabinet, steps, or simple building blocks without adding object-specific fields.

The exact reviewed tools are:

1. `houdini_scene_info`
2. `houdini_node_type_info`
3. `houdini_graph_validate`
4. `houdini_graph_apply`
5. `houdini_graph_verify`

Those five names describe the frozen future graph contract. The active Gate B2C runtime surface is the strict two-tool subset `houdini_scene_info` and `houdini_node_type_info`; the other three names remain dormant until a later gate explicitly authorizes them.

The current version enables only bounded OBJ/SOP creation beneath one new, validated `/obj/HIA_Graph_<id>` container. Context is a versioned field: later OBJ, SOP, DOP, LOP, VOP, MaterialX, COP, TOP, KineFX, or APEX adapters require a separately reviewed schema version rather than an open enum. P2-V does not provide arbitrary Python, HScript, expressions, callbacks, `eval`, shell execution, filesystem access, HIP save, rendering, caching, HDA creation or publishing, node deletion as a public tool, modification of an existing user node, or references outside the request-owned container.

## Trust and transport path

```text
Codex app-server (pinned 0.144.3; reasoning and tool selection)
    -> project-local MCP subprocess over stdio
Project-local Houdini MCP adapter (exactly two B2C read-only tools)
    -> authenticated HTTP to 127.0.0.1:<random-port>
Existing Bearer-authenticated loopback Bridge (validation and bounded queue)
    <- nonblocking authenticated status/read dispatch
Houdini Intelligence Panel main-thread read adapter
    -> bounded hou API reads on the Houdini UI thread only
Live Houdini session
```

The app-server remains stdio-only and is never exposed on a network socket. The MCP adapter is a project-local stdio child process, not an HTTP server. The Bridge retains its random loopback port and random session Bearer token; neither the token nor the scene gateway is exposed to a LAN or the internet. The Panel does not block its UI thread while waiting for Bridge work.

No component in this path uses Computer Use or screen takeover. B2C has no scene-mutation path: every graph tool and every write/cook/save/render/HDA operation remains disabled.

## Component responsibilities

### Codex app-server and Codex

- Codex is the sole reasoning component. Within the HIA MCP namespace it may select only the two registered B2C read tools.
- The exact two-tool claim is MCP-scoped: pinned Codex 0.144.3 has no locally proven stable native-tool allowlist that globally hides its own shell, patch, or filesystem tools. B2C fixes every Thread and Turn to a read-only, network-disabled, no-escalation policy instead of overstating that boundary.
- The frozen general graph contract remains design input for a later gate; B2C sends neither executable code nor a graph mutation request.
- The pinned Codex 0.144.3 capability and generated stable protocol baseline remain authoritative for this project.
- Existing exclusions remain in force: no experimental Schema, `dynamicTools`, process API, `thread/shellCommand`, or app-server WebSocket.

### Project-local stdio MCP adapter

- Publishes exactly `houdini_scene_info` and `houdini_node_type_info` in B2C with versioned JSON Schema. The three graph tools remain denied even though their frozen `0.1.0` artifacts stay in the repository.
- Denies every unregistered tool and rejects unknown fields where the versioned Schema requires a closed object.
- Performs structural, size, count, string, enum, and deadline validation before forwarding a request.
- Converts schema-admitted execution failures into structured MCP tool results without leaking tokens, tracebacks, or internal paths; cancellation, queue, and shutdown failures remain bounded adapter errors.
- Does not import `hou`, inspect the filesystem, make planning decisions, rewrite prompts, or execute code.
- Uses a bounded wait and cancellation path rather than keeping an unbounded tool call alive.
- Reads the authenticated status facade only to obtain trusted current correlation values; it never trusts a caller-supplied attestation or technical session snapshot.

### Authenticated loopback Bridge

- Authenticates every adapter and Panel request using the existing session Bearer token.
- Maintains a bounded in-memory request queue and one result slot per request; it is not a second chat store.
- Correlates `request_id`, Codex Thread/Turn, HIP session, revision, idempotency key, deadline, and result.
- Exposes a bounded authenticated status facade for the current safe read correlation context and five-type availability summary; reading it does not renew the capability lease.
- Rejects replay, expired requests, queue overflow, unknown tools, malformed results, and session mismatch with structured errors.
- Does not call `hou`, approve a change, synthesize a graph, or broaden the declared scope.

### Panel main-thread executor

- Reuses the P1 transport model: standard-library HTTP runs only in daemon workers, with one worker reserved for event polling and at most two control-request workers. Workers place plain Python dictionaries into a bounded thread-safe queue and never touch a widget or emit a Qt signal.
- Uses one main-thread `QTimer` to drain that queue. Every request has a finite monotonic deadline and generation; stale or post-disposal results are discarded without blocking the Houdini UI.
- Dispatches accepted work from that main-thread timer drain through an equivalent Houdini-supported main-thread scheduling mechanism; no worker emits a Qt signal carrying work.
- Performs only bounded read dispatch in B2C. Network callbacks and Bridge worker threads never call `hou`.
- Revalidates the trusted session, revision, catalog digest, Schema digest, requested allowlisted type, and deadline immediately before each read.
- Posts a structured result to the Bridge and does not retain duplicate conversation history.

### Houdini runtime adapter

- Discovers live capabilities from `hou` at runtime.
- Exposes only bounded scene metadata and the reviewed live OBJ/SOP type metadata in B2C.
- Contains no node creation, parameter mutation, connection, deletion, cook, save, render, HDA, or Undo operation in this gate.
- Leaves the future graph transaction implementation and verification path disabled until Gate B3 or a later explicitly authorized gate.

## Runtime discovery and live authority

No P2 component hard-codes a Houdini installation directory, Houdini version, Python version, or the existence and parameter layout of a node type.

The launcher or process supervisor discovers candidate Houdini installations only within separately authorized, bounded locations. Once the Panel is loaded, a trusted internal capability attestation reports the live process nonce, build, platform, Python ABI, HIP identity, available contexts, node-type catalog digest, and reviewed read-profile Schema digest. Schema digests use sorted-key, whitespace-free UTF-8 JSON so Git line-ending conversion cannot change protocol identity. This attestation is transport state, not an untrusted tool argument or a public `houdini_scene_info` response. `houdini_node_type_info` returns only the bounded node metadata defined by its output schema.

Static recipes may name reviewed logical types such as an OBJ geometry container, SOP box, SOP merge, and SOP output/null, but the live schema is authoritative. A missing or unattested live process returns `HOUDINI_UNAVAILABLE`. A build, catalog, schema-hash, type, parameter, tuple, or context mismatch returns `CAPABILITY_MISMATCH`. The executor does not guess a substitute or fall back to Python.

The public schemas require an exact `hip_session_id` and `base_scene_revision`, including for `houdini_scene_info`. This is not satisfied with a `"current"` wildcard. In B2C the authenticated `GET /v1/scene/status` facade supplies the current safe correlation context, and the Bridge-owned parameter facade replaces technical session/revision fields from that trusted snapshot before dispatch. Caller-supplied attestation, session, revision, catalog, or Schema values never become authority. If no current snapshot exists, dispatch fails with `HOUDINI_UNAVAILABLE`; a changed session or older revision fails closed rather than being silently rebased.

## Tool surface

| Tool | Access | P2-V purpose | Mutation |
|---|---|---|---|
| `houdini_scene_info` | Read-only | Return the bounded HIP session, revision, enabled contexts, and summaries of HIA-owned graphs. | None |
| `houdini_node_type_info` | Read-only | Resolve a requested allowlisted OBJ/SOP type and its writable typed-parameter Schema from the current Houdini instance. The live Schema is authoritative. | None |
| `houdini_graph_validate` | Read-only | Validate, normalize, summarize, and compute the canonical digest of a declarative graph without changing scene state. | None |
| `houdini_graph_apply` | Exact approval required | Apply the exact normalized graph inside one new `/obj/HIA_Graph_<id>` container under session, revision, deadline, digest, and idempotency controls. | Creates only the request-owned container and declared children. |
| `houdini_graph_verify` | Read-only | Read and verify the created graph's nodes, typed parameters, connections, flags, cook state, scope, and canonical graph digest. | None |

The read-only tools do not return arbitrary scene dumps, scripts, parameter expressions, secrets, environment variables, or filesystem paths. Requests and responses are bounded by explicit maximum counts and string sizes in the versioned Schemas.

Only the first two rows are available in Gate B2C. The remaining graph rows document the frozen `0.1.0` contract and are not registered, callable, or implied by the presence of their repository schemas.

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

JSON Schema closes and bounds each accepted value, but B1 must also run deterministic validators for relations that the ten schemas do not express completely:

- request-local node IDs and name hints are unique, parameter names are unique per node, parent references resolve inside the graph, and every connection endpoint resolves to an owned node and valid port;
- the graph is acyclic where the enabled context requires it, connection inputs are not multiply assigned, and all node/connection/count bounds hold;
- the current SOP slice has exactly one display node and one render node, both flags identify the same declared node, and no implicit output role is inferred from its name;
- every live-resolved type and parameter exists in the current attested Houdini catalog, uses an admitted typed value (`float`, `int`, `bool`, `string`, or homogeneous tuple), and exposes no expression, callback, code, file, or spare-parameter surface;
- the target is a new HIA-owned root matching `/obj/HIA_Graph_<id>`, and no graph item may address a pre-existing or external node;
- normalization is deterministic and the canonical graph digest changes for any target, node, type, parameter, connection, flag, or layout change; a separate request/approval digest also binds session, revision, idempotency, deadline, and correlation fields;
- result correlation echoes the submitted request, session, base revision, idempotency key, and graph digest, and the resulting revision may not regress;
- an apply replay record is keyed by the same idempotency key and canonical digest as the top-level request;
- `container_path`, every created path, and every residual path agree with the exact request-owned container;
- node-type results correspond exactly and uniquely to the requested query set;
- validation/verification check status, issues, overall validity, normalized graph, and graph digest are mutually consistent;
- success and failure branches agree with `ok`, `structured_error`, created/changed paths, replay state, and revision advancement.

Any mismatch is rejected before it can complete another request. No validator evaluates strings, imports `hou`, or broadens an enum.

## Gate B2C read-only execution sequence

1. Codex invokes either `houdini_scene_info` or `houdini_node_type_info` through the project-local stdio MCP child.
2. The adapter accepts only the pinned MCP handshake and exact two-tool allowlist, then queries the authenticated status facade for a current trusted snapshot.
3. The Bridge-owned parameter facade supplies the trusted HIP session and revision. For a node-type request, only the reviewed canonical type name remains caller intent; technical attestation fields cannot be forged through tool arguments.
4. The Bridge validates the active read profile and enqueues a bounded read for the Panel. Missing or stale capability state fails with a structured `HOUDINI_UNAVAILABLE` or `CAPABILITY_MISMATCH` result.
5. The Panel's main-thread timer executes only bounded `hou` reads and posts one correlated plain-dictionary result. Workers never call `hou` and no request changes scene state.
6. Closing the Panel or losing the live capability causes a finite explicit failure. It does not hang, renew a stale lease, start a replacement service, or infer success.

The launcher briefly asks Windows for an ephemeral IPv4 loopback port, releases the probe, and supplies the resulting Bridge URL only through the owned Bridge and Houdini child environments. The Bridge must bind that exact origin before app-server startup or fail closed; it does not retry on another port. The Bearer token is also passed only through owned child environments. Bootstrap contains neither URL nor credential, and neither may appear in configuration values, command arguments, logs, diagnostics, or documentation. The separate Panel executor credential is never inherited by Codex or the MCP child.

### Future graph transaction sequence (inactive in B2C)

The validate/approve/apply/verify sequence and transaction rules below remain the frozen design for a later gate. No part of that sequence is registered or executable in B2C. Disconnects never imply write success, and no dormant write may be replayed automatically.

1. Codex invokes one of the five frozen graph-contract tools.
2. The adapter validates the versioned Schema, attaches transport correlation, and submits the request to the authenticated Bridge.
3. `houdini_graph_validate` resolves the live allowlist, normalizes the complete graph, computes its summary and digest, and returns without scene mutation.
4. For `houdini_graph_apply`, the request remains non-executable until an exact approval proof binds that normalized graph and its correlation fields.
5. The Panel obtains work through bounded, authenticated, nonblocking polling. At most one live-HIP write enters `starting` or `inProgress` state.
6. The Panel main-thread executor repeats every precondition check against current live state before mutation.
7. A read-only tool executes directly on the main thread. A write executes only as the transaction described below.
8. The executor performs mandatory internal verification and increments the scene revision only for a successful committed mutation.
9. The Panel posts the structured result to the Bridge; the Bridge resolves the waiting adapter call once.
10. Exact idempotent retries return the recorded result; changed reuse returns `IDEMPOTENCY_CONFLICT`.

## `houdini_graph_apply` transaction

### Exact approval

Approval is per invocation and binds the complete normalized graph, graph schema version, canonical graph digest, request/approval digest, `request_id`, Thread, Turn, HIP session and fingerprint, base revision, permission level, idempotency key, trusted absolute deadline, target/root, every live-resolved node type, every name and parent reference, every typed parameter, every connection, every display/render flag, optional layout request, and a closed side-effect summary. Any change invalidates approval. The natural-language phrase that led Codex to the graph is not approval authority. P2-V provides no permanent, wildcard, or object-class approval.

The approval UI must show a bounded exact summary of the new HIA-owned graph and state that the operation does not save the HIP. Denial or dismissal returns `APPROVAL_DENIED` without calling a mutating `hou` API.

### Preconditions

- `hip_session_id` equals the live session.
- `base_scene_revision` equals the current revision; last-write-wins is forbidden.
- The deadline has not expired.
- The idempotency record is new or is an exact replay with a known result.
- The target is one direct child of `/obj` and matches the reviewed `HIA_Graph_<id>` naming grammar.
- The target name does not already exist.
- Every node type, node name, parameter, value, and connection passes the live-schema and static-policy checks.
- The canonical request has a matching, unexpired approval proof.

### Allowed mutation

The executor creates one new OBJ geometry container and only the normalized SOP nodes declared inside it. It cannot traverse to or reference an existing user node, alter the current selection as a required side effect, or modify `/obj` children outside the new container.

The normalized graph contains:

- a versioned context declaration (OBJ root with SOP children in this stage);
- a target/root policy requiring one new HIA-owned container;
- bounded request-local node IDs, live-resolved allowlisted types, name hints, and parent references;
- closed typed parameters using only finite `float`, `int`, `bool`, bounded `string`, or homogeneous tuples;
- owned-node source/output to destination/input connections;
- explicit display/render flags and an optional bounded layout request;
- exact session, revision, idempotency, deadline, and canonical graph digest.

The initial safe type allowlist may remain deliberately small (`Object/geo`, `Sop/box`, `Sop/transform`, `Sop/merge`, and `Sop/null`), but it is a capability allowlist rather than an asset recipe. Node types and parameter names are resolved against the live catalog before mutation. The same schema must accept fixtures with different node counts, names, parameter values, and topology.

The entire creation executes inside one `hou.undos.group`, so one user Undo removes the complete request-owned graph. The tool never invokes Undo on the user's behalf after a successful commit.

### Failure containment

The executor records the newly created container object as request-owned before creating children. If a later operation fails, rollback may destroy only that exact container created by the current request, while still inside the controlled transaction. It must first prove object identity, parent `/obj`, expected HIA name, current request ownership, and absence before this request. It never searches for similarly named nodes and never deletes or repairs pre-existing content.

If those proofs are unavailable, the executor stops and returns a high-severity structured error rather than guessing. No public delete tool is exposed.

### Postconditions

- The container and all normalized declared children exist with the expected live types.
- Connections match the approved declaration.
- Parameter values round-trip to the accepted values.
- Display/render flags exactly match the normalized declaration and no undeclared node receives a flag.
- Cook and node errors are returned as bounded warnings/errors.
- Every created path is below the exact request-owned container.
- `created_nodes` is complete, `changed_nodes` contains no pre-existing path, and the successful revision advances exactly once.

Postcondition failure triggers the same request-scoped rollback before a result is committed.

## Revision, HIP session, and idempotency

`hip_session_id` changes whenever the active HIP identity is replaced, cleared, or reloaded. A monotonic in-session `scene_revision` advances for accepted tool writes and for observed manual scene changes relevant to the protected scope. The exact Houdini event callbacks used to observe manual changes must be capability-tested in the live build; missing reliable observation blocks writes rather than permitting stale updates.

A request based on an older revision returns `SCENE_CONFLICT` with the safe current revision and requires Codex to inspect again. A request from another HIP session returns `HIP_SESSION_MISMATCH`. Neither path performs a mutation.

Idempotency state stores only technical correlation and a canonical request/result digest. It does not contain chat history or semantic memory. Retention is bounded to the current Bridge/HIP session for P2-V unless a reviewed persistence design is approved later.

## Project-scoped Codex MCP configuration

Gate B2C adds one reviewed repository artifact, `.codex/config.toml`, with one server named `houdini_intelligence`. Its static reviewed default is a stdio command for `D:\Python_3.10\python.exe`, arguments `-B -m hia_houdini_mcp.stdio`, project `cwd` `E:\houdini-intelligence-agent`, `required = false`, a 5-second startup timeout, and a 65-second tool timeout. The non-required repository default lets ordinary Codex Desktop development sessions load without a live Houdini launcher environment. The Bridge uses per-process Codex `-c` overrides to replace the command with the already verified `sys.executable` and set `required=true` for the owned Houdini runtime; runtime code never rewrites the tracked TOML.

The configuration enables exactly `houdini_scene_info` and `houdini_node_type_info` and explicitly disables `houdini_graph_validate`, `houdini_graph_apply`, and `houdini_graph_verify`. It forwards only the reviewed environment variable names needed by the owned child. It contains no literal Bridge URL, Bearer value, API key, login material, executor credential, shell command string, network listener, or additional MCP tool.

The dynamic `HIA_BRIDGE_URL` and `HIA_BRIDGE_TOKEN` exist only in short-lived owned child environments and never in bootstrap. `HIA_SCENE_EXECUTOR_TOKEN` is intentionally absent from the Codex and MCP child environment. A Python 3.10 standard-library closed-grammar source contract test validates the fixed repository syntax without a third-party parser. Separately, the pinned Codex 0.144.3 executable's finite read-only `mcp get --json` command provides authoritative syntax evidence without starting the server.

## Security invariants

- Only `127.0.0.1` with a random port and random Bearer token is used for Bridge control traffic.
- The token is never logged, returned in tool results, persisted in chat, or placed in command-line arguments.
- The adapter has no arbitrary URL, command, environment, or path parameter.
- The active two-tool read allowlist is enforced independently by Codex configuration, MCP registration, Bridge routing, and Panel dispatch; the frozen five-tool contract does not broaden B2C.
- A read tool cannot be upgraded into a write by a request field.
- All `hou` calls execute on the Houdini UI thread; the live HIP has one writer.
- No request can create outside one new `/obj/HIA_Graph_*` container or refer to existing user nodes.
- No save, file write, render, HDA, Python, HScript, expression, `eval`, shell, or public delete surface exists.
- Manual edits cause conflict rather than last-write-wins.
- Unknown fields, types, methods, node types, parameters, paths, and states fail closed.
- The live Houdini schema overrides static assumptions; no fallback executes arbitrary code.

## Reference-image workflow and future tool families

Reference images remain Codex inputs, not inputs to a second vision service. The intended loop is:

```text
user image
  -> Codex native visual understanding
  -> Codex produces a general graph request
  -> houdini_graph_validate
  -> exact normalized-graph approval
  -> houdini_graph_apply
  -> separately authorized independent render or screenshot
  -> Codex native visual review and iteration
```

Codex alone performs visual interpretation, modeling choices, uncertainty handling, planning, dialogue, and memory. OpenCV may later supply bounded deterministic measurements, but no additional visual Agent, model, planner, RAG layer, or semantic-memory service is permitted.

Capabilities that exceed the current OBJ/SOP graph transaction require separately reviewed versioned tool families. They are not fields, modes, escape hatches, or opaque payloads inside `houdini_graph_apply`: geometry/query, MaterialX/materials, Solaris/LOP, Karma render jobs, animation/keyframes, simulation/cache jobs, HDA validation/publishing, and approval-gated controlled Python jobs each receive their own future contract and phase gate.

## Frozen Gate B0 defaults for B1

These conservative defaults are frozen for Gate B1. Changing one requires a versioned contract review:

1. Bridge routing is limited to `POST /v1/scene/requests`, `GET /v1/scene/requests/{request_id}/result`, `GET /v1/scene/requests/next`, `POST /v1/scene/requests/{request_id}/approval`, `POST /v1/scene/requests/{request_id}/result`, and `POST /v1/scene/requests/{request_id}/cancel`. `POST` accepts atomically and returns HTTP 202 with the request ID, canonical digest, and pending state, or HTTP 200 for an already terminal exact replay. The adapter obtains a terminal HTTP 200 result through result long-polls of at most 1,000 ms until the original absolute deadline; 202 remains pending and never means scene success. No unbounded HTTP request exists.
2. The typed scene-approval route is independent of P1's app-server `/v1/approval` route and cannot answer one of its requests. Its closed body contains only `decision=allow|deny`, the exact `request_digest`, Bridge `launch_id`, and `generation`. The authenticated Bridge stamps receipt time and, only for an exact pending apply, creates an internal one-use proof expiring at the earlier of the absolute request deadline or 60 seconds. The proof is never returned to MCP arguments; digest/generation mismatch, duplicate decision, expiry, or disconnect denies execution.
3. The queue holds at most 32 requests, an event/result poll waits at most 1,000 ms, and an HTTP JSON body is at most 262,144 bytes. The schema's 100–60,000 ms budget is converted once to an absolute monotonic deadline that no retry or poll can reset.
4. The stdio adapter admits only JSON-RPC `initialize`, `notifications/initialized`, `ping`, `tools/list`, `tools/call`, and `notifications/cancelled`. Its historical B1 baseline was MCP `2024-11-05`; B2C negotiates only the pinned stable protocol versions reviewed against Codex 0.144.3 and never enables an experimental transport. A UTF-8 JSONL line is at most 262,144 bytes with nesting at most 32. An unknown request receives JSON-RPC method-not-found; an unknown notification is safely recorded and ignored; malformed, duplicate-key, non-finite, oversized, or over-deep input terminates that protocol session without replay.
5. Canonical bytes are UTF-8 JSON with recursively sorted object keys, no insignificant whitespace, preserved Unicode, rejected duplicate keys, and rejected NaN/infinity. SHA-256 binds contract version, tool name, request/Thread/Turn, HIP session/fingerprint, base revision, idempotency key, permission, trusted absolute monotonic deadline, and complete arguments.
6. Identifiers, names, node/connection counts, typed values, flags, layout, and strings are exactly bounded by the versioned general graph schemas. The schema contains no asset role, fixed object topology, fixed dimensions, or hidden recipe. B1 adds no aliases.
7. B1 accepts only a fixed fake capability attestation whose process/session/catalog/schema digests match its fixtures. The trusted session snapshot is bound to one Bridge launch identifier and monotonically increasing generation; Panel disconnect, Bridge restart, HIP replacement, or generation change invalidates it. Without a current attestation, all five tools fail with `HOUDINI_UNAVAILABLE`; a mismatch fails with `CAPABILITY_MISMATCH`. No live dispatch exists in B1.
8. Approval is external to untrusted MCP arguments, one-use, and bound to the canonical digest. It expires at the earlier of the trusted absolute request deadline or 60 seconds after grant. Denial, dismissal, disconnect, reuse, or any changed bound field fails without queueing a write.
9. Cancellation before executor claim is terminal. Cancellation after claim is only a request to the deterministic executor and cannot be reported as successful until a terminal result exists. Queue overflow and shutdown reject new work; shutdown resolves every pending waiter once.
10. Idempotency records are in-memory, scoped to the current Bridge/HIP session, and capped at 256 terminal entries. Exact same-key/same-digest replays return the recorded result; changed digests return `IDEMPOTENCY_CONFLICT`. B1 never assumes success across a restart or automatically retries an indeterminate write.
11. The public read summaries are exactly the bounded output-schema fields. Process/build/catalog data stays in the trusted attestation; no selection dump, arbitrary scene tree, environment, user filesystem path, or raw parameter data is returned.
12. Apply must perform mandatory internal postcondition validation. A later Codex call to `houdini_graph_verify` is an additional read, not a substitute. B1 proves both only with fake state.
13. B2C resolves only the exact project-local two-tool read-only MCP TOML and finite read chain. Write approval UI, Undo, cook, rollback, and every graph mutation behavior remain unresolved and fail closed until separately approved B3/B4 work.

No unresolved live item may be filled by a permissive default.

## Gate B sub-gates

### B0 — Design review

Produce and review architecture, five versioned tool pairs, general graph fixtures, threat model, offline contract tests, and test plan. The contract test rejects asset-specific roles and fixed structures in protocol files. No MCP configuration, service startup, Houdini process, or `hou` write is authorized.

### B1 — Offline transport and contract implementation

After corrected B0 approval, implement the stdio MCP adapter, five-tool deny-by-default registration, deterministic general graph validator, authenticated Bridge queue, structured results, and a fake Panel executor. B1 is fake-only and live-disabled: validate all Schema, approval, revision, idempotency, Unicode, timeout, authentication, replay, and shutdown behavior against at least two structurally different fixtures without importing `hou`, starting Houdini, or dispatching to a live process.

### B2 — Read-only Houdini capability slice

Gate B2A implemented the Panel-side main-thread read adapter and offline fake-Houdini tests. Gate B2B was accepted manually in Houdini and established the bounded build/session/revision/catalog view without authorizing an automated write or treating screenshots as machine-generated evidence.

Gate B2C connects that existing read adapter to the pinned Codex app-server through the project-local stdio MCP child and authenticated Bridge. Exactly `houdini_scene_info` and `houdini_node_type_info` are enabled. The Bridge-owned status parameter facade supplies trusted current correlation data; caller arguments cannot forge attestation. `houdini_graph_validate`, `houdini_graph_apply`, and `houdini_graph_verify` remain disabled. Missing capability returns `HOUDINI_UNAVAILABLE`, mismatch returns `CAPABILITY_MISMATCH`, and Panel loss returns a bounded explicit failure. Do not mutate the scene.

### B3 — Write-path simulation and approval acceptance

Use a pure-Python fake scene graph and executor, with no `hou` module, to prove exact approval binding, single-writer serialization, transaction boundaries, modeled one-step Undo semantics, conflict rejection, idempotent replay, rollback confinement, and postcondition verification. No real HIP mutation is authorized and fake behavior is not evidence about Houdini.

### B4 — Separately approved live graph write

Only after B0–B3 evidence is reviewed, request explicit approval for one live `houdini_graph_apply` against the displayed HIP session, base revision, complete normalized graph, canonical digest, and target container. Verify a selected acceptance fixture, response structure, replay behavior, and one user Undo. Object semantics are supplied by Codex and the fixture, never by the tool. Do not save the HIP.

### B5 — P2-V closeout

Run the complete offline suite and the approved finite live acceptance matrix, report all unverified behavior and warnings, inspect the Git diff, and wait for explicit submission approval. Do not enter rendering, HDA, arbitrary Python, wider DCC contexts, or later phases.
