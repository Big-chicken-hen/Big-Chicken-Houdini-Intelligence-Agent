# Safety policy

This policy has the highest priority. When safety cannot be proven, stop and ask; never guess.

## Filesystem rules

- The only project root is `E:\houdini-intelligence-agent`.
- Project source, environments, caches, temporary data, renders, jobs, logs, checkpoints, quarantine records, and published artifacts must not be created on `C:`.
- Never modify or clean `C:\Users`, AppData, a user home directory, `CODEX_HOME`, Houdini user configuration, a drive root, or any path outside the project root without separate authorization for one exact absolute path.
- Reject empty paths, drive roots, UNC paths, Windows device paths, alternate data streams, outside-root paths, traversal that escapes the root, and every junction, symlink, mount point, or other reparse point.
- Apply Windows case-insensitive path semantics. A path is writable only when its normalized absolute form is a strict ordinary child of the approved project root.
- Before a future delete or move: inventory read-only; canonicalize the root and every target; check junctions, symlinks, UNC/device syntax, ADS, and traversal; display exact targets and scope; then obtain separate explicit approval.
- Never recursively delete a computed, expanded, uncertain, or unverified path. Prefer moving approved cleanup candidates to a project-local quarantine with a manifest when that phase is authorized.
- Use one native PowerShell/Windows chain with literal paths and argument arrays. Never hand enumerated paths to another shell.
- Never use destructive Git commands or overwrite unrelated user work.

`src/hia_core/path_policy.py` is validation-only. It must not delete, move, quarantine, clean, create, or modify a target.

## Houdini and scene rules

- Never claim a Houdini connection, scene state, schema, node operation, cook, render, image interpretation, HIP/HDA understanding, or test result without real evidence.
- Never modify a scene without approval matching that exact operation and risk level.
- Every high-risk operation requires explicit approval and a secret-free audit record.
- Do not automatically save or overwrite HIP files, alter HDA definitions, delete nodes, disconnect existing wiring, or scan arbitrary filesystem locations.
- The current live Houdini schema overrides static documentation, examples, skills, and model knowledge; report conflicts.
- All live HOM/UI calls run on Houdini's main thread. RPC I/O only validates and queues.
- Writes use scene revision, HIP fingerprint, idempotency, bounded scope, structured errors, and one undo group. Stale writes fail with `SCENE_CONFLICT`; last-write-wins is forbidden.
- Future services bind to `127.0.0.1` by default and use authentication. Never expose the app-server or Scene Gateway directly.

## Intelligence and data rules

- Codex is the only intelligent subject. Do not create another Agent, LLM, planner, RAG system, vector database, semantic-memory service, or external vision model.
- Houdini and sidecars provide deterministic execution only. Do not operate Houdini via screen takeover or Computer Use.
- Codex-native subagents are phase-gated, proposal-first, and never independent live-HIP writers.
- Technical databases may store identifiers, revisions, jobs, artifacts, idempotency, and audit data, but not duplicate chats, embeddings, semantic summaries, or custom memory.
- Retrieval and preference storage do not train model weights and must never be described as doing so.
- User-inspectable knowledge, cases, rules, memory, and preferences must remain exportable and deletable.

## Execution and reporting rules

- Never expose unrestricted arbitrary Python, shell execution, `exec`, or `eval` as product tools.
- Any future Python capability must follow the isolated, clone, and explicitly unsafe-live tiers described in the architecture.
- Keep credentials, tokens, and secrets out of audit records and user-facing errors.
- Return structured JSON-serializable errors. Diagnostic tracebacks may be logged but are not sufficient user-facing output.
- Run all available tests and report their actual results. Mark unavailable live checks as unverified and explain why.
- Stay within the currently authorized phase and stop at its boundary.

## Current P2-V Gate B4A dormant fake-HOM boundary

Gate B2C completed at commit `edf7f3a`; its HTTPS transport hotfix completed at commit `3213625`; Gate B3 completed at commit `862defff`. The corrected five-tool `schemas/houdini-mcp/0.1.0` contract and two-tool `schemas/houdini-mcp/0.2.0` read-only contract are frozen and must not be edited in place. Gate B4A authorizes only a dormant dependency-injected HOM graph write adapter and fake-HOM tests. Gate B4B, live `hou`, and every real Houdini write remain unauthorized.

Production remains strictly read-only. `.codex/config.toml`, the launcher, production app-server command, `stdio.main()`, Bridge, Panel dispatch, and live capability path must continue to expose only `houdini_scene_info` and `houdini_node_type_info`; `houdini_graph_validate`, `houdini_graph_apply`, and `houdini_graph_verify` remain unregistered and uncallable there. No production path may import, construct, register, or call the B4A adapter, and no request, environment variable, configuration, default constructor, command-line argument, or network service may enable it.

The tracked project server remains `enabled = true` and `required = false`; only the Bridge-owned app-server child may override it to `required=true` through a per-process argument. Every live HOM read remains on Houdini's UI main thread, and the B2C attestation, safe status facade, lease, secret-redaction, and independent executor-credential controls remain mandatory and unchanged.

The adapter receives its fake `hou_module`, read adapter, UI-main-thread identity, clock, control guard, and claim authority only by explicit dependency injection. It must not import `hou`, PySide6, or Qt; start a thread, process, listener, or background task; provide `main()`; interpret natural language; or become an approval authority. It accepts only an already normalized graph paired with the exact internal `SceneQueue` request and executor claim. The claim authority seals that exact non-cancelled object pair into an opaque token and redeems the resulting binding once at execution; copied, reconstructed, cancelled, absent, reused, and replayed claims or bindings fail closed. Caller booleans such as `approved`, `allow`, and `confirmed` never grant write authority.

Every preflight check completes before the first fake-HOM mutation: main-thread identity, write latch, normalized graph and frozen Schema, graph and approval-binding digests, deadline/cancel/shutdown, HIP session, scene revision, `/obj` fingerprint, exact target absence, live type/parameter catalog, transaction identity, and the non-blocking single-writer guard. Failure means zero mutation, zero Undo group, and zero revision change.

The first certified catalog contains only `Object/geo`, `Sop/box`, `Sop/transform`, `Sop/merge`, and `Sop/null`; `Sop/transform` live-resolves to `xform`. This is the first catalog for one catalog-driven Houdini graph engine, not a permanent writer boundary or an asset recipe. Later types or contexts require separately reviewed catalog/protocol versions and risk policy; B4A adds no per-node handlers, general DCC framework, new Schema, new protocol version, or extra context.

The adapter may create only one new validated `/obj/HIA_Graph_<id>` root and declared children owned by the current transaction. Every child uses the same catalog-resolved `createNode(..., run_init_scripts=False)` path. Before any subsequent mutation, each returned root or child must be proven to be one exact new identity matching the approved name, exact path, resolved type, session identity, parent identity and membership, and exact `hou_module.node(path)` registry lookup. Failure or ambiguity is indeterminate and freezes writes. It uses typed setters for only the frozen safe parameters, connects only retained objects created by that transaction, and sets only declared flags. Existing nodes, parameters, connections, flags, selection, and current-node state are immutable to B4A. Expressions, HScript, Python SOPs, arbitrary user-data keys, Python or shell execution, save, render, cache, HDA, external files, and caller-selected cook are forbidden.

All fake-HOM mutations occur in exactly one `undos.group("HIA: Apply Graph")` and use the existing seven stages. The injected atomic control guard wraps every mutator, mandatory non-cancellable containment, and the finalizer, but it is not trusted to retain thread affinity. Every `mutate`, `contain`, and `finalize` callback rechecks the Houdini UI-main-thread identity immediately on entry before accessing adapter, scene, Undo, observer, or revision state. A factory failure before enter is recoverable only when no Undo boundary was attempted; any enter or exit exception is explicitly contained, treated as indeterminate, freezes writes, and forbids a blind follow-up mutation. A missing candidate output uses the unified confined rollback and single Undo-close path rather than returning early. If a root or child creation may have partially mutated and raises without returning its exact identity, rollback cannot be proven, so the outcome is indeterminate and later writes are latched off.

The injected clock must return a finite, non-negative numeric value both at adapter entry and in locked preflight. A raised exception, boolean, non-number, NaN, infinity, or negative value fails closed before Undo entry with zero fake-HOM mutation and zero revision change. B4A establishes only these fake guards; it does not verify real HOM scheduling, main-thread behavior, or timing.

Publication of `committed`, `rolled_back`, and `indeterminate` must each call its containment/finalization boundary exactly once and return that exact report object. After an indeterminate publication with retained owned nodes, one bounded best-effort refresh installs observers on the retained scope before the independent capability report is sampled, including when owned-write finish failed. A refresh already attempted by the commit path is never retried. The publication/refresh and independent reports must match on availability, session, outcome-specific revision and fingerprint, and catalog; an indeterminate revision may not be lower than base plus one.

Mandatory postcondition verification re-reads only the retained owned root and its direct declared children; it never substitutes request state for observed state, recursively scans `/obj`, reports request layout as observed, or fabricates layout work. Success and rollback require the live `/obj` lookup to remain the exact retained parent and every pre-existing child fingerprint to remain unchanged. The root's children must equal the complete retained set by identity, session, exact path, name, and resolved type; any extra, replacement, missing, or partially created but unreturned child forbids blind rollback.

Success requires every approved path lookup to return the exact retained identity, zero root input/output connections, and exact child input/output connection cardinality with both endpoints owned by identity by this transaction. After Undo exit, full observed verification runs again before success publication. Every post-mutation failure attempts confined rollback of only that exact root after the same path, object/session/parent/name/ownership/transaction/digest, retained-child, unchanged-pre-existing-fingerprint, and bidirectional connection-confinement proofs. An external-to-owned or owned-to-external connection forbids blind destruction. After destroy, root and retained-child paths must all be absent; the exact parent and pre-existing fingerprint must be restored, and the entire proof repeats after Undo exit. Any ambiguity or rollback exception returns the frozen indeterminate result and latches later writes. `KeyboardInterrupt` and `SystemExit`, including an interruption raised during rollback, finish best-effort containment or freeze and are then re-raised.

B4A callback evidence is intentionally narrow. If fake-HOM delivers an event while an owned expectation is armed, only the exact registered callback-source identity may coalesce; deletion models `ChildDeleted` on the retained parent. A delivered mismatch, external event, late event, or off-main-thread event invalidates. A zero-event expectation is not evidence that no mutation occurred and B4A does not establish that any real HOM mutator must emit an event. Observer installation for newly created nodes and reliable event coverage for `createNode`, `setUserData`, parameter, connection, flag, and destruction mutations are mandatory B4B capability-probe blockers. The fake post-commit refresh/report check is not live evidence and must not be used to bypass those probes.

A successful fake apply enters exactly one fake Undo group and advances the existing scene revision exactly once. Table and stairs must use the same translation path, and a pre-existing sentinel must remain unchanged across success, every injected failure, rollback, and interruption. Fake-HOM proves only call order, state-machine behavior, and safety constraints; it does not prove real Houdini Undo, cook, callback, rollback, main-thread scheduling, or version compatibility.

Gate B4A must not start Houdini, hython, MCP, Bridge, app-server, or another service; import or call real `hou`; modify a real HIP; enable graph tools; or enter B4B. Future B4B requires separate approval and is limited to one selected fixture, one apply in one blank unsaved disposable HIP, and one manual Undo. B4A must not modify frozen Schema, fixtures, production wiring, configuration, launchers, services, contracts, or project-external files. All B4A changes remain unstaged and uncommitted pending separate user approval.
