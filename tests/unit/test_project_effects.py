from __future__ import annotations

import copy
from dataclasses import replace
import hashlib
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
    Requirement,
    RequirementStatus,
    Role,
    RoleThread,
    RuntimeBudget,
    TurnState,
    authoritative_task_identity,
)
from services.bridge.hia_bridge.project_effects import (
    CompletedTurn,
    ProjectEffectError,
    ProjectEffectExecutor,
)
from services.bridge.hia_bridge.project_guidance import RequirementDelta, publish_guidance
from services.bridge.hia_bridge.project_lifecycle import (
    LifecycleEvent,
    ProjectEvent,
    reduce_project,
)
from services.bridge.hia_bridge.project_payloads import FULL_BLUEPRINT_SECTION_IDS
from services.bridge.hia_bridge.project_registry import (
    ProjectAttachment,
    ProjectRecord,
    ProjectRegistry,
)
from services.bridge.hia_bridge.project_runner import _pending_effect
from services.bridge.hia_bridge.project_thread_factory import ProjectThreadFactory
from tests.unit.project_test_support import observable_thread_response, server_transports


def _detail(label: str, size: int) -> str:
    actors = "builder reviewer designer operator artist rigger architect modeler engineer craftsperson supervisor specialist technician planner fabricator inspector author coordinator animator researcher developer".split()
    actions = "connects measures shapes aligns verifies documents constructs balances refines inspects positions configures preserves compares assembles routes exposes tests records evaluates repairs".split()
    targets = "roof frame support surface control output facade railing window doorway foundation stair canopy chassis cabin material camera light geometry network profile joint panel".split()
    reasons = "clearance editability silhouette contact evidence delivery proportion spacing continuity stability recognition dependency alignment accuracy hierarchy accessibility consistency readability integrity provenance".split()
    qualities = "precise modular coherent readable durable procedural reversible measurable visible native bounded layered proportional connected stable explicit organized responsive clean consistent".split()
    methods = "sweeping extruding beveling grouping routing sampling comparing capturing inspecting naming binding transforming merging filtering projecting validating".split()
    contexts = "object geometry material lighting animation simulation rendering viewport timeline hierarchy branch network interface output reference assembly".split()
    value = ""
    index = 0
    seed = sum(ord(character) for character in label)
    while len(value) < size:
        actor = actors[(seed + index * 5) % len(actors)]
        action = actions[(seed + index * 7) % len(actions)]
        target = targets[(seed + index * 11) % len(targets)]
        reason = reasons[(seed + index * 13) % len(reasons)]
        quality = qualities[(seed + index * 17) % len(qualities)]
        method = methods[(seed + index * 19) % len(methods)]
        context = contexts[(seed + index * 23) % len(contexts)]
        value += (
            f"{label} the {actor} {action} the editable {target} with native nodes "
            f"through a {quality} {method} method inside the {context} context so the "
            f"measured {reason} remains visible in the review evidence at "
            f"/obj/asset/{actor}_{action}_{target}_{reason}. "
        )
        index += 1
    return value[:size]


def _full_stage() -> dict:
    return {
        "depth": "full",
        "stage_id": "stage-1",
        "requirement_ids": ["req-1"],
        "ordered_steps": [
            {
                "step_id": "step-1",
                "dependencies": [],
                "requirement_ids": ["req-1"],
                "user_fact_ids": ["fact-1"],
                "target_network_region": {
                    "context": "SOP",
                    "target": _detail("networktarget", 2500),
                },
                "native_operation_strategy": {
                    "native_nodes": ["polyextrude", "sweep"],
                    "operation": _detail("nativeoperation", 2500),
                },
                "connections": [
                    {"from": "source/0", "to": "body/0", "purpose": _detail("connection", 2500)}
                ],
                "parameter_dependencies": [
                    {"parameter": "body/height", "depends_on": "fact-1", "effect": _detail("parameter", 2500)}
                ],
                "expected_result": {"visible": _detail("visible", 2500), "editable": _detail("editable", 2500)},
                "evidence": {"visual": _detail("visualevidence", 2500), "technical": _detail("technicalevidence", 2500)},
                "minimum_repair": {"trigger": _detail("repairtrigger", 2500), "operation": _detail("repairoperation", 2500)},
            }
        ],
        "evidence_contract": {"capture": True, "technical": True},
        "reviewers": ["visual_review", "technical_review"],
        "failure_minimum_repair": "repair only the observed defect",
    }


def _plan(anchor: str | None = None) -> dict:
    if anchor is None:
        task_id, _ = authoritative_task_identity("build a Houdini asset")
        anchor = f"task:{task_id}"
    return {
        "schema": "hia-project-plan/1",
        "task_description": {
            "description": _detail("taskdescription", 2500),
            "source_anchors": [anchor],
        },
        "user_facts": [
            {"fact_id": "fact-1", "description": _detail("userfact", 2500), "source_anchor": anchor}
        ],
        "blueprint_sections": [
            {
                "section_id": section_id,
                "title": section_id.replace("_", " "),
                "description": _detail(f"blueprint section {section_id}", 3000),
                "source_anchors": [anchor],
                "user_fact_ids": ["fact-1"],
                "requirement_ids": ["req-1"],
                "stage_ids": ["stage-1"],
            }
            for section_id in FULL_BLUEPRINT_SECTION_IDS
        ],
        "requirements": [
            {
                "requirement_id": "req-1",
                "kind": "hard_constraint",
                "description": "Preserve the complete authoritative task constraint",
                "source_ref": anchor,
                "user_fact_ids": ["fact-1"],
            }
        ],
        "stages": [_full_stage()],
    }


def _direct_plan(anchor: str | None = None) -> dict:
    plan = copy.deepcopy(_plan(anchor))
    plan["blueprint_sections"] = plan["blueprint_sections"][:1]
    stage = plan["stages"][0]
    stage["depth"] = "direct"
    stage.pop("evidence_contract")
    stage.pop("reviewers")
    stage.pop("failure_minimum_repair")
    return plan


def _plan_with_two_requirements() -> dict:
    plan = copy.deepcopy(_plan())
    plan["requirements"].append(
        {
            "requirement_id": "req-2",
            "kind": "visual_quality",
            "description": "Preserve the second independently reviewable constraint",
            "source_ref": plan["task_description"]["source_anchors"][0],
            "user_fact_ids": ["fact-1"],
        }
    )
    for section in plan["blueprint_sections"]:
        section["requirement_ids"].append("req-2")
    stage = plan["stages"][0]
    stage["requirement_ids"].append("req-2")
    stage["ordered_steps"][0]["requirement_ids"].append("req-2")
    return plan


def _authorization(
    plan: dict | None = None,
    revision: int = 1,
    *,
    failed_key: str | None = None,
) -> dict:
    plan = plan or _plan()
    digest = hashlib.sha256(
        json.dumps(
            plan,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    stage_ids = [str(stage["stage_id"]) for stage in plan["stages"]]
    step_ids = [
        str(step["step_id"])
        for stage in plan["stages"]
        for step in stage["ordered_steps"]
    ]
    anchors = set(plan["task_description"]["source_anchors"])
    anchors.update(str(item["source_anchor"]) for item in plan["user_facts"])
    references = {
        "task_anchors": anchors,
        "blueprint_sections": {
            str(item["section_id"]) for item in plan["blueprint_sections"]
        },
        "requirements": {
            str(item["requirement_id"]) for item in plan["requirements"]
        },
        "stages_and_steps": set((*stage_ids, *step_ids)),
        "anti_filler": {f"blueprint:{revision}", digest},
        "native_strategy_and_dependencies": set(step_ids),
        "evidence_contracts": set((*stage_ids, *step_ids)),
        "minimum_repairs": set((*stage_ids, *step_ids)),
    }
    semantic_review = {
        name: {
            "status": "fail" if name == failed_key else "pass",
            "referenced_ids": sorted(identifiers),
            "findings": (
                [f"{name} does not meet the task-specific semantic contract"]
                if name == failed_key
                else []
            ),
        }
        for name, identifiers in references.items()
    }
    return {
        "schema": "hia-project-authorization/1",
        "authorized": True,
        "blueprint_revision": revision,
        "blueprint_sha256": digest,
        "stage_ids": stage_ids,
        "semantic_review": semantic_review,
    }


def _claim(disposition: str = "verified", claim_id: str = "req-1") -> dict:
    if disposition == "verified":
        return {
            "disposition": "verified",
            "claim_id": claim_id,
            "evidence_refs": ["tool-validate", "tool-capture"],
            "actual_evidence": "structured evidence inspected",
        }
    return {
        "disposition": "failed",
        "claim_id": claim_id,
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
        self.goal_ack_override: dict | None = None
        self.goal_error: Exception | None = None
        self._turn_number = 0
        self._lock = threading.Lock()

    def queue(self, role: Role, action: str, *payloads: dict) -> None:
        self.responses.setdefault((role.value, action), []).extend(payloads)

    def request(self, method, params):
        with self._lock:
            self.calls.append((method, dict(params)))
            if method == "thread/start":
                role = params["threadSource"].rsplit("/", 1)[-1]
                return observable_thread_response(params, f"thread-{role}")
            if method == "turn/start":
                self._turn_number += 1
                turn_id = f"turn-{self._turn_number}"
                envelope = json.loads(params["input"][0]["text"])
                thread_id = params["threadId"]
                role = thread_id.removeprefix("thread-")
                self.turns[turn_id] = (thread_id, role, envelope)
                return {"turn": {"id": turn_id}}
            if method == "thread/goal/set":
                if self.goal_error is not None:
                    raise self.goal_error
                if self.goal_ack_override is not None:
                    return {"goal": dict(self.goal_ack_override)}
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
        payload_error_code = None
        payload_error_message = None
        if isinstance(payload, dict) and "__payload_error__" in payload:
            payload_error_code = str(payload["__payload_error__"])
            payload_error_message = "simulated terminal payload error"
            payload = {}
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
            payload_error_code=payload_error_code,
            payload_error_message=payload_error_message,
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
                        "mode": "viewport",
                        "width": 640,
                        "height": 360,
                        "source_state": {
                            "camera": {"path": "/obj/review_cam"},
                            "viewport": {"name": "persp1"},
                        },
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
            thread_factory=ProjectThreadFactory(
                client,
                root,
                "hia_mcp_v2",
                server_transports(),
            ),
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
        current = self.registry.require(self.state.project_id)
        self.registry.put(
            ProjectRecord(
                self.state,
                "build a Houdini asset",
                current.attachments,
            )
        )
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


def _review_payload(role: Role, disposition="verified", requirement_ids=("req-1",)):
    return {
        "schema": "hia-project-review/1",
        "stage_id": "stage-1",
        "reviewer": role.value,
        "claims": [_claim(disposition, requirement_id) for requirement_id in requirement_ids],
    }


def _not_applicable_review(role: Role, requirement_id="req-1") -> dict:
    return {
        "schema": "hia-project-review/1",
        "stage_id": "stage-1",
        "reviewer": role.value,
        "claims": [
            {
                "disposition": "not_applicable",
                "claim_id": requirement_id,
                "exemption": {
                    "reason": "the stage requirement is demonstrably outside this review surface",
                    "evidence_refs": ["tool-validate", "tool-capture"],
                },
            }
        ],
    }


def _decision(decision="pass", approved_exemptions=None):
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
        "approved_exemptions": list(approved_exemptions or ()),
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
            _authorization(),
        )

    def test_recovered_active_goal_is_held_before_oldest_effect_turn(self):
        self.client.queue(
            Role.SUPERVISOR,
            "scene_task_eligibility",
            {
                "schema": "hia-project-eligibility/1",
                "disposition": "eligible",
                "reason": "requires the live scene",
            },
        )
        harness = EffectHarness(self.root, self.client)

        harness.run_one()

        goal_index = next(
            index
            for index, (method, params) in enumerate(self.client.calls)
            if method == "thread/goal/set" and params["status"] == "paused"
        )
        turn_index = next(
            index
            for index, (method, _params) in enumerate(self.client.calls)
            if method == "turn/start"
        )
        self.assertLess(goal_index, turn_index)

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
        visual = next(item for item in starts if item["threadId"] == "thread-visual_review")
        technical = next(
            item for item in starts if item["threadId"] == "thread-technical_review"
        )
        self.assertEqual(
            [{"type": "localImage", "path": str(self.capture.resolve())}],
            visual["input"][1:],
        )
        self.assertEqual(["text"], [entry["type"] for entry in technical["input"]])
        stored_capture = next(
            item
            for item in harness.artifacts.get_named("project-1", "current_execution")[
                "evidence"
            ]
            if item["tool"] == "hia_capture_viewport"
        )
        self.assertEqual("tool-capture", stored_capture["item_id"])
        self.assertEqual(1.0, stored_capture["frame"])
        self.assertEqual([str(self.capture.resolve())], stored_capture["artifact_paths"])
        self.assertEqual("persp1", stored_capture["view"]["viewport"]["name"])
        for item in starts:
            if item["threadId"] != "thread-execution":
                self.assertEqual("readOnly", item["sandboxPolicy"]["type"])
            envelope = json.loads(item["input"][0]["text"])
            if envelope["action"] in {
                "scene_task_eligibility",
                "create_plan_and_stage_cards",
                "authorize_plan",
            }:
                capsule = envelope["authoritative_task"]
                self.assertEqual("hia-authoritative-task/1", capsule["schema"])
                self.assertEqual("build a Houdini asset", capsule["task_text"])
                self.assertEqual([], capsule["attachments"])
            else:
                self.assertNotIn("authoritative_task", envelope)
                self.assertEqual(
                    {
                        "task_id": state.authoritative_task_id,
                        "sha256": state.authoritative_task_sha256,
                    },
                    envelope["authoritative_task_ref"],
                )
        self.assertEqual(
            "build a Houdini asset",
            harness.registry.require("project-1").authoritative_task_text,
        )
        goals = [params for method, params in self.client.calls if method == "thread/goal/set"]
        self.assertEqual("completed", goals[-1]["status"])
        self.assertEqual("thread-supervisor", goals[-1]["threadId"])

    def test_each_reviewer_must_cover_every_stage_requirement_before_pass(self):
        plan = _plan_with_two_requirements()
        self.client.queue(
            Role.SUPERVISOR,
            "scene_task_eligibility",
            {"schema": "hia-project-eligibility/1", "disposition": "eligible", "reason": "scene task"},
        )
        self.client.queue(Role.PLANNING, "create_plan_and_stage_cards", plan)
        self.client.queue(Role.SUPERVISOR, "authorize_plan", _authorization(plan))
        self.client.queue(Role.EXECUTION, "execute_stage", _execution_payload())
        self.client.queue(
            Role.VISUAL_REVIEW,
            "review_stage",
            _review_payload(Role.VISUAL_REVIEW, requirement_ids=("req-1",)),
        )
        self.client.queue(
            Role.TECHNICAL_REVIEW,
            "review_stage",
            _review_payload(
                Role.TECHNICAL_REVIEW, requirement_ids=("req-1", "req-2")
            ),
        )
        self.client.queue(
            Role.VISUAL_REVIEW,
            "correct_stage_review",
            _review_payload(
                Role.VISUAL_REVIEW, requirement_ids=("req-1", "req-2")
            ),
        )
        self.client.queue(Role.SUPERVISOR, "decide_stage_review", _decision())
        harness = EffectHarness(self.root, self.client)
        state = harness.run_to_terminal()
        self.assertEqual(ProjectStatus.COMPLETED, state.status)
        self.assertEqual(1, state.stage.schema_correction_count)
        actions = [
            json.loads(params["input"][0]["text"])["action"]
            for method, params in self.client.calls
            if method == "turn/start"
        ]
        self.assertIn("correct_stage_review", actions)
        decision_request = next(
            json.loads(params["input"][0]["text"])
            for method, params in self.client.calls
            if method == "turn/start"
            and json.loads(params["input"][0]["text"])["action"]
            == "decide_stage_review"
        )
        self.assertEqual(
            ["req-1", "req-2"],
            decision_request["review_coverage"]["requirement_ids"],
        )
        for proof in decision_request["review_coverage"]["reviewers"].values():
            self.assertTrue(proof["complete"])
            self.assertEqual(["req-1", "req-2"], proof["claim_ids"])

    def test_unapproved_not_applicable_claim_cannot_be_passed(self):
        self._queue_common()
        self.client.queue(Role.EXECUTION, "execute_stage", _execution_payload())
        self.client.queue(
            Role.VISUAL_REVIEW,
            "review_stage",
            _not_applicable_review(Role.VISUAL_REVIEW),
        )
        self.client.queue(
            Role.TECHNICAL_REVIEW,
            "review_stage",
            _review_payload(Role.TECHNICAL_REVIEW),
        )
        self.client.queue(Role.SUPERVISOR, "decide_stage_review", _decision())
        harness = EffectHarness(self.root, self.client)
        with self.assertRaises(ProjectEffectError) as raised:
            harness.run_to_terminal()
        self.assertEqual("INVALID_STAGE_DECISION", raised.exception.code)

    def test_evidence_bound_not_applicable_claim_requires_exact_supervisor_approval(self):
        self._queue_common()
        self.client.queue(Role.EXECUTION, "execute_stage", _execution_payload())
        self.client.queue(
            Role.VISUAL_REVIEW,
            "review_stage",
            _not_applicable_review(Role.VISUAL_REVIEW),
        )
        self.client.queue(
            Role.TECHNICAL_REVIEW,
            "review_stage",
            _review_payload(Role.TECHNICAL_REVIEW),
        )
        approval = {
            "reviewer": Role.VISUAL_REVIEW.value,
            "requirement_id": "req-1",
            "reason": "the stage requirement is demonstrably outside this review surface",
            "evidence_refs": ["tool-validate", "tool-capture"],
        }
        self.client.queue(
            Role.SUPERVISOR,
            "decide_stage_review",
            _decision(approved_exemptions=[approval]),
        )
        harness = EffectHarness(self.root, self.client)
        self.assertEqual(ProjectStatus.COMPLETED, harness.run_to_terminal().status)

    def test_visual_verified_claim_cannot_cite_only_technical_evidence(self):
        harness = EffectHarness(self.root, self.client)
        payload = _review_payload(Role.VISUAL_REVIEW)
        payload["claims"][0]["evidence_refs"] = ["tool-validate"]
        execution = {
            "evidence": [
                {
                    "item_id": "tool-validate",
                    "tool": "hia_validate",
                    "artifact_paths": [],
                },
                {
                    "item_id": "tool-capture",
                    "tool": "hia_capture_viewport",
                    "artifact_paths": [str(self.capture.resolve())],
                },
            ]
        }
        with self.assertRaises(ProjectEffectError) as raised:
            harness.executor._parse_review_payload(
                payload, Role.VISUAL_REVIEW, _full_stage(), execution
            )
        self.assertEqual("REVIEW_EVIDENCE_KIND_MISMATCH", raised.exception.code)

    def test_review_claim_ids_reject_unknown_duplicate_and_missing_requirements(self):
        harness = EffectHarness(self.root, self.client)
        execution = {
            "evidence": [
                {
                    "item_id": "tool-capture",
                    "tool": "hia_capture_viewport",
                    "artifact_paths": [str(self.capture.resolve())],
                }
            ]
        }
        stage_two = copy.deepcopy(_full_stage())
        stage_two["requirement_ids"].append("req-2")
        stage_two["ordered_steps"][0]["requirement_ids"].append("req-2")
        cases = (
            (
                "UNKNOWN_REVIEW_CLAIM",
                _review_payload(Role.VISUAL_REVIEW, requirement_ids=("req-x",)),
                _full_stage(),
            ),
            (
                "DUPLICATE_REVIEW_CLAIM",
                _review_payload(
                    Role.VISUAL_REVIEW, requirement_ids=("req-1", "req-1")
                ),
                _full_stage(),
            ),
            (
                "MISSING_REVIEW_CLAIM",
                _review_payload(Role.VISUAL_REVIEW, requirement_ids=("req-1",)),
                stage_two,
            ),
        )
        for expected_code, payload, stage in cases:
            with self.subTest(code=expected_code), self.assertRaises(
                ProjectEffectError
            ) as raised:
                harness.executor._parse_review_payload(
                    payload, Role.VISUAL_REVIEW, stage, execution
                )
            self.assertEqual(expected_code, raised.exception.code)

    def test_visual_unverified_claim_does_not_invent_capture_binding(self):
        harness = EffectHarness(self.root, self.client)
        payload = {
            "schema": "hia-project-review/1",
            "stage_id": "stage-1",
            "reviewer": Role.VISUAL_REVIEW.value,
            "claims": [
                {
                    "disposition": "unverified",
                    "claim_id": "req-1",
                    "missing_evidence": ["front capture"],
                    "minimum_next_observation": "capture the missing front view",
                }
            ],
        }
        execution = {
            "evidence": [
                {
                    "item_id": "tool-capture",
                    "tool": "hia_capture_viewport",
                    "artifact_paths": [str(self.capture.resolve())],
                }
            ]
        }
        parsed = harness.executor._parse_review_payload(
            payload, Role.VISUAL_REVIEW, _full_stage(), execution
        )
        self.assertEqual("unverified", parsed["claims"][0]["disposition"])

    def test_role_capsule_includes_complete_attachment_identity_without_artifact_copy(self):
        self.client.queue(
            Role.SUPERVISOR,
            "scene_task_eligibility",
            {"schema": "hia-project-eligibility/1", "disposition": "ineligible", "reason": "test"},
        )
        harness = EffectHarness(self.root, self.client)
        record = harness.registry.require("project-1")
        attachment = ProjectAttachment(
            str(self.root / "reference.png"), "a" * 64, 123
        )
        harness.registry.put(
            ProjectRecord(record.state, record.authoritative_task_text, (attachment,)),
            expected_revision=record.state.revision,
        )
        harness.run_one()
        envelope = next(iter(self.client.turns.values()))[2]
        self.assertEqual(
            {
                "attachment_anchor": f"attachment:{'a' * 64}",
                "path": str(self.root / "reference.png"),
                "sha256": "a" * 64,
                "size_bytes": 123,
            },
            envelope["authoritative_task"]["attachments"][0],
        )
        start = next(
            params for method, params in self.client.calls if method == "turn/start"
        )
        self.assertEqual(
            [{"type": "localImage", "path": str(self.root / "reference.png")}],
            start["input"][1:],
        )
        self.assertNotIn("authoritative_task", harness.artifacts.project("project-1"))

    def test_reference_images_reach_intake_planning_and_full_plan_authorization(self):
        self._queue_common()
        harness = EffectHarness(self.root, self.client)
        record = harness.registry.require("project-1")
        image = ProjectAttachment(str(self.root / "brief.webp"), "b" * 64, 456)
        harness.registry.put(
            ProjectRecord(record.state, record.authoritative_task_text, (image,)),
            expected_revision=record.state.revision,
        )
        for _ in range(4):
            harness.run_one()
        relevant = {
            envelope["action"]: params["input"][1:]
            for method, params in self.client.calls
            if method == "turn/start"
            for envelope in [json.loads(params["input"][0]["text"])]
            if envelope["action"]
            in {"scene_task_eligibility", "create_plan_and_stage_cards", "authorize_plan"}
        }
        self.assertEqual(
            {
                "scene_task_eligibility",
                "create_plan_and_stage_cards",
                "authorize_plan",
            },
            set(relevant),
        )
        for images in relevant.values():
            self.assertEqual(
                [{"type": "localImage", "path": str(self.root / "brief.webp")}],
                images,
            )

    def test_authorization_must_reference_every_actual_blueprint_identifier(self):
        self.client.queue(
            Role.SUPERVISOR,
            "scene_task_eligibility",
            {"schema": "hia-project-eligibility/1", "disposition": "eligible", "reason": "scene task"},
        )
        self.client.queue(Role.PLANNING, "create_plan_and_stage_cards", _plan())
        authorization = _authorization()
        authorization["semantic_review"]["blueprint_sections"]["referenced_ids"].pop()
        self.client.queue(Role.SUPERVISOR, "authorize_plan", authorization)
        harness = EffectHarness(self.root, self.client)
        for _ in range(3):
            harness.run_one()
        with self.assertRaises(ProjectEffectError) as raised:
            harness.run_one()
        self.assertEqual("INVALID_AUTHORIZATION_SCHEMA", raised.exception.code)

    def test_authorization_rejects_failed_semantic_item_even_when_boolean_is_true(self):
        self.client.queue(
            Role.SUPERVISOR,
            "scene_task_eligibility",
            {"schema": "hia-project-eligibility/1", "disposition": "eligible", "reason": "scene task"},
        )
        self.client.queue(Role.PLANNING, "create_plan_and_stage_cards", _plan())
        self.client.queue(
            Role.SUPERVISOR,
            "authorize_plan",
            _authorization(failed_key="native_strategy_and_dependencies"),
        )
        harness = EffectHarness(self.root, self.client)
        for _ in range(3):
            harness.run_one()
        with self.assertRaises(ProjectEffectError) as raised:
            harness.run_one()
        self.assertEqual("PLAN_NOT_AUTHORIZED", raised.exception.code)

    def test_authorization_is_bound_to_exact_blueprint_revision_and_hash(self):
        for field, value in (
            ("blueprint_revision", 99),
            ("blueprint_sha256", "0" * 64),
        ):
            with self.subTest(field=field):
                client = FakeProjectClient(self.capture)
                client.queue(
                    Role.SUPERVISOR,
                    "scene_task_eligibility",
                    {"schema": "hia-project-eligibility/1", "disposition": "eligible", "reason": "scene task"},
                )
                client.queue(Role.PLANNING, "create_plan_and_stage_cards", _plan())
                authorization = _authorization()
                authorization[field] = value
                client.queue(Role.SUPERVISOR, "authorize_plan", authorization)
                case_root = self.root / field
                case_root.mkdir()
                harness = EffectHarness(case_root, client)
                for _ in range(3):
                    harness.run_one()
                with self.assertRaises(ProjectEffectError) as raised:
                    harness.run_one()
                self.assertEqual("PLAN_NOT_AUTHORIZED", raised.exception.code)

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

    def test_ineligible_keeps_supervisor_project_open_for_the_next_message(self):
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
        self.assertEqual(ProjectStatus.INTAKE, state.status)
        self.assertEqual({Role.SUPERVISOR}, set(state.roles))
        self.assertFalse(any(method == "thread/start" for method, _ in self.client.calls))
        goal_updates = [
            params for method, params in self.client.calls
            if method == "thread/goal/set"
        ]
        self.assertEqual(1, len(goal_updates))
        self.assertEqual("paused", goal_updates[0]["status"])

    def test_second_message_is_current_intake_and_a_valid_plan_source(self):
        self.client.queue(
            Role.SUPERVISOR,
            "scene_task_eligibility",
            {
                "schema": "hia-project-eligibility/1",
                "disposition": "ineligible",
                "reason": "你好！",
            },
            {
                "schema": "hia-project-eligibility/1",
                "disposition": "eligible",
                "reason": "最新消息要求操作 Houdini 场景",
            },
        )
        harness = EffectHarness(self.root, self.client)
        harness.run_to_terminal()
        updated = publish_guidance(
            harness.state,
            "创建一个可编辑的程序化资产",
            target_role=Role.SUPERVISOR,
        )
        guidance = updated.guidance[-1]
        harness.state = replace(
            updated,
            pending_effects=(
                PendingEffect("effect-start-intake-second", "start_intake"),
            ),
        )
        record = harness.registry.require("project-1")
        harness.registry.put(
            ProjectRecord(
                harness.state,
                record.authoritative_task_text,
                record.attachments,
            ),
            expected_revision=record.state.revision,
        )
        self.client.queue(
            Role.PLANNING,
            "create_plan_and_stage_cards",
            _direct_plan(anchor=f"guidance:{guidance.guidance_id}"),
        )

        harness.run_one()
        eligibility_turns = [
            json.loads(params["input"][0]["text"])
            for method, params in self.client.calls
            if method == "turn/start"
            and json.loads(params["input"][0]["text"])["action"]
            == "scene_task_eligibility"
        ]
        self.assertEqual(
            "创建一个可编辑的程序化资产",
            eligibility_turns[-1]["current_submission"]["text"],
        )
        self.assertEqual(
            f"guidance:{guidance.guidance_id}",
            eligibility_turns[-1]["current_submission"]["source_anchor"],
        )
        harness.run_one()
        harness.run_one()
        self.assertEqual(ProjectStatus.AUTHORIZATION, harness.state.status)
        planning_envelope = next(
            json.loads(params["input"][0]["text"])
            for method, params in self.client.calls
            if method == "turn/start"
            and json.loads(params["input"][0]["text"])["action"]
            == "create_plan_and_stage_cards"
        )
        self.assertIn("smallest depth", planning_envelope["response_rules"]["depth_policy"]["selection"])
        self.assertEqual(
            "direct|focused|full",
            planning_envelope["response_contract"]["stages"][0]["depth"],
        )
        self.assertEqual(1, len(planning_envelope["response_contract"]["blueprint_sections"]))

    def test_resume_goal_returns_typed_ack_event_before_lifecycle_activation(self):
        harness = EffectHarness(self.root, self.client)
        state = replace(
            harness.state,
            status=ProjectStatus.RESUMING,
            resume_status=ProjectStatus.PLANNING,
            pending_effects=(PendingEffect("effect-resume", "resume_goal", {}),),
        )
        result = harness.executor.execute(state, state.pending_effects[0])

        self.assertEqual(ProjectStatus.RESUMING, result.state.status)
        self.assertEqual(ProjectEvent.GOAL_RESUMED, result.event.kind)
        goal = [params for method, params in self.client.calls if method == "thread/goal/set"][-1]
        self.assertEqual("paused", goal["status"])

    def test_verified_recovery_clears_only_stale_persisted_turn_activity(self):
        harness = EffectHarness(self.root, self.client)
        harness.executor._factory = type(
            "RecoveryFactory",
            (),
            {"validate_recovery_identity": lambda _self, _state: None},
        )()
        active_turn = TurnState(
            role=Role.PLANNING,
            thread_id="thread-planning",
            turn_id="turn-timed-out",
            active=True,
            consumed_turns=0,
        )
        state = replace(
            harness.state,
            status=ProjectStatus.INTERRUPTED,
            resume_status=ProjectStatus.PLANNING,
            turns={Role.PLANNING: active_turn},
            pending_effects=(PendingEffect("verify-recovery", "verify_recovery"),),
        )

        result = harness.executor.execute(state, state.pending_effects[0])

        self.assertEqual(ProjectEvent.RECOVERY_VALIDATED, result.event.kind)
        self.assertFalse(result.state.turns[Role.PLANNING].active)
        self.assertIsNone(result.state.turns[Role.PLANNING].turn_id)

    def test_resume_goal_rejects_missing_or_mismatched_native_goal_identity(self):
        for goal in (
            {"status": "paused"},
            {"threadId": "wrong-thread", "status": "paused"},
            {"threadId": "thread-supervisor", "status": "active"},
        ):
            with self.subTest(goal=goal):
                self.client.goal_ack_override = goal
                harness = EffectHarness(self.root, self.client)
                state = replace(
                    harness.state,
                    status=ProjectStatus.RESUMING,
                    resume_status=ProjectStatus.EXECUTING_STAGE,
                    pending_effects=(PendingEffect("effect-resume", "resume_goal", {}),),
                )
                result = harness.executor.execute(state, state.pending_effects[0])
                self.assertEqual(ProjectEvent.GOAL_RESUME_FAILED, result.event.kind)
                self.assertEqual("goal_ack_mismatch", result.event.data["error"])

    def test_resume_goal_rpc_failure_is_needs_attention_not_active_or_completed(self):
        self.client.goal_error = RuntimeError("transport rejected request")
        harness = EffectHarness(self.root, self.client)
        state = replace(
            harness.state,
            status=ProjectStatus.RESUMING,
            resume_status=ProjectStatus.REVIEWING_STAGE,
            pending_effects=(PendingEffect("effect-resume", "resume_goal", {}),),
        )
        result = harness.executor.execute(state, state.pending_effects[0])
        next_state, commands = reduce_project(state, result.event)

        self.assertEqual(ProjectEvent.GOAL_RESUME_FAILED, result.event.kind)
        self.assertEqual(ProjectStatus.NEEDS_ATTENTION, next_state.status)
        self.assertEqual(ProjectStatus.REVIEWING_STAGE, next_state.resume_status)
        self.assertEqual((), commands)

    def test_resume_goal_timeout_requires_confirmed_pause(self):
        self.client.goal_error = TimeoutError("unknown native result")
        harness = EffectHarness(self.root, self.client)
        state = replace(
            harness.state,
            status=ProjectStatus.RESUMING,
            resume_status=ProjectStatus.EXECUTING_STAGE,
            pending_effects=(PendingEffect("effect-resume", "resume_goal", {}),),
        )
        result = harness.executor.execute(state, state.pending_effects[0])
        next_state, commands = reduce_project(state, result.event)

        self.assertEqual(ProjectEvent.PROJECT_INTERRUPTED, result.event.kind)
        self.assertEqual(ProjectStatus.PAUSING, next_state.status)
        self.assertEqual("pause_goal", commands[0].kind.value)

    def test_repeated_schema_failure_exhausts_budget_and_never_passes(self):
        invalid = {"schema": "hia-project-eligibility/1", "disposition": "eligible"}
        self.client.queue(Role.SUPERVISOR, "scene_task_eligibility", invalid, invalid, invalid)
        budget = RuntimeBudget(max_schema_corrections=2)
        harness = EffectHarness(self.root, self.client, budget)
        result = harness.run_one()
        self.assertEqual(ProjectEvent.BUDGET_EXHAUSTED, result.event.kind)
        self.assertEqual("max_schema_corrections", result.event.data["reason"])
        self.assertNotEqual(ProjectStatus.PROVISIONING_ROLES, harness.state.status)

    def test_terminal_invalid_json_closes_turn_before_bounded_correction(self):
        self.client.queue(
            Role.SUPERVISOR,
            "scene_task_eligibility",
            {"__payload_error__": "INVALID_AGENT_JSON"},
            {
                "schema": "hia-project-eligibility/1",
                "disposition": "eligible",
                "reason": "scene task",
            },
        )
        harness = EffectHarness(self.root, self.client)

        result = harness.run_one()

        self.assertEqual(ProjectEvent.SCENE_ELIGIBLE, result.event.kind)
        turn = result.state.turns[Role.SUPERVISOR]
        self.assertFalse(turn.active)
        self.assertEqual(2, turn.consumed_turns)
        self.assertEqual(1, result.state.stage.schema_correction_count)
        starts = [
            json.loads(params["input"][0]["text"])
            for method, params in self.client.calls
            if method == "turn/start"
        ]
        self.assertEqual(
            "INVALID_AGENT_JSON",
            starts[-1]["schema_correction"]["error"],
        )

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

    def test_every_role_request_carries_an_explicit_structured_contract(self):
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
        harness.run_one()
        envelope = next(iter(self.client.turns.values()))[2]
        self.assertEqual(
            "hia-project-eligibility/1",
            envelope["response_contract"]["schema"],
        )
        self.assertEqual("one JSON object only", envelope["response_rules"]["format"])

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

    def test_material_delta_revises_full_artifact_then_requires_supervisor_authorization(self):
        self._queue_common()
        harness = EffectHarness(self.root, self.client)
        for _ in range(4):
            harness.run_one()
        self.assertEqual(1, harness.state.blueprint_revision)
        self.assertEqual(1, harness.state.authorized_blueprint_revision)

        revised_plan = json.loads(json.dumps(_plan()))
        anchor = revised_plan["requirements"][0]["source_ref"]
        revised_plan["requirements"] = [
            {
                "requirement_id": "req-2",
                "kind": "structure",
                "description": "Replace the broad structure with the smaller user scope",
                "source_ref": anchor,
                "user_fact_ids": ["fact-1"],
            }
        ]
        revised_plan["stages"][0]["requirement_ids"] = ["req-2"]
        revised_plan["stages"][0]["ordered_steps"][0]["requirement_ids"] = ["req-2"]
        for section in revised_plan["blueprint_sections"]:
            section["requirement_ids"] = ["req-2"]

        updated = publish_guidance(
            harness.state,
            "shrink the structure scope and replace the broad requirement",
            requirement_delta=RequirementDelta(
                add=(Requirement("req-2", "structure", source_ref=anchor),),
                supersede={"req-1": "req-2"},
            ),
            force_replan=True,
        )
        replanning, commands = reduce_project(
            updated, LifecycleEvent(ProjectEvent.MATERIAL_REPLAN_REQUIRED)
        )
        effect = _pending_effect(replanning, 0, commands[0].kind.value, commands[0].data or {})
        harness.state = replace(replanning, pending_effects=(effect,))
        harness.registry.put(
            ProjectRecord(harness.state, "build a Houdini asset"),
            expected_revision=harness.registry.require("project-1").state.revision,
        )
        self.client.queue(Role.PLANNING, "create_plan_and_stage_cards", revised_plan)
        self.client.queue(
            Role.SUPERVISOR,
            "authorize_plan",
            _authorization(revised_plan, revision=2),
        )

        harness.run_one()
        planning_envelope = [
            json.loads(params["input"][0]["text"])
            for method, params in self.client.calls
            if method == "turn/start" and params["threadId"] == "thread-planning"
        ][-1]
        self.assertEqual(
            "shrink the structure scope and replace the broad requirement",
            planning_envelope["material_revision_requests"][0]["text"],
        )
        self.assertTrue(
            planning_envelope["material_revision_requests"][0]["force_replan"]
        )
        self.assertEqual(ProjectStatus.AUTHORIZATION, harness.state.status)
        self.assertEqual(2, harness.state.blueprint_revision)
        self.assertEqual(1, harness.state.authorized_blueprint_revision)
        planning = [
            json.loads(params["input"][0]["text"])
            for method, params in self.client.calls
            if method == "turn/start" and params["threadId"] == "thread-planning"
        ][-1]
        self.assertEqual(1, planning["current_blueprint"]["_blueprint_revision"])
        self.assertEqual("req-2", planning["material_requirement_deltas"][0]["delta"]["add"][0]["requirement_id"])
        self.assertEqual(
            {"req-1": "req-2"},
            planning["material_requirement_deltas"][0]["delta"]["supersede"],
        )
        self.assertEqual(2, planning["target_blueprint_revision"])
        self.assertEqual(2, len(harness.artifacts.get_named("project-1", "plan_history")))

        harness.run_one()
        self.assertEqual(ProjectStatus.EXECUTING_STAGE, harness.state.status)
        self.assertEqual(2, harness.state.authorized_blueprint_revision)
        requirement_status = {
            item.requirement_id: item.status for item in harness.state.requirements
        }
        self.assertEqual(RequirementStatus.ACTIVE, requirement_status["req-2"])
        self.assertEqual(
            RequirementStatus.SUPERSEDED_BY_USER,
            requirement_status["req-1"],
        )

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
