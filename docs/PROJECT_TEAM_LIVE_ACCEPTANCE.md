# Project-team live acceptance

This checklist separates source and test evidence from evidence produced by an
actual Houdini process. Passing unit tests, an app-server smoke, or a standalone
Qt test does **not** prove that the embedded Houdini Panel, HOM execution, or HIP
write path works.

Run the complete checklist once with each target below. Record the exact build;
do not infer compatibility from the marketing version alone.

| Target | Interpreter expected from Houdini | Required result |
|---|---|---|
| Houdini 21.0.440 | Python 3.11 | Complete all cases |
| Houdini 22.x candidate | Python 3.13 | Complete all cases and record the exact Houdini build |

The repository contains version-specific UI-ready entry points for Python
3.10, 3.11, and 3.13. Their source and mocked startup behavior are tested, but
the Python 3.13 entry point is not evidence of an embedded Houdini 22 pass.

## Evidence record

Create one record per target under the project-local `.runtime` directory. Do
not add credentials, bearer tokens, complete environment dumps, or private HIP
contents. Each record must contain:

- repository commit and dirty-tree status;
- exact `hou.applicationVersionString()` and `sys.version`;
- launcher session ID only if redacted to its last eight characters;
- Bridge and HIA health result, without tokens;
- Project ID, Goal ID, and all five role Thread IDs;
- every tested Turn ID and role;
- Execution HIA tool-item IDs used as technical evidence;
- `hia_capture_viewport` item IDs, requested frames, returned media type, file
  hashes, and resolved safe paths;
- saved/unsaved HIP state for each path test;
- timestamps and observed result for every numbered case;
- defects, stack traces with secrets removed, and whether the case was retried.

Use `passed`, `failed`, `blocked`, or `not_run`. Never replace an unexecuted
case with “expected to pass.”

## Preconditions

1. Use a clean clone or an explicitly recorded review worktree. Keep `.runtime`
   below that project root. Do not copy state from another checkout.
2. Install and license the target Houdini normally. Do not modify its install
   directory or user preferences for this test.
3. Prepare the project-managed runtime through the supported launcher flow.
   Confirm all local services bind only to `127.0.0.1` and that a new launcher
   session receives a fresh authentication token.
4. Keep a disposable test HIP separate from user work. For saved-HIP cases,
   create a writable directory and save a new disposable HIP there. For
   unsaved-HIP cases, start from a new untitled scene and do not save it.
5. Ensure no other project team owns the same Houdini scene. Stop or finish any
   previous test project before beginning another.
6. In Houdini's Python Shell, record without changing the scene:

   ```python
   import hou, sys
   print(hou.applicationVersionString())
   print(sys.version)
   ```

7. Open the Big-Chicken Python Panel and record its Codex, Bridge, Houdini, HIA
   MCP V2, and scene identity indicators. If any required connection is absent,
   mark the integration cases `blocked`; do not substitute a mocked service.

## A. Startup and Panel restart

1. Launch Houdini through the project launcher with HIA MCP V2 selected.
2. Open the Panel, close that pane, then open a fresh Panel instance.
3. Verify the reopened Panel reconnects once, contains no duplicate messages,
   and does not duplicate Qt signal effects when controls are clicked once.
4. Close and reopen the Panel while Bridge remains running. Verify ordinary
   tasks and project containers are still distinct and the selected native
   Thread identity is restored only when it still exists.
5. Close Houdini normally. Relaunch it and repeat steps 1-4. This is the Panel
   restart case; a Python module reload inside the same process is not a
   substitute.

## B. Project creation and permission isolation

1. In a new task submission, explicitly choose `项目团队` and submit a small but
   unmistakable Houdini scene request that requires a visible two-part assembly
   and an editable native node network.
2. Verify the initial native acknowledgement returns without waiting for the
   entire project to finish.
3. Verify the project receives one persisted Project ID and one native Goal ID.
4. After scene eligibility is accepted, verify exactly five persisted native
   role Threads exist: Supervisor, Planning, Execution, Visual Review, and
   Technical Review. Confirm the four non-Execution roles were created lazily,
   not before eligibility.
5. Inspect the actual app-server tool inventories. Execution alone may contain
   `hia_mcp_v2`/HOM/HIP-write capabilities. Supervisor, Planning, Visual Review,
   and Technical Review must expose neither `hia_mcp_v2` nor
   `houdini_intelligence`. A prompt saying “read only” is insufficient.
6. Resume each role Thread once and verify Project/Role/Thread/Goal association
   comes from persisted Bridge identity, not title, cwd, or model name.
7. Change the next-Turn model/settings for one reviewer only. Verify the change
   applies to that role's next Turn, leaves the current Turn untouched, and does
   not change the ordinary-task/new-project defaults.

## C. Real Execution write and evidence delivery

1. Wait for the authorized current stage card. Verify it references stable
   active requirement IDs and the authoritative task ID/hash; it must not depend
   on a character-count threshold.
2. Let Execution perform a real HIA/HOM write in the disposable current HIP.
   Confirm native editable nodes are created and the scene revision changes.
3. Record every completed Execution HIA tool item used as technical evidence.
   Confirm each item belongs to the current Execution Thread and Turn and has a
   successful terminal status.
4. Capture a real viewport image through `hia_capture_viewport`. Confirm the
   evidence binds the real tool item, exact frame, returned image content/type,
   file header, hash, and safe resolved path.
5. Verify Visual Review and Technical Review receive the same stage evidence
   by reference. Each review must identify its evidence IDs; a natural-language
   claim that it saw the result is not sufficient.
6. Verify the two reviewers can run concurrently as read-only work while
   Execution remains the sole serialized scene writer.

## D. Deliberate defect and repair loop

1. Through the authorized Execution route, deliberately create one visible and
   measurable defect in the disposable asset: either make one part penetrate a
   host surface or leave a required support visibly suspended. Record this as a
   test-only defect in the evidence record.
2. Capture a fresh viewport image and deterministic geometry/relationship
   evidence that demonstrates the defect.
3. Verify at least one reviewer marks the affected requirement failed or
   unverified, references the real evidence, and describes the minimum repair.
   The stage must not pass merely because a review-turn budget was reached.
4. Verify Supervisor issues a repair instruction tied to the failed requirement
   and evidence. It must not write the HIP itself.
5. Let Execution make the minimum repair. Verify a new Execution Turn and new
   successful HIA tool items are used; stale tool items from the defective Turn
   must be rejected as proof of the repair.
6. Capture a new image and recompute technical evidence. Confirm the new image
   hash differs when a visible repair is claimed and the contact/clearance
   measurement now passes.
7. Run both reviews again. Complete the stage only after both independently cite
   the new evidence and the lifecycle reaches a legal accepted transition.

## E. Guidance during execution

1. While a project is active, append a project-level instruction that changes a
   visible requirement. Verify it receives a guidance version and reaches every
   applicable role before project completion.
2. Append a role-specific instruction to Visual Review. Verify it reaches only
   that role plus Supervisor's coordination view as designed.
3. Issue a later instruction that removes or reduces an earlier requirement
   (for example, remove an animation or simplify a secondary detail). Verify the
   old requirement becomes `removed_by_user` or `superseded_by_user`, a shorter
   valid stage card is accepted, and the removed work no longer blocks finish.
4. Append guidance while a role Turn is active. Verify it is preserved and
   consumed by the next applicable role Turn rather than falling through to an
   ordinary unrelated conversation.

## F. Stop and bounded-attention behavior

1. Start a stage, press Stop, and verify no new Execution write Turn starts.
   Already-entered UI-thread HOM may finish; record that distinction.
2. Verify the project/Goal becomes paused or otherwise explicitly stoppable and
   the Panel does not claim completion.
3. Resume only through the supported user action and verify permissions and role
   identities have not drifted.
4. In a separate disposable run, trigger one configured no-progress or budget
   limit. Verify the lifecycle enters `needs_attention`, exposes the reason,
   consumed turns, recent error/evidence/repair, and offers continue, revised
   guidance, or stop. It must not auto-pass.

## G. Bridge restart recovery

1. With a project active but no HIA/HOM call in flight, stop only the Bridge by
   the supported lifecycle control and start it again. Do not kill Houdini.
2. Reopen/refresh the Panel. Verify persisted Project/Goal/role identities,
   current stage, guidance versions, budgets, and Execution ownership recover.
3. Resume each role and confirm its original permission profile. In particular,
   no read-only role may gain HIA inventories after restart.
4. Append guidance after recovery and verify it is recorded once without an HTTP
   timeout or duplicate ordinary Turn.
5. Complete one further real Execution/review transition to prove recovery is
   operational rather than a read-only snapshot.

## H. Saved and unsaved HIP capture paths

Run both subcases and record the exact resolved capture path:

1. **Saved HIP:** save the disposable HIP in a normal writable directory. A
   current-scene viewport capture may use `<hip-parent>/.hia/screenshots` after
   path safety checks. Verify the path, file header, and evidence binding.
2. **Unsaved HIP:** create a new untitled scene and do not save. Capture the
   viewport and verify it falls back below
   `<project-root>/.runtime/cache/screenshots` (or the current documented
   project-runtime capture root), never a guessed HIP-relative path.
3. Also test a deliberately unsafe/unwritable saved location if a safe disposable
   fixture is available. It must use the project-runtime fallback or return a
   clear failure; it must not write outside the allowed roots.

## I. HIA disconnect

1. With no write in flight, stop HIA MCP V2 while keeping Bridge and Houdini
   open.
2. Submit a read-only status check and then a stage that would require a scene
   write. Verify the disconnected state is explicit and no unknown HOM is run.
3. Confirm reviewers cannot manufacture evidence from model text or old tool
   items while HIA is disconnected.
4. Restore HIA through the supported launcher/runtime lifecycle. Verify session,
   PID, module, executor identity, and scene binding before allowing a new write.
5. Perform one fresh read-only call and one fresh capture. Do not reuse evidence
   from before the disconnect as proof of the restored state.

## J. Thread transfer

1. Enable transfer for a disposable project and trigger the configured native
   migration threshold for one idle role Thread. Do not migrate an internal
   native subagent.
2. Verify forked Thread context, role, model/settings, read/write inventory,
   Project ID, and Goal ID before commit.
3. Confirm the Panel selection changes to the replacement identity only after a
   successful commit. Then verify only the superseded native Thread is deleted.
4. Inject a fork/verification failure in a separate run. Confirm rollback keeps
   the old Thread and project usable; normal project completion must not depend
   on migration success.
5. Restart Bridge after a prepared-but-uncommitted transfer and verify recovery
   chooses a deterministic safe state without duplicating role membership.

## Completion rule

A target is embedded-Houdini tested only when all applicable cases A-J have
recorded passing evidence from that exact Houdini/Python build. Unit tests,
mocked app-server tests, standalone Qt tests, and source compilation remain
separate evidence levels. If Houdini 22/Python 3.13 has not completed this list,
describe it as source/CI-covered and **embedded unverified**.
