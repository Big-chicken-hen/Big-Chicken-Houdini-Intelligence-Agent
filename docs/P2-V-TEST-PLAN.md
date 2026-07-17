# P2-V test plan

## Status and authorization boundary

Gate B0, Gate B1, Gate B2, and Gate B3 are complete. Gate B2C closed the real pinned-Codex-to-stdio-MCP-to-authenticated-Bridge-to-Panel read chain at commit `edf7f3a`; the direct HTTPS transport hotfix closed at commit `3213625`; Gate B3 closed at commit `862defff`. Gate B4A is authorized only for one dormant dependency-injected HOM graph write adapter and fake-HOM tests.

Production registration remains limited to `houdini_scene_info` and `houdini_node_type_info`. The B4A adapter is never imported, constructed, registered, or called by production. Gate B4B, every real scene write, real `hou`, Houdini GUI, hython, and the live MCP/Bridge/app-server chain remain unauthorized.

## Acceptance claim

Full P2-V eventually succeeds only when Codex can use the same general graph contract to validate and create multiple editable Houdini networks under new `/obj/HIA_Graph_<id>` containers. B3 proved the deterministic fake transaction model. B4A tests the low-level adapter's HOM call order, state transitions, and safety constraints against fake-HOM using table and structurally different stairs fixtures. Neither gate proves live node creation, Undo, cook, callbacks, rollback, main-thread scheduling, or Houdini-version compatibility.

Passing Schema tests alone does not prove live Houdini behavior. Evidence is accumulated in five layers and the separately approved live acceptance layer is mandatory before any live claim.

## Test environments

| Environment | Purpose | Scene writes |
|---|---|---|
| Standard-library CPython | JSON parsing, schema-contract checks, validators, queues, idempotency, and transport fakes | None |
| Fake MCP client and fake Panel gateway | End-to-end correlation, approval, timeout, and error mapping | None |
| Injected B4A fake-HOM | Low-level HOM calls, catalog translation, postconditions, Undo-group boundary, and rollback containment | Simulated only |
| Houdini read-only probe | Runtime version, node-type, parameter, and main-thread capability discovery | None |
| Future B4B blank unsaved disposable HIP | One selected graph apply and one manual Undo | One separately approved container |

No test saves or overwrites a HIP, writes an HDA, renders, installs a package, modifies Houdini preferences, or writes outside `E:\houdini-intelligence-agent`. Runtime records, if later approved, remain below `.runtime`.

## Layer 1: offline schema contract

These tests run with Python `-B` and require no third-party package.

### Contract inventory

- `P2-C001`: `manifestVersion` is `1.0`, `schemaVersion` is frozen pre-release `0.1.0`, `contractStatus` is `frozen_pre_release`, and the manifest exposes exactly `houdini_scene_info`, `houdini_node_type_info`, `houdini_graph_validate`, `houdini_graph_apply`, and `houdini_graph_verify`. The rejected four-tool draft was never published or enabled; breaking changes must increment the schema version.
- `P2-C002`: every referenced input and output schema exists, parses as UTF-8 JSON, uses JSON Schema draft 2020-12, and has an object root with `additionalProperties: false`.
- `P2-C003`: annotations mark only `houdini_graph_apply` as write-capable; it is idempotent, not open-world, and not destructive to existing data. The other four tools are read-only.
- `P2-C004`: only the five reviewed graph-level tools are exposed. No standalone node/parameter mutation alias, arbitrary Python, shell, HScript, expression, file, render, HDA, delete, or save tool exists.
- `P2-C005`: the manifest records `schemaDigestEncoding=canonical-json-utf8-v1`; each of the ten SHA-256 values is computed from strict parsed JSON serialized as sorted-key, whitespace-free UTF-8 with preserved Unicode. Line-ending or indentation conversion cannot change protocol identity.
- `P2-C006`: protocol files contain no asset-specific node role, fixed object topology, fixed primitive count, or object dimensions. A source-contract denylist rejects legacy table-role tokens outside `tests/fixtures`.
- `P2-C007`: JSON loading rejects duplicate object keys and non-finite constants; every local `$ref` resolves inside its own schema. Tests distinguish accepted object boundaries from constraint fragments such as `if`, `then`, and `contains`.

### Request envelope

- `P2-C010`: write input requires `request_id`, `thread_id`, `turn_id`, `hip_session_id`, `base_scene_revision`, `idempotency_key`, `deadline_ms`, and `permission_level=scene_write`.
- `P2-C011`: read inputs require correlation identifiers and `permission_level=scene_read`; missing, empty, overlong, control-character, and unknown fields fail validation.
- `P2-C012`: revisions are non-negative integers, deadlines are finite and bounded, and identifiers have explicit length and character limits.
- `P2-C013`: NaN, infinity, numeric strings, boolean-as-number values, and oversized arrays are rejected before dispatch.

### Declarative graph

- `P2-C020`: both `tests/fixtures/p2_v/table_graph.json` and `tests/fixtures/p2_v/stairs_graph.json` validate through the same Schema and deterministic cross-field test oracle, and differ in node count, names, parameter values, and topology. Their static parameter names are offline candidates only; live acceptance must replace or reject them according to `houdini_node_type_info` from the active build.
- `P2-C021`: context is versioned and currently permits only an OBJ root with SOP children. The target must be one new HIA-owned `Object/geo` container matching `HIA_Graph_<id>` beneath `/obj`.
- `P2-C022`: request-local node IDs and name hints are bounded and unique, parent references resolve, and only `Sop/box`, `Sop/transform`, `Sop/merge`, and `Sop/null` are admitted for this version. Namespaces, unreviewed versions, HDAs, Python nodes, and category changes fail.
- `P2-C023`: parameter names are unique per node and assignments are closed typed values: finite `float`, bounded `int`, `bool`, bounded inert `string`, or a homogeneous bounded tuple. Expressions, callbacks, code, backticks, ramps, buttons, multiparms, file paths, and spare parameters fail.
- `P2-C024`: connections refer only to owned request-local node IDs and bounded source/output and destination/input ports. Unknown endpoints, duplicate input slots, cycles where prohibited, self-links, external paths, and cross-container references fail.
- `P2-C025`: explicit display/render flags and optional bounded layout are admitted; undeclared flags and layout modes fail. The current SOP slice requires exactly one display node and one render node, both flags on the same declared node; its name has no implicit output role and no topology is fixed.
- `P2-C026`: the approval binding covers `request_id`, Thread, Turn, HIP session and fingerprint, expected revision, permission, idempotency key, trusted deadline, graph Schema version, complete normalized target/nodes/types/names/parents/parameters/connections/flags/layout, canonical graph digest, and a closed side-effect summary. Mutation of any bound field changes the request/approval digest and invalidates the prior approval.
- `P2-C027`: absolute filesystem paths, `..`, UNC, device paths, ADS, URI values, environment expansion, executable text, and references to existing scene nodes have no accepted field.

### Responses and errors

- `P2-C030`: every output contains `ok`, request/Thread/Turn correlation, HIP session, base and resulting scene revisions, idempotency key, result, warnings, and a structured error branch. Schema conditionals require exactly one coherent branch: success has a non-null result and null error; failure has null result and a non-null bounded error. Only apply reports created/changed paths, and every such path remains inside the request-owned HIA graph.
- `P2-C031`: successful validate/apply/verify outputs agree on one stable canonical graph digest; apply reports only nodes created inside its new container, idempotency replay state, and the reviewed Undo label.
- `P2-C032`: error objects are JSON serializable and contain a stable code, safe message, retryability, and non-secret details. Tracebacks and Bearer tokens are never the sole or exposed response.
- `P2-C033`: deterministic validation requires output correlation to echo request/session/revision/idempotency fields, prevents revision regression, and rejects a container path or replay record that does not match the submitted request.
- `P2-C034`: node-type results correspond uniquely and exactly to the query set; verification checks, issues, validity, and graph digest cannot contradict one another.
- `P2-C035`: without a current fake attestation matching the reviewed contract hashes, B1 fails all five tools with `HOUDINI_UNAVAILABLE` or `CAPABILITY_MISMATCH`; no live fallback exists.

## Layer 2: pure deterministic components

These tests use no real `hou`. Gate B3 covers the complete validate -> scene approval -> transactional apply -> mandatory internal postcondition -> verify -> optional test-only simulated Undo chain. Gate B4A adds the dormant low-level HOM-call adapter beneath that frozen behavior and tests it only with explicit fake-HOM injection.

### Validation and canonicalization

- Canonical graph hashing is order-stable and includes the graph-contract version, target/root, and complete normalized graph. A separate request/approval digest additionally binds HIP session, base revision, permission level, idempotency key, deadline, Thread/Turn, and side-effect summary.
- The graph digest changes when any target, node, live type, typed parameter, connection, flag, or layout item changes. The request/approval digest also changes when session, revision, idempotency key, deadline, correlation, or side effects change.
- `houdini_graph_validate` is pure: its fake scene fingerprint and revision are unchanged, repeated normalization produces identical bytes/digest, and its result is the only graph form eligible for approval.
- Validation is deny-by-default and completes before queue insertion.
- Unknown tool names, protocol methods, fields, node types, parameters, and error codes fail closed.

### Approval

- A read-only call cannot be relabeled as a write call after validation.
- `houdini_graph_apply` creates a Panel approval containing the exact normalized target, every child/type/name/parent/parameter/connection/flag/layout item, graph Schema version, request/Thread/Turn, HIP session/fingerprint, base revision, permission level, idempotency key, trusted deadline, Undo label, closed side-effect summary, and both graph/request digests.
- Denial, dismissal, expiry, Panel disconnect, digest mismatch, or session change produces no queued scene operation.
- Approval is one-use, request-bound, Turn-bound, session-bound, and time-bounded. It cannot become a permanent grant.
- A late approval for an old request cannot authorize a newer request.

### Single writer and deadline

- The offline queue accepts one fake write at a time and preserves FIFO correlation without allowing two fake executor invocations concurrently. This is not evidence about `hou`.
- Two simultaneous apply requests cannot both pass the atomic revision check.
- Expired work is rejected before main-thread dispatch. A cancelled or timed-out request cannot execute later from the queue.
- Read requests may wait behind the active write but cannot observe a half-built graph as a committed result.

### Revision and HIP session

- A new/opened HIP changes `hip_session_id` and invalidates all queued approvals and idempotency entries from the previous session.
- A boundary fingerprint change advances the monotonic scene revision before comparing `base_scene_revision`.
- An old revision returns `SCENE_CONFLICT`; last-write-wins and implicit rebasing are absent.
- A different HIP session returns `HIP_SESSION_MISMATCH`, even if the numeric revision is equal.

### Idempotency

- First use of a key plus graph digest creates one result.
- The same key, session, and graph returns the original result with `replay.replayed=true`, the same nested/top-level idempotency key, and a consistent `original_request_id`; it performs no second fake executor invocation.
- The same key with a different graph, session, or approval digest returns `IDEMPOTENCY_CONFLICT`.
- B1 does not assume success across a simulated Bridge restart and does not auto-replay an indeterminate write. Later live reconciliation of a request-owned tagged container remains unverified until its separately approved gate.

### Gate B3 fake transaction state and rollback

- Declared graphs are never stored as observed scene truth. The simulator maintains independent observed node identity, type, typed parameters, connections, flags, layout, and ownership records, and verify reconstructs canonical graph data from those records.
- A non-HIA sentinel is present before every transaction. Success, every injected failure, rollback, and simulated Undo preserve its complete observed state.
- Root creation, child creation, typed parameter assignment, connection creation, flags/layout, internal postcondition verification, and commit are fixed deterministic phases with one failure injection point at every mutation boundary.
- Deterministic unexpected `RuntimeError` injection covers root publication, every mutation phase, postcondition, commit, result construction, and audit construction. `KeyboardInterrupt` and `SystemExit` injection proves best-effort containment followed by re-raise, with no traceback or exception text in results or audit.
- Claim-after cancel, deadline, and shutdown tests pause after mutation boundaries and between two boundaries. The guard check and next mutation share one arbiter, while final commit competes under the same authority; tests prove both cancel-wins and commit-wins outcomes without a late write or contradictory terminal result.
- One re-entrant scene/snapshot lock covers apply and simulated Undo. Concurrent scene-info, verify, and capability-snapshot readers at every boundary observe only the complete pre-transaction or post-transaction state, or a bounded `WRITE_IN_PROGRESS` result.
- A failed transaction rolls back only the exact root object identity created and owned by that request. Rollback never searches by name or prefix and never touches a pre-existing collision.
- Rollback-proof tampering independently covers the mapping key, root path, target parent/name, Python object identity, recorded identity, transaction ID, and ownership. Every mismatch becomes indeterminate and freezes fake writes without deleting the ambiguous object.
- Rollback failure returns `ROLLBACK_FAILED` or `SCENE_STATE_INDETERMINATE`, marks fake scene state indeterminate, and blocks later writes without an automatic retry.
- Mandatory internal postcondition verification runs before commit. Tampering with independent observed state makes apply fail with `POSTCONDITION_FAILED` or makes external verify fail with `VERIFY_FAILED` or an equivalent frozen structured result.
- External verify tests independently tamper parameters, connections, flags, ownership, transaction anchors, mapping keys, object identities, and cook state. Missing or schema-invalid cook markers return a schema-valid `VERIFY_FAILED`; all ten checks and overall validity remain derived from observed state.
- A successful apply advances revision exactly once and produces exactly one simulated Undo record. One test-only Undo restores the full pre-transaction observed state and leaves the sentinel unchanged.
- Both fixtures traverse the identical implementation path. No branch may use table/stairs labels, fixed node counts, asset roles, or name-derived semantics.

### Gate B4A dormant fake-HOM adapter

- Source-contract tests prove the adapter imports neither `hou` nor Qt, has no `main()` or enable switch, starts no service or background task, and is absent from every production import, constructor, registration, dispatch, and `tools/list` path.
- Wrong-thread calls; frozen/indeterminate state; stale HIP session, revision, or `/obj` fingerprint; expired/cancelled/shutting-down work; missing or altered approval binding; target collision; live type absence; and live parameter conflict all fail before any fake-HOM mutation, Undo group, or revision change. Claim tests require the exact `SceneQueue` request and executor claim objects to be consumed once by the injected authority; copied, reconstructed, absent, and reused claims are rejected. Clock tests inject exceptions, booleans, non-numbers, NaN, infinities, and negatives at adapter entry and locked preflight and require zero mutation, zero Undo entry, and zero revision change.
- Table and stairs traverse one catalog-driven translator. The B4A certified catalog sample contains `Object/geo`, `Sop/box`, `Sop/transform`, `Sop/merge`, and `Sop/null`; `Sop/transform` resolves exactly to `xform`. Tests prove these names occur only in injected catalog data, not a permanent writer allowlist or per-node branch. Future admission is represented by a separately reviewed catalog/protocol version without changing the transaction engine.
- One success creates exactly one Object/geo root and creates every child through the same catalog-resolved `createNode(..., run_init_scripts=False)` path. Before the returned object receives any later mutation, tests prove it is a new identity with the approved name, exact path, resolved type, session, parent identity/membership, and exact registry lookup. The retained children must be the exact complete declared set by identity/session/path/name/type; extra, replacement, missing, and partially created but unreturned children are rejected. Success and rollback independently test equal-path identity substitution rather than trusting path text alone.
- The existing read adapter's explicitly armed owned-write token coalesces an actually delivered fake event only when its callback source is the exact identity expected for the active mutator. Delivered missing-source, mismatched, external, late, and off-main-thread events invalidate rather than coalesce. A zero-event expectation is accepted and is explicitly not evidence that no mutation occurred or that real HOM will emit an event. Unarmed B2 read behavior remains unchanged.
- Parameters, connections, flags, cook/error observation, or ownership tampering makes the internal postcondition fail. Expected request data cannot substitute for observed fake-HOM state. Postcondition tests prove it reads only the retained root and direct declared children, never recursively scans `/obj`, and neither echoes the request's validation-only layout as observed nor invokes a layout mutation. Success additionally proves zero root input/output connections and exact child input/output cardinality with both endpoints resolved by identity inside the transaction; duplicate, missing, external-to-owned, and owned-to-external edges fail.
- Deterministic injection covers all seven stages: `create_root`, `create_nodes`, `set_parameters`, `connect_nodes`, `set_flags_layout`, `postcondition`, and `commit`. Every provable failure destroys only the exact retained root and leaves the sentinel and all pre-existing content unchanged.
- The atomic control guard wraps every fake-HOM mutator plus the final commit callable; injected cancellation, deadline, or shutdown cannot be followed by another mutation. Adversarial guards that invoke `mutate`, `contain`, or `finalize` callbacks from a non-UI thread are rejected by a fresh identity check at each callback entry before adapter, scene, Undo, observer, or revision access. Undo-group factory, enter, and exit failures are covered separately. Factory failure before enter performs zero mutation; partial-enter/exit uncertainty and root/child partial creation without a proven returned identity produce `SCENE_STATE_INDETERMINATE` and latch later writes without blind cleanup. A missing candidate output exercises the same rollback, single Undo-close, outcome-publication, and proof path rather than an early return.
- Root or retained-child identity replacement prevents destruction. Before rollback, tests require the live `/obj` identity and pre-existing child fingerprints to match the baseline, the complete retained-child identity/session/path/name/type set to match, and both input/output directions to be transaction-confined; any extra/replacement/unreturned child or external-to-owned/owned-to-external endpoint forbids blind `destroy()`. Completed rollback proves every root/child path absent and restores the exact parent registry/fingerprint, then repeats the proof after Undo exit. Success also repeats full observed verification after Undo exit and before publication. Rollback exceptions produce `SCENE_STATE_INDETERMINATE`; `KeyboardInterrupt` and `SystemExit`, including injection inside rollback, finish best-effort containment or freeze and then re-raise. Two writers cannot enter mutation simultaneously.
- `committed`, `rolled_back`, and `indeterminate` publication tests each require an exactly-once guarded callback, the exact returned report object, and agreement with an independently sampled capability report on availability, session, catalog, and outcome-specific revision/fingerprint. An indeterminate publication that retains owned nodes performs one bounded best-effort refresh even if finish failed, installs observers on that retained scope, and only then matches the independent report. A refresh already attempted by commit is not retried, and reported indeterminate revision is at least base plus one. Skipped, repeated, replaced, stale, unrefreshed-retained, or contradictory reports cannot claim success or proven rollback.
- Replay and changed-key conflict remain compatible with the existing B3/`SceneQueue` rules. B4A adds no second queue, approval authority, idempotency store, transaction framework, catalog registry, or per-node handler.

## Layer 3: fake MCP, Bridge, and Panel integration

This layer records the completed Gate B1 five-tool offline harness and the Gate B3 fake-only extension. It does not describe the production registration, which remains independently restricted to the two read tools in Layer 4.

- Initialize a standard stdio MCP session and verify `tools/list` exposes exactly five schemas. JSON-RPC alone is written to stdout; diagnostics use stderr.
- Admit only `initialize`, `notifications/initialized`, `ping`, `tools/list`, `tools/call`, and `notifications/cancelled` for the frozen offline MCP `2024-11-05` baseline. Unknown requests receive method-not-found, unknown notifications are safely ignored, and malformed, duplicate-key, non-finite, over-262,144-byte, or over-depth-32 lines terminate the fake protocol session without replay.
- Reject unlisted MCP methods and tools before an authenticated Bridge request is emitted.
- Confirm the adapter connects only to the inherited `127.0.0.1:<random>` Bridge URL and sends the runtime Bearer token without logging it.
- A missing, wrong, expired, or prior-launch authorization token receives a structured rejection. The current session token may authenticate independent requests but never substitutes for request correlation, approval, or idempotency.
- Verify the six frozen scene routes only. Submit returns 202 pending (or 200 for an exact terminal replay); result polls remain 202 until one terminal 200 and each wait is at most 1,000 ms without resetting the absolute deadline.
- Verify the typed `POST /v1/scene/requests/{request_id}/approval` route cannot answer or reuse P1 `/v1/approval`, accepts only `allow|deny` plus the exact digest/launch/generation, and creates no reusable or caller-visible capability.
- A graph apply becomes a bounded Bridge request, a single fake-Panel event, one approval, one fake-executor queue item, and one correlated result; no Houdini main-thread behavior is claimed.
- Unknown, duplicate, late, mismatched, and malformed Panel results cannot complete another MCP call.
- Panel or Bridge shutdown completes pending calls with a safe terminal error and leaves no adapter child process.
- No component rewrites the user prompt, plans a graph, stores chat, or calls another model. Codex remains the only reasoning component.
- Control-plane cancellation, queue overflow, and shutdown are returned as bounded `CANCELLED`, `QUEUE_FULL`, and `SHUTTING_DOWN` JSON-RPC/MCP adapter errors rather than unreviewed tool-result codes.
- A Bridge launch/generation change, Panel disconnect, HIP replacement, or restart invalidates the trusted session snapshot. Requests never accept a `"current"` session wildcard and cannot revive a stale snapshot by changing arguments.

## Layer 4: finite read-only Houdini probe

Gate B2A implemented this layer offline and Gate B2B completed a separate user-performed GUI acceptance. Gate B2C extends it only through the real pinned Codex/stdio-MCP/Bridge/Panel read path. Before dispatch, a trusted attestation must match the current process nonce, build, HIP session/fingerprint, revision, catalog digest, and reviewed Schema digest. Missing attestation returns `HOUDINI_UNAVAILABLE`; mismatch returns `CAPABILITY_MISMATCH`.

- Discover the active Houdini build and executable at runtime; do not trust a compiled path or documentation value.
- Confirm the Python and PySide versions, current UI thread identity, HIP session creation, and main-thread scheduling primitive.
- Query the live OBJ/SOP catalogs for `Object/geo`, `Sop/box`, `Sop/transform`, `Sop/merge`, and `Sop/null` and record exact type names.
- Query the live parameter templates required by the approved fixture, record tuple lengths, value types, defaults, bounds, expression/callback capability, and intersect them with the static safety allowlist.
- If the live schema conflicts with the static tool contract, stop with `CAPABILITY_MISMATCH`; do not guess or auto-expand the allowlist.
- Confirm no Python SOP, HDA, file, render, save, delete, expression, callback, or shell surface is discoverable through the five P2 tool schemas.
- Enable exactly `houdini_scene_info` and `houdini_node_type_info`. Keep `houdini_graph_validate`, `houdini_graph_verify`, and `houdini_graph_apply` disabled throughout B2C.

### Gate B2C offline registration and lifecycle tests

- Parse the fixed `.codex/config.toml` source with a Python 3.10 standard-library closed-grammar test and require one server named `houdini_intelligence`, the exact reviewed field set, an explicit project `cwd`, finite startup/tool timeouts, and no unknown table or key. Independently verify those fields with the pinned Codex 0.144.3 finite read-only `mcp get --json` command; do not start the MCP server.
- Require the tracked project default to remain `enabled = true` and `required = false`, so an ordinary Codex Desktop project task can recover while Houdini is offline. Separately require the Bridge-owned app-server command to pass the per-process override `mcp_servers.houdini_intelligence.required=true`, preserving fail-closed startup for the controlled Houdini lifecycle without rewriting the tracked file.
- Require `enabled_tools` to equal the two read tools and `disabled_tools` to equal the three graph tools. Reject a URL transport, literal environment map, token value, executor credential, extra tool, or open-world server option.
- Run fake-process integration for initialize, initialized notification, `tools/list`, both read calls, malformed input, unknown method/tool, timeout, cancellation, disconnect, and child shutdown. The advertised list must contain exactly two tools.
- Verify the Bridge obtains current correlation data from authenticated `GET /v1/scene/status` and overwrites technical session/revision parameters from that trusted snapshot. Forged caller technical values must not become authority or renew the capability lease.
- Verify the launcher selects one OS-assigned IPv4 loopback port, passes the resulting URL only through owned child environments, and requires the Bridge to bind that exact origin without fallback. Verify that neither URL nor token appears in bootstrap and that both remain absent from printed or persisted diagnostics. Errors must redact both. The Panel executor credential must not be present in the Codex or MCP child environment.
- Verify Panel unavailability and Panel closure return a finite structured failure. No test may start Houdini automatically, use QtNetwork, mutate the frozen `0.1.0` graph schemas, or invoke a graph tool.

### Gate B2C manual real-GUI acceptance

The user performs this finite test after the complete offline suite passes:

1. Start Houdini only through the project launcher and open an empty, unsaved HIP. Do not use Python Shell.
2. Record the baseline scene revision, dirty state, selection, node count, parameter values, and connections.
3. Open Houdini Intelligence, create a new Codex Thread, and ask Codex to call `houdini_scene_info`.
4. Verify the response reports the live Houdini build, HIP session, and revision rather than fixture data or a caller-supplied value.
5. Ask Codex to call `houdini_node_type_info` for `Sop/transform`. Verify the live type/parameter result includes the reviewed transform-to-`xform` mapping from the active build.
6. Confirm the revision, dirty state, selection, nodes, parameters, connections, and on-disk HIP state are unchanged after both calls.
7. Close the Panel during a read, or issue one read after the Panel capability is gone, and verify a bounded explicit failure rather than a hang or fabricated result.
8. Confirm `tools/list` and all visible tool activity expose no graph, Python, shell, save, render, cache, HDA, or other write surface.

The screenshots and observations from this manual run are user evidence. They must not be described as an automated Houdini test.

## Future Gate B4B: single live Houdini write acceptance (not authorized)

Completion of B4A does not authorize B4B. Before B4B, show one selected fixture, the exact blank unsaved disposable HIP state, graph request, node names, parameters, connections, and owned-root rollback scope, then obtain a separate approval.

### Baseline evidence

1. Use one blank unsaved disposable scene and record `hip_session_id`, scene revision, `/obj` structural fingerprint, selection, current node, and all pre-existing node paths.
2. Verify the requested container name does not exist.
3. Before any production write path can be enabled, the separately approved B4B acceptance must capture live capability-probe evidence for observer installation on newly created nodes and actual event coverage of the exact HOM mutations exercised by its one selected apply and manual Undo, including `createNode`, `setUserData`, typed parameter, connection, flag, and destruction calls. Record which events and callback-source identities the active build really delivers. If reliable coverage cannot be proven from that bounded acceptance, stop and request a separate scope; do not infer reliability from B4A fake events or add extra mutations under the existing B4B approval.
4. Display the exact approval payload and obtain one decision.

### One selected fixture

1. Select exactly one of the reviewed table or stairs fixtures. Record that Codex native reasoning, not a hard-coded prompt mapper or object-specific tool, produced the general graph request.
2. Run `houdini_graph_validate` and record its normalized graph, bounded summary, and canonical digest. Assert the read-only operation changes neither the scene fingerprint nor revision.
3. Display and approve the complete normalized graph. Submit the unchanged graph and digest to `houdini_graph_apply`.
4. Assert one new `/obj/HIA_Graph_<id>` exists and its live nodes, names, typed parameters, connections, display/render flags, cook status, and graph digest exactly match the normalized declaration. No pre-existing node may appear in `changed_nodes`.
5. Run `houdini_graph_verify` and require it to agree with the validate/apply digest and report no undeclared node, parameter, connection, or flag.
6. Require no node error from the selected fixture's ordinary result; do not add a caller-selected force-cook step.
7. Assert the scene revision advances exactly once for the apply and remains unchanged for validate/verify reads.

### Undo and isolation

- Invoke one Houdini Undo manually and verify the entire new container disappears.
- Verify all pre-existing node paths, types, parameters, connections, flags, selection, and current-node state match the baseline.
- Redo is not required for P2-V and must not be invoked automatically.
- Verify the HIP was not saved, its on-disk timestamp was not changed, and no HDA, render, cache, or external file was created.

Do not replay the apply, run a second fixture, or deliberately inject the full B4A conflict, cancellation, timeout, rollback, or destructive failure matrix in real Houdini during B4B.

## B4A offline structured error matrix

Schema-valid tool-result tests cover the closed union admitted by every output schema: `INVALID_ARGUMENT`, `SCHEMA_INVALID`, `NODE_TYPE_NOT_ALLOWED`, `NODE_TYPE_UNAVAILABLE`, `PARAMETER_NOT_ALLOWED`, `PARAMETER_TYPE_MISMATCH`, `PATH_SCOPE_VIOLATION`, `GRAPH_INVALID`, `TOPOLOGY_NOT_ALLOWED`, `DIGEST_MISMATCH`, `APPROVAL_REQUIRED`, `APPROVAL_DENIED`, `APPROVAL_MISMATCH`, `APPROVAL_EXPIRED`, `DEADLINE_EXCEEDED`, `HIP_SESSION_MISMATCH`, `SCENE_CONFLICT`, `IDEMPOTENCY_CONFLICT`, `NAME_CONFLICT`, `MAIN_THREAD_REQUIRED`, `CAPABILITY_MISMATCH`, `HOUDINI_UNAVAILABLE`, `WRITE_IN_PROGRESS`, `GRAPH_NOT_FOUND`, `OWNERSHIP_MISMATCH`, `COOK_FAILED`, `VERIFY_FAILED`, `POSTCONDITION_FAILED`, `ROLLBACK_FAILED`, `SCENE_STATE_INDETERMINATE`, `BRIDGE_DISCONNECTED`, and `INTERNAL_ERROR`. Separately, adapter/transport tests exercise `AUTH_REQUIRED`, `TOOL_NOT_ALLOWED`, `MALFORMED_REQUEST`, `REQUEST_TOO_LARGE`, `CANCELLED`, `QUEUE_FULL`, and `SHUTTING_DOWN` without inserting those values into tool outputs.

Every error test also asserts `ok=false`, no false created/changed paths, no secret fields, and a safe retryability value.

## Evidence package

The B4A review package must contain:

- Base commit and exact frozen schema versions.
- Sanitized request, approval digest, graph digest, and response.
- Fake-HOM certified catalog and exact calls exercised.
- Table/stairs observed-node, parameter, connection, flag, Undo-group, and revision evidence.
- Seven-stage injection, exact creation-return/registry and complete retained-child-set proof, unchanged live `/obj`/pre-existing fingerprints, success root-zero/bidirectional-child connection proof, rollback bidirectional connection confinement, post-destroy and post-Undo path-absence proof, rollback interruption re-raise, unified missing-candidate cleanup, and indeterminate-latch evidence.
- Exact `committed`/`rolled_back`/`indeterminate` publication report and independent capability-report agreement, including finish-failure best-effort retained-scope refresh, no repeat after a commit refresh attempt, and the indeterminate revision floor.
- A clear statement that fake callback identity checks do not prove real event delivery, new-node observer coverage, or `setUserData` event reliability; those remain B4B capability-probe blockers.
- Full offline and fake-integration test output.
- Frozen-file diffs, production two-tool `tools/list`, zero graph registration, Git status, and empty staging evidence.
- For B2C, the exact two-tool `tools/list`, sanitized real read results, bounded Panel-loss result, and before/after no-change evidence.
- Explicitly unverified real Houdini Undo, cook, callback, rollback, and version-compatibility claims.

A later separately approved B4B report must add the exact Houdini build/catalog plus before/apply/Undo scene fingerprints, revision sequence, selected created-node list, and actual manual outcomes.

Screenshots are optional supporting evidence and never substitute for `hou`-derived verification.

## Gate B4A exit criteria

- The frozen `0.1.0` graph schemas, frozen `0.2.0` read schemas, table/stairs fixtures, contracts, configuration, launchers, services, architecture, threat model, and this plan remain unchanged except for the authorized documentation updates; frozen-file diffs are empty.
- Table and stairs complete the same dormant-adapter fake-HOM path through the five-type first certified catalog sample; there is no permanent five-type writer boundary, asset-specific branch, per-node handler, or general DCC framework. Future types remain gated by a new reviewed catalog/protocol version.
- Every preflight failure has zero fake-HOM mutation, zero Undo group, and zero revision change. Exact non-cancelled `SceneQueue` claim sealing plus one-shot binding redemption, fresh UI-thread checks at every guard callback entry, invalid/raising clock values at entry and locked preflight, every exactly-once atomic guarded mutator/finalizer/containment action, all seven mutation stages, Undo factory/partial-enter/exit failure, root/child exact new-identity and registry proof before later mutation, complete retained-child-set proof, root/child partial-unreturned failure, unchanged live `/obj` and pre-existing fingerprints, strict observed parameter types, root-zero and child bidirectional connection endpoint/cardinality proof, rollback rejection of external-to-owned and owned-to-external edges, root/child observed errors, equal-path wrapper identity substitution, parent-sourced modeled `ChildDeleted` rollback, exact root/child path absence after destroy and again after Undo exit, success re-verification after Undo exit, unified missing-candidate cleanup, rollback uncertainty, cancellation, deadline, shutdown, interruption including rollback interruption, and single-writer cases are deterministic and tested.
- Every `committed`, `rolled_back`, and `indeterminate` fake publication returns its exact guarded report and agrees with an independent capability report; retained indeterminate scope is best-effort bounded-refreshed even after finish failure, an already attempted commit refresh is not retried, and indeterminate revision is at least base plus one. Fake callback tests prove exact source identity only for events actually delivered by the fake; they do not claim mandatory live event delivery, zero-event detection, new-node observer coverage, or `setUserData` event reliability. Those remain B4B blockers.
- A successful fake apply creates one owned root and catalog-resolved children with `run_init_scripts=False`, enters exactly one fake Undo group, advances the existing scene revision exactly once, and passes mandatory bounded observed-state postcondition verification without touching the sentinel, recursively scanning `/obj`, or fabricating layout state.
- Every error is JSON-serializable, schema-valid, secret-free, and contains no fabricated created/changed path. Observed-state tampering cannot be hidden by a saved request graph.
- Existing tests and the complete offline suite remain green; `git diff --check` passes.
- Production `.codex/config.toml`, launcher, app-server lifecycle, `stdio.main()`, Bridge, and Panel dispatch remain unchanged. Production `tools/list` contains exactly the two read tools, registers zero graph tools, and no production file imports or constructs the adapter.
- B4A source imports neither real `hou` nor Qt and exposes no save/cook/render/cache/HDA/arbitrary-code or external-call product capability. No real service or Houdini process is started and no real HIP is modified; fake thread/clock tests are not evidence about live HOM scheduling or timing.
- The staging area remains empty and all B4A changes remain uncommitted until the user explicitly approves a commit. Passing B4A does not authorize B4B or any live write.
