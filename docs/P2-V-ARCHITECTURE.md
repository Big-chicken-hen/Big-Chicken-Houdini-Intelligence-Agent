# P2-V architecture design review

## Review status and authorization boundary

Gate B0, Gate B1, Gate B2, and Gate B3 are complete. The Gate B2C real read-only MCP chain completed at commit `edf7f3a`, its direct HTTPS transport hotfix completed at commit `3213625`, and the pure-Python Gate B3 transaction simulation completed at commit `862defff`. The production profile continues to expose exactly `houdini_scene_info` and `houdini_node_type_info` through the pinned Codex 0.144.3 app-server, project-scoped stdio MCP sidecar, authenticated loopback Bridge, and live Panel.

Gate B4A is authorized only to add one dormant dependency-injected HOM graph write adapter and test it against fake-HOM. The adapter is not imported, constructed, registered, or callable through any production path. Gate B4A does not authorize Gate B4B, real `hou`, a real scene write, cook, render, save, HDA operation, or arbitrary code. `houdini_graph_validate`, `houdini_graph_apply`, and `houdini_graph_verify` remain disabled and unregistered in the production Codex profile.

Schema versions `0.1.0` and `0.2.0` are frozen pre-release. The former is the five-tool graph contract and the latter is the production two-tool read-only contract. Neither directory may be edited in place; any future breaking change requires a new schema version.

Codex remains the sole intelligent agent, planner, dialogue owner, and memory source. The MCP adapter, Bridge, Panel executor, and Houdini runtime perform only deterministic validation, transport, execution, and verification. They do not add an Agent, LLM, planner, RAG system, semantic memory, prompt rewriting, or screen control.

## P2-V scope

P2-V defines one general declarative graph surface. A table is the first acceptance fixture, not a tool, product boundary, fixed topology, or naming convention. The same contract must represent structurally different assets such as a stool, cabinet, steps, or simple building blocks without adding object-specific fields.

The exact reviewed tools are:

1. `houdini_scene_info`
2. `houdini_node_type_info`
3. `houdini_graph_validate`
4. `houdini_graph_apply`
5. `houdini_graph_verify`

Those five names describe the frozen graph contract. Gate B3 exercised all five only inside an explicitly constructed offline fake harness; Gate B4A consumes the already validated graph contract only through an explicitly constructed dormant adapter and fake-HOM. The production runtime surface remains the strict two-tool subset `houdini_scene_info` and `houdini_node_type_info`; the other three names remain dormant in production until a later gate explicitly authorizes them.

The frozen graph contract expresses only bounded OBJ/SOP creation beneath one new, validated `/obj/HIA_Graph_<id>` container. B4A implements that current contract against fake-HOM without enabling creation in a production runtime. The five admitted types are the first certified catalog for one catalog-driven Houdini graph engine, not a permanent writer boundary. Later node types or Houdini contexts require separately reviewed catalog/protocol versions and context-specific risk policy, not per-node handlers, asset-specific branches, an open enum, or a general DCC framework. P2-V does not provide arbitrary Python, HScript, expressions, callbacks, `eval`, shell execution, filesystem access, HIP save, rendering, caching, HDA creation or publishing, node deletion as a public tool, modification of an existing user node, or references outside the request-owned container.

## Trust and transport path

```text
Codex app-server (pinned 0.144.3; reasoning and tool selection)
    -> project-local MCP subprocess over stdio
Project-local Houdini MCP adapter (exactly two production read-only tools)
    -> authenticated HTTP to 127.0.0.1:<random-port>
Existing Bearer-authenticated loopback Bridge (validation and bounded queue)
    <- nonblocking authenticated status/read dispatch
Houdini Intelligence Panel main-thread read adapter
    -> bounded hou API reads on the Houdini UI thread only
Live Houdini session
```

The app-server remains stdio-only and is never exposed on a network socket. The MCP adapter is a project-local stdio child process, not an HTTP server. The Bridge retains its random loopback port and random session Bearer token; neither the token nor the scene gateway is exposed to a LAN or the internet. The Panel does not block its UI thread while waiting for Bridge work.

No component in this path uses Computer Use or screen takeover. Gate B4A does not modify this production path: every graph tool and every write/cook/save/render/HDA operation remains disabled there.

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
- The production runtime path contains no node creation, parameter mutation, connection, deletion, cook, save, render, HDA, or Undo operation in B4A.
- Does not import, construct, register, or call the dormant B4A write adapter; every production graph transaction path remains disabled until a separately authorized later gate.

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

## Completed Gate B2C production read-only execution sequence

1. Codex invokes either `houdini_scene_info` or `houdini_node_type_info` through the project-local stdio MCP child.
2. The adapter accepts only the pinned MCP handshake and exact two-tool allowlist, then queries the authenticated status facade for a current trusted snapshot.
3. The Bridge-owned parameter facade supplies the trusted HIP session and revision. For a node-type request, only the reviewed canonical type name remains caller intent; technical attestation fields cannot be forged through tool arguments.
4. The Bridge validates the active read profile and enqueues a bounded read for the Panel. Missing or stale capability state fails with a structured `HOUDINI_UNAVAILABLE` or `CAPABILITY_MISMATCH` result.
5. The Panel's main-thread timer executes only bounded `hou` reads and posts one correlated plain-dictionary result. Workers never call `hou` and no request changes scene state.
6. Closing the Panel or losing the live capability causes a finite explicit failure. It does not hang, renew a stale lease, start a replacement service, or infer success.

The launcher briefly asks Windows for an ephemeral IPv4 loopback port, releases the probe, and supplies the resulting Bridge URL only through the owned Bridge and Houdini child environments. The Bridge must bind that exact origin before app-server startup or fail closed; it does not retry on another port. The Bearer token is also passed only through owned child environments. Bootstrap contains neither URL nor credential, and neither may appear in configuration values, command arguments, logs, diagnostics, or documentation. The separate Panel executor credential is never inherited by Codex or the MCP child.

### Gate B3 pure-Python transaction sequence

Gate B3 reuses the frozen schemas, `SchemaRegistry`, normalization/digest functions, typed scene approval, `SceneQueue`, and both fixtures. It does not start the real MCP, Bridge, app-server, Panel, Houdini, or hython, and it does not add a production handler. The simulation profile is available only to tests or an explicitly named headless offline harness.

1. Validate the request through the frozen contract and normalize the complete graph without changing observed fake scene state.
2. Bind one explicit approval to the full normalized graph, graph and approval digests, target, nodes, parameters, connections, flags, layout, Thread/Turn, HIP session/fingerprint/revision, idempotency key, permission, and deadline.
3. Atomically reserve the single fake writer and re-check session, fingerprint, revision, deadline, approval, idempotency, and ownership preconditions.
4. Under one re-entrant scene/snapshot lock, create a new fake HIA-owned root object, declared child objects, typed parameters, owned-node connections, flags, and optional layout through fixed, individually fault-injectable mutation phases. A content-blind control arbiter makes the check for cancel, deadline, or shutdown and each following mutation one atomic authority step.
5. Re-read independent observed fake state, reconstruct the canonical graph, and run mandatory internal postcondition verification before commit.
6. Commit exactly once at the final control authority point, atomically publish committed state, revision, fingerprint, dirty state, Undo, and audit, and record one test-only simulated Undo transaction.
7. On every ordinary failure after mutation begins, roll back only the current request's proven root mapping key and object identity. `KeyboardInterrupt` and `SystemExit` attempt the same containment before being re-raised. An exception or uncertainty during rollback freezes later fake writes.
8. External verify independently reconstructs observed state and derives session, revision, target, ownership, nodes, parameters, connections, flags, cook, and graph-digest checks from that state. An observed value outside the frozen output schema produces a structured `VERIFY_FAILED`, not an uncontrolled validation exception. Exact idempotent replay returns the original result without a second execution; changed reuse returns `IDEMPOTENCY_CONFLICT`.

### Gate B4A dormant fake-HOM adapter sequence

Gate B4A adds one low-level adapter with explicit `hou_module`, read-adapter, UI-main-thread identity, clock, atomic control guard, and claim-authority dependencies. It has no `main()`, enable switch, service, production registration, natural-language logic, approval UI, queue, or idempotency store. Its `apply_prevalidated` entry consumes only an already normalized graph paired with the exact internal `SceneQueue` request and executor claim. The injected authority consumes the same request and claim objects once; copies, reconstructions, absent claims, and second consumption fail closed. Request booleans never grant authority.

Before its first fake-HOM mutation, the adapter recomputes graph and approval-binding digests and rechecks main-thread identity, frozen/indeterminate state, deadline/cancel/shutdown, HIP session, scene revision, `/obj` fingerprint, exact target absence, the active type/parameter catalog, transaction identity, and a non-blocking single-writer guard. The injected clock is sampled at adapter entry and again inside locked preflight; each result must be numeric, finite, non-negative, and not boolean. A clock exception or invalid value fails closed before Undo entry with zero mutation and revision change. The adapter then performs the existing seven stages inside exactly one injected `undos.group("HIA: Apply Graph")`. The same atomic control guard wraps every mutating callable and the final commit callable, so cancellation, deadline, shutdown, mutation, and finalization have one authority order. The guard is not a thread-affinity authority: every `mutate`, `contain`, and `finalize` callback rechecks the Houdini UI-main-thread identity at callback entry before reading or changing adapter, scene, Undo, observer, or revision state. Immediately after every root or child `createNode` return, and before any following mutation, one common proof requires a new object identity plus the approved name, exact path, resolved type, session identity, parent identity/membership, and an exact `hou_module.node(path)` registry lookup. Table and stairs traverse the same catalog-driven translation path.

Undo-group creation and explicit `__enter__`/`__exit__` are inside the same authority lock as scene and revision containment. A factory failure before enter may end with a recoverable zero-scene-mutation result. Once `__enter__` is attempted, any enter or exit exception makes the Undo publication boundary indeterminate, freezes later writes, and permits no blind follow-up mutation. A missing candidate output enters the same confined rollback and one Undo-close path as any other pre-commit failure. Publication of each `committed`, `rolled_back`, or `indeterminate` outcome must invoke its guarded callback exactly once and return the exact callback report. For retained indeterminate scope, even an owned-write finish failure is followed by one bounded best-effort observer refresh before independent report comparison; a commit-path refresh that was already attempted is not retried. The reports must agree on availability, session, catalog, and outcome-specific revision/fingerprint, and indeterminate revision is at least base plus one. The adapter retains the returned root and child objects with their session IDs, parent, exact paths/names/types, ownership markers, transaction ID, and graph digest. If any creation raises after a possible partial mutation but before returning that exact identity, confinement cannot be proved, so the writer freezes and reports an indeterminate result.

Mandatory postcondition verification reads only that retained root and its direct declared children. It does not recursively inspect `/obj`, reuse request values as observation, report the validation-only layout request as observed, or invoke a layout mutator. Both success and rollback require the live `/obj` lookup to be the exact retained parent and every pre-existing `/obj` child identity/fingerprint to remain unchanged. The root's children must equal the complete retained declaration by identity, session, exact path, name, and resolved type; any extra, replacement, missing, or partially created but unreturned child makes blind rollback unsafe.

Success requires each approved exact path lookup to resolve to the retained root or child identity, the root's complete input/output connection collections to be empty, and the children collectively to expose exactly the declared input/output cardinality with every source and destination endpoint resolving by identity inside the current transaction. After successful Undo exit, this full observed-state proof is repeated before publication. A post-mutation failure may call `destroy()` only after the root's exact path lookup, retained-child proof, unchanged-pre-existing fingerprint, every other identity proof, and bidirectional connection confinement succeed. Any external-to-owned or owned-to-external connection makes blind destruction unsafe and the result indeterminate. After destroy, the root path and all retained-child paths must be absent and the exact parent/pre-existing fingerprint restored; the complete rollback proof is repeated after Undo exit. Ambiguity or rollback failure latches later writes as indeterminate. `KeyboardInterrupt` and `SystemExit`, including one raised by rollback itself, complete best-effort containment or write freeze before the applicable interruption is re-raised.

## `houdini_graph_apply` transaction contract and B3/B4A offline evidence

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

The B3 executor creates one new observed fake OBJ geometry container and only the normalized fake SOP nodes declared inside it. The B4A adapter performs the corresponding low-level calls only against explicitly injected fake-HOM in this gate; every declared child uses the common catalog-resolved `createNode` path with `run_init_scripts=False`. Neither path may traverse to or reference the pre-existing sentinel or any object outside the new request-owned container. This remains offline evidence, not a real Houdini node operation.

The normalized graph contains:

- a versioned context declaration (OBJ root with SOP children in this stage);
- a target/root policy requiring one new HIA-owned container;
- bounded request-local node IDs, live-resolved allowlisted types, name hints, and parent references;
- closed typed parameters using only finite `float`, `int`, `bool`, bounded `string`, or homogeneous tuples;
- owned-node source/output to destination/input connections;
- explicit display/render flags and an optional bounded layout request;
- exact session, revision, idempotency, deadline, and canonical graph digest.

The B4A certified catalog is deliberately small (`Object/geo`, `Sop/box`, `Sop/transform`, `Sop/merge`, and `Sop/null`), with `Sop/transform` resolving to `xform`, but it is the first capability catalog rather than an asset recipe or permanent engine boundary. Node types and parameter names are resolved against the injected active catalog before mutation. The same catalog-driven engine must accept fixtures with different node counts, names, parameter values, and topology. Later expansion requires separately reviewed catalog/protocol versions and risk policy.

Gate B3 records one pure-Python simulated Undo transaction, and Gate B4A verifies one fake-HOM Undo-group call boundary plus injected thread/clock guards. Future B4B must separately prove that one live apply and one manual Undo affect only the selected request-owned graph; neither B3 nor B4A proves real `hou.undos.group`, HOM main-thread scheduling, or live timing semantics.

### Failure containment

The simulator records the newly created root and each returned child identity before further mutation. Rollback may remove only that exact request-owned root after the live `/obj` object, unchanged pre-existing child fingerprints, root identity/name/path/type/session/registry, complete retained-child identity/session/path/name/type set, ownership, graph digest, and bidirectional connection confinement all match. An extra, replacement, or partially created but unreturned child blocks blind rollback. After destruction every retained root/child path must be absent and the exact baseline parent membership restored; Undo exit is followed by the same proof again. The adapter never searches for similarly named objects and never deletes or repairs the sentinel or other pre-existing content.

If those proofs are unavailable, the executor stops and returns a high-severity structured error rather than guessing. No public delete tool is exposed.

### Postconditions

- The live `/obj` lookup is the retained parent, its pre-existing child fingerprints are unchanged, and the container's complete child set has exactly the expected identities, session IDs, parents, names, live types, paths, and path-to-object registry mappings.
- The root has no input or output connection, and every child input/output connection matches the approved declaration with exact transaction-owned source/destination identities and exact total cardinality.
- Parameter values round-trip to the accepted values.
- Display/render flags exactly match the normalized declaration and no undeclared node receives a flag.
- Cook and node errors are returned as bounded warnings/errors.
- Every created path is below the exact request-owned container.
- `created_nodes` is complete, `changed_nodes` contains no pre-existing path, and the successful revision advances exactly once.

Postcondition failure triggers the same request-scoped rollback before a result is committed.

## Revision, HIP session, and idempotency

`hip_session_id` changes whenever the active HIP identity is replaced, cleared, or reloaded. A monotonic in-session `scene_revision` advances for accepted tool writes and for observed manual scene changes relevant to the protected scope. The exact Houdini event callbacks used to observe manual changes must be capability-tested in the live build; missing reliable observation blocks writes rather than permitting stale updates.

A request based on an older revision returns `SCENE_CONFLICT` with the safe current revision and requires Codex to inspect again. A request from another HIP session returns `HIP_SESSION_MISMATCH`. Neither path performs a mutation.

B4A may add only dormant owned-write `begin`/`finish` coordination to the existing read adapter. An unforgeable in-process token binds one transaction to its base session, revision, fingerprint, and observer sequence. Each fake mutator arms one exact registered callback-source object by identity; for modeled root `ChildDeleted`, that object is the retained `/obj` parent while the deleted root is event detail. If an event is actually delivered, only a matching source may coalesce; a delivered missing-source, unexpected, external, late, or off-main-thread event invalidates instead. A zero-event expectation is allowed and proves neither that no mutation occurred nor that a real HOM call is observable.

Every `committed`, `rolled_back`, and `indeterminate` publication returns its exact guarded report and must match an independent capability report. If an indeterminate outcome retains the owned root or children, the exact publication is followed by one bounded best-effort refresh that installs observers on that retained scope even when finish failed; the refreshed state and only then the independently sampled report must agree. A refresh already attempted on the commit path is never repeated. Outcome-specific revision and fingerprint rules prevent a rollback from claiming a changed snapshot or an indeterminate result from claiming the base snapshot, and an indeterminate revision is never below base plus one. B4A's fake refresh models observer installation and report agreement only. It does not prove that observers can be installed soon enough on live newly created nodes, that every live mutator produces an event, or that `setUserData` and other mutations have reliable event coverage. Those are fail-closed B4B capability-probe blockers; when this dormant coordination is unused, B2 read behavior remains unchanged.

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

Declarative Houdini graph creation may expand beyond the first OBJ/SOP catalog only through separately reviewed catalog/protocol versions and context-specific risk policy consumed by the same graph engine. It must not grow through per-node handlers, asset-specific modes, opaque payloads, or a general DCC framework. Non-graph capabilities such as geometry queries, Karma rendering, animation, simulation/cache jobs, HDA validation/publishing, and approval-gated controlled Python remain separate future contracts and phase gates.

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
12. Apply must perform mandatory internal postcondition validation. A later Codex call to `houdini_graph_verify` is an additional read, not a substitute. B3 and B4A provide only fake-state evidence for these boundaries.
13. B2C resolves only the exact project-local two-tool read-only MCP TOML and finite read chain. B3 proved pure-Python approval, transaction, rollback, postcondition, and simulated Undo behavior; B4A may prove only the dormant adapter's fake-HOM call order and guards. Write approval UI, live cook, callbacks, live rollback, real `hou.undos.group`, and every real graph mutation remain unresolved and fail closed until separately approved B4B work.

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

### B4A — Dormant HOM graph adapter, fake-HOM only

Implement one dependency-injected, production-unwired adapter that consumes the frozen normalized graph and internal approval binding. Exercise table, stairs, conflict, cancellation, deadline, rollback, postcondition, and all seven mutation-stage failures only with fake-HOM. Do not import real `hou`, start Houdini, enable graph tools, or modify a real HIP.

### B4B — Separately approved single live graph acceptance

Only after B4A evidence is reviewed, request explicit approval for one selected fixture, one live apply in one blank unsaved disposable HIP, and one manual Undo. That bounded acceptance must also capture capability-probe evidence for observer installation on the newly created nodes and actual event/source coverage of each mutation it exercises, including `setUserData`; if the one apply and Undo cannot prove reliable coverage, stop and request a separate scope rather than adding mutations. Compare all pre-existing scene state before, after, and after Undo; do not save the HIP or deliberately replay the complete destructive failure-injection matrix in real Houdini. B4B is not authorized by B4A completion.

### B5 — P2-V closeout

Run the complete offline suite and the approved finite live acceptance matrix, report all unverified behavior and warnings, inspect the Git diff, and wait for explicit submission approval. Do not enter rendering, HDA, arbitrary Python, wider DCC contexts, or later phases.
