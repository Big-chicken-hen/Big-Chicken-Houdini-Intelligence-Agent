# P2-V threat model

## Status and scope

The five-tool security contract at `0.1.0` and two-tool read-only contract at `0.2.0` remain frozen pre-release. Gate B2C completed the pinned Codex 0.144.3 read chain at commit `edf7f3a`, its HTTPS transport hotfix completed at `3213625`, Gate B3 completed at `862defff`, and Gate B4A completed at `0b3b5a51c0ad95aee382794d74852d2d541bf880`. Gate B4B authorizes only one dedicated local Panel, the frozen stairs fixture, one live Apply, and one user-triggered Undo in a blank unsaved disposable HIP.

Until the user performs the finite GUI run, the active Houdini build's write behavior, event ordering, `setUserData` event delivery, wrapper identity stability, rollback behavior, and one-step Undo behavior remain **unverified with real `hou`**. Static node and parameter names are intersected with runtime introspection immediately before the single write. A live-schema or observer conflict fails closed; static documentation and fake-HOM never override the running Houdini build.

Codex remains the sole intelligent agent, planner, and memory source. The MCP adapter, authenticated Bridge, Panel executor, and dormant injected HOM adapter are deterministic validation and execution components only. They must not add an Agent, planner, prompt rewriter, RAG system, semantic memory, or model call.

The frozen future P2-V contract contains:

1. `houdini_scene_info`
2. `houdini_node_type_info`
3. `houdini_graph_validate`
4. `houdini_graph_apply`
5. `houdini_graph_verify`

The production MCP registration admits only `houdini_scene_info` and `houdini_node_type_info`. `houdini_graph_validate`, `houdini_graph_apply`, and `houdini_graph_verify` remain disabled and unregistered there; their use by the completed offline B3 harness, dormant B4A adapter, or dedicated B4B local Panel grants no production runtime capability. All other tool names and unknown fields fail closed. Every write outside the one manually confirmed B4B transaction remains unauthorized.

## Security invariants

- The network boundary remains the existing random-port HTTP Bridge bound and verified as `127.0.0.1` only. The Codex app-server and MCP adapter use stdio and are never exposed on a socket. P2-V adds no listener, remote mode, WebSocket, fixed port, LAN access, or internet-facing endpoint.
- Every HTTP request is authenticated with the existing random session Bearer token. Tokens, approval capabilities, and process credentials never enter Git, configuration values, command arguments, logs, events, errors, or audit rows.
- The independent Panel executor credential is inherited only by the owned Panel path. Codex and its MCP child never receive `HIA_SCENE_EXECUTOR_TOKEN`.
- Every live-scene write runs on Houdini's UI main thread through one FIFO writer queue. RPC, HTTP, stdio, and standard-library HTTP worker threads never call `hou`; workers return only plain dictionaries through a bounded queue that one main-thread `QTimer` drains.
- Every write is bound to `hip_session_id`, HIP fingerprint, `base_scene_revision`, `thread_id`, `turn_id`, `request_id`, deadline, permission level, approval digest, and idempotency key. These values are rechecked immediately before mutation on the main thread.
- There is at most one in-flight write for a live HIP and no concurrent write for the same Thread. Last-write-wins is forbidden.
- The B4A first certified catalog admits `Object/geo`, `Sop/box`, `Sop/transform`, `Sop/merge`, and `Sop/null`, with `Sop/transform` resolving to `xform`. This is the initial safety catalog for one catalog-driven Houdini graph engine, not an asset recipe or permanent writer boundary. The current frozen protocol version rejects every other type; later expansion requires separately reviewed catalog/protocol versions and risk policy rather than per-node handlers or a general DCC framework.
- A write may create exactly one new container matching `/obj/HIA_Graph_<id>`, where `<id>` is a bounded server-validated ASCII identifier. The container must not already exist. Every child is declared by a request-local ID, live-resolved catalog-admitted type, bounded name hint, and owned parent reference.
- Graph size and connection cardinality are bounded. Node IDs, name hints, and per-node parameter names are unique; parents and every source/output and destination/input endpoint resolve inside the same request-owned container; inputs are not multiply assigned; and the current SOP graph is acyclic. No fixed number of primitives, fixed node roles, object dimensions, or topology is part of the protocol.
- Only closed typed parameters (`float`, `int`, `bool`, bounded `string`, or homogeneous tuple), reviewed connection indices, explicit flags, and optional bounded layout are admitted. The current SOP slice has exactly one display node and one render node, with both flags on the same declared node and no name-implied output role. Parameters are intersected with the reviewed catalog policy and live schema. Expressions, callbacks, code, buttons, multiparms, file parameters, and arbitrary names never reach `hou`.
- No request field accepts a filesystem path, unconstrained node path, Python, HScript, expression, backtick command, environment expansion, callback, script language, serialized code, or shell command. The only node-path form is the validated HIA-owned target/root; graph references use request-local IDs. String values are never evaluated.
- P2-V never deletes, renames, reparents, disconnects, rewires, changes flags on, or sets parameters on an existing user node. The only internal destruction allowed is rollback of the exact container object proven to have been created by the current failing operation.
- P2-V never saves or overwrites a HIP, writes a file, creates a cache/render/HDA, changes a Houdini definition, installs a package, edits configuration, or expands network access.
- One successful apply is enclosed completely in one `hou.undos.group`. B4A verifies only the fake-HOM call boundary. The current user-run B4B sequence must prove that one manual Undo removes the selected request-owned graph and changes nothing else; that finite proof still does not make production graph tools ready.

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
| Node-type aliases or parameter names escape the certified catalog | Elevation of privilege, tampering | For B4A, match exact category and canonical type against the injected first certified catalog sample: `Object/geo`, `Sop/box`, `Sop/transform`, `Sop/merge`, and `Sop/null`; resolve transform exactly to `xform`. These names are test catalog data, not a permanent writer allowlist or type branch. Reject unreviewed namespaces, versions, aliases, HDAs, and dynamic types. Intersect the reviewed parameter policy with injected catalog introspection. Later catalog growth requires a new reviewed catalog/protocol version and risk policy, not a permissive fallback or a transaction-engine change. |
| Scene revision changes between validation and mutation (TOCTOU) | Tampering, repudiation | Capture session/fingerprint/revision on receipt, then atomically re-read and compare all three after dequeue and immediately before the first `hou` write on the main thread. Manual edits advance revision. A mismatch returns `SCENE_CONFLICT` with no mutation; no last-write-wins or silent rebase. |
| Two Turns or clients write concurrently | Tampering, denial of service | Use one FIFO writer queue and a live-HIP write lock; atomically reserve `starting/inProgress` state per Thread and HIP. A second write returns structured `WRITE_IN_PROGRESS`/HTTP 409 before approval consumption or mutation. Read tools cannot promote themselves into writes. |
| Same idempotency key is used for different operations | Tampering, repudiation | Scope the key to project/HIP session/tool and store a canonical argument plus approval digest. Same key and same digest returns the recorded result without executing. Same key and different digest returns `IDEMPOTENCY_CONFLICT`/409. Failed or indeterminate records cannot be silently retried as new work. |
| Approval is substituted, widened, delayed, copied, or applied to another HIP/Turn | Spoofing, tampering, repudiation | Show the complete normalized graph and bounded summary. The one-time decision binds request/thread/turn, target/root, every node/type/parameter/connection/flag/layout item, HIP session/fingerprint, revision, idempotency key, deadline, schema version, and canonical graph digest. B4A additionally requires an injected claim authority to seal the exact non-cancelled internal `SceneQueue` request/claim object pair into an opaque token and redeem the resulting binding once at execution; a copy, reconstruction, cancellation, absence, reuse, or replay has no authority. The initiating natural-language phrase and caller booleans grant no authority. Re-normalization must match immediately before execution. Any mismatch, expiry, or reuse returns a frozen-contract approval error with no write. |
| Validate and apply normalize the same graph differently | Tampering, repudiation | Put canonicalization in one versioned deterministic implementation and bind its version plus canonical bytes into the digest. `houdini_graph_apply` repeats normalization against the current live type Schema and requires exact digest equality with the approved `houdini_graph_validate` result. A live Schema, target, revision, or normalized-byte difference invalidates approval and returns a structured mismatch without mutation. |
| An I/O, worker, or untrusted guard thread calls `hou` | Tampering, denial of service | The gateway's I/O side may only parse, validate, and enqueue immutable work. The injected control guard is not trusted to preserve scheduling: every `mutate`, `contain`, and `finalize` callback rechecks Houdini UI-main-thread identity at callback entry before adapter, scene, Undo, observer, or revision access. B4B captures the Panel construction thread and fails before HOM access on any other thread; fake evidence still does not verify the live scheduler. |
| An injected clock raises or returns an invalid time | Tampering, denial of service | Validate the clock at adapter entry and again under locked preflight. Reject booleans, non-numbers, NaN, infinities, negatives, and exceptions before Undo entry with zero mutation and revision change. Never substitute wall-clock guesses or treat an invalid sample as unexpired. B4A proves only fake fail-closed behavior, not live timing semantics. |
| A request targets a prior HIP after New/Open/reload | Tampering, repudiation | Generate a new `hip_session_id` when the HIP lifecycle changes and compute a fresh fingerprint. Invalidate pending approvals, queued writes, and idempotency scope from the old session. Recheck at dequeue. Return `HIP_SESSION_MISMATCH` rather than applying to the current file. |
| `/obj/HIA_Graph_<id>` or a declared child name already exists | Tampering, availability | Validate the identifier and every name hint, then check the exact container path on the main thread. Never overwrite, reuse, merge into, rename, or choose a silent suffix. Return `NAME_CONFLICT` without mutation. Immediately after each root or child `createNode` return and before any later mutation, prove a new identity with the approved name, exact path, resolved type, session, parent identity/membership, and exact `hou.node(path)` registry lookup. A substituted or ambiguous return freezes writes; a partial root or child creation that raises without returning its identity is never guessed or cleaned by name. |
| Existing nodes are changed or destroyed through path/connection escape | Tampering, elevation of privilege | The caller supplies no arbitrary destination path. Success and rollback require the live `/obj` lookup to remain the exact parent and every pre-existing child identity/fingerprint to remain unchanged. The root's direct children must be exactly the retained declared set by identity/session/path/name/resolved type; extra, replacement, missing, or partially created but unreturned children make ownership unprovable. All connection endpoints must be exact transaction identities. Success requires zero root edges and exact child input/output cardinality in both directions. |
| An exception leaves a partial graph or rollback damages user data | Tampering, repudiation | Track the exact root plus the complete retained child set, session IDs, parent, paths/names/types, registry identities, fixed ownership, transaction ID, and graph digest. Rollback may call `destroy()` only after unchanged live `/obj`/pre-existing fingerprints, exact retained children, exact paths, and a bidirectional scan of root/child connections succeed. Any extra/replacement/unreturned child or external-to-owned/owned-to-external edge forbids blind destruction. After destroy, every root/child path must be absent and the parent registry/fingerprint restored; repeat the proof after Undo exit. It must never re-find by prefix or delete an existing collision. `KeyboardInterrupt` or `SystemExit` during the original mutation or rollback is re-raised only after best-effort containment or freeze completes. |
| A cancel, deadline, shutdown, or finalizer races a HOM mutation | Tampering, repudiation | An injected atomic control guard wraps every mutator, non-cancellable rollback/Undo/revision containment, and the single final commit callable. Each wrapper must invoke its callback exactly once and return that exact result, and each callback independently reasserts UI-thread identity because guard scheduling is untrusted. A skipped, repeated, replaced, or cross-thread callback cannot report success. A terminal control outcome cannot be followed by another mutation; a post-commit `BaseException` freezes before re-raise. |
| An owned-write publication is forged, stale, or contradictory | Spoofing, tampering, repudiation | For each `committed`, `rolled_back`, or `indeterminate` outcome, require an exactly-once guarded publication callback and the exact returned report object. If indeterminate state retains owned nodes, run one bounded best-effort refresh even after finish failure to install observers before sampling the independent report; never retry when commit already attempted refresh. The publication/refresh and independent report must agree on availability, session, catalog, and outcome-specific revision/fingerprint. A rolled-back report must match the base snapshot; indeterminate revision is at least base plus one. Any disagreement or unrefreshed retained scope freezes writes and cannot report success or proven rollback. |
| Fake callback behavior is mistaken for live-Houdini coverage | Tampering, repudiation, denial of service | B4A proves only fake delivery. B4B therefore reads back exact callback registration, attaches observers to every returned node before its next mutation, and requires typed operation/source evidence from the one live Apply. Zero, mismatched, external, late, off-main-thread, or overflowing events fail closed; no extra write may be added to improve coverage. If the exact owned root nevertheless remains, the result exposes `manual_undo_required=true` so the user can perform the one authorized cleanup Undo without converting incomplete evidence into a passing Gate. |
| Undo grouping omits a parameter, connection, flag, rollback, or post-exit proof | Tampering, repudiation | Enclose creation, typed parameters, wiring, flags, bounded postcondition, and commit bookkeeping in one `hou.undos.group`. B4B does not invoke Undo in code: whenever the exact owned root remains, the user presses Ctrl+Z once, then the controller performs only bounded reads and exact deletion-journal checks. A complete cleanup after an `ok=false` apply remains a failed acceptance. A successful acceptance still leaves automatic rollback behavior unverified. |
| Postcondition inspection escapes owned scope or fabricates state | Information disclosure, tampering | Re-read only the retained owned root and its direct declared children. Do not recursively scan `/obj`, inspect unrelated nodes, substitute request values for observations, echo validation-only layout as observed, or invoke layout mutation. Any mismatch fails before commit. |
| A cook or `hou` call blocks Houdini | Denial of service | Admit only a bounded acyclic graph from the current reviewed catalog and risk policy; enforce node/connection/count, string, tuple, and finite numeric bounds; reject expired work before execution; avoid caller-selected arbitrary cooks and nodes with external dependencies. Record deadlines and stop scheduling new writes after overrun. In-process HOM cannot be reliably killed safely, so a started main-thread stall remains a documented residual risk and must never trigger process-name termination. |
| Logs, events, tracebacks, or errors disclose secrets or duplicate chat | Information disclosure | Use structured secret-free logging with field allowlists. Redact Authorization, environment, approval nonce/capability, raw stdio, raw prompt/chat, filesystem/user paths, and credential-bearing exception text. Audit canonical argument hashes and bounded technical metadata rather than raw chat. Tracebacks may go only to project-local diagnostic logs after redaction and are never the sole user-facing error. |
| MCP tools silently expand through discovery or a dependency update | Elevation of privilege | Pin the adapter and frozen five-tool manifest, while independently requiring the active B2C `tools/list` and project configuration to contain exactly the two read tools. Explicitly disable all three graph tools and reject unknown calls even if Codex advertises them. Any tool-surface change requires a new review gate. |
| Experimental app-server methods or transport become dependencies | Elevation of privilege, tampering | Continue using the pinned stable Codex 0.144.3 stdio contract. Exclude experimental Schema, app-server WebSocket, `dynamicTools`, process APIs, `thread/shellCommand`, remote control, Computer Use, and screen takeover. A method absent from the reviewed stable allowlist fails closed. |
| Runtime or installer changes project/global configuration | Tampering, elevation of privilege | `.codex/config.toml` is a reviewed tracked source artifact with one closed server table and a non-required default so an ordinary Codex Desktop project session cannot be denied by the absent Houdini launcher environment. Runtime code never rewrites it or changes global `CODEX_HOME`, Houdini preferences, registry, PATH, or user/machine environment. The Bridge may override only the verified child command and `required=true` through per-process Codex `-c`, preserving fail-closed behavior for the owned Houdini runtime. Fail on an unexpected server, key, tool, URL transport, or environment value. |
| Bridge URL or Bearer token is persisted or disclosed | Spoofing, information disclosure | The tracked TOML contains environment variable names only. The launcher obtains an OS-selected IPv4 loopback port and supplies `HIA_BRIDGE_URL` and `HIA_BRIDGE_TOKEN` only through owned child environments. The Bridge must bind that exact origin or fail closed. Bootstrap explicitly contains neither value. Neither value may enter command arguments, config values, logs, diagnostics, tool results, or acceptance documents. Redaction tests cover every failure path. |
| Codex or MCP inherits the Panel executor credential | Elevation of privilege | Build the child environment from an explicit allowlist that excludes `HIA_SCENE_EXECUTOR_TOKEN`. The ordinary Bridge Bearer can access only the bounded status/read surface; it cannot publish or impersonate live capability. |
| A caller forges HIP session, revision, catalog, or Schema fields | Spoofing, tampering | The authenticated status facade returns only the current safe correlation context. The Bridge-owned parameter facade replaces technical fields from that trusted snapshot and never accepts caller attestation. Status reads do not renew the capability lease; stale or absent state fails closed. |
| Panel loss leaves an MCP call hanging or causes stale fallback | Denial of service, spoofing | Bound status, queue, tool, and shutdown waits. Panel disconnect or capability expiry returns one explicit terminal error, discards late results by generation, and never starts a replacement Panel, renews stale state, or fabricates a read. |

## Capability attestation and staged live enablement

B1's capability fixture was explicitly fake. B2A/B2B added and manually accepted the Panel-side read adapter, and B2C connected only that read slice to the real MCP chain. Neither B3 nor B4A changes or starts that production chain. Each active production read still fails with `HOUDINI_UNAVAILABLE` if a current trusted capability attestation is absent and with `CAPABILITY_MISMATCH` if its process nonce, build, HIP session/fingerprint, revision, catalog digest, or Schema digest differs.

The attestation is issued by the trusted Panel side and bound to one current Houdini process and HIP session. It is never accepted from untrusted MCP arguments, and a value from a prior process or HIP cannot be refreshed by changing a request field. The authenticated status facade exposes only safe current correlation fields and a five-type availability summary; it does not expose process nonce, publisher/observer identity, executor credential, or a full parameter catalog, and reading it does not renew the lease. The Bridge-owned parameter facade supplies exact session and revision values with no `"current"` wildcard.

Exactly `houdini_scene_info` and `houdini_node_type_info` remain production-enabled after attestation. `houdini_graph_validate`, `houdini_graph_verify`, and `houdini_graph_apply` stay disabled at configuration, registration, Bridge routing, and normal Panel dispatch layers. The authorized live write exists only behind the dedicated local B4B Panel and one process latch; neither a model request nor fake-test success can reach it.

## Write transaction and fail-closed order

Gate B3 models the deterministic validation, approval, single-writer, mutation, postcondition, rollback, idempotency, and Undo state transitions below entirely in Python. It substitutes independent observed fake objects for Houdini nodes and never executes the network, Panel, or `hou` steps. Passing B3 is evidence about the state machine only; each live step remains unverified until the user completes the B4B GUI sequence.

`houdini_graph_apply` must follow this order; a later step may not compensate for skipping an earlier one:

1. Authenticate the loopback HTTP request and enforce request/message bounds.
2. Resolve only an exact admitted MCP tool and validate against the pinned schema with no extra properties.
3. Run the same deterministic normalization rules as `houdini_graph_validate`, compute the canonical graph/request digests, and never evaluate a caller string.
4. Verify the one-time approval is current and bound to the complete normalized graph, target, every node/type/parameter/connection/flag/layout field, both digests, Thread/Turn, HIP session/fingerprint, revision, idempotency key, and deadline.
5. Atomically check/reserve the per-HIP and per-Thread writer state and idempotency record.
6. Enqueue an immutable work item. The I/O path returns no success before a terminal executor result exists.
7. On the Houdini main thread, re-read HIP session, fingerprint, revision, deadline, collision state, live node types, and admitted parameter definitions.
8. Enter one Undo group; after each root or child creation, prove its exact new identity, approved path/name/type/session/parent, and registry mapping before any later mutation; then set only typed admitted values, connect only owned nodes, and set exactly the declared flags/layout.
9. Verify exact identity and path lookup, type, parent, name, typed parameters, connections, flags, cook state, canonical graph digest, ownership set, and absence of changes outside the new graph.
10. Record the terminal idempotency result, advance scene revision exactly once, release writer state, emit the structured response, and append the secret-free audit record.

Any failure before the mutation phase produces no observed fake scene mutation. A B3 failure during or after mutation rolls back only the proven new root object identity and verifies the sentinel remains unchanged. If rollback or post-state certainty fails, the simulator returns `ROLLBACK_FAILED` or `SCENE_STATE_INDETERMINATE`, disables further fake writes for that session, and does not auto-retry or assume success. Equivalent live-Houdini containment remains unresolved after B4A.

The B3 simulator closes the claim-after control race with a fake-only arbiter: cancel, deadline, shutdown, each next mutation, and final commit compete for one authority lock, and every mutation performs its control check while that lock remains held. One re-entrant scene/snapshot lock keeps apply and simulated Undo exclusive and prevents reads from observing staging or rollback state. All exceptions after publication enter exact-object confined rollback; interruption exceptions are re-raised only after best-effort rollback or write freeze. The proof includes exact mapping key, path, Python object identity, recorded identity, transaction, ownership, target parent/name, and Undo/audit identity. Verification reconstructs all ten checks from observed fake state and converts values outside the frozen schema to a bounded structured failure.

Gate B4A adds only the dormant low-level adapter beneath that model. It seals the exact non-cancelled internal `SceneQueue` request/claim pair and redeems the resulting binding once, consumes the existing normalized graph, recomputes both digests, and rechecks main-thread identity, latch, control state, session, revision, fingerprint, target absence, injected catalog, transaction identity, and a non-blocking writer guard before any fake-HOM mutation. It reuses the existing Schema, normalization, approval-binding, and `SceneQueue` semantics; it creates no second queue, approval system, idempotency store, transaction framework, registry, service, or protocol. Table and stairs use the same catalog-driven translator.

All B4A fake-HOM mutations occur in exactly one Undo group through the seven existing stages. The atomic control guard wraps every mutator and finalizer, but every callback revalidates UI-thread identity because guard scheduling is untrusted. Clock values are finite/non-negative at entry and locked preflight or the operation fails before Undo/mutation. Group factory/enter/exit failures have explicit containment, and missing candidate output uses the unified rollback/Undo-close path. Every creation return is identity- and registry-proven before later mutation; the complete retained child identity/session/path/name/type set must match exactly. Success preserves the live `/obj` parent and pre-existing fingerprints, rechecks exact paths, zero root connections, and both directions of every child connection, then repeats full observed verification after Undo exit before publication. Rollback requires the same parent/fingerprint/child/connection confinement before destroy; extra/replacement/unreturned children or any external edge blocks blind destroy. It proves all root/child paths absent and baseline registry restored both after destroy and again after Undo exit. Every outcome publication requires exact guarded-report and independent capability-report agreement; retained indeterminate scope gets one best-effort bounded refresh even after finish failure, never a second commit refresh, and reports revision at least base plus one. `KeyboardInterrupt` and `SystemExit`, including during rollback, complete best-effort containment or freeze before re-raise. Fake events prove exact delivered-source identity only, and fake thread/clock guards do not verify live HOM scheduling, timing, callback delivery, or coverage. These tests prove the adapter's fake call order and guards only, not real Houdini semantics.

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

The completed B2C evidence proves the project configuration is closed to one stdio server, forwards only reviewed environment variable names, enables exactly two read tools, disables all three graph tools, embeds no URL/token/credential, and matches the pinned Codex 0.144.3 syntax contract. B4A preserved that evidence unchanged, and B4B must continue to preserve it.

Gate B3 offline tests proved independent declared/observed state, a preserved sentinel, exact approval binding and one-use semantics, per-HIP single writer, stale session/fingerprint/revision rejection, exact replay and changed-content conflict, deterministic failure injection at every mutation boundary, identity-scoped rollback, rollback-failure write freeze, mandatory internal postconditions, observed-state tamper detection, exactly one revision advance, exactly one simulated Undo transaction, and full restoration by one test-only Undo.

B4A proved that table and structurally different stairs traverse one dormant adapter path without an asset-role field, fixed structure, name-derived semantics, or per-node handler. It also proved preflight zero-mutation failures, exact transform-to-`xform` resolution, typed parameter calls, exactly one fake Undo group, one revision advance, observed-state postconditions, all seven failure stages, exact-root rollback, interruption re-raise, indeterminate write latch, and preserved pre-existing content. Every result excludes tokens, environment data, raw chat, tracebacks, and user paths. Frozen Schema hashes and the exact production two-tool `tools/list` remain unchanged.

The authorized B4B live acceptance is limited to the frozen stairs fixture in one blank unsaved disposable HIP and must prove:

1. All `hou` reads and writes execute on the UI main thread.
2. The live build reports every canonical type and admitted typed parameter required by the selected fixture.
3. Before production enablement, an explicitly reviewed live capability probe establishes observer installation for newly created nodes and actual event/source coverage for every used mutation, including `setUserData`; absent or unreliable coverage blocks writes rather than inheriting fake behavior.
4. One successful request creates only one approved `/obj/HIA_Graph_<id>` root with no connections and exactly its normalized declared children, typed values, bidirectionally verified transaction-owned wiring/cardinality, flags, and graph digest. If a root remains after an `ok=false` acceptance, it enters only the mandatory manual-cleanup branch and cannot satisfy this success claim.
5. One manual Undo removes the whole new graph and restores every pre-existing path, parameter, connection, flag, selection, and current-node value to the baseline. Cleanup restoration after an `ok=false` apply is recorded as cleanup only, never as Gate acceptance.
6. The single acceptance does not replay apply or deliberately inject the complete destructive B4A failure matrix.
7. No test saves the HIP, writes an artifact, renders, publishes an HDA, or opens another network listener.

At the time of this offline implementation report, none of these real-`hou` write, callback-order, rollback, wrapper-identity, version-compatibility, or Undo claims has been executed. B3, B4A, and B4B fake evidence do not change that status. The user-run Panel result is the only evidence that can close the one live Apply and manual Undo claims; even a successful run leaves real automatic rollback unverified.

## Residual risks

- A process running as the same Windows user may inspect Houdini/Bridge memory or environment and steal the Bearer token. Loopback and token controls do not provide OS-user isolation.
- In-process `hou` calls and Houdini cooks cannot be safely hard-killed without risking the user's session. Strict graph/type/count/deadline bounds reduce but do not eliminate UI stalls.
- Correct main-thread scheduling, node-type names, parameter semantics, callback delivery/source identities, observer attachment to newly created nodes, `setUserData` event coverage, rollback, and Undo behavior depend on the live Houdini build. The one-shot B4B Panel fails closed when its bounded acceptance cannot prove them; fake evidence cannot satisfy the live claims.
- A HIP may contain callbacks, locked content, or vendor behavior that changes when nodes are created under `/obj`; static validation cannot prove those side effects absent.
- Scene revision tracking can miss manual mutations unless every relevant Houdini lifecycle/change callback is reliable. Post-verification and conservative conflict handling reduce, but cannot erase, this risk.
- A schema-valid graph can still be aesthetically or semantically wrong. Codex reasoning, the user's exact approval, and deterministic verification address intent; they do not make geometric judgment infallible.
- A crash or power loss during in-process mutation may bypass normal rollback and audit completion. P2-V deliberately does not auto-save or reload, so user inspection may be required.

## Explicit non-goals

B4A and the frozen `0.1.0` graph contract do not provide or permit:

- Modification of existing user graphs, deletion tools, renaming/reparenting existing nodes, arbitrary selection edits, or arbitrary node/parameter introspection beyond the reviewed bounded read-only response.
- Node types outside the B4A first certified `Object/geo`, `Sop/box`, `Sop/transform`, `Sop/merge`, and `Sop/null` catalog, or targets outside a new `/obj/HIA_Graph_<id>` namespace. Later additions require a new reviewed catalog/protocol version and risk policy.
- Python, HScript, expressions, `exec`, `eval`, shell/process execution, callbacks, screen takeover, Computer Use, or arbitrary filesystem paths.
- HIP save/overwrite, file import/export, cache, render, simulation, background job, USD/Solaris, material, animation, HDA creation/publishing, or preference/configuration changes.
- A new network service, fixed port, remote worker, WebSocket, LAN/public access, bypass of Bearer authentication, or direct exposure of the app-server/Houdini gateway.
- Experimental app-server APIs, `dynamicTools`, process APIs, `thread/shellCommand`, or automatic MCP/tool discovery outside the five pinned tools.
- A second Agent, LLM, planner, RAG/vector store, semantic-memory service, or duplicate chat database.
- Production-readiness claims before the user-run B4B live-`hou` main-thread, post-verification, and one-manual-Undo evidence passes; fake-HOM rollback tests are not live rollback evidence.
