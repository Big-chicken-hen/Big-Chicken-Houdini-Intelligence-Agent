# P2-V test plan

## Status and authorization boundary

Gate B0, Gate B1, and Gate B2 are complete. Gate B2C closed the real pinned-Codex-to-stdio-MCP-to-authenticated-Bridge-to-Panel read chain at commit `edf7f3a`; the direct HTTPS transport hotfix closed at commit `3213625`. Gate B3 is authorized only for pure-Python fake scene transaction and approval testing.

Gate B3 may invoke the three graph tools only through an explicitly constructed offline fake profile. Production registration remains limited to `houdini_scene_info` and `houdini_node_type_info`. B4, every real scene write, `hou`, Houdini GUI, hython, and the live MCP/Bridge/app-server chain remain unauthorized.

## Acceptance claim

Full P2-V eventually succeeds only when Codex can use the same five-tool general graph contract to validate and create multiple editable OBJ/SOP networks under new `/obj/HIA_Graph_<id>` containers. B3 proves only the deterministic fake transaction, approval, rollback, verification, idempotency, conflict, and simulated Undo model using table and structurally different stairs fixtures. It does not prove live node creation, cook, rollback, main-thread scheduling, or `hou.undos.group`.

Passing Schema tests alone does not prove live Houdini behavior. Evidence is accumulated in five layers and the separately approved live acceptance layer is mandatory before any live claim.

## Test environments

| Environment | Purpose | Scene writes |
|---|---|---|
| Standard-library CPython | JSON parsing, schema-contract checks, validators, queues, idempotency, and transport fakes | None |
| Fake MCP client and fake Panel gateway | End-to-end correlation, approval, timeout, and error mapping | None |
| Houdini read-only probe | Runtime version, node-type, parameter, and main-thread capability discovery | None |
| Disposable unsaved HIP acceptance scene | Exact graph creation, cook, verification, and Undo | One separately approved container |
| Conflict/failure disposable HIP scenes | Revision, idempotency, rollback, and failure injection | Separately approved test containers only |

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

These tests are written before any `hou` implementation and use fakes. Gate B3 extends this layer into a complete validate -> scene approval -> transactional apply -> mandatory internal postcondition -> verify -> optional test-only simulated Undo chain.

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

## Future Gate B4: live Houdini write acceptance (not authorized)

This is not authorized by approval of this design document. Before running it, show the exact disposable HIP state, graph request, node names, parameters, connections, and rollback scope, then obtain a separate approval.

### Baseline evidence

1. Use an unsaved disposable scene and record `hip_session_id`, scene revision, `/obj` structural fingerprint, selection, current node, and all pre-existing node paths.
2. Verify the requested container name does not exist.
3. Display the exact approval payload and obtain one decision.

### Successful general graph fixtures

1. Ask Codex to create the asset represented by `tests/fixtures/p2_v/table_graph.json`. Record that Codex native reasoning, not a hard-coded prompt mapper or object-specific tool, produced a general graph request.
2. Run `houdini_graph_validate` and record its normalized graph, bounded summary, and canonical digest. Assert the read-only operation changes neither the scene fingerprint nor revision.
3. Display and approve the complete normalized graph. Submit the unchanged graph and digest to `houdini_graph_apply`.
4. Assert one new `/obj/HIA_Graph_<id>` exists and its live nodes, names, typed parameters, connections, display/render flags, cook status, and graph digest exactly match the normalized declaration. No pre-existing node may appear in `changed_nodes`.
5. Run `houdini_graph_verify` and require it to agree with the validate/apply digest and report no undeclared node, parameter, connection, or flag.
6. Repeat the validate/approve/apply/verify workflow in a fresh disposable HIP for `tests/fixtures/p2_v/stairs_graph.json`. Its node count and topology must differ from the first fixture while using the same five tools and Schema.
7. Assert neither execution path branches on an asset label, special node role, fixed primitive count, fixed dimensions, or one fixed connection pattern.
8. Cook the declared outputs and require no node error. Compare any declared geometric bounds within a documented numeric tolerance without inventing object semantics.
9. Assert the scene revision advances exactly as defined for each apply and remains unchanged for validate/verify reads.

### Undo and isolation

- Invoke one Houdini Undo manually and verify the entire new container disappears.
- Verify all pre-existing node paths, types, parameters, connections, flags, selection, and current-node state match the baseline.
- Redo is not required for P2-V and must not be invoked automatically.
- Verify the HIP was not saved, its on-disk timestamp was not changed, and no HDA, render, cache, or external file was created.

### Conflict and retry matrix

- Reapply the same approved key and graph before Undo: return the recorded result and create no duplicate.
- Reuse the key with one changed dimension: return `IDEMPOTENCY_CONFLICT` and create nothing.
- Manually create or change a node after reading scene info, then send the old base revision: return `SCENE_CONFLICT` and create nothing.
- Open a different disposable HIP before dispatch: return `HIP_SESSION_MISMATCH` and create nothing.
- Pre-create the requested HIA container name: return `NAME_CONFLICT`; never enter or modify that container.
- Start two writes concurrently: one may proceed after approval; the other must fail or wait and then revalidate against the new revision.

### Failure injection

Inject deterministic failures after container creation, after creation of a declared node, after a typed parameter assignment, after a connection, and before final flag assignment. Each failure must remove only the container created by that request within the same bounded operation. Existing nodes must remain byte-for-byte equivalent under the structural fixture. A rollback failure is a critical `ROLLBACK_FAILED` result and must never be reported as success.

## Required structured error matrix

Schema-valid tool-result tests cover the closed union admitted by every output schema: `INVALID_ARGUMENT`, `SCHEMA_INVALID`, `NODE_TYPE_NOT_ALLOWED`, `NODE_TYPE_UNAVAILABLE`, `PARAMETER_NOT_ALLOWED`, `PARAMETER_TYPE_MISMATCH`, `PATH_SCOPE_VIOLATION`, `GRAPH_INVALID`, `TOPOLOGY_NOT_ALLOWED`, `DIGEST_MISMATCH`, `APPROVAL_REQUIRED`, `APPROVAL_DENIED`, `APPROVAL_MISMATCH`, `APPROVAL_EXPIRED`, `DEADLINE_EXCEEDED`, `HIP_SESSION_MISMATCH`, `SCENE_CONFLICT`, `IDEMPOTENCY_CONFLICT`, `NAME_CONFLICT`, `MAIN_THREAD_REQUIRED`, `CAPABILITY_MISMATCH`, `HOUDINI_UNAVAILABLE`, `WRITE_IN_PROGRESS`, `GRAPH_NOT_FOUND`, `OWNERSHIP_MISMATCH`, `COOK_FAILED`, `VERIFY_FAILED`, `POSTCONDITION_FAILED`, `ROLLBACK_FAILED`, `SCENE_STATE_INDETERMINATE`, `BRIDGE_DISCONNECTED`, and `INTERNAL_ERROR`. Separately, adapter/transport tests exercise `AUTH_REQUIRED`, `TOOL_NOT_ALLOWED`, `MALFORMED_REQUEST`, `REQUEST_TOO_LARGE`, `CANCELLED`, `QUEUE_FULL`, and `SHUTTING_DOWN` without inserting those values into tool outputs.

Every error test also asserts `ok=false`, no false created/changed paths, no secret fields, and a safe retryability value.

## Evidence package

The later implementation acceptance report must contain:

- Git commit and exact schema version.
- Houdini build and discovered live node/parameter contract.
- Sanitized request, approval digest, graph digest, and response.
- Before/after/Undo scene fingerprints and revision sequence.
- Exact created-node list and connection verification.
- Full offline and fake-integration test output.
- Manual live acceptance steps and actual outcomes.
- For B2C, the exact two-tool `tools/list`, sanitized real read results, bounded Panel-loss result, and before/after no-change evidence.
- Unverified or skipped cases with reasons.

Screenshots are optional supporting evidence and never substitute for `hou`-derived verification.

## Gate B3 exit criteria

- The five-tool inventory, frozen `0.1.0` graph schemas, frozen `0.2.0` read schemas, fixtures, architecture, threat model, and this plan agree exactly; start/end SHA-256 inventories are identical.
- Table and stairs complete the same fake validate/approve/apply/internal-postcondition/verify/Undo path, and every mutation boundary, approval outcome, stale state, idempotency outcome, rollback outcome, cancellation, deadline, and shutdown case is deterministic and tested.
- Every error is JSON-serializable, schema-valid, secret-free, and contains no fabricated created/changed path. Observed-state tampering cannot be hidden by a saved request graph.
- Existing tests and the complete offline suite remain green; `git diff --check` passes.
- Production `.codex/config.toml`, launcher, app-server lifecycle, `stdio.main()`, Bridge, Panel, and live read adapter remain unchanged. Production `tools/list` contains exactly the two read tools and rejects all three graph tools before transport.
- B3 source does not import `hou` or expose create/set/connect/destroy/save/cook/render/cache/HDA/arbitrary-code product capabilities. No real service or Houdini process is started.
- The staging area remains empty and all B3 changes remain uncommitted until the user explicitly approves a commit. Passing B3 does not authorize B4 or any live write.
