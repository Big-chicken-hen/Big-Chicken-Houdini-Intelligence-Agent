# P2-V threat model

## Status and scope

This document is the security gate for the first P2-V scene-writing vertical slice. It is a design artifact only. No Houdini GUI, MCP server, scene gateway, or `hou` write has been started or implemented as evidence for this document.

The live Houdini 21.0 schema, main-thread behavior, cooking behavior, rollback behavior, and one-step Undo behavior are **not yet verified with real `hou`**. Static node and parameter names in a later implementation must be intersected with runtime introspection before writes are enabled. A live-schema conflict fails closed; static documentation never overrides the running Houdini build.

Codex remains the sole intelligent agent, planner, and memory source. The MCP adapter, authenticated Bridge, Panel executor, and `hou` adapter are deterministic validation and execution components only. They must not add an Agent, planner, prompt rewriter, RAG system, semantic memory, or model call.

The only P2-V tools admitted by the MCP server are:

1. `houdini_scene_info`
2. `houdini_node_type_info`
3. `houdini_graph_apply`
4. `houdini_graph_verify`

All other tool names and all unknown fields fail closed. The first two and the fourth tool are read-only. `houdini_graph_apply` is the only write tool.

## Security invariants

- The network boundary remains the existing random-port HTTP Bridge bound and verified as `127.0.0.1` only. The Codex app-server and MCP adapter use stdio and are never exposed on a socket. P2-V adds no listener, remote mode, WebSocket, fixed port, LAN access, or internet-facing endpoint.
- Every HTTP request is authenticated with the existing random session Bearer token. Tokens, approval capabilities, and process credentials never enter logs, events, errors, or audit rows.
- Every live-scene write runs on Houdini's UI main thread through one FIFO writer queue. RPC, HTTP, stdio, and standard-library HTTP worker threads never call `hou`; workers return only plain dictionaries through a bounded queue that one main-thread `QTimer` drains.
- Every write is bound to `hip_session_id`, HIP fingerprint, `base_scene_revision`, `thread_id`, `turn_id`, `request_id`, deadline, permission level, approval digest, and idempotency key. These values are rechecked immediately before mutation on the main thread.
- There is at most one in-flight write for a live HIP and no concurrent write for the same Thread. Last-write-wins is forbidden.
- The only admitted canonical node types are `Object/geo`, `Sop/box`, `Sop/merge`, and `Sop/null`, written here as `geo`, `box`, `merge`, and `null`. Namespaced aliases, versioned variants, operator aliases, assets, and every other type are rejected.
- A write may create exactly one new container matching `/obj/HIA_Table_<id>`, where `<id>` is a bounded server-validated ASCII identifier. The container must not already exist. The only children are five `box` nodes named `tabletop_box`, `leg_front_left`, `leg_front_right`, `leg_back_left`, and `leg_back_right`; one `merge` named `merge_table`; and one `null` named `OUT_TABLE`.
- `merge_table` receives exactly the five boxes, and `OUT_TABLE` receives only `merge_table`. `OUT_TABLE` is the only node given the display/render flags. Graph size, ordering, and connection cardinality are bounded by the tool schema.
- Only finite numeric box dimensions and translations, reviewed connection indices, and the required output flags are writable. Parameters are matched against an exact per-type allowlist and the live schema. No arbitrary parameter name or value is forwarded to `hou`.
- No request field accepts a filesystem path, node path chosen by the caller, Python, HScript, expression, backtick command, environment expansion, callback, script language, serialized code, or shell command. String expressions are never evaluated or assigned.
- P2-V never deletes, renames, reparents, disconnects, rewires, changes flags on, or sets parameters on an existing user node. The only internal destruction allowed is rollback of the exact container object proven to have been created by the current failing operation.
- P2-V never saves or overwrites a HIP, writes a file, creates a cache/render/HDA, changes a Houdini definition, installs a package, edits configuration, or expands network access.
- One successful apply is enclosed completely in one `hou.undos.group`. A later live acceptance test must prove that one Undo removes the new table and changes nothing else before scene writing can be called production-ready.

## Assets

| Asset | Required property |
|---|---|
| User's live HIP and all pre-existing nodes | Integrity and availability; no implicit save or destructive modification |
| Newly created table graph | Exact declared topology, editable numeric dimensions, deterministic ownership and provenance |
| HIP session identity, fingerprint, and scene revision | Freshness and atomic conflict detection |
| Idempotency records | A retry cannot duplicate a table; a key cannot be rebound to different arguments |
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
    | exact four-tool allowlist and strict JSON Schema
    v
Bearer-authenticated 127.0.0.1 Bridge
    | authenticated request envelope; no prompt rewriting
    v
Houdini Panel gateway / single-writer queue
    | validated work item; no hou call on I/O thread
    v
Houdini UI main-thread executor
    | revalidation, one Undo group, bounded hou calls, post-verification
    v
live HIP (user asset)
```

Trust does not imply accepting inputs without validation:

- Codex is trusted to reason, but model output and tool arguments are treated as untrusted data at every deterministic boundary.
- The MCP adapter is trusted only to enforce its pinned schemas and translate the four admitted tools. It receives no general execution capability.
- The Bridge is trusted to authenticate, correlate, bound, queue, and audit. It does not decide scene intent.
- The Panel gateway is trusted to dispatch to the main thread and report current HIP state. It does not execute a caller-supplied program.
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
| Accidental non-loopback exposure | Information disclosure, spoofing, elevation of privilege | Bind explicitly to IPv4 `127.0.0.1` with an OS-selected random port and verify the bound address after startup. Reject `0.0.0.0`, `::`, hostnames that may resolve externally, fixed port 8100, port forwarding, and a second scene-gateway listener. Startup fails if loopback-only binding cannot be proven. |
| Malicious, oversized, deeply nested, non-finite, or extra tool arguments | Tampering, denial of service, elevation of privilege | Validate byte size, JSON depth, array counts, string lengths, finite numbers and safe dimension ranges; use schemas with `additionalProperties: false`; canonicalize before approval and hashing. Reject unknown fields and values before queuing. Never pass generic dictionaries through to `hou`. |
| Arbitrary Python, HScript, expression, callback, shell, eval, or filesystem path injection | Elevation of privilege, tampering, information disclosure | Publish no execute tool and accept no code/path fields. Forbid expression setters, `hou.hscript`, `eval`, `exec`, Python SOPs, callbacks, environment expansion, backticks, file parameters, shell/process APIs, and arbitrary strings. `houdini_graph_apply` maps typed numeric/connection fields to reviewed calls only. |
| Node-type aliases or parameter names escape the allowlist | Elevation of privilege, tampering | Match exact category and canonical type name against only `geo`, `box`, `merge`, and `null`; reject namespaces, versions, aliases, HDAs, and dynamic types. Intersect a static per-type parameter allowlist with read-only live introspection. Reject spare parameters, expressions, multiparms, buttons, callbacks, and every parameter not explicitly admitted. |
| Scene revision changes between validation and mutation (TOCTOU) | Tampering, repudiation | Capture session/fingerprint/revision on receipt, then atomically re-read and compare all three after dequeue and immediately before the first `hou` write on the main thread. Manual edits advance revision. A mismatch returns `SCENE_CONFLICT` with no mutation; no last-write-wins or silent rebase. |
| Two Turns or clients write concurrently | Tampering, denial of service | Use one FIFO writer queue and a live-HIP write lock; atomically reserve `starting/inProgress` state per Thread and HIP. A second write returns structured `WRITE_IN_PROGRESS`/HTTP 409 before approval consumption or mutation. Read tools cannot promote themselves into writes. |
| Same idempotency key is used for different operations | Tampering, repudiation | Scope the key to project/HIP session/tool and store a canonical argument plus approval digest. Same key and same digest returns the recorded result without executing. Same key and different digest returns `IDEMPOTENCY_CONFLICT`/409. Failed or indeterminate records cannot be silently retried as new work. |
| Approval is substituted, widened, delayed, or applied to another HIP/Turn | Spoofing, tampering, repudiation | Show the exact canonical graph, target namespace, dimensions, side effects, base revision, and operation digest. The one-time decision binds user action, request/thread/turn, HIP session/fingerprint, revision, idempotency key, schema version, and expiry. Re-canonicalization must match immediately before execution. Any mismatch, expiry, or reuse returns `APPROVAL_MISMATCH` with no write. |
| An I/O or worker thread calls `hou` | Tampering, denial of service | The gateway's I/O side may only parse, validate, and enqueue immutable work. Assert UI-main-thread identity in the executor before every read/write batch. Failure returns the schema-admitted `MAIN_THREAD_REQUIRED`; it never falls back to calling `hou` on the current thread. |
| A request targets a prior HIP after New/Open/reload | Tampering, repudiation | Generate a new `hip_session_id` when the HIP lifecycle changes and compute a fresh fingerprint. Invalidate pending approvals, queued writes, and idempotency scope from the old session. Recheck at dequeue. Return `HIP_SESSION_MISMATCH` rather than applying to the current file. |
| `/obj/HIA_Table_<id>` or a child name already exists | Tampering, availability | Validate the identifier and check the exact container path on the main thread. Never overwrite, reuse, merge into, rename, or choose a silent suffix. Return `NAME_CONFLICT` without mutation. Children are created only under the newly returned container object, not located globally by name. |
| Existing nodes are changed or destroyed through path/connection escape | Tampering, elevation of privilege | The caller supplies no arbitrary destination path. All created-node and connection endpoints must be object identities allocated during the current operation and members of its ownership set. Reject connections to pre-existing nodes and parent traversal. No delete tool is published. Post-verification confirms that changed nodes are a subset of the newly created container graph. |
| An exception leaves a partial table or rollback damages user data | Tampering, repudiation | Track the exact container object returned by the current create call and tag transient operation ownership. On failure, rollback may act only on that exact new object after proving identity and ownership. It must never search by prefix or delete an existing collision. If cleanup cannot be proven or fails, return `ROLLBACK_FAILED`, mark scene state indeterminate, block later writes, and require user inspection; never save the HIP. |
| Undo grouping omits a parameter, connection, flag, or rollback action | Tampering, repudiation | Enclose container creation, all child creation, numeric parameter sets, wiring, flags, layout if admitted, and commit bookkeeping in one `hou.undos.group`. No mutation may occur before or after it. Live tests must snapshot existing nodes and prove one Undo removes only the table. Until that test passes, Undo completeness remains unverified and scene writing is not production-approved. |
| A cook or `hou` call blocks Houdini | Denial of service | Admit only the fixed small graph of primitive boxes, merge, and null; enforce exact node/count and finite size bounds; reject expired work before execution; avoid caller-selected cooks and nodes with external dependencies. Record deadlines and stop scheduling new writes after overrun. In-process HOM cannot be reliably killed safely, so a started main-thread stall remains a documented residual risk and must never trigger process-name termination. |
| Logs, events, tracebacks, or errors disclose secrets or duplicate chat | Information disclosure | Use structured secret-free logging with field allowlists. Redact Authorization, environment, approval nonce/capability, raw stdio, raw prompt/chat, filesystem/user paths, and credential-bearing exception text. Audit canonical argument hashes and bounded technical metadata rather than raw chat. Tracebacks may go only to project-local diagnostic logs after redaction and are never the sole user-facing error. |
| MCP tools silently expand through discovery or a dependency update | Elevation of privilege | Pin the adapter and four-tool manifest; compare the runtime tool list and schema hashes to reviewed artifacts. Reject unknown calls even if Codex advertises them. Do not copy or start an upstream `hwebserver`; if third-party code is later copied, pin its commit and retain its MIT license and source record. Any tool-surface change requires a new review gate. |
| Experimental app-server methods or transport become dependencies | Elevation of privilege, tampering | Continue using the pinned stable Codex 0.144.3 stdio contract. Exclude experimental Schema, app-server WebSocket, `dynamicTools`, process APIs, `thread/shellCommand`, remote control, Computer Use, and screen takeover. A method absent from the reviewed stable allowlist fails closed. |
| Runtime or installer changes project/global configuration | Tampering, elevation of privilege | Runtime code never writes `.codex/config.toml`, global `CODEX_HOME`, Houdini preferences, registry, PATH, or user/machine environment. Any future project-local MCP registration is a reviewed source artifact, never runtime-generated or self-modified. Verify configuration provenance/hash at startup and fail on unexpected server/tool entries. |

## Capability attestation and staged live enablement

B1 is fake-only and contains no live dispatcher. Its capability fixture is explicitly marked fake and must match the reviewed schema hashes. Every tool fails with `HOUDINI_UNAVAILABLE` if a current trusted capability attestation is absent and with `CAPABILITY_MISMATCH` if its process nonce, build, HIP session/fingerprint, revision, catalog digest, or schema hashes differ.

The attestation is issued by the trusted Panel side and bound to one current Houdini process and HIP session. It is never accepted from untrusted MCP arguments, and a value from a prior process or HIP cannot be refreshed by changing a request field. The public `houdini_scene_info` input still requires exact session and revision values; the trusted Bridge session snapshot supplies them to Codex-visible connection state before request construction, with no `"current"` wildcard.

In B2, after separate approval, at most `houdini_scene_info` and `houdini_node_type_info` may be live-enabled after attestation. `houdini_graph_verify` and `houdini_graph_apply` remain disabled. A live write cannot be enabled before the separately approved later write gate, regardless of schema validity or fake-test success.

## Write transaction and fail-closed order

`houdini_graph_apply` must follow this order; a later step may not compensate for skipping an earlier one:

1. Authenticate the loopback HTTP request and enforce request/message bounds.
2. Resolve only an exact admitted MCP tool and validate against the pinned schema with no extra properties.
3. Canonicalize the fixed graph and compute its request digest without evaluating any caller string.
4. Verify the one-time approval is current and bound to that digest, Thread/Turn, HIP session/fingerprint, revision, idempotency key, and deadline.
5. Atomically check/reserve the per-HIP and per-Thread writer state and idempotency record.
6. Enqueue an immutable work item. The I/O path returns no success before a terminal executor result exists.
7. On the Houdini main thread, re-read HIP session, fingerprint, revision, deadline, collision state, live node types, and admitted parameter definitions.
8. Enter one Undo group, create the new container, retain its object identity as the ownership root, create only the seven declared SOP children, set only typed admitted values, connect only owned nodes, and set `OUT_TABLE` flags.
9. Verify exact type, parent, name, numeric parameters, five merge inputs, output connection, flags, ownership set, and absence of changes outside the new graph.
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

Messages are safe for the user and must not contain tokens, approval material, raw environment data, raw chat, or a traceback. `details` is a bounded, code-specific object. Expected fail-closed tool-result codes include `AUTH_REQUIRED`, `TOOL_NOT_ALLOWED`, `SCHEMA_INVALID`, `NODE_TYPE_NOT_ALLOWED`, `PARAMETER_NOT_ALLOWED`, `PATH_SCOPE_VIOLATION`, `APPROVAL_REQUIRED`, `APPROVAL_MISMATCH`, `HIP_SESSION_MISMATCH`, `SCENE_CONFLICT`, `WRITE_IN_PROGRESS`, `IDEMPOTENCY_CONFLICT`, `NAME_CONFLICT`, `MAIN_THREAD_REQUIRED`, `DEADLINE_EXCEEDED`, `CAPABILITY_MISMATCH`, `HOUDINI_UNAVAILABLE`, `VERIFY_FAILED`, `ROLLBACK_FAILED`, and `SCENE_STATE_INDETERMINATE`.

`CANCELLED`, `QUEUE_FULL`, and `SHUTTING_DOWN` are bounded JSON-RPC/MCP adapter errors, not values injected into tool outputs whose schemas do not admit them. Request-size rejection occurs at the HTTP/adapter boundary before a tool result exists. Every layer preserves exactly-once correlation and never turns a control-plane failure into a false scene success.

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

Before real scene writes are enabled, offline tests must prove schema closure, exact tool/type/parameter allowlists, path/name bounds, finite-number checks, request size limits, unknown-field rejection, approval binding, stale revision/session rejection, per-Thread and per-HIP serialization, idempotent replay, changed-key conflict, loopback/Bearer rejection, structured errors, log redaction, and rollback ownership logic.

Real Houdini acceptance must then prove, on a disposable unsaved test HIP:

1. All `hou` reads and writes execute on the UI main thread.
2. The live build reports the expected canonical `geo`, `box`, `merge`, and `null` types and admitted numeric parameters.
3. A successful request creates only `/obj/HIA_Table_<id>` and the seven exact children, with five editable boxes, correct wiring, and `OUT_TABLE` display flags.
4. One Undo removes the whole new table and leaves a pre-existing sentinel graph byte-for-byte/parameter-for-parameter unchanged.
5. Collision, stale revision, stale HIP session, duplicate idempotency, altered-key replay, expired approval, and a second concurrent write all fail without mutation.
6. Injected failure after each mutation boundary either removes only the current new container or returns `SCENE_STATE_INDETERMINATE` and blocks later writes without touching user nodes.
7. No test saves the HIP, writes an artifact, renders, publishes an HDA, or opens another network listener.

At the time of this document, none of these real-`hou` claims has been executed. They remain explicitly unverified.

## Residual risks

- A process running as the same Windows user may inspect Houdini/Bridge memory or environment and steal the Bearer token. Loopback and token controls do not provide OS-user isolation.
- In-process `hou` calls and Houdini cooks cannot be safely hard-killed without risking the user's session. The tiny fixed graph reduces but does not eliminate UI stalls.
- Correct main-thread scheduling, node-type names, parameter semantics, callbacks, cooking, rollback, and Undo behavior depend on the live Houdini build and require the real acceptance gate above.
- A HIP may contain callbacks, locked content, or vendor behavior that changes when nodes are created under `/obj`; static validation cannot prove those side effects absent.
- Scene revision tracking can miss manual mutations unless every relevant Houdini lifecycle/change callback is reliable. Post-verification and conservative conflict handling reduce, but cannot erase, this risk.
- A schema-valid table can still be aesthetically or semantically wrong. Codex reasoning, the user's approval, and deterministic verification address intent; they do not make geometric judgment infallible.
- A crash or power loss during in-process mutation may bypass normal rollback and audit completion. P2-V deliberately does not auto-save or reload, so user inspection may be required.

## Explicit non-goals

P2-V does not provide or permit:

- General Houdini graph editing, modification of existing nodes, deletion tools, renaming, reparenting, arbitrary selection edits, or arbitrary node/parameter introspection beyond the reviewed read-only response.
- Node types other than `geo`, `box`, `merge`, and `null`, or targets outside a new `/obj/HIA_Table_<id>` namespace.
- Python, HScript, expressions, `exec`, `eval`, shell/process execution, callbacks, screen takeover, Computer Use, or arbitrary filesystem paths.
- HIP save/overwrite, file import/export, cache, render, simulation, background job, USD/Solaris, material, animation, HDA creation/publishing, or preference/configuration changes.
- A new network service, fixed port, remote worker, WebSocket, LAN/public access, bypass of Bearer authentication, or direct exposure of the app-server/Houdini gateway.
- Experimental app-server APIs, `dynamicTools`, process APIs, `thread/shellCommand`, or automatic MCP/tool discovery outside the four pinned tools.
- A second Agent, LLM, planner, RAG/vector store, semantic-memory service, or duplicate chat database.
- Production-readiness claims before the listed live-`hou` main-thread, rollback, post-verification, and one-Undo tests pass.
