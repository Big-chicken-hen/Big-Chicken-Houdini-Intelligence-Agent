# Full Goal Blueprint and Panel Project

Use this contract for every Full Houdini Goal and for project allocation whenever a
new submission explicitly chooses **项目团队**. It defines the user-visible construction
specification, complete current-stage acceptance card, real-evidence repair loop, and
Panel-owned Codex project. It does not define a backend Agent, a second planner, a
scheduler, or a workflow state machine.

Resolve the user's **单个 AI / 项目团队** choice before applying the Direct, Focused, or
Full depth from `build-brief-and-review.md`. The choice controls the collaboration
container; complexity controls only the actual blueprint, research, execution, and
review depth inside that choice. A hidden risk classification must never reverse the
user's result. A bounded correction that continues an active Goal remains inside that
Goal's current stage card and project.

## Contents

- [Stay within adjacent contracts](#stay-within-adjacent-contracts)
- [Preserve these invariants](#preserve-these-invariants)
- [Resolve the user choice before complexity depth](#resolve-the-user-choice-before-complexity-depth)
- [Reuse the five project Threads](#reuse-the-five-project-threads)
- [Coordinate through the Codex app-server](#coordinate-through-the-codex-app-server)
- [Allocate Full responsibilities](#allocate-full-responsibilities)
- [Keep authority and provenance explicit](#keep-authority-and-provenance-explicit)
- [Expand sparse prompts into a complete visible specification](#expand-sparse-prompts-into-a-complete-visible-specification)
- [Use these stable user-visible blueprint sections](#use-these-stable-user-visible-blueprint-sections)
- [Authorize the full blueprint, then use bounded loop payloads](#authorize-the-full-blueprint-then-use-bounded-loop-payloads)
- [Complete current stage card contract](#complete-current-stage-card-contract)
- [Run the stage build and acceptance loop](#run-the-stage-build-and-acceptance-loop)
- [Use available native subagents only inside read-only roles](#use-available-native-subagents-only-inside-read-only-roles)
- [Migrate a Thread only after its third real automatic compaction](#migrate-a-thread-only-after-its-third-real-automatic-compaction)
- [Keep the project visible and steerable in Panel](#keep-the-project-visible-and-steerable-in-panel)
- [Reject false complexity](#reject-false-complexity)

## Stay within adjacent contracts

This reference is a thin Full-only coordination layer. It consumes existing contracts;
it does not replace or expand their authority.

- `build-brief-and-review.md` owns Direct, Focused, and Full depth. It scales the
  research, blueprint, and review work inside the user's resolved choice; it never
  chooses the single-Thread or project-team container.
- `knowledge-and-memory.md` owns uncertainty-triggered retrieval, the optional Context
  Pack, and explicit durable project-memory actions. Coordination reuses supplied
  evidence; it never forces a Context Pack or lookup and never writes project memory.
- The existing native Goal and Focus surfaces remain authoritative. A team-selected new
  task creates or binds one native Goal through that existing surface. Project
  membership, titles, stage cards, review evidence, and Panel labels neither copy the
  Goal or Focus schema nor invent fields or transitions. Terms such as `incomplete`
  describe a handoff or evidence outcome, never a new Goal or Focus state.
- `visual-validation.md` owns the temporary bounded EffectSpec experiment. Project
  Threads may route its resulting evidence but never create, persist, enlarge, or turn
  the EffectSpec into Goal state.
- Each professional Skill retains its domain methods and trigger boundary. Planning
  coordinates modeling, material, lighting, FX, animation, simulation,
  render, research, and review handoffs; it does not absorb those Skill contracts.

## Preserve these invariants

- Keep Codex as the only reasoning and planning system.
- Treat the user's latest explicit requirements as the highest-authority task facts.
- Resolve **单个 AI / 项目团队** before complexity depth. Single creates no team;
  project team creates the native Goal, Panel project, and all five real Threads for
  that newly submitted task.
- Keep the complete blueprint and its revisions visible through the Planning Thread
  and the established Panel blueprint surface.
- Keep Execution as the only role allowed to call mutating HIA/HOM capabilities or save
  the current HIP. Bridge enforces this at Thread creation: Supervisor, Planning, and
  both review Threads receive empty `hia_mcp_v2` and `houdini_intelligence` inventories.
  Prompt instructions are an additional semantic boundary, not the enforcement layer.
- For every team-selected task, keep one Panel project for the native Goal and reuse
  exactly five real project Threads for every stage and correction: Supervisor,
  Planning, Execution, Visual Review, and Technical Review. This is the baseline for
  every model.
- Never create a Thread per stage, subsystem, reviewer, retry, or source.
- At initial authorization and after every material blueprint revision, give Supervisor
  the complete Planning blueprint for strict review. After authorization, keep each
  repeated execution and review loop bounded to the hard-constraint capsule, complete
  current stage card, and latest evidence delta.
- Plan the whole semantic stage before authoring it, then use bounded scene writes for
  one coherent change at a time.
- Require both applicable technical evidence and applicable visual evidence before a
  stage passes.
- Keep the native Goal active throughout planning, Execution, both review returns, and
  every repair cycle. Complete it only after every applicable stage has passed with
  fresh actual image content and claim-specific technical evidence.
- Keep all project workers inside the Panel project container so they do not clutter
  the user's top-level task list.
- Let the user change any project Thread to any model supported by Panel and append
  guidance without changing project membership or creating a new top-level task.
- Run Visual Review and Technical Review independently and read-only after every stage;
  missing either required Thread is an activation failure, not a reason to merge roles.
- Keep Goal state, stage cards, and both review results on their existing surfaces; do
  not copy their schema into this project contract, project memory, or a new ledger.

## Resolve the user choice before complexity depth

Resolve the per-submission selector first. Choosing **单个 AI** keeps that newly
submitted task in the original Panel Thread and creates no team project or five-role
roster. Choosing **项目团队** automatically creates one Panel project, one native Goal,
and all five real project Threads for that newly submitted task. Resume an existing
project only when the submission explicitly continues that same native Goal; never
create another project for a later stage or correction of it.

Direct, Focused, and Full are depth descriptions, not hidden routing decisions. They
may shorten or expand the blueprint, evidence, and review work that the chosen route
actually needs, but they must not override, downgrade, or upgrade the user's explicit
single/team choice. Do not claim that a request stayed single because it was Direct or
Focused, and do not create a team after the user selected single.

Associate a team project with the existing native Goal identity and keep all role
Threads inside that container. Do not derive membership from a title, current working
directory, model, service tier, or risk label.

Panel is the user's control surface for the project. It displays the five roles,
current Thread state, model, blueprint/stage guidance, and review evidence that the
Bridge actually receives from the app-server. The user may select a supported model
per Thread, switch it for later turns, and append role-specific or project-wide
guidance. Route that guidance into the existing project Threads; do not fork a new
top-level task merely because the model or instruction changed.

The single-AI choice is a deliberate user outcome, not a technical limitation or
failure mode. It uses the original Thread and scales its blueprint and evidence to the
actual task depth. The project-team choice always establishes all five Threads before
the first write, while its blueprint and review detail still scale to the actual task.

User-facing team settings must name these results in ordinary language. Machine
storage values are implementation details and must not be the primary labels. A
setting changes project launch behavior only; it never changes the complexity depth,
evidence bar, single-writer boundary, or any Goal, Focus, Context Pack, EffectSpec, or
project memory contract.

If the Bridge cannot create or resume the required app-server Threads, surface the
specific project activation failure in Panel and keep the native Goal active and
incomplete. Do not
claim that Panel work is inherently single-Thread, silently substitute a lower-quality
route, or compensate with one giant HOM script.

## Reuse the five project Threads

Every project-team Full Goal project has exactly these five stable, user-visible roles. Use
the Chinese role name as the primary Panel label and the English name as a secondary
aid.

| Project role | Authority and durable responsibility |
| --- | --- |
| **监督（Supervisor）** | Owns Goal-level decisions, keeps the native Goal active through planning, both reviews, and every repair, receives and strictly reviews the complete blueprint at initial authorization and after material revisions, then receives bounded current-stage loop payloads, rejects or returns generic and under-specified plans, accepts or rejects real technical and visual evidence, selects the minimum repair, and completes the Goal only after every stage really passes. It never mutates or saves the HIP. |
| **方案（Planning）** | Owns the complete user-visible, task-specific advanced construction blueprint, research and source findings, reference observations, architecture, provenance ledgers, fully expanded stage cards, native-node proposals, dependency plans, bounded HOM drafts, revision history, and downstream contracts. It never mutates or saves the HIP. |
| **执行（Execution）** | Is the only role allowed to call mutating HIA/HOM capabilities or save the current HIP. Each turn receives only the approved current stage card plus its hard constraints and latest evidence delta, executes bounded changes for that stage, and returns real scene evidence, diffs, errors, and limitations to Supervisor. It must never receive or execute the whole asset plan as one batch. |
| **视觉审查（Visual Review）** | Independently and read-only reviews the stage's usable actual image content, visible acceptance claims, reference match, composition, temporal appearance, material/light response, largest visual deviation, and minimum visual repair. |
| **技术审查（Technical Review）** | Independently and read-only reviews the stage's routed live scene facts, graph and dependency integrity, semantic expectations, geometry relationships, cache/render evidence, performance claims, largest technical deviation, and minimum technical repair. |

Use stable role keys `supervisor`, `planning`, `execution`, `visual_review`, and
`technical_review` only as transport identifiers; never use them as substitute primary
labels. Resume these same five Threads for every stage and correction of the Goal.
Never replace Execution with Supervisor, Planning, a reviewer, or an internal subagent,
and never allow a second writer. Review Threads never mutate the HIP, never start their
own repair loop, and never replace a core responsibility.

Keep all five Threads associated with the same Panel project and Goal.
Do not reuse a worker for a different Goal merely because the asset or technique looks
similar. Thread history is the collaboration record; do not add a second roster
database, watcher, scheduler, or reasoning service. Project lifecycle follows the
existing Panel project controls: preserve role histories and keep the user's main
project visible after final handoff. Never delete a project or role Thread as cleanup.
The only automatic deletion exception is the verified third-compaction migration
defined below, which deletes exactly one superseded Thread after its replacement has
passed every check.

## Coordinate through the Codex app-server

The Bridge owns project-Thread transport for HIA Panel. It creates a missing project
member with the stable Codex app-server `thread/start` method family and starts each
bounded role turn with `turn/start`. Use the live app-server contracts for parameters
and responses; this reference intentionally freezes only those method families, not a
payload schema. Resume the existing project member when possible instead of starting a
duplicate Thread.

The Bridge associates each app-server Thread with the Panel project, Goal, role key,
and user-visible role title, then forwards state and output to Panel. It does not
reason about the blueprint, choose repairs, rank evidence, or authorize a HIP write.
Those remain Codex role responsibilities. Project membership is explicit Bridge state;
do not infer it from a common directory, matching title, or model name.

At project activation, ensure all five members exist before the first scene write.
Send exact user facts and project-wide guidance to Supervisor and Planning. After
Planning publishes the complete blueprint, route that entire artifact to Supervisor
for initial authorization; repeat this full-artifact review after every material
revision. Only after authorization: Send Execution only the approved hard-constraint
capsule, one complete current stage card, and latest evidence delta; never send all
future stage cards or ask it to execute the whole asset in one turn. Route later user
guidance from Panel to the relevant existing member with `turn/start`. A user-selected
model change applies to subsequent turns of that member and does not create a new role,
project, or top-level task.

Only Execution receives HIA scene capabilities. When Bridge starts a non-Execution
project member, it applies the flat overrides in `thread/start.config` so both
`hia_mcp_v2` and `houdini_intelligence` tool inventories are empty. Resuming that
member must preserve the same restriction. This is a Bridge-enforced capability
boundary, not a request that roles merely obey in prose.
Supervisor, Planning, Visual Review, and Technical Review consume real scene facts,
diffs, errors, captures, and render evidence routed from Execution; they cannot call
HIA/HOM themselves. Do not add a second planner, copied app-server schema, local
collaboration database, or separate autonomous orchestration service.

If project activation or a required role turn fails, preserve the current HIP, expose
the exact Bridge/app-server failure and affected role in Panel, and keep the relevant
claim incomplete. Retry only through the existing bounded Bridge recovery contract.
Do not invent successful worker output, shift write authority to another role, or
silently degrade the project into a different coordination model.

## Allocate Full responsibilities

Before the first project-team scene write, allocate these responsibilities across the active
Panel project Threads:

- Send the exact user facts and the initial observable completion claim to the
  Planning responsibility.
- Have Planning resolve target-specific research questions, known scene
  context, architectural decisions, source provenance, native-node strategy, and
  explicitly labeled uncertainty.
- Have Planning synthesize the full user-visible blueprint from User facts,
  supplied references, research, architecture, and explicitly labeled assumptions.
- Require Planning to expand a sparse complete-deliverable prompt into target-specific
  construction decisions and ordered steps rather than stopping at generic stage names.
- Give Supervisor the complete synthesized blueprint, every stage card, provenance,
  and production depth-check result for strict initial authorization. Have Supervisor
  verify task anchors, required structure, anti-repetition/filler integrity,
  claim-specific live-scene and actual-image evidence, dependency or relationship
  checks, and minimum repair rules; return any failing blueprint to Planning.
- Give Execution only the approved hard-constraint capsule, first complete stage card,
  and current evidence delta after those artifacts are available.
- After every stage, send unchanged technical claims and routed live evidence to
  Technical Review and unchanged visual claims plus usable actual image content to
  Visual Review. Start both read-only review turns in parallel and wait for both before
  Supervisor accepts the stage or selects a repair.

Supervisor resolves tradeoffs, accepts evidence, and authorizes each bounded stage
instruction. Planning maintains the authoritative plan. Execution alone performs the
scene write. Reviewers provide evidence only and cannot receive HIA/HOM tools.

## Keep authority and provenance explicit

Maintain separate ledgers in the blueprint. Never blend their language into one
unattributed design summary.

Keep User facts in their own highest-authority section or table column and keep Codex
assumptions in a separate labeled section or column. An assumption may resolve a gap
reversibly, but it must never occupy the User-fact column or silently weaken a User
fact. Preserve this separation in every blueprint revision and current stage card.

### User facts

Label every item **User fact**. Preserve the user's wording when precision matters.
Include positive requirements, explicit negative constraints, named references,
requested style, deliverables, output location, timing, performance limits, and any
instruction to approximate or not approximate.

Apply this authority order:

- the user's latest explicit instruction for this Goal;
- earlier explicit user instructions that the latest instruction did not supersede;
- verified current-scene facts;
- reference observations supported by cited evidence;
- Codex assumptions.

A reference, convention, inferred feasibility issue, or reviewer preference cannot
silently weaken a User fact. If two User facts conflict, expose the conflict and ask
only for the decision that changes the result. If a requirement proves infeasible,
report it as blocked or incomplete; do not edit the requirement into something easier.

### Reference observations

Label every item **Reference observation**. Record the source path or URL, relevant
view/frame/version, what is directly observable, and what remains interpretation.
Separate geometry observations from camera, lens, lighting, material, display, and
post-processing observations. A reference observation informs construction but is not
proof that the live Houdini result matches.

### Codex assumptions

Label every item **Codex assumption**. State why the assumption is needed, its visible
or technical consequence, confidence, how it can be revised, and what evidence could
replace it. Prefer reversible assumptions when a sparse prompt leaves room for design
judgment. Do not relabel an assumption as a User fact or Reference observation.

### Verified-scene provenance

Label live observations **Verified scene fact** and include the scene path, frame or
time, Houdini build when relevant, and the read-only evidence used. A remembered node
name, source document, or successful script return is not a Verified scene fact.

## Expand sparse prompts into a complete visible specification

Treat a sparse request for a complete named asset, effect, shot, material, animation,
or simulation as a request for a resolved deliverable unless the user explicitly asks
for a blockout, proxy, placeholder, or technical test.

Automatically expand the request before the first write. Make the target-specific
blueprint detailed enough to remove material ambiguity from identity, systems,
dependencies, construction, evidence, and handoffs. A sparse prompt for a complete
asset still requires a genuinely several-thousand-to-tens-of-thousands-scale advanced
construction blueprint rather than a short outline.

Use the existing production validator's `task-specific information unit` measurement;
do not reproduce its algorithm or payload schema here. Before Supervisor may authorize
a Full blueprint, require all of these minimum information floors:

- at least **10,000 task-specific information units** across the complete blueprint;
- at least **2,500 task-specific information units** in every complete stage card;
- at least **350 task-specific information units** in every ordered construction step.

These information floors are necessary but never sufficient. The same authorization
must pass the production semantic checks for:

- **task anchors:** the Goal, User facts, target identity, relevant references or scene
  facts, subsystem responsibility, construction decision, expected result, evidence,
  and minimum repair remain traceable to the actual task;
- **required structure:** every applicable user-visible section, complete stage card,
  ordered step, dependency, output, review claim, and downstream handoff is present;
- **anti-repetition and anti-filler:** copied phrases, circular paraphrases, canned
  recipes, generic checklists, decorative prose, and padding neither count as
  task-specific information nor satisfy the floor;
- **stage and step semantic completeness:** each stage and step resolves its own
  network region, native operation or node strategy, connections, key parameter
  dependencies, result, evidence, and failure minimum repair.

Length is an auxiliary depth signal, not a quality score: meeting a number cannot
compensate for a missing task anchor, structure, or construction decision. Conversely,
a semantically strong artifact below any required information floor is still
incomplete and returns to Planning. These checks govern only the Houdini Full blueprint
authorization; they do not create a general Planner, reusable Gate, or workflow state
machine.

A title-only sequence of broad phases is a routing outline, not a construction
blueprint. Expand every applicable phase into the target's actual network regions,
native construction strategy, ordered connections, parameter dependencies, expected
results, evidence, and bounded repair decisions before Supervisor authorization.
Generic verbs such as build, refine, add detail, or finish do not substitute for those
decisions.

Do not pad the blueprint with repeated prose, generic checklists, or a canned recipe.
Every line must change construction, review, evidence, risk, or downstream use. Derive
the actual subsystems, stages, node strategy, and parameters from the current Goal,
references, scene, and Houdini evidence. Never hard-code a domain or asset-family
recipe into this Skill.

Detail means resolving the requested deliverable at professional construction depth;
it does not authorize speculative features, unnecessary subsystems, decorative node
counts, or complexity the Goal does not need. Prefer explicit reversible assumptions
over silent invention, and keep each assumption visibly separate from User facts.

Do not use node quantity, Box quantity, network-box quantity, script length, stage
quantity, capture quantity, or iteration quantity as a proxy for quality. A concise
stage may be correct; a long network may still be wrong.

## Use these stable user-visible blueprint sections

Planning publishes the following natural-language sections in this order through its
project Thread and the established Panel blueprint surface. In a single-AI Full Goal,
the original Panel Thread publishes the same sections. A section may say `Not
applicable` with a reason; do not silently omit a section that affects the Goal.

### Goal and observable completion

State the intended result, audience or use, completeness promise, observable quality
bar, and what would make the result recognizably correct.

### User facts and hard constraints

List the authoritative User facts, explicit prohibitions, required deliverables,
output-location rules, scope boundaries, and user-approved approximations.

### Reference observations

List source-linked visual and technical observations without turning interpretation
into fact.

### Codex assumptions

List reversible assumptions, consequences, confidence, and replacement evidence.

### Verified scene facts

List current live-scene observations that have claim-specific proof. Include the scene
path, frame or range, Houdini build when relevant, evidence source, and the boundary of
what the observation establishes. Do not promote source text, plausible reasoning, or
successful node creation into a verified scene fact.

### Conflicts, risks, and unverified claims

Expose unresolved requirement conflicts, version or technique uncertainty, scene
unknowns, licensing limits, cost risks, and claims that current evidence cannot settle.

### Design language and recognition features

Define silhouette, scale, proportions, hierarchy, negative spaces, motion language,
surface identity, lighting intent, composition, and the features that distinguish the
target from a generic primitive assembly.

### Subsystems and responsibilities

Describe each coherent subsystem, why it exists, its inputs, outputs, dependencies,
edit boundary, owner role, and failure effect. Include modeling, materials, lighting,
animation, simulation, FX, USD/render, or delivery subsystems only when applicable.

### Editable controls and parameter dependencies

List controls that map to user decisions. Record units, meaningful range or selection,
driver, dependents, update propagation, interaction with other controls, and visible or
technical effect.

### Outputs and delivery contract

Name requested scene roots, explicit `OUT_*` or material/render outputs, semantic
groups or attributes, frame/range coverage, caches, USD, renders, exports, and actual
destination rules.

### Stage map

Give every applicable stage a natural-language title, purpose, prerequisites,
downstream consumer, and current disposition. Merge or skip inapplicable semantic
stages instead of inventing work to satisfy a count. A generic phase title without
target-specific construction content cannot be authorized as a stage plan.

### Complete stage acceptance cards

Include one complete card for every stage. Keep future cards in the Planning Thread
and revise them when evidence or user instructions change. Each card must contain at
least 2,500 production-measured task-specific information units after repeated or
filler content is excluded. Supervisor receives all cards inside the complete blueprint
for initial and material-revision authorization; after authorization, give Supervisor,
Execution, and reviewers only the complete current card needed for the active loop.

### Evidence and review ledger

Record each technical and visual claim, evidence status, evidence artifact or live
path, frame/view/build, reviewer, highest-impact issue, minimum repair, and missing
proof.

### Revision history

Record the user instruction or evidence that caused a material blueprint change, the
sections/cards affected, and whether downstream contracts need revalidation. Do not
copy routine transcript chatter.

## Authorize the full blueprint, then use bounded loop payloads

In a project-team Full Goal, Planning must send Supervisor the complete synthesized
blueprint for initial authorization. That authorization payload includes every stable
user-visible section, all complete stage cards, provenance and assumption ledgers,
outputs and downstream contracts, the evidence/review plan, revision history, and the
production depth and semantic-check result. Supervisor must see the actual artifact;
a pointer, short brief, capsule, selected excerpt, or Planning assurance is not enough
for strict authorization.

Supervisor reviews the complete artifact against User facts, task anchors, required
structure, the 10,000/2,500/350 information floors, anti-repetition/filler integrity,
native construction detail, dependencies, evidence claims, prohibitions, and minimum
repairs. A generic plan, phase-only list, undefined instruction to add detail,
under-floor artifact, or blueprint that leaves graph topology and dependencies for
Execution to invent is not authorizable. Supervisor must either expand the
authorization request with established missing requirements or return the plan or card
to Planning for task-specific expansion. Planning remains the single authoritative
blueprint owner. Authorization and revision must finish before the fully expanded
current card reaches Execution.

After Supervisor authorizes that full version, each repeated execution and review loop
uses exactly these bounded semantic parts:

- **Global hard-constraint capsule:** the current Goal, all applicable User facts and
  explicit prohibitions, output and safety boundaries, sole-writer rule, source or
  licensing constraints that affect execution, and unresolved blockers.
- **Complete current stage card:** every field in the card contract below, in full.
- **Latest evidence delta:** only new evidence or blueprint revisions that change the
  current card.

Do not re-paste the already authorized full blueprint, all future stage cards, long
source bodies, complete subtask transcripts, or the entire evidence history into every
execution or review turn. Do not compress the current card into a pointer or short
brief. The card must remain self-contained enough to build and review the current
stage without reopening the whole blueprint. Execution and both review Threads never
need the whole blueprint merely because Supervisor received it for authorization.

When a user correction, evidence result, or global constraint materially changes the
blueprint, update the authoritative Planning artifact first, identify every affected
stage and downstream contract, and send the complete revised blueprint back to
Supervisor for renewed strict authorization. Only after that approval resume bounded
loop payloads. A local evidence delta that does not revise the blueprint stays in the
current loop package. Never rely on an old summary after a user correction.

In a single-AI Full Goal, the original Thread must perform the same complete-blueprint
review at initial authorization and after material revisions, then keep its authoring
and separated review passes bounded to the same capsule, complete current card, and
latest evidence delta without inventing project roles.

## Complete current stage card contract

Use a natural-language stage title, not a short number. Every card must contain every
heading below and at least 2,500 task-specific information units under the production
measurement. `Not applicable` requires a target-specific reason and cannot be used to
evade the floor or a task-relevant decision.

### Stage objective

Define the single observable stage outcome, its scope, its change boundary, and the
completion claim the stage is meant to support.

### Prerequisites

List passed upstream claims, required scene state, required research or installed-node
evidence, source assets, frame/range, units, coordinate conventions, and unresolved
conditions that would block safe execution.

### Inputs

Name live scene paths, upstream outputs, reference views, source data, semantic groups
or attributes, materials, caches, and the applicable User facts, Reference
observations, and Codex assumptions.

### Ordered construction steps

Describe the target-specific build in executable semantic order. For each meaningful
step, provide every item below. Plan enough detail that neither Supervisor nor
Execution must invent the graph node by node. Every ordered step must contain at least
350 production-measured task-specific information units after repeated, generic, and
filler content is excluded.

- **Network region and responsibility:** name the affected scene path, network, subnet,
  material graph, LOP stage, solver area, or other bounded region and the construction
  responsibility performed there.
- **Native operation or node strategy:** name the suitable existing operation or
  installed native node family, its purpose, creation or reuse rule, and any unresolved
  installed-type uncertainty. Do not guess a versioned internal type.
- **Inputs and connections:** name upstream inputs, ports or semantic relationships,
  branch and merge order, outputs consumed downstream, and connection conditions.
- **Key parameter dependencies:** name decisive controls, units or spaces, values or
  ranges and reasons, drivers, dependents, propagation rules, and interactions that
  can change the result.
- **Expected result:** state the local visible, structural, semantic, temporal, or
  delivery result and the authoritative output produced by the step.
- **Evidence:** state the exact live scene fact, relationship check, semantic result,
  frame/view, capture, render, or other claim-specific proof required before the next
  step may rely on the result.
- **Failure minimum repair:** state the smallest coherent region or control set to
  revise if the evidence fails, what passed work must remain untouched, and which
  evidence must be reacquired.

### Native-node strategy

Prefer suitable existing nodes and parameters, then standard installed native Houdini
nodes and networks. Use one or a few cohesive Execution-authored HOM batches only to
orchestrate a bounded coherent change. State why direct geometry construction or an
in-scene Python/script node is necessary when native nodes cannot reasonably express
the result. Record installed-type uncertainty rather than guessing a versioned type.

### Authoring batches

Split execution by semantic responsibility and review boundary. One card may require
several bounded writes. Never put an entire complex asset, all stages, or unrelated
subsystems into one giant HOM script merely because subagents are unavailable.

### Parameter dependencies

For each decisive parameter or control, record the driver, dependent nodes or outputs,
unit or space, chosen value or range and reason, update rule, interaction with other
controls, and the evidence that proves propagation works.

### Outputs

Name the stage's live scene outputs, semantic groups, attributes, materials, caches,
render products, explicit output nodes, and any temporary evidence artifacts. State
which output is authoritative.

### Visible characteristics

Describe the silhouette, proportions, negative spaces, depth, contact, motion phase,
surface response, lighting separation, composition, or other actual image content the
user and reviewer should see at this stage.

### Structural relationships

State hosts, anchors, supports, contacts, spans, ordering, dependency direction,
clearance, minimum separation, allowed overlap, forbidden intersection, and update
behavior as applicable. Use scale-relative numeric expectations when a precise spatial
claim matters.

### Prohibitions

Repeat every global or local negative constraint that the stage could violate. Include
forbidden equivalent geometry or behavior, placeholder substitutions, destructive
scope expansion, parallel live-scene writes, hidden dependencies, and any disallowed
legacy or renderer route relevant to the Goal.

### Technical evidence

List claim-specific live evidence: paths, node errors, connections, parameters,
semantic expectations, geometry facts, dependency propagation, frame/range, cache
freshness, renderer support, measured cost, and exact pass conditions. A clean cook or
node existence alone is insufficient when the claim is relational or semantic.

### Visual evidence

List the required actual image content, intended view/camera/aspect, representative
frame or bounded sequence, visible features to compare, capture/display limitations,
and exact pass conditions. In a project-team Goal, usable actual image content is
required for every stage review and every repair review. Even a technically oriented
stage must provide a representative image that can expose visual regression; missing
or unusable image content keeps the stage unverified. Do not replace image evidence
with a node list, successful cook, or prose description.

### Reviewer

Name Technical Review and Visual Review as applicable, with their professional domains;
do not merge them into one reviewer. State what evidence each independent pass receives
and which claim it must return unchanged after a repair. Both lanes are mandatory for
every project-team stage and repair cycle. A single-AI route may mark one lane not
applicable only with a claim-specific reason and evidence that the other lane fully
settles the bounded task.

### Failure minimum repair

Define how to select the largest consequential deviation, the smallest coherent region
or control set allowed to change, evidence that must be reacquired, and passed regions
that must remain untouched. Do not pre-author a generic fallback that weakens the Goal.

### Downstream contract

State exactly what the next stage may rely on: authoritative paths and outputs,
topology or schema, groups/attributes/primvars, transforms and units, parameter
interfaces, material or cache handoff, invariants, remaining risks, and invalidation
conditions.

### Card evidence disposition

Report technical evidence and visual evidence separately as `pending`, `verified`,
`unverified`, or `failed`. Report the stage disposition as `not started`, `active`,
`needs repair`, `passed`, `blocked`, or `not applicable`. These are user-visible
evidence labels, not mandatory transitions and not a state machine.

## Run the stage build and acceptance loop

Keep the existing native Goal `active` throughout this entire loop. A Planning return,
an Execution success, one reviewer pass, or one stage pass never completes the Goal.

For each applicable stage in a project-team Full Goal:

- Enter the loop only after Supervisor has received and authorized the complete
  Planning blueprint and production validation result.
- Have Planning issue the complete current stage card and any changed hard
  constraints through its project Thread and Panel surface.
- Have Supervisor check the card against the current Goal, user guidance, and existing
  evidence. Expand the authorization request with established missing detail or return
  a generic or under-specified card to Planning; authorize only a task-specific,
  fully expanded card.
- Send Execution only that fully expanded current stage card, its hard constraints,
  and latest evidence delta. A returned generic card never reaches Execution. Never
  send future cards or ask it to author the whole asset in one turn.
- Have Execution inspect only the live context required by the card, plan the affected
  graph region, and execute bounded authoring batches. Only Execution may use mutating
  HIA/HOM capabilities on the current HIP.
- Have Execution return the card's claim-specific technical evidence from the real
  scene plus usable actual image content for every stage and every repair. If either is
  unavailable, the stage remains unverified and cannot advance.
- Start Technical Review and Visual Review in parallel after the stage. Give unchanged
  technical claims and routed live evidence to Technical Review; give unchanged visual
  claims and usable actual image content to Visual Review. Both remain independent and
  read-only.
- Wait for both review returns. Supervisor combines their real evidence without merging
  the claims, accepts the stage only when both applicable evidence sets verify, or
  selects the single highest-impact repair.
- If either applicable pass fails or remains unverified, name the largest consequential
  deviation and return the card's minimum coherent repair to Supervisor.
- Treat a generic or phase-only plan, visibly substandard result, interpenetration,
  unsupported or floating construction, incorrect support or contact, insufficient
  clearance, and a Box-heavy stand-in for requested finished construction as concrete
  failures when applicable. Supervisor issues the smallest directed repair that fixes
  the evidenced defect without reopening passed regions.
- Have Supervisor approve the bounded repair, Planning revise the card only when its
  contract changed, and Execution repair only that region. Reacquire both usable actual
  image content and claim-specific technical evidence, then return the same claims to
  both reviews. If that repair materially revises the full blueprint or any downstream
  contract, pause the bounded loop and send the complete revised artifact to Supervisor
  for renewed authorization before Execution continues.
- Continue the Supervisor-driven repair and parallel review loop without a fixed
  iteration count until both applicable evidence sets are verified. Stop earlier only
  when the user stops the Goal or a genuine blocker is evidenced; then mark the affected
  claim and downstream contract incomplete or blocked rather than manufacturing a pass.
- After a pass, have Planning record the evidence delta, freeze the downstream
  contract, and issue the next complete stage card.
- Only after every applicable stage has passed both review lanes with real evidence may
  Supervisor mark the existing native Goal complete through its established Goal
  surface. A pause, tool failure, or genuine blocker does not manufacture completion;
  retain the active Goal and expose the limitation through the existing surface unless
  the user explicitly ends it there.

Do not turn review into a numeric score, fixed iteration ritual, or automatic approval.
Do not advance because a call succeeded, a Box exists, a chosen node count was reached,
or a reviewer is unavailable.

Keep Goal state, stage cards, Technical Review evidence, and Visual Review evidence on
their established surfaces. This loop consumes those values but never copies their
schema into a second Goal record, phase system, review database, or project memory.

For a single-AI Full Goal, perform the same stage-card and evidence loop in the original
Panel Thread. Keep scene writes bounded to the current card, then perform visual and
technical review as separated read-only passes before the next write. Do not create
project roles or pretend that independent reviewer Threads ran.

## Use available native subagents only inside read-only roles

The five real project Threads are the baseline for every model. Any model supported by
HIA Panel may serve any project role; do not hard-code a model family, model ID,
service tier, or reasoning level into role identity. A user model change keeps the
same project membership and role history.

At each Supervisor, Planning, Visual Review, or Technical Review turn, use only
capabilities that the current model and runtime actually expose. When a native
subagent tool is available and that read-only role has genuinely parallel,
non-overlapping work, it must dispatch bounded internal subagents for appropriate
research, structural analysis, visual review, or technical review, then synthesize
their returns into the owning project Thread. Sol Ultra is an important
capability-bearing case, not a version or model-ID dependency.

Every internal subagent assignment is read-only. Execution deliberately does not
proactively spawn or delegate to native subagents: they inherit its HIA scene-write
capability, so prompt-only restraint would not prove the sole-writer boundary.
Execution instead remains a serialized mainline writer while the other four project
Threads provide the parallel research and review lanes. Do not dispatch overlapping
subagents to repeat the same claim or parallelize work whose dependency order is
inherently sequential.

Native subagents are an extra internal layer: they do not become project roles,
app-server project Threads, top-level tasks, or replacements for the five real
Threads, full blueprint, current stage card, Execution authority, or dual acceptance.
When the native tool is unavailable or no suitable parallel read-only work exists, do
not claim or invent subagent activity. The five project Threads still perform the same
work without lowering blueprint detail, evidence, review separation, or repair quality.

## Migrate a Thread only after its third real automatic compaction

This is the user's sole standing authorization for automatic Thread replacement. It
applies independently to an ordinary Panel task Thread and to each of the five real
project-role Threads. Count only real automatic context-compaction events reported by
the Codex app-server for that exact Thread. Do not infer events from token estimates,
create a local counter as a second authority, or trigger migration for an internal
native subagent.

Immediately after that Thread's third reported automatic compaction, use the native
`thread/fork` method to create one replacement. Do not create a local summary, invoke a
manual compact operation, persist chat bodies, replay a copied transcript, or invent a
replacement context. The native fork is the context-transfer mechanism.

Before changing ownership or deleting anything, validate the replacement against the
live app-server result:

- required conversation and task context is present and usable;
- the ordinary-task identity or exact project role is unchanged;
- the selected model is unchanged unless the user already requested a change;
- the capability boundary is unchanged, including sole-writer HIA/HOM/HIP access for
  Execution and empty scene-tool inventories for non-Execution roles;
- the native Goal identity and, when applicable, Panel project membership are exact.

Only after every check succeeds may the lifecycle owner switch Panel/project ownership
to the replacement and precisely delete the one superseded old Thread through the live
native contract. Never delete the Goal, project container, another role, multiple
Threads, or chat history in bulk. If fork, validation, reassociation, or deletion
preconditions fail, retain the old Thread unchanged, expose the exact failure, and do
not claim migration success.

This verified third-compaction migration is the only automatic deletion exception.
Outside it, never automatically delete a Goal project or its Threads. Do not migrate
Ultra or other internal subagents, create a local summarizer, manually compact, persist
chat bodies, or treat ordinary project cleanup as migration.

## Keep the project visible and steerable in Panel

HIA Panel is the user-facing owner of project presentation and guidance. Show one
project container for the Goal, its five project Threads, each role's current supported
model and state, Planning content, active stage card, and returned review evidence when
those values are supplied by the Bridge/app-server. Keep worker
Threads inside the project container rather than adding them to the top-level task
list.

Panel lets the user open any project Thread, change its model for later turns, and add
guidance to that role or to the project. The Bridge routes the instruction to the same
app-server Thread with `turn/start` and preserves role history. Never infer or invent
Thread output, state, model, or project membership that the Bridge did not provide.

The Project Team settings surface uses intuitive outcome wording. The saved default and
the per-submission selector expose only **单个 AI / 项目团队**. Resolve the selected
outcome only for the newly submitted task, then return the Panel selector to the saved
default immediately after send. The one-shot choice never writes back to that default.

Resolve the per-submission choice before complexity. **单个 AI** creates no team for
that new task; **项目团队** creates its native Goal, Panel project, and five real
Threads. Direct, Focused, and Full scale the actual work depth inside the chosen route
and never override it. Never split, merge, or rebuild an already running project
because the persistent default later changes. These settings route a new task; they
are not a Planner, Gate, Goal transition, or quality level. Primary labels must not
expose internal persistence values.

Keep existing ownership boundaries intact: Goal and Focus remain on their established
surfaces; Context Pack remains optional evidence; EffectSpec remains transient; and
project memory changes remain explicit. The project view may link to or display their
public outputs, but must not create duplicate Goal, Focus, Context Pack, EffectSpec, or
memory state machines.

Bridge owns app-server transport, Thread/project association, and reported lifecycle
state. Codex roles own reasoning, blueprint content, execution decisions, and evidence
judgment. Panel owns display and user guidance. Do not create a second roster schema,
planner, status machine, or hidden evidence ledger outside those established owners.

## Reject false complexity

- Do not build or add another Agent, LLM, planner, autonomous RAG system, state machine,
  workflow engine, scheduler, monitoring loop, or local collaboration database.
- Do not encode asset-specific construction recipes in this reference.
- Do not measure quality by lines, nodes, Boxes, network boxes, calls, screenshots,
  reviewers, stages, or iterations.
- Do not turn the required 10,000/2,500/350 information floors into a quality score or
  a reusable generic Gate. They are Houdini Full authorization minimums and remain
  insufficient without task-anchor, structure, anti-repetition/filler, and semantic
  completeness checks.
- Do not confuse completeness with padding, repeated prose, speculative overdesign, or
  features outside the Goal.
- Do not add a second Planner to repair weak plans; return them to the same Planning
  Thread, and do not turn revision into a workflow state machine.
- Do not hide a sparse request behind a generic blockout when a complete asset was
  requested.
- Do send Supervisor the complete blueprint for initial and material-revision
  authorization; do not repeatedly re-paste that authorized artifact into every
  execution or review loop.
- Do not compress the complete current stage card into a short summary.
- Do not let Supervisor, Planning, review, or Ultra internal subagents write the live
  HIP; Execution is the only writer.
- Do not fan out project Threads or live-scene calls.
- Do not discard role history before final evidence and limitations are published.
  Never delete a Goal project or role Thread as cleanup; only the verified
  third-compaction `thread/fork` migration may precisely delete its superseded Thread.
