# Architecture

## Product definition

The product is a rich Codex client embedded in a Houdini Python Panel. Users converse in natural language and attach reference images. Codex is the sole intelligent core: it owns dialogue, vision understanding, reasoning, planning, memory, tool selection, iterative judgment, and native subagents. Houdini owns UI, deterministic tools, scene execution, and artifacts.

The project will not implement a second Agent, LLM, planner, RAG system, vector database, semantic-memory service, or external vision model. It will not use screen takeover or Computer Use to operate Houdini. All scene work will use deterministic Houdini `hou` APIs, MCP tools, and background jobs.

## Components

### Houdini Python Panel

The panel provides conversation, image drop, current-scene context, approvals, tool activity, previews, jobs, and publishing UI. It does not reason or store semantic memory.

### Protocol Bridge and Process Supervisor

An external local service supervises `codex app-server`. Panel-to-Bridge communication uses authenticated localhost HTTP/WebSocket. Bridge-to-app-server communication uses stable stdio JSONL. The Bridge forwards protocol events, reconnects, and maps messages; it does not rewrite prompts or plan work.

### codex app-server

The app-server provides Threads, Turns, history, approvals, streaming events, text input, and `localImage` input. Core operations use `thread/start`, `thread/resume`, `thread/read`, `thread/fork`, `turn/start`, and `turn/interrupt`. A supported Codex version is pinned and its JSON Schema generated. Experimental app-server WebSocket, `dynamicTools`, process APIs, and `thread/shellCommand` are not core dependencies.

### Codex

Codex is the only intelligent subject. It interprets images, produces ModelSpec and ScenePatch proposals, selects deterministic tools, evaluates previews, decides whether to revise or ask the user, manages history and memories, and may use Codex-native subagents in the authorized phase. The repository contains no custom agent orchestrator.

### Houdini MCP Tool Host

An external sidecar is registered through project-scoped `.codex/config.toml`. It publishes deterministic, JSON-Schema-described tools to Codex and connects through authenticated loopback RPC to the Houdini Scene Gateway.

### Houdini Scene Gateway

The RPC I/O thread validates and queues messages but never calls `hou` directly. All live-scene and UI HOM work is scheduled on Houdini's main thread. Writes carry a HIP fingerprint, base scene revision, idempotency key, permission level, and deadline. Each approved tool change is contained in one `hou.undos.group`. Complex modeling is generated, cooked, and validated in a staging subnet before commit. Long cooks are delegated to `hython`.

### Job Manager and workers

Long work uses `submit/status/cancel/result` rather than blocking an MCP call. Planned workers include husk/Karma rendering, `hython` clone execution, constrained Python jobs, and HDA publishing. Cancellation terminates only the relevant worker process tree, never Houdini.

### Technical state and artifacts

SQLite stores only technical state: `project_uuid`, HIP fingerprint, Codex thread identifier, scene revision, jobs, artifacts, idempotency, and audit records. It does not store duplicate chat history, embeddings, semantic summaries, or custom memory. Codex Threads and Codex Memories own conversation history and recall.

## Tool architecture

Generic deterministic tools cover system capability and license probes; HIP, selection, frame, FPS, checkpoints, revisions, scene trees, and errors; node describe/create/connect/copy/rename/flags/cook/layout/delete; parameter get/set/tuple/expression/multiparm/button/keyframe; and geometry bounds, attributes, groups, topology, UVs, normals, cache, and export.

Domain adapters and recipes cover:

- SOP primitives, transforms, booleans, extrusion, bevel, sweep, mirror, copy, remesh, and subdivision.
- Reference images, backplates, scale markers, and camera-match information.
- LOP/USD stages, references, payloads, sublayers, variants, cameras, lights, and render settings.
- MaterialX/VOP graphs, textures, UDIM, binding, GeomSubset, and displacement.
- Keyframes, interpolation, expressions, constraints, CHOP, KineFX, bake, cache, and motion blur.
- Preview/final rendering, AOVs, status, cancellation, logs, and artifacts.
- HDA validation, creation, versioning, testing, publishing, installation, and reload.

Complete Houdini coverage combines generic node/parameter/catalog introspection with high-frequency domain recipes rather than hard-coding one tool per node. Context-specific adapters are used for OBJ, SOP, DOP, LOP, VOP, MaterialX, COP, TOP, KineFX, and APEX. The current live Houdini schema is authoritative over static documentation or model knowledge, and conflicts are reported.

## Scene command contract

Every request contains at least `request_id`, `thread_id`, `turn_id`, `hip_session_id`, `base_scene_revision`, `idempotency_key`, `tool_name`, `arguments`, `deadline`, and `permission_level`.

Every response contains `ok`, `scene_revision`, `created_nodes`, `changed_nodes`, `artifacts`, `warnings`, `structured_error`, and `job_id`.

Manual scene edits advance the revision. A patch based on an old revision returns `SCENE_CONFLICT`; last-write-wins is forbidden. Repeating an idempotency key must not duplicate nodes, jobs, or publication.

## Image-modeling loop

1. The panel imports single images, multiview sets, blueprints, or scale references.
2. Images are copied to session artifacts on `E:`; originals are never deleted.
3. The app-server supplies images to Codex as `localImage` input.
4. Codex creates a ModelSpec describing asset type, units, coordinates, image roles, camera assumptions, landmarks, scale, part hierarchy, geometry, dimensions, transforms, symmetry, modeling method, material/animation needs, topology, UVs, subdivision, provenance, confidence, evidence, alternatives, uncertainties, acceptance cameras, and visual checks.
5. ModelSpec becomes a revision-bound ScenePatch with preconditions, postconditions, scope claims, and rollback strategy.
6. Houdini builds editable procedural SOP networks.
7. Fixed reference cameras drive external Karma renders for clay, silhouette, wireframe, normal, depth, ID, and beauty outputs.
8. OpenCV may compute deterministic perspective, landmark, silhouette, and difference metrics; it is not a second AI.
9. References, renders, AOVs, and difference images return to Codex as `localImage` inputs.
10. Codex chooses whether to revise, ask the user, switch candidates, or finish.

A single image cannot establish absolute scale, hidden surfaces, internal structure, or occluded geometry. Those conclusions are labeled as inference, never presented as observed precision.

## Rendering and DCC model

Preview rendering exports an immutable USD snapshot at a fixed revision, then runs external husk with Karma CPU/XPU. Results include EXR, PNG proxies, AOVs, logs, and a manifest. The panel shows file-based previews. Presets include clay-fast, silhouette, wireframe, normal-depth-ID, beauty-draft, turntable, and final. A render from an old revision remains viewable but cannot automatically modify the current scene.

Procedural SOP remains the editable source. Solaris assembles SOP Import, Reference, Sublayer, Payload, and Variant layers. Asset, material, and shot layers remain separate. Recommended paths are `/World/geo`, `/World/cameras`, `/World/lights`, `/materials`, and `/Render`. MaterialX is the common material baseline with generic VOP support. Karma CPU/XPU behavior is capability-checked rather than assumed. Animation spans objects, parameters, cameras, lights, materials, LOP time samples, CHOP, KineFX, simulation caches, and motion blur.

## Codex-native parallelism

Only Codex-native subagents are used. The initial target is four threads and depth one. Subagents may inspect images, analyze geometry, propose materials, or review renders in parallel, but default to returning proposals. Root Codex is the sole arbiter and the live HIP is always single-writer. Disjoint component proposals may merge; topology edits to the same component conflict; stale-revision proposals are rejected. True parallel execution uses HIP copies, immutable USD layers, or cache artifacts.

## Python execution tiers

Future arbitrary Python is divided into three explicit risk tiers:

1. `exec_isolated`: low-privilege CPython without `hou`, read-only inputs, one writable job directory, no network by default, and bounded subprocess, CPU, memory, time, disk, and output.
2. `exec_hython_clone`: `hython` opens a checkpoint or USD snapshot, never the live HIP, and emits BGEO, USD, HDA, caches, or a ScenePatch.
3. `exec_live_unsafe`: disabled by default, displays complete code/hash/targets/side effects, checkpoints first, requires one-time user approval, cannot be permanently approved or invoked by a subagent, and warns that in-process Python cannot be truly sandboxed or reliably interrupted and that filesystem/external effects are not fully undoable.

No unrestricted Python, shell, `exec`, or `eval` interface is part of the current phase.

## HDA publishing

Publishing validates namespace and semantic version, builds a staging HDA, installs/instantiates/cooks it in a fresh `hython`, tests parameters, I/O, and dependencies, creates a thumbnail/manifest/checksum, atomically promotes to an `E:` publish root, then installs or reloads it in live Houdini. Existing semantic versions are never overwritten. Failure preserves staging evidence and does not replace last-known-good. The manifest records Houdini build, source revision, Codex thread/turn, dependencies, and hashes.

## Network boundary

All control services default to `127.0.0.1` with random tokens. The app-server remains stdio and is not directly network-exposed. Remote mode exposes only an authenticated Job Gateway using HTTPS/WSS, TLS, bearer tokens or mTLS, rate limits, and replay protection. Workers register Houdini build, licenses, GPU, and Karma delegate but never receive Codex login credentials. Neither the Houdini Scene Gateway nor app-server is exposed directly to a LAN or the public internet.
