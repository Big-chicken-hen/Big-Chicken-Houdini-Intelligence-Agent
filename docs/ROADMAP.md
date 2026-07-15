# Roadmap

Each phase requires explicit authorization. Completion of one phase never authorizes the next.

## P0 — Specification, safety, and installation preparation

Deliverables: supported Houdini/Codex version policy; generated app-server schema; capability probes; path policy; threat model; repository safety baseline; installation plan.

Acceptance: every project write stays on `E:`; experimental APIs are not core dependencies; path traversal, UNC/device, ADS, reparse-point, AppData, and drive-root cases are rejected; all offline tests pass. P0-B specifically delivers the minimal repository baseline and validation-only path policy. P0-C requires separate approval.

## P1 — Codex connection and Houdini Panel

Deliverables: Python Panel; local Bridge; supervised stdio app-server; streaming messages; stop/interrupt; approvals; Thread start/resume/read; reconnect behavior.

Acceptance: the UI stays responsive; restart resumes the same Codex Thread; the project keeps no custom chat-history store; control services remain authenticated and loopback-only.

## P2 — Houdini MCP foundation

Deliverables: Houdini MCP host; Scene Gateway; main-thread queue; deterministic node/parameter/connect operations; revision checks; idempotency; undo grouping; structured errors; audit trail.

Acceptance: a natural-language request can create an editable procedural SOP table; one Undo reverts the complete approved operation; retries do not duplicate work; stale revisions fail instead of overwriting scene changes.

P2-V begins with a mandatory design-review gate: architecture, the exact four-tool schema inventory, threat model, and test plan are produced and verified offline before an MCP adapter, Bridge scene route, Panel executor, or `hou` write is implemented. Design approval does not itself authorize a live scene operation.

## P3 — Image modeling

Deliverables: single/multiview image input; ModelSpec; reference cameras and backplates; scale markers; foundational procedural SOP recipes; explicit evidence and uncertainty fields.

Acceptance: one single-image and one multiview asset complete the modeling loop; occluded and hidden structure is labeled as inferred; no false absolute precision is claimed.

## P4 — Independent rendering and visual QA

Deliverables: immutable USD snapshot; external husk/Karma workers; AOVs; file-based preview; status/cancel/result; Codex visual review and revision loop.

Acceptance: Houdini remains usable during renders; cancellation stops only the worker tree; stale-revision renders cannot modify the current scene; at least one evidenced visual-improvement iteration completes.

## P5 — Full DCC coverage

Deliverables: Solaris/USD assembly; MaterialX and VOP tools; Karma CPU/XPU capability handling; cameras/lights; animation; CHOP/KineFX; simulation caches; `hython` clone jobs.

Acceptance: produce a layered USD scene with materials, lighting, animated camera or asset, and a rendered frame sequence; unsupported delegate features are reported rather than assumed.

## P6 — Arbitrary Python tiers

Deliverables: constrained `exec_isolated`; snapshot-based `exec_hython_clone`; disabled-by-default and per-use-approved `exec_live_unsafe`; checkpointing; resource limits; code hashes; audit and security tests.

Acceptance: AppData access, traversal, junctions, network use, subprocess creation, timeouts, and resource exhaustion are blocked or terminated according to policy; live unsafe execution cannot receive permanent approval or subagent access.

## P7 — Automated HDA publishing

Deliverables: namespace/version validation; staging HDA; clean-`hython` install/instance/cook tests; dependency checks; thumbnail; manifest; checksum; atomic promotion; live install/reload.

Acceptance: a new semantic-versioned HDA installs and cooks in a clean process; failure preserves staging diagnostics and never overwrites an existing version or last-known-good artifact.

## P8 — Codex-native multi-agent work

Deliverables: native Codex subagents; proposal schema; root arbitration; single-writer enforcement; conflict detection; stale-proposal rejection; parallel read-only, render, and immutable-artifact workflows.

Acceptance: no second agent framework exists; concurrent work loses no updates; stale proposals are rejected; live HIP writes remain serialized and explicitly approved.

## P9 — Network and release hardening

Deliverables: authenticated remote Job Gateway; TLS/mTLS or bearer policy; rate limiting and replay defense; worker registration; recovery; contract tests; version matrix; fault tests; release packaging.

Acceptance: app-server and Houdini are never directly exposed; disconnects and worker crashes recover safely; workers never receive Codex credentials; production workflows do not depend on experimental APIs.

## Milestones

- P2: natural language produces a procedural editable table.
- P4: reference images produce a model that Codex visually reviews and improves.
- P7: a versioned HDA is tested and published automatically.
- P9: multi-agent, network-worker, and full DCC production capabilities are hardened.
