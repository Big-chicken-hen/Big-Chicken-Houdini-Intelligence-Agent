from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import threading
import unittest

from services.bridge.hia_bridge.project_artifacts import ProjectArtifactStore
from services.bridge.hia_bridge.project_contracts import (
    PendingEffect,
    ProjectState,
    ProjectStatus,
    Role,
    RoleThread,
    RuntimeBudget,
    authoritative_task_identity,
)
from services.bridge.hia_bridge.project_effects import (
    CompletedTurn,
    ProjectEffectExecutor,
)
from services.bridge.hia_bridge.project_guidance import publish_guidance
from services.bridge.hia_bridge.project_lifecycle import ProjectEvent, reduce_project
from services.bridge.hia_bridge.project_registry import ProjectRecord, ProjectRegistry
from services.bridge.hia_bridge.project_runner import _pending_effect
from services.bridge.hia_bridge.project_thread_factory import ProjectThreadFactory


def _full_stage() -> dict:
    return {
        "depth": "full",
        "stage_id": "stage-1",
        "requirement_ids": ["req-1"],
        "ordered_steps": [{"step_id": "step-1", "operation": "build"}],
        "evidence_contract": {"capture": True, "technical": True},
        "reviewers": ["visual_review", "technical_review"],
        "failure_minimum_repair": "repair only the observed defect",
    }


def _plan() -> dict:
    return {
        "schema": "hia-project-plan/1",
        "requirements": [
            {"requirement_id": "req-1", "kind": "hard_constraint", "source_ref": "task-ref"}
        ],
        "stages": [_full_stage()],
    }


def _claim(disposition: str = "verified") -> dict:
    if disposition == "verified":
        return {
            "disposition": "verified",
            "claim_id": "claim-1",
            "evidence_refs": ["tool-validate", "tool-capture"],
            "actual_evidence": "structured evidence inspected",
        }
    return {
        "disposition": "failed",
        "claim_id": "claim-1",
        "evidence_refs": ["tool-validate", "tool-capture"],
        "deviation": "observed structural defect",
        "minimum_repair": "correct the observed contact relationship",
    }


class FakeProjectClient:
    def __init__(self, capture: Path) -> None:
        self.capture = capture
        self.calls: list[tuple[str, dict]] = []
        self.turns: dict[str, tuple[str, str, dict]] = {}
        self.responses: dict[tuple[str, str], list[dict]] = {}
        self.wait_barrier: threading.Barrier | None = None
        self.on_wait = None
        self.timeout_actions: set[str] = set()
        self._turn_number = 0
        self._lock = threading.Lock()

    def queue(self, role: Role, action: str, *payloads: dict) -> None:
        self.responses.setdefault((role.value, action), []).extend(payloads)

    def request(self, method, params):
        with self._lock:
            self.calls.append((method, dict(params)))
            if method == "thread/start":
                role = params["threadSource"].rsplit("/", 1)[-1]
                return {"thread": {"id": f"thread-{role}"}, "model": params.get("model")}
            if method == "turn/start":
                self._turn_number += 1
                turn_id = f"turn-{self._turn_number}"
                envelope = json.loads(params["input"][0]["text"])
                thread_id = params["threadId"]
                role = thread_id.removeprefix("thread-")
                self.turns[turn_id] = (thread_id, role, envelope)
                return {"turn": {"id": turn_id}}
            if method == "thread/goal/set":
                return {
                    "goal": {
                        "threadId": params["threadId"],
                        "status": params["status"],
                    }
                }
            if method == "thread/delete":
                return {"deleted": True}
        raise AssertionError(method)

    def wait_for_turn(self, thread_id, turn_id, timeout_seconds):
        if timeout_seconds <= 0:
            raise TimeoutError("expired")
        stored_thread, role, envelope = self.turns[turn_id]
        if stored_thread != thread_id:
            raise AssertionError("wrong thread")
        action = envelope["action"]
        if action in self.timeout_actions:
            raise TimeoutError("simulated timeout")
        if self.wait_barrier is not None and action == "review_stage":
            self.wait_barrier.wait(timeout=2)
        callback = self.on_wait
        if callback is not None:
            callback(role, action, envelope)
        with self._lock:
            payload = self.responses[(role, action)].pop(0)
        events = ()
        if action in {"execute_stage", "execute_repair"}:
            events = self._execution_events(thread_id, turn_id)
        return CompletedTurn(
            thread_id=thread_id,
            turn_id=turn_id,
            status="completed",
            payload=payload,
            events=events,
            elapsed_seconds=1,
        )

    def _execution_events(self, thread_id: str, turn_id: str):
        def event(item_id, tool, structured):
            return {
                "method": "item/completed",
                "params": {
                    "threadId": thread_id,
                    "turnId": turn_id,
                    "item": {
                        "id": item_id,
                        "type": "mcpToolCall",
                        "server": "hia_mcp_v2",
                        "tool": tool,
                        "status": "completed",
                        "arguments": {},
                        "result": {"structuredContent": structured, "content": []},
                    },
                },
            }

        return (
            event("tool-validate", "hia_validate", {"ok": True, "result": {"valid": True}}),
            event(
                "tool-capture",
                "hia_capture_viewport",
                {
                    "ok": True,
                    "result": {
                        "absolute_path": str(self.capture),
                        "requested_frame": 1,
                        "actual_frame": 1,
                        "quality_frame": 1,
                    },
                },
            ),
        )


class EffectHarness:
    def __init__(self, root: Path, client: FakeProjectClient, budget=None) -> None:
        self.registry = ProjectRegistry(root / "projects.json")
        self.artifacts = ProjectArtifactStore(root / "artifacts.json")
        task_id, task_hash = authoritative_task_identity("build a Houdini asset")
        state = ProjectState(
            project_id="project-1",
            goal_thread_id="thread-supervisor",
            authoritative_task_id=task_id,
            authoritative_task_sha256=task_hash,
            status=ProjectStatus.INTAKE,
            roles={Role.SUPERVISOR: RoleThread(Role.SUPERVISOR, "thread-supervisor")},
            budget=budget or RuntimeBudget(),
        )
        self.state = self._with_effect(state, "start_intake")
        self.registry.put(ProjectRecord(self.state, "build a Houdini asset"))
        self.executor = ProjectEffectExecutor(
            client=client,
            registry=self.registry,
            thread_factory=ProjectThreadFactory(client, root),
            artifacts=self.artifacts,
            allowed_evidence_roots=[root],
            total_timeout_seconds=5,
        )

    def run_one(self):
        effect = self.state.pending_effects[0]
        result = self.executor.execute(self.state, effect)
        base = replace(result.state, pending_effects=self.state.pending_effects[1:])
        if result.event is None:
            next_state = base
        else:
            next_state, commands = reduce_project(base, result.event)
            effects = tuple(
                _pending_effect(next_state, index, command.kind.value, command.data or {})
                for index, command in enumerate(commands, start=len(base.pending_effects))
            )
            next_state = replace(next_state, pending_effects=(*base.pending_effects, *effects))
        self.state = next_state
        self.registry.put(ProjectRecord(self.state, "build a Houdini asset"))
        return result

    def run_to_terminal(self, limit=30):
        for _ in range(limit):
            if not self.state.pending_effects:
                return self.state
            self.run_one()
        raise AssertionError("effect chain did not terminate")

    @staticmethod
    def _with_effect(state, kind, data=None):
        effect = PendingEffect(f"effect-{kind}", kind, data or {})
        return replace(state, pending_effects=(effect,))


def _execution_payload():
    return {
        "schema": "hia-project-execution/1",
        "stage_id": "stage-1",
        "evidence_refs": [
            {"item_id": "tool-validate"},
            {"item_id": "tool-capture", "frame": 1},
        ],
        "claims_visual_change": True,
    }


def _review_payload(role: Role, disposition="verified"):
    return {
        "schema": "hia-project-review/1",
        "stage_id": "stage-1",
        "reviewer": role.value,
        "claims": [_claim(disposition)],
    }


def _decision(decision="pass"):
    repair = None
    if decision == "repair":
        repair = {
            "stage_id": "stage-1",
            "defect_hash": "defect-1",
            "instructions": [{"operation": "fix_contact", "target": "part-a"}],
            "evidence_refs": ["tool-validate", "tool-capture"],
        }
    return {
        "schema": "hia-project-stage-decision/1",
        "stage_id": "stage-1",
        "decision": decision,
        "final_stage": True,
        "repair_card": repair,
    }


class ProjectEffectExecutorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.capture = self.root / "capture.png"
        self.capture.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 32)
        self.client = FakeProjectClient(self.capture)

    def tearDown(self):
        self.temp.cleanup()

    def _queue_common(self):
        self.client.queue(
            Role.SUPERVISOR,
            "scene_task_eligibility",
            {"schema": "hia-project-eligibility/1", "disposition": "eligible", "reason": "scene task"},
        )
        self.client.queue(Role.PLANNING, "create_plan_and_stage_cards", _plan())
        self.client.queue(
            Role.SUPERVISOR,
            "authorize_plan",
            {
                "schema": "hia-project-authorization/1",
                "authorized": True,
                "stage_ids": ["stage-1"],
            },
        )

    def test_single_stage_pass_reaches_goal_complete_with_exact_ownership(self):
        self._queue_common()
        self.client.queue(Role.EXECUTION, "execute_stage", _execution_payload())
        self.client.queue(Role.VISUAL_REVIEW, "review_stage", _review_payload(Role.VISUAL_REVIEW))
        self.client.queue(
            Role.TECHNICAL_REVIEW, "review_stage", _review_payload(Role.TECHNICAL_REVIEW)
        )
        self.client.queue(Role.SUPERVISOR, "decide_stage_review", _decision())
        self.client.wait_barrier = threading.Barrier(2)
        harness = EffectHarness(self.root, self.client)
        state = harness.run_to_terminal()
        self.assertEqual(ProjectStatus.COMPLETED, state.status)
        self.assertEqual(set(Role), set(state.roles))
        starts = [params for method, params in self.client.calls if method == "turn/start"]
        eligibility = next(
            item
            for item in starts
            if json.loads(item["input"][0]["text"])["action"] == "scene_task_eligibility"
        )
        self.assertEqual("low", eligibility["effort"])
        self.assertEqual(
            0,
            json.loads(eligibility["input"][0]["text"])["native_subagent_budget"],
        )
        execution = next(item for item in starts if item["threadId"] == "thread-execution")
        self.assertEqual("workspaceWrite", execution["sandboxPolicy"]["type"])
        for item in starts:
            if item["threadId"] != "thread-execution":
                self.assertEqual("readOnly", item["sandboxPolicy"]["type"])
        goals = [params for method, params in self.client.calls if method == "thread/goal/set"]
        self.assertEqual("completed", goals[-1]["status"])
        self.assertEqual("thread-supervisor", goals[-1]["threadId"])

    def test_failed_reviews_drive_supervisor_repair_and_execution_repair(self):
        self._queue_common()
        self.client.queue(Role.EXECUTION, "execute_stage", _execution_payload())
        self.client.queue(Role.VISUAL_REVIEW, "review_stage", _review_payload(Role.VISUAL_REVIEW, "failed"))
        self.client.queue(
            Role.TECHNICAL_REVIEW, "review_stage", _review_payload(Role.TECHNICAL_REVIEW)
        )
        self.client.queue(Role.SUPERVISOR, "decide_stage_review", _decision("repair"), _decision())
        # The second review round passes.
        self.client.queue(Role.VISUAL_REVIEW, "review_stage", _review_payload(Role.VISUAL_REVIEW))
        self.client.queue(
            Role.TECHNICAL_REVIEW, "review_stage", _review_payload(Role.TECHNICAL_REVIEW)
        )
        harness = EffectHarness(self.root, self.client)
        # Run through the failed decision so the generated repair hash is available.
        while harness.state.status is not ProjectStatus.REPAIRING_STAGE:
            harness.run_one()
        repair = harness.artifacts.get_named("project-1", "repair_card")
        self.client.queue(
            Role.SUPERVISOR,
            "authorize_minimum_repair",
            {
                "schema": "hia-project-repair-authorization/1",
                "stage_id": "stage-1",
                "authorized": True,
                "repair_hash": repair["repair_hash"],
            },
        )
        self.client.queue(Role.EXECUTION, "execute_repair", _execution_payload())
        state = harness.run_to_terminal()
        self.assertEqual(ProjectStatus.COMPLETED, state.status)
        actions = [
            json.loads(params["input"][0]["text"])["action"]
            for method, params in self.client.calls
            if method == "turn/start"
        ]
        self.assertIn("authorize_minimum_repair", actions)
        self.assertIn("execute_repair", actions)
        self.assertEqual(1, state.stage.repair_count)

    def test_ineligible_creates_no_worker_threads_and_pauses_goal(self):
        self.client.queue(
            Role.SUPERVISOR,
            "scene_task_eligibility",
            {
                "schema": "hia-project-eligibility/1",
                "disposition": "ineligible",
                "reason": "not a scene task",
            },
        )
        harness = EffectHarness(self.root, self.client)
        state = harness.run_to_terminal()
        self.assertEqual(ProjectStatus.NOT_APPLICABLE, state.status)
        self.assertEqual({Role.SUPERVISOR}, set(state.roles))
        self.assertFalse(any(method == "thread/start" for method, _ in self.client.calls))
        goal = [params for method, params in self.client.calls if method == "thread/goal/set"][-1]
        self.assertEqual("paused", goal["status"])

    def test_repeated_schema_failure_exhausts_budget_and_never_passes(self):
        invalid = {"schema": "hia-project-eligibility/1", "disposition": "eligible"}
        self.client.queue(Role.SUPERVISOR, "scene_task_eligibility", invalid, invalid, invalid)
        budget = RuntimeBudget(max_schema_corrections=2)
        harness = EffectHarness(self.root, self.client, budget)
        result = harness.run_one()
        self.assertEqual(ProjectEvent.BUDGET_EXHAUSTED, result.event.kind)
        self.assertEqual("max_schema_corrections", result.event.data["reason"])
        self.assertNotEqual(ProjectStatus.PROVISIONING_ROLES, harness.state.status)

    def test_repeated_repairs_with_no_new_evidence_enter_needs_attention(self):
        self._queue_common()
        self.client.queue(Role.EXECUTION, "execute_stage", _execution_payload())
        for _ in range(3):
            self.client.queue(
                Role.VISUAL_REVIEW,
                "review_stage",
                _review_payload(Role.VISUAL_REVIEW, "failed"),
            )
            self.client.queue(
                Role.TECHNICAL_REVIEW,
                "review_stage",
                _review_payload(Role.TECHNICAL_REVIEW),
            )
            self.client.queue(Role.SUPERVISOR, "decide_stage_review", _decision("repair"))
        harness = EffectHarness(self.root, self.client)
        while harness.state.status is not ProjectStatus.REPAIRING_STAGE:
            harness.run_one()
        repair = harness.artifacts.get_named("project-1", "repair_card")
        for _ in range(3):
            self.client.queue(
                Role.SUPERVISOR,
                "authorize_minimum_repair",
                {
                    "schema": "hia-project-repair-authorization/1",
                    "stage_id": "stage-1",
                    "authorized": True,
                    "repair_hash": repair["repair_hash"],
                },
            )
            self.client.queue(Role.EXECUTION, "execute_repair", _execution_payload())
        for _ in range(20):
            if harness.state.status is ProjectStatus.NEEDS_ATTENTION:
                break
            harness.run_one()
        self.assertEqual(ProjectStatus.NEEDS_ATTENTION, harness.state.status)
        self.assertIn("repair_added_no_new_evidence", harness.state.attention_reason)
        self.assertNotEqual(ProjectStatus.COMPLETED, harness.state.status)

    def test_timeout_interrupts_without_provisioning_workers(self):
        self.client.timeout_actions.add("scene_task_eligibility")
        harness = EffectHarness(self.root, self.client)
        result = harness.run_one()
        self.assertEqual(ProjectEvent.PROJECT_INTERRUPTED, result.event.kind)
        self.assertEqual(ProjectStatus.PAUSING, harness.state.status)
        harness.run_one()
        self.assertEqual(ProjectStatus.INTERRUPTED, harness.state.status)
        self.assertEqual({Role.SUPERVISOR}, set(harness.state.roles))

    def test_late_guidance_is_consumed_in_a_revision_turn_before_plan_is_used(self):
        self._queue_common()
        self.client.queue(Role.PLANNING, "create_plan_and_stage_cards", _plan())
        harness = EffectHarness(self.root, self.client)
        # Intake and worker provisioning.
        harness.run_one()
        harness.run_one()
        injected = False

        def on_wait(role, action, envelope):
            nonlocal injected
            if role == Role.PLANNING.value and action == "create_plan_and_stage_cards" and not injected:
                injected = True
                record = harness.registry.require("project-1")
                updated = publish_guidance(record.state, "remove the obsolete animation stage", target_role=Role.PLANNING)
                harness.registry.put(ProjectRecord(updated, record.authoritative_task_text))

        self.client.on_wait = on_wait
        harness.run_one()
        planning_starts = [
            json.loads(params["input"][0]["text"])
            for method, params in self.client.calls
            if method == "turn/start" and params["threadId"] == "thread-planning"
        ]
        self.assertEqual(2, len(planning_starts))
        self.assertEqual([], planning_starts[0]["guidance"])
        self.assertEqual(
            "remove the obsolete animation stage",
            planning_starts[1]["guidance"][0]["text"],
        )
        self.assertEqual(1, harness.state.guidance_consumed[Role.PLANNING])

    def test_advance_stage_returns_exact_state_only_effect_result(self):
        harness = EffectHarness(self.root, self.client)
        plan = _plan()
        second = dict(_full_stage())
        second["stage_id"] = "stage-2"
        plan["stages"].append(second)
        harness.artifacts.put_named("project-1", "plan", plan)
        state = replace(
            harness.state,
            status=ProjectStatus.EXECUTING_STAGE,
            stage=replace(harness.state.stage, stage_id="stage-1", ordinal=1, repair_count=4),
            pending_effects=(PendingEffect("effect-advance", "advance_stage", {}),),
        )
        result = harness.executor.execute(state, state.pending_effects[0])
        self.assertIsNone(result.event)
        self.assertEqual("stage-2", result.state.stage.stage_id)
        self.assertEqual(2, result.state.stage.ordinal)
        self.assertEqual(0, result.state.stage.repair_count)

    def test_advance_stage_without_next_card_fails_explicitly(self):
        harness = EffectHarness(self.root, self.client)
        harness.artifacts.put_named("project-1", "plan", _plan())
        state = replace(
            harness.state,
            status=ProjectStatus.EXECUTING_STAGE,
            stage=replace(harness.state.stage, stage_id="stage-1", ordinal=1),
            pending_effects=(PendingEffect("effect-advance", "advance_stage", {}),),
        )
        with self.assertRaisesRegex(Exception, "no exact next stage"):
            harness.executor.execute(state, state.pending_effects[0])

    def test_execution_rejects_native_subagents_even_below_generic_budget(self):
        self._queue_common()
        self.client.queue(Role.EXECUTION, "execute_stage", _execution_payload())
        harness = EffectHarness(self.root, self.client)
        harness.run_one()
        harness.run_one()
        harness.run_one()
        harness.run_one()
        original_wait = self.client.wait_for_turn

        def wait_with_subagent(thread_id, turn_id, timeout_seconds):
            result = original_wait(thread_id, turn_id, timeout_seconds)
            return replace(result, native_subagents=1)

        self.client.wait_for_turn = wait_with_subagent
        with self.assertRaisesRegex(Exception, "cannot delegate"):
            harness.run_one()


if __name__ == "__main__":
    unittest.main()
