# P2-V test plan

## Status and authorization boundary

This approved Gate B0 verification plan is frozen pre-release for the first Houdini scene-operation vertical slice. Its approval authorizes Gate B1 offline adaptation only, not a real MCP process, Houdini connection, `hou` call, or scene write.

Gate B1 may now preserve and adapt the existing uncommitted drafts against the exact frozen contract. B1 is fake-only and live-disabled. Automated verification must not start Houdini GUI. B2 and every later live-Houdini test require separate approval; a later live write test additionally requires exact approval for the proposed graph and active HIP session.

## Acceptance claim

P2-V succeeds only when Codex can use the same five-tool general graph contract to validate and create multiple editable OBJ/SOP networks under new `/obj/HIA_Graph_<id>` containers. A table is the first acceptance fixture, while a structurally different stairs fixture proves that the protocol contains no object-specific roles, fixed primitive count, dimensions, or topology. Each approved graph must be removable by one Houdini Undo, exact retries must not duplicate work, changed retries must fail, and no pre-existing node may change.

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

These tests are written before any `hou` implementation and use fakes.

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

## Layer 3: fake MCP, Bridge, and Panel integration

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

This layer requires separate approval to start Houdini, but it performs no scene write. Before dispatch, a trusted attestation must match the current process nonce, build, HIP session/fingerprint, revision, catalog digest, and all reviewed schema hashes. Missing attestation returns `HOUDINI_UNAVAILABLE`; mismatch returns `CAPABILITY_MISMATCH`.

- Discover the active Houdini build and executable at runtime; do not trust a compiled path or documentation value.
- Confirm the Python and PySide versions, current UI thread identity, HIP session creation, and main-thread scheduling primitive.
- Query the live OBJ/SOP catalogs for `Object/geo`, `Sop/box`, `Sop/transform`, `Sop/merge`, and `Sop/null` and record exact type names.
- Query the live parameter templates required by the approved fixture, record tuple lengths, value types, defaults, bounds, expression/callback capability, and intersect them with the static safety allowlist.
- If the live schema conflicts with the static tool contract, stop with `CAPABILITY_MISMATCH`; do not guess or auto-expand the allowlist.
- Confirm no Python SOP, HDA, file, render, save, delete, expression, callback, or shell surface is discoverable through the five P2 tool schemas.
- Enable at most `houdini_scene_info` and `houdini_node_type_info`. Keep `houdini_graph_verify` and `houdini_graph_apply` disabled throughout B2.

## Layer 5: live Houdini write acceptance

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
- Unverified or skipped cases with reasons.

Screenshots are optional supporting evidence and never substitute for `hou`-derived verification.

## Design-review exit criteria

Frozen B0 evidence:

- The five-tool inventory, ten schemas, fixtures, architecture, threat model, and this plan agree exactly.
- All JSON files parse, references resolve, forbidden capabilities are absent, and offline contract tests pass.
- Existing P0/P1 tests remain green.
- The pre-existing B1 adapter, Bridge, contract, and fake-executor drafts were excluded from B0 evidence. B1 may now adapt them offline, but they remain unregistered and unexecuted as product capability. No `.codex/config.toml`, live Panel executor, Houdini process, or `hou` implementation is enabled.
- The working tree remains uncommitted until the user reviews this design package and explicitly approves the next sub-gate.
