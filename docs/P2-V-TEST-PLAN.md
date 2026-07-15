# P2-V test plan

## Status and authorization boundary

This document is the verification plan for the first Houdini scene-operation vertical slice. It is a design-review artifact only. Creating this plan does not authorize an MCP process, a Houdini connection, a `hou` call, or a scene write.

Offline B1 implementation begins only after the architecture, tool schemas, threat model, and this plan pass B0 review. B1 is fake-only and live-disabled. Automated verification must not start Houdini GUI. B2 and every later live-Houdini test require separate approval; a later live write test additionally requires exact approval for the proposed graph and active HIP session.

## Acceptance claim

The P2-V claim is satisfied only when the exact natural-language request “生成一张简单、可编辑的四条腿桌子。” causes Codex to choose the reviewed `houdini_graph_apply` tool and an approved call creates one new, editable SOP table under a new `/obj/HIA_Table_<id>` container. One Houdini Undo must remove that whole container, a retry must not create a duplicate, and no pre-existing node may change.

Passing schema tests alone does not prove this claim. Evidence is accumulated in five layers and the live acceptance layer is mandatory.

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

- `P2-C001`: `manifestVersion` is `1.0`, `schemaVersion` is `0.1.0`, and the manifest exposes exactly `houdini_scene_info`, `houdini_node_type_info`, `houdini_graph_apply`, and `houdini_graph_verify`.
- `P2-C002`: every referenced input and output schema exists, parses as UTF-8 JSON, uses JSON Schema draft 2020-12, and has an object root with `additionalProperties: false`.
- `P2-C003`: annotations mark only `houdini_graph_apply` as write-capable; it is idempotent, not open-world, and not destructive to existing data. The other three tools are read-only.
- `P2-C004`: tool and schema names contain no aliases that could expose a generic node, parameter, Python, shell, HScript, expression, file, render, HDA, delete, or save tool.
- `P2-C005`: the manifest records the SHA-256 of each of the eight reviewed schemas and every hash matches the exact UTF-8 file bytes.
- `P2-C006`: JSON loading rejects duplicate object keys and non-finite constants; every local `$ref` resolves inside its own schema. Tests distinguish accepted object boundaries from constraint fragments such as `if`, `then`, and `contains`.

### Request envelope

- `P2-C010`: write input requires `request_id`, `thread_id`, `turn_id`, `hip_session_id`, `base_scene_revision`, `idempotency_key`, `deadline_ms`, and `permission_level=scene_write`.
- `P2-C011`: read inputs require correlation identifiers and `permission_level=scene_read`; missing, empty, overlong, control-character, and unknown fields fail validation.
- `P2-C012`: revisions are non-negative integers, deadlines are finite and bounded, and identifiers have explicit length and character limits.
- `P2-C013`: NaN, infinity, numeric strings, boolean-as-number values, and oversized arrays are rejected before dispatch.

### Declarative graph

- `P2-C020`: the valid table fixture has parent `/obj`, one `geo` container matching `HIA_Table_<id>`, five `box` nodes, one `merge`, one `null`, six connections, and output `OUT_TABLE`.
- `P2-C021`: the seven required child roles occur exactly once. Missing, duplicate, renamed, or additional roles fail.
- `P2-C022`: only OBJ `geo` and SOP `box`, `merge`, and `null` types are accepted. Namespaces, version suffixes, HDAs, Python nodes, subnet injection, and category changes fail.
- `P2-C023`: box nodes accept only three-number `size` and `translate` tuples within the reviewed bounds. Parameter names, expressions, strings, ramps, buttons, and spare parameters fail.
- `P2-C024`: connections are acyclic and exactly match five boxes into distinct merge inputs followed by merge into `OUT_TABLE`. Self-links, cross-container paths, unknown endpoints, duplicate input slots, and extra wiring fail.
- `P2-C025`: parent path, container type, output role, unit, rollback policy, and Undo label are constants. Absolute filesystem paths, `..`, UNC, device, ADS, and URI values have no accepted field.

### Responses and errors

- `P2-C030`: every output contains `ok`, HIP session, scene revision, node-change arrays, warnings, and a nullable structured error.
- `P2-C031`: successful apply output reports only nodes created inside its new container, a stable graph digest, idempotency replay state, and the fixed Undo label.
- `P2-C032`: error objects are JSON serializable and contain a stable code, safe message, retryability, and non-secret details. Tracebacks and Bearer tokens are never the sole or exposed response.
- `P2-C033`: deterministic validation requires output correlation to echo request/session/revision/idempotency fields, prevents revision regression, and rejects a container path or replay record that does not match the submitted request.
- `P2-C034`: node-type results correspond uniquely and exactly to the query set; verification checks, issues, validity, and graph digest cannot contradict one another.
- `P2-C035`: without a current fake attestation matching the reviewed contract hashes, B1 fails all four tools with `HOUDINI_UNAVAILABLE` or `CAPABILITY_MISMATCH`; no live fallback exists.

## Layer 2: pure deterministic components

These tests are written before any `hou` implementation and use fakes.

### Validation and canonicalization

- Canonical JSON hashing is order-stable and includes the HIP session, base revision, permission level, and complete graph.
- The approval digest and graph digest change when any node, parameter, connection, flag, session, revision, or side effect changes.
- Validation is deny-by-default and completes before queue insertion.
- Unknown tool names, protocol methods, fields, node types, parameters, and error codes fail closed.

### Approval

- A read-only call cannot be relabeled as a write call after validation.
- `houdini_graph_apply` creates a Panel approval containing the exact container, seven children, parameters, six connections, HIP session, base revision, Undo label, and argument digest.
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

- Initialize a standard stdio MCP session and verify `tools/list` exposes exactly four schemas. JSON-RPC alone is written to stdout; diagnostics use stderr.
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
- Query the live OBJ/SOP catalogs for `geo`, `box`, `merge`, and `null` and record exact type names.
- Query the live parameter templates required for box size and translation and record tuple lengths, numeric types, defaults, and bounds.
- If the live schema conflicts with the static tool contract, stop with `CAPABILITY_MISMATCH`; do not guess or auto-expand the allowlist.
- Confirm no Python SOP, HDA, file, render, save, delete, expression, or shell surface is discoverable through the four P2 tool schemas.
- Enable at most `houdini_scene_info` and `houdini_node_type_info`. Keep `houdini_graph_verify` and `houdini_graph_apply` disabled throughout B2.

## Layer 5: live Houdini write acceptance

This is not authorized by approval of this design document. Before running it, show the exact disposable HIP state, graph request, node names, parameters, connections, and rollback scope, then obtain a separate approval.

### Baseline evidence

1. Use an unsaved disposable scene and record `hip_session_id`, scene revision, `/obj` structural fingerprint, selection, current node, and all pre-existing node paths.
2. Verify the requested container name does not exist.
3. Display the exact approval payload and obtain one decision.

### Successful table

1. Send “生成一张简单、可编辑的四条腿桌子。” through the Panel.
2. Record that Codex, not a hard-coded prompt mapper, chose `houdini_graph_apply` and supplied a schema-valid declarative graph.
3. Assert one new `/obj/HIA_Table_<id>` exists with exactly:

   ```text
   tabletop_box
   leg_front_left
   leg_front_right
   leg_back_left
   leg_back_right
   merge_table
   OUT_TABLE
   ```

4. Assert the five boxes expose editable numeric size and translation tuples, all five feed unique inputs of `merge_table`, `merge_table` feeds `OUT_TABLE`, and only `OUT_TABLE` has the final display/render flags.
5. Cook the output and require no node error. Compare bounds to the declared graph within a documented numeric tolerance.
6. Assert the scene revision advanced exactly as defined and the response lists no changed pre-existing node.

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

Inject deterministic failures after container creation, after a box, after connection creation, and before final flag assignment. Each failure must remove only the container created by that request within the same bounded operation. Existing nodes must remain byte-for-byte equivalent under the structural fixture. A rollback failure is a critical `ROLLBACK_FAILED` result and must never be reported as success.

## Required structured error matrix

At minimum, schema-valid tool-result tests exercise the codes admitted by the relevant output schema, including `INVALID_ARGUMENT`, `TOOL_NOT_ALLOWED`, `NODE_TYPE_NOT_ALLOWED`, `PARAMETER_NOT_ALLOWED`, `TOPOLOGY_NOT_ALLOWED`, `APPROVAL_REQUIRED`, `APPROVAL_DENIED`, `APPROVAL_EXPIRED`, `DEADLINE_EXCEEDED`, `HIP_SESSION_MISMATCH`, `SCENE_CONFLICT`, `IDEMPOTENCY_CONFLICT`, `NAME_CONFLICT`, `MAIN_THREAD_REQUIRED`, `CAPABILITY_MISMATCH`, `HOUDINI_UNAVAILABLE`, `COOK_FAILED`, `ROLLBACK_FAILED`, `BRIDGE_DISCONNECTED`, and `INTERNAL_ERROR`. Separately, adapter/transport tests exercise `CANCELLED`, `QUEUE_FULL`, and `SHUTTING_DOWN` without inserting those values into tool outputs.

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

Before implementation approval:

- The four-tool inventory, schemas, architecture, threat model, and this plan agree exactly.
- All JSON files parse, references resolve, forbidden capabilities are absent, and offline contract tests pass.
- Existing P0/P1 tests remain green.
- No MCP adapter, Bridge scene endpoint, Panel executor, `.codex/config.toml` entry, or `hou` implementation exists yet.
- The working tree remains uncommitted until the user reviews this design package and explicitly approves the next sub-gate.
