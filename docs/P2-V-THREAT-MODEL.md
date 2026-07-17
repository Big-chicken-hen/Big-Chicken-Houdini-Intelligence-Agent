# P2-V threat model

## Status and scope

The Gate B0 five-tool security contract remains frozen pre-release at `0.1.0`. Gate B1 is complete, and Gate B2A/B2B established the bounded read-only Houdini adapter through offline tests and a separate manual GUI acceptance. Gate B2C is authorized only to connect the pinned Codex 0.144.3 app-server to that read adapter through one project-local stdio MCP child and the authenticated loopback Bridge.

The schema of the active, runtime-discovered Houdini build, its main-thread behavior, cooking behavior, rollback behavior, and one-step Undo behavior are **not yet verified with real `hou`**. Static node and parameter names in fixtures are only offline candidates and must be intersected with runtime introspection before writes are enabled. A live-schema conflict fails closed; static documentation never overrides the running Houdini build.

Codex remains the sole intelligent agent, planner, and memory source. The MCP adapter, authenticated Bridge, Panel executor, and `hou` adapter are deterministic validation and execution components only. They must not add an Agent, planner, prompt rewriter, RAG system, semantic memory, or model call.

The frozen future P2-V contract contains:

1. `houdini_scene_info`
2. `houdini_node_type_info`
3. `houdini_graph_validate`
4. `houdini_graph_apply`
5. `houdini_graph_verify`

The active B2C MCP registration admits only `houdini_scene_info` and `houdini_node_type_info`. `houdini_graph_validate`, `houdini_graph_apply`, and `houdini_graph_verify` are explicitly disabled and unregistered; their frozen schemas do not grant runtime capability. All other tool names and all unknown fields fail closed. Gate B3 and every write remain unauthorized.

## Security invariants

- The network boundary remains the existing random-port HTTP Bridge bound and verified as `127.0.0.1` only. The Codex app-server and MCP adapter use stdio and are never exposed on a socket. P2-V adds no listener, remote mode, WebSocket, fixed port, LAN access, or internet-facing endpoint.
- Every HTTP request is authenticated with the existing random session Bearer token. Tokens, approval capabilities, and process credentials never enter Git, configuration values, command arguments, logs, events, errors, or audit rows.
- The independent Panel executor credential is inherited only by the owned Panel path. Codex and its MCP child never receive `HIA_SCENE_EXECUTOR_TOKEN`.
- Every live-scene write runs on Houdini's UI main thread through one FIFO writer queue. RPC, HTTP, stdio, and standard-library HTTP worker threads never call `hou`; workers return only plain dictionaries through a bounded queue that one main-thread `QTimer` drains.
- Every write is bound to `hip_session_id`, HIP fingerprint, `base_scene_revision`, `thread_id`, `turn_id`, `request_id`, deadline, permission level, approval digest, and idempotency key. These values are rechecked immediately before mutation on the main thread.
- There is at most one in-flight write for a live HIP and no concurrent write for the same Thread. Last-write-wins is forbidden.
- The initial admitted canonical node types are `Object/geo`, `Sop/box`, `Sop/transform`, `Sop/merge`, and `Sop/null`. This is a safety allowlist, not an asset recipe. Namespaced aliases, unreviewed versions, operator aliases, HDAs, and every other type are rejected.
- A write may create exactly one new container matching `/obj/HIA_Graph_<id>`, where `<id>` is a bounded server-validated ASCII identifier. The container must not already exist. Every child is declared by a request-local ID, live-resolved allowlisted type, bounded name hint, and owned parent reference.
- Graph size and connection cardinality are bounded. Node IDs, name hints, and per-node parameter names are unique; parents and every source/output and destination/input endpoint resolve inside the same request-owned container; inputs are not multiply assigned; and the current SOP graph is acyclic. No fixed number of primitives, fixed node roles, object dimensions, or topology is part of the protocol.
- Only closed typed parameters (`float`, `int`, `bool`, bounded `string`, or homogeneous tuple), reviewed connection indices, explicit flags, and optional bounded layout are admitted. The current SOP slice has exactly one display node and one render node, with both flags on the same declared node and no name-implied output role. Parameters are intersected with an exact per-type safety allowlist and the live schema. Expressions, callbacks, code, buttons, multiparms, file parameters, and arbitrary names never reach `hou`.
- No request field accepts a filesystem path, unconstrained node path, Python, HScript, expression, backtick command, environment expansion, callback, script language, serialized code, or shell command. The only node-path form is the validated HIA-owned target/root; graph references use request-local IDs. String values are never evaluated.
- P2-V never deletes, renames, reparents, disconnects, rewires, changes flags on, or sets parameters on an existing user node. The only internal destruction allowed is rollback of the exact container object proven to have been created by the current failing operation.
- P2-V never saves or overwrites a HIP, writes a file, creates a cache/render/HDA, changes a Houdini definition, installs a package, edits configuration, or expands network access.
- One successful apply is enclosed completely in one `hou.undos.group`. A later live acceptance test must prove that one Undo removes the new request-owned graph and changes nothing else before scene writing can be called production-ready.

## Assets

| Asset | Required property |
|---|---|
| User's live HIP and all pre-existing nodes | Integrity and availability; no implicit save or destructive modification |
| Newly created HIA-owned graph | Exact normalized declaration, typed parameters, deterministic ownership and provenance |
| HIP session identity, fingerprint, and scene revision | Freshness and atomic conflict detection |
| Idempotency records | A retry cannot duplicate a graph; a key cannot be rebound to different normalized arguments |
| User approval | Authenticity, freshness, exact scope, and binding to the canonical operation |
| Bridge Bearer token and process credentials | Confidentiality; never persisted or logged |
| MCP/tool allowlist and JSON Schemas | Integrity and version pinning; no runtime expansion |
| Main-thread writer queue | Single-writer ordering and bounded execution |
| Audit records | Accurate, secret-free, append-only technical evidence |
| Structured errors | Safe diagnostics without traceback-only or secret-bearing responses |

Chat history, prompt text, and semantic summaries are not P2-V technical-state assets to duplicate. Codex Threads remain authoritative for conversation history.

## Trust boundaries and data flow

```text
Codex app-server
    | stable stdio JSONL; tool arguments are untrusted
    v
project-local MCP adapter
    | exact two-tool B2C read allowlist and strict JSON Schema
    v
Bearer-authenticated 127.0.0.1 Bridge
    | trusted status parameter facade; no prompt rewriting
    v
Houdini Panel gateway / bounded read queue
    | validated read item; no hou call on I/O thread
    v
Houdini UI main-thread executor
    | attestation revalidation and bounded hou reads only
    v
live HIP (user asset)
```

Trust does not imply accepting inputs without validation:

- Codex is trusted to reason, but model output and tool arguments are treated as untrusted data at every deterministic boundary.
- The MCP adapter is trusted only to enforce its pinned schemas and translate the two B2C read tools. It receives no general execution capability.
- The Bridge is trusted to authenticate, correlate, bound, queue, and audit. It does not decide scene intent.
- The Panel gateway is trusted to dispatch bounded reads to the main thread and report current HIP state. It does not execute a caller-supplied program or scene mutation.
- `hou` and the loaded HIP are inside the scene-execution boundary, but node names, callbacks, state changes, and runtime schema are not assumed benign or stable.

## Attacker and failure model

P2-V accounts for:

- A same-user local process probing the loopback port, stealing or replaying a leaked token, or racing requests.
- Malformed, oversized, duplicated, out-of-order, or polluted stdio and HTTP messages.
- Prompt injection, model error, or compromised conversation content producing malicious but syntactically valid tool arguments.
- Stale Panel, Bridge, MCP, Thread, Turn, or HIP-session state after restart, HIP load/new, interrupt, or manual scene edits.
- A hostile or unusual existing HIP containing colliding names, locked nodes, callbacks, unusual operator definitions, or manual changes during a request.
- Accidental operator error, incomplete exception handling, main-thread misuse, cook stalls, and partial rollback/Undo behavior.
- A future code or configuration change that silently expands the MCP, app-server, node, parameter, filesystem, or network surface.

This phase does not claim protection from an administrator, kernel compromise, a debugger attached to Houdini, or arbitrary code already executing as the same Windows user. Those actors can read process memory and alter the running program.

## STRIDE-style threats and controls

| Threat | Class | Required controls and failure behavior |
|---|---|---|
| Non-JSON output, log text, or forged responses pollute app-server/MCP stdio | Tampering, spoofing, denial of service | Keep stdout protocol-only and stderr separate; impose line and message-size bounds; require valid JSON-RPC version, identifier correlation, known method, and exact schema. A malformed stdout line closes the affected protocol session and rejects pending writes; it is never interpreted as a tool result or log. Restart may target only the exact owned child process and does not auto-replay a write. |
| Bearer token disclosure or replay | Spoofing, elevation of privilege, repudiation | Generate a high-entropy per-launch token, pass it only to owned child processes, compare safely, redact it everywhere, and reject missing or malformed authorization. Bind each write approval to a canonical request digest and one session. `request_id`, deadline, approval nonce, and idempotency semantics make an identical replay a no-op and reject a changed replay. Token theft by another same-user process remains a residual risk. |
| Accidental non-loopback exposure | Information disclosure, spoofing, elevation of privilege | The launcher obtains an OS-selected ephemeral IPv4 loopback port, and the Bridge binds and verifies that exact `127.0.0.1` origin before app-server startup. Reject `0.0.0.0`, `::`, hostnames that may resolve externally, fixed port 8100, port forwarding, and a second scene-gateway listener. Startup fails if the selected port was taken or loopback-only binding cannot be proven; it never falls back to another address. |
| Malicious, oversized, deeply nested, non-finite, or extra tool arguments | Tampering, denial of service, elevation of privilege | Validate byte size, JSON depth, array counts, string lengths, finite numbers and safe dimension ranges; use schemas with `additionalProperties: false`; canonicalize before approval and hashing. Reject unknown fields and values before queuing. Never pass generic dictionaries through to `hou`. |
| Arbitrary Python, HScript, expression, callback, shell, eval, or filesystem path injection | Elevation of privilege, tampering, information disclosure | Publish no execute tool and accept no filesystem or unconstrained node-path field. Forbid expression setters, `hou.hscript`, `eval`, `exec`, Python SOPs, callbacks, environment expansion, backticks, file parameters, shell/process APIs, and executable strings. `houdini_graph_apply` maps closed typed values, owned local-ID connections, and flags to reviewed calls only. |
| Node-type aliases or parameter names escape the allowlist | Elevation of privilege, tampering | Match exact category and canonical type name against only `Object/geo`, `Sop/box`, `Sop/transform`, `Sop/merge`, and `Sop/null`; reject unreviewed namespaces, versions, aliases, HDAs, and dynamic types. Intersect a static per-type safety allowlist with read-only live introspection. Reject spare parameters, expressions, multiparms, buttons, callbacks, and every parameter not explicitly admitted. |
| Scene revision changes between validation and mutation (TOCTOU) | Tampering, repudiation | Capture session/fingerprint/revision on receipt, then atomically re-read and compare all three after dequeue and immediately before the first `hou` write on the main thread. Manual edits advance revision. A mismatch returns `SCENE_CONFLICT` with no mutation; no last-write-wins or silent rebase. |
| Two Turns or clients write concurrently | Tampering, denial of service | Use one FIFO writer queue and a live-HIP write lock; atomically reserve `starting/inProgress` state per Thread and HIP. A second write returns structured `WRITE_IN_PROGRESS`/HTTP 409 before approval consumption or mutation. Read tools cannot promote themselves into writes. |
| Same idempotency key is used for different operations | Tampering, repudiation | Scope the key to project/HIP session/tool and store a canonical argument plus approval digest. Same key and same digest returns the recorded result without executing. Same key and different digest returns `IDEMPOTENCY_CONFLICT`/409. Failed or indeterminate records cannot be silently retried as new work. |
| Approval is substituted, widened, delayed, or applied to another HIP/Turn | Spoofing, tampering, repudiation | Show the complete normalized graph and bounded summary. The one-time decision binds request/thread/turn, target/root, every node/type/parameter/connection/flag/layout item, HIP session/fingerprint, revision, idempotency key, deadline, schema version, and canonical graph digest. The initiating natural-language phrase grants no authority. Re-normalization must match immediately before execution. Any mismatch, expiry, or reuse returns `APPROVAL_MISMATCH` with no write. |
| Validate and apply normalize the same graph differently | Tampering, repudiation | Put canonicalization in one versioned deterministic implementation and bind its version plus canonical bytes into the digest. `houdini_graph_apply` repeats normalization against the current live type Schema and requires exact digest equality with the approved `houdini_graph_validate` result. A live Schema, target, revision, or normalized-byte difference invalidates approval and returns a structured mismatch without mutation. |
| An I/O or worker thread calls `hou` | Tampering, denial of service | The gateway's I/O side may only parse, validate, and enqueue immutable work. Assert UI-main-thread identity in the executor before every read/write batch. Failure returns the schema-admitted `MAIN_THREAD_REQUIRED`; it never falls back to calling `hou` on the current thread. |
| A request targets a prior HIP after New/Open/reload | Tampering, repudiation | Generate a new `hip_session_id` when the HIP lifecycle changes and compute a fresh fingerprint. Invalidate pending approvals, queued writes, and idempotency scope from the old session. Recheck at dequeue. Return `HIP_SESSION_MISMATCH` rather than applying to the current file. |
| `/obj/HIA_Graph_<id>` or a declared child name already exists | Tampering, availability | Validate the identifier and every name hint, then check the exact container path on the main thread. Never overwrite, reuse, merge into, rename, or choose a silent suffix. Return `NAME_CONFLICT` without mutation. Children are created only under the newly returned container object, not located globally by name. |
| Existing nodes are changed or destroyed through path/connection escape | Tampering, elevation of privilege | The caller supplies no arbitrary destination path. All created-node and connection endpoints must be object identities allocated during the current operation and members of its ownership set. Reject connections to pre-existing nodes and parent traversal. No delete tool is published. Post-verification confirms that changed nodes are a subset of the newly created container graph. |
| An exception leaves a partial graph or rollback damages user data | Tampering, repudiation | Track the exact container object returned by the current create call and tag transient operation ownership. On failure, rollback may act only on that exact new object after proving identity and ownership. It must never search by prefix or delete an existing collision. If cleanup cannot be proven or fails, return `ROLLBACK_FAILED`, mark scene state indeterminate, block later writes, and require user inspection; never save the HIP. |
| Undo grouping omits a parameter, connection, flag, or rollback action | Tampering, repudiation | Enclose container creation, all declared child creation, typed parameter sets, wiring, flags, admitted layout, and commit bookkeeping in one `hou.undos.group`. No mutation may occur before or after it. Live tests must snapshot existing nodes and prove one Undo removes only the request-owned graph. Until that test passes, Undo completeness remains unverified and scene writing is not production-approved. |
| A cook or `hou` call blocks Houdini | Denial of service | Admit only a bounded acyclic graph from the small reviewed type allowlist; enforce node/connection/count, string, tuple, and finite numeric bounds; reject expired work before execution; avoid caller-selected arbitrary cooks and nodes with external dependencies. Record deadlines and stop scheduling new writes after overrun. In-process HOM cannot be reliably killed safely, so a started main-thread stall remains a documented residual risk and must never trigger process-name termination. |
| Logs, events, tracebacks, or errors disclose secrets or duplicate chat | Information disclosure | Use structured secret-free logging with field allowlists. Redact Authorization, environment, approval nonce/capability, raw stdio, raw prompt/chat, filesystem/user paths, and credential-bearing exception text. Audit canonical argument hashes and bounded technical metadata rather than raw chat. Tracebacks may go only to project-local diagnostic logs after redaction and are never the sole user-facing error. |
| MCP tools silently expand through discovery or a dependency update | Elevation of privilege | Pin the adapter and frozen five-tool manifest, while independently requiring the active B2C `tools/list` and project configuration to contain exactly the two read tools. Explicitly disable all three graph tools and reject unknown calls even if Codex advertises them. Any tool-surface change requires a new review gate. |
| Experimental app-server methods or transport become dependencies | Elevation of privilege, tampering | Continue using the pinned stable Codex 0.144.3 stdio contract. Exclude experimental Schema, app-server WebSocket, `dynamicTools`, process APIs, `thread/shellCommand`, remote control, Computer Use, and screen takeover. A method absent from the reviewed stable allowlist fails closed. |
| Runtime or installer changes project/global configuration | Tampering, elevation of privilege | `.codex/config.toml` is a reviewed tracked source artifact with one closed server table and a non-required default so an ordinary Codex Desktop project session cannot be denied by the absent Houdini launcher environment. Runtime code never rewrites it or changes global `CODEX_HOME`, Houdini preferences, registry, PATH, or user/machine environment. The Bridge may override only the verified child command and `required=true` through per-process Codex `-c`, preserving fail-closed behavior for the owned Houdini runtime. Fail on an unexpected server, key, tool, URL transport, or environment value. |
| Bridge URL or Bearer token is persisted or disclosed | Spoofing, information disclosure | The tracked TOML contains environment variable names only. The launcher obtains an OS-selected IPv4 loopback port and supplies `HIA_BRIDGE_URL` and `HIA_BRIDGE_TOKEN` only through owned child environments. The Bridge must bind that exact origin or fail closed. Bootstrap explicitly contains neither value. Neither value may enter command arguments, config values, logs, diagnostics, tool results, or acceptance documents. Redaction tests cover every failure path. |
| Codex or MCP inherits the Panel executor credential | Elevation of privilege | Build the child environment from an explicit allowlist that excludes `HIA_SCENE_EXECUTOR_TOKEN`. The ordinary Bridge Bearer can access only the bounded status/read surface; it cannot publish or impersonate live capability. |
| A caller forges HIP session, revision, catalog, or Schema fields | Spoofing, tampering | The authenticated status facade returns only the current safe correlation context. The Bridge-owned parameter facade replaces technical fields from that trusted snapshot and never accepts caller attestation. Status reads do not renew the capability lease; stale or absent state fails closed. |
| Panel loss leaves an MCP call hanging or causes stale fallback | Denial of service, spoofing | Bound status, queue, tool, and shutdown waits. Panel disconnect or capability expiry returns one explicit terminal error, discards late results by generation, and never starts a replacement Panel, renews stale state, or fabricates a read. |

## Capability attestation and staged live enablement

B1's capability fixture was explicitly fake. B2A/B2B added and manually accepted the Panel-side read adapter, while B2C connects only that existing read slice to the real MCP chain. Each active read fails with `HOUDINI_UNAVAILABLE` if a current trusted capability attestation is absent and with `CAPABILITY_MISMATCH` if its process nonce, build, HIP session/fingerprint, revision, catalog digest, or Schema digest differs.

The attestation is issued by the trusted Panel side and bound to one current Houdini process and HIP session. It is never accepted from untrusted MCP arguments, and a value from a prior process or HIP cannot be refreshed by changing a request field. The authenticated status facade exposes only safe current correlation fields and a five-type availability summary; it does not expose process nonce, publisher/observer identity, executor credential, or a full parameter catalog, and reading it does not renew the lease. The Bridge-owned parameter facade supplies exact session and revision values with no `"current"` wildcard.

In B2C, exactly `houdini_scene_info` and `houdini_node_type_info` may be live-enabled after attestation. `houdini_graph_validate`, `houdini_graph_verify`, and `houdini_graph_apply` remain disabled at configuration, registration, Bridge routing, and Panel dispatch layers. A live write cannot be enabled before a separately approved later gate, regardless of schema validity, fake-test success, or model request.

## Write transaction and fail-closed order

`houdini_graph_apply` must follow this order; a later step may not compensate for skipping an earlier one:

1. Authenticate the loopback HTTP request and enforce request/message bounds.
2. Resolve only an exact admitted MCP tool and validate against the pinned schema with no extra properties.
3. Run the same deterministic normalization rules as `houdini_graph_validate`, compute the canonical graph/request digests, and never evaluate a caller string.
4. Verify the one-time approval is current and bound to the complete normalized graph, target, every node/type/parameter/connection/flag/layout field, both digests, Thread/Turn, HIP session/fingerprint, revision, idempotency key, and deadline.
5. Atomically check/reserve the per-HIP and per-Thread writer state and idempotency record.
6. Enqueue an immutable work item. The I/O path returns no success before a terminal executor result exists.
7. On the Houdini main thread, re-read HIP session, fingerprint, revision, deadline, collision state, live node types, and admitted parameter definitions.
8. Enter one Undo group, create the new container, retain its object identity as the ownership root, create only normalized declared SOP children, set only typed admitted values, connect only owned nodes, and set exactly the declared flags/layout.
9. Verify exact type, parent, name, typed parameters, connections, flags, cook state, canonical graph digest, ownership set, and absence of changes outside the new graph.
10. Record the terminal idempotency result, advance scene revision exactly once, release writer state, emit the structured response, and append the secret-free audit record.

Any failure before step 8 produces no scene mutation. A failure during or after step 8 rolls back only the proven new ownership root. If rollback or post-state certainty fails, the executor returns an indeterminate error, disables further writes for that HIP session, and asks the user to inspect or Undo manually. It does not auto-save, auto-retry, reload the HIP, or kill Houdini.

## Structured error contract

Every tool response is JSON-serializable and contains its reviewed scene envelope. This is a complete schema-valid `houdini_scene_info` failure example:

```json
{
  "request_id": "request-001",
  "ok": false,
  "hip_session_id": "hip-session-001",
  "base_scene_revision": 11,
  "scene_revision": 12,
  "idempotency_key": "idempotency-key-0001",
  "created_nodes": [],
  "changed_nodes": [],
  "artifacts": [],
  "warnings": [],
  "structured_error": {
    "code": "SCENE_CONFLICT",
    "message": "The live scene changed before this operation could start.",
    "retryable": true,
    "details": {
      "issues": [
        {
          "code": "SCENE_CONFLICT",
          "message": "Expected revision 11 but observed revision 12."
        }
      ]
    }
  },
  "job_id": null,
  "scene": null
}
```

Messages are safe for the user and must not contain tokens, approval material, raw environment data, raw chat, or a traceback. `details` is a bounded, code-specific array. The five output schemas admit exactly this closed tool-result union: `INVALID_ARGUMENT`, `SCHEMA_INVALID`, `NODE_TYPE_NOT_ALLOWED`, `NODE_TYPE_UNAVAILABLE`, `PARAMETER_NOT_ALLOWED`, `PARAMETER_TYPE_MISMATCH`, `PATH_SCOPE_VIOLATION`, `GRAPH_INVALID`, `TOPOLOGY_NOT_ALLOWED`, `DIGEST_MISMATCH`, `APPROVAL_REQUIRED`, `APPROVAL_DENIED`, `APPROVAL_MISMATCH`, `APPROVAL_EXPIRED`, `DEADLINE_EXCEEDED`, `HIP_SESSION_MISMATCH`, `SCENE_CONFLICT`, `IDEMPOTENCY_CONFLICT`, `NAME_CONFLICT`, `MAIN_THREAD_REQUIRED`, `CAPABILITY_MISMATCH`, `HOUDINI_UNAVAILABLE`, `WRITE_IN_PROGRESS`, `GRAPH_NOT_FOUND`, `OWNERSHIP_MISMATCH`, `COOK_FAILED`, `VERIFY_FAILED`, `POSTCONDITION_FAILED`, `ROLLBACK_FAILED`, `SCENE_STATE_INDETERMINATE`, `BRIDGE_DISCONNECTED`, and `INTERNAL_ERROR`. A deterministic tool may emit only the relevant member and never invent another code.

`AUTH_REQUIRED`, `TOOL_NOT_ALLOWED`, `MALFORMED_REQUEST`, `REQUEST_TOO_LARGE`, `CANCELLED`, `QUEUE_FULL`, and `SHUTTING_DOWN` are bounded HTTP/JSON-RPC/MCP control-plane errors, not values injected into tool outputs whose schemas do not admit them. Request-size rejection occurs at the HTTP/adapter boundary before a tool result exists. Every layer preserves exactly-once correlation and never turns a control-plane failure into a false scene success.

Errors never trigger last-write-wins, an automatic destructive retry, a save/reload, a broader tool call, or fallback to arbitrary code.

## Audit fields

Audit is technical metadata, not a duplicate conversation log. Each read/write attempt records only bounded, secret-free fields:

- Audit event ID, schema/tool-contract version, Codex version, Bridge version, and Houdini build.
- Received, approved, queued, started, and finished timestamps plus deadline outcome.
- `request_id`, `thread_id`, `turn_id`, `tool_name`, permission level, and operation class.
- `hip_session_id`, a safe HIP fingerprint digest, base/observed/result scene revisions, and writer-queue sequence.
- Idempotency key digest, canonical argument digest, approval digest/decision/time, and whether a result was replayed from the idempotency record.
- Created-node paths from the new graph, post-verification summary, Undo-group label, result code, warnings, and rollback status.
- Authentication outcome and remote socket address only after confirming/redacting it as loopback; never the Bearer value.

Do not record the Bearer token, approval nonce/capability, credentials, process environment, raw prompt or reply, duplicate chat history, full stdio lines, arbitrary parameter payloads, user filesystem paths, or unredacted exception/traceback text.

## Verification gates

Before B2C can close, offline tests must prove the project configuration is closed to one stdio server, forwards only reviewed environment variable names, enables exactly two read tools, disables all three graph tools, embeds no URL/token/credential, and matches the pinned Codex 0.144.3 syntax contract. Fake integration must prove exact `tools/list`, trusted status-parameter injection, rejection of forged technical fields, token redaction, executor-credential isolation, bounded timeout/disconnect/shutdown, and no graph dispatch.

A user-performed real GUI acceptance must then call both read tools through Codex, confirm the live build/session/revision and `Sop/transform` type/parameter data, prove scene revision/nodes/parameters/connections/selection/dirty state remain unchanged, and prove Panel loss terminates the request explicitly rather than hanging. This evidence is manual and must not be described as an automated Houdini run.

Before real scene writes are enabled, offline tests must prove schema closure, the exact five-tool/type/parameter allowlists, path/name bounds, typed-value and finite-number checks, request size limits, unknown-field rejection, deterministic normalization/digest, complete-graph approval binding, stale revision/session rejection, per-Thread and per-HIP serialization, idempotent replay, changed-key conflict, loopback/Bearer rejection, structured errors, log redaction, and rollback ownership logic. At least one asset fixture structurally different from the first table fixture must pass the same graph contract. Protocol files must fail a source-contract test if they encode table roles or a fixed structure.

Real Houdini acceptance must then prove, on a disposable unsaved test HIP:

1. All `hou` reads and writes execute on the UI main thread.
2. The live build reports the expected canonical `Object/geo`, `Sop/box`, `Sop/transform`, `Sop/merge`, and `Sop/null` types and admitted typed parameters.
3. A successful request creates only one approved `/obj/HIA_Graph_<id>` root and exactly its normalized declared children, typed values, wiring, flags, and graph digest.
4. One Undo removes the whole new graph and leaves a pre-existing sentinel graph byte-for-byte/parameter-for-parameter unchanged.
5. Collision, stale revision, stale HIP session, duplicate idempotency, altered-key replay, expired approval, and a second concurrent write all fail without mutation.
6. Injected failure after each mutation boundary either removes only the current new container or returns `SCENE_STATE_INDETERMINATE` and blocks later writes without touching user nodes.
7. No test saves the HIP, writes an artifact, renders, publishes an HDA, or opens another network listener.

At the time of this document, none of these real-`hou` claims has been executed. They remain explicitly unverified.

## Residual risks

- A process running as the same Windows user may inspect Houdini/Bridge memory or environment and steal the Bearer token. Loopback and token controls do not provide OS-user isolation.
- In-process `hou` calls and Houdini cooks cannot be safely hard-killed without risking the user's session. Strict graph/type/count/deadline bounds reduce but do not eliminate UI stalls.
- Correct main-thread scheduling, node-type names, parameter semantics, callbacks, cooking, rollback, and Undo behavior depend on the live Houdini build and require the real acceptance gate above.
- A HIP may contain callbacks, locked content, or vendor behavior that changes when nodes are created under `/obj`; static validation cannot prove those side effects absent.
- Scene revision tracking can miss manual mutations unless every relevant Houdini lifecycle/change callback is reliable. Post-verification and conservative conflict handling reduce, but cannot erase, this risk.
- A schema-valid graph can still be aesthetically or semantically wrong. Codex reasoning, the user's exact approval, and deterministic verification address intent; they do not make geometric judgment infallible.
- A crash or power loss during in-process mutation may bypass normal rollback and audit completion. P2-V deliberately does not auto-save or reload, so user inspection may be required.

## Explicit non-goals

P2-V does not provide or permit:

- Modification of existing user graphs, deletion tools, renaming/reparenting existing nodes, arbitrary selection edits, or arbitrary node/parameter introspection beyond the reviewed bounded read-only response.
- Node types outside the current `Object/geo`, `Sop/box`, `Sop/transform`, `Sop/merge`, and `Sop/null` safety allowlist, or targets outside a new `/obj/HIA_Graph_<id>` namespace.
- Python, HScript, expressions, `exec`, `eval`, shell/process execution, callbacks, screen takeover, Computer Use, or arbitrary filesystem paths.
- HIP save/overwrite, file import/export, cache, render, simulation, background job, USD/Solaris, material, animation, HDA creation/publishing, or preference/configuration changes.
- A new network service, fixed port, remote worker, WebSocket, LAN/public access, bypass of Bearer authentication, or direct exposure of the app-server/Houdini gateway.
- Experimental app-server APIs, `dynamicTools`, process APIs, `thread/shellCommand`, or automatic MCP/tool discovery outside the five pinned tools.
- A second Agent, LLM, planner, RAG/vector store, semantic-memory service, or duplicate chat database.
- Production-readiness claims before the listed live-`hou` main-thread, rollback, post-verification, and one-Undo tests pass.
