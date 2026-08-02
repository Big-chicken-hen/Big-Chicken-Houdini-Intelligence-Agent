"""Bounded executor for project-team lifecycle effects.

The executor performs RPC work and returns a complete replacement state plus an
optional lifecycle event.  It does not persist lifecycle state itself.  This is
an intentional narrow contract for the runner: an ``event=None`` result means
only that the external effect was acknowledged and its deterministic state
change is already present; it never means that a stage or project passed.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass, replace
import hashlib
import json
from pathlib import Path
import time
from typing import Any, Callable, Mapping, Protocol, Sequence

from .project_artifacts import ProjectArtifactStore
from .project_budget import ProgressObservation, record_progress
from .project_contracts import (
    PendingEffect,
    ProjectState,
    Requirement,
    RequirementStatus,
    Role,
    StageState,
)
from .project_evidence import EvidenceValidationResult, validate_evidence
from .project_guidance import (
    mark_guidance_consumed,
    pending_guidance,
    validate_requirement_coverage,
)
from .project_lifecycle import LifecycleEvent, ProjectEvent
from .project_payloads import (
    FULL_BLUEPRINT_SECTION_IDS,
    parse_review_claim,
    parse_stage_card,
    validate_blueprint_information,
    validate_plan_structure,
)
from .project_registry import ProjectRegistry
from .project_thread_factory import ProjectThreadFactory
from .project_turns import TurnOwnershipLedger


class ProjectEffectClient(Protocol):
    def request(self, method: str, params: Mapping[str, Any]) -> Any: ...

    def wait_for_turn(
        self, thread_id: str, turn_id: str, timeout_seconds: float
    ) -> "CompletedTurn": ...


@dataclass(frozen=True)
class CompletedTurn:
    thread_id: str
    turn_id: str
    status: str
    payload: Mapping[str, Any]
    events: tuple[Mapping[str, Any], ...] = ()
    elapsed_seconds: int = 0
    native_subagents: int = 0
    payload_error_code: str | None = None
    payload_error_message: str | None = None


@dataclass(frozen=True)
class EffectResult:
    state: ProjectState
    event: LifecycleEvent | None


@dataclass(frozen=True)
class _TurnCall:
    role: Role
    action: str
    request_id: str
    thread_id: str
    turn_id: str
    guidance_ids: tuple[str, ...]


class ProjectEffectError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class _BudgetStop(Exception):
    def __init__(self, state: ProjectState, reason: str) -> None:
        self.state = state
        self.reason = reason


class _Interrupted(Exception):
    def __init__(self, state: ProjectState, reason: str) -> None:
        self.state = state
        self.reason = reason


class ProjectEffectExecutor:
    """Execute one persisted effect with exact ownership and finite waiting."""

    def __init__(
        self,
        *,
        client: ProjectEffectClient,
        registry: ProjectRegistry,
        thread_factory: ProjectThreadFactory,
        artifacts: ProjectArtifactStore,
        allowed_evidence_roots: Sequence[str | Path],
        total_timeout_seconds: float = 300.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if total_timeout_seconds <= 0:
            raise ValueError("total_timeout_seconds must be positive")
        self._client = client
        self._registry = registry
        self._factory = thread_factory
        self._artifacts = artifacts
        self._allowed_roots = tuple(allowed_evidence_roots)
        self._timeout = float(total_timeout_seconds)
        self._clock = clock
        self._ledger = TurnOwnershipLedger()

    def execute(self, state: ProjectState, effect: PendingEffect) -> EffectResult:
        if not state.pending_effects or state.pending_effects[0].effect_id != effect.effect_id:
            raise ProjectEffectError(
                "EFFECT_OWNERSHIP_MISMATCH",
                "executor may run only the oldest persisted project effect",
            )
        deadline = min(
            self._clock() + self._timeout,
            self._clock() + max(0, state.budget.max_elapsed_seconds - state.elapsed_seconds),
        )
        try:
            return self._execute(state, effect, deadline)
        except _BudgetStop as stop:
            return EffectResult(
                stop.state,
                LifecycleEvent(ProjectEvent.BUDGET_EXHAUSTED, {"reason": stop.reason}),
            )
        except _Interrupted as stop:
            return EffectResult(
                stop.state,
                LifecycleEvent(ProjectEvent.PROJECT_INTERRUPTED, {"reason": stop.reason}),
            )
        except TimeoutError:
            return EffectResult(
                state,
                LifecycleEvent(
                    ProjectEvent.PROJECT_INTERRUPTED,
                    {"reason": "project_effect_timeout"},
                ),
            )

    def _execute(
        self, state: ProjectState, effect: PendingEffect, deadline: float
    ) -> EffectResult:
        kind = effect.kind
        if kind == "start_intake":
            return self._intake(state, effect, deadline)
        if kind == "provision_workers":
            provisioned = self._factory.provision_workers(state)
            self._artifacts.put_effect(
                state.project_id,
                effect.effect_id,
                kind,
                {"role_threads": {r.value: b.thread_id for r, b in provisioned.roles.items()}},
            )
            return EffectResult(provisioned, LifecycleEvent(ProjectEvent.ROLES_PROVISIONED))
        if kind == "request_plan":
            return self._plan(state, effect, deadline)
        if kind == "request_authorization":
            return self._authorize(state, effect, deadline)
        if kind == "start_execution":
            return self._execute_stage(state, effect, deadline)
        if kind == "start_reviews":
            return self._review_stage(state, effect, deadline)
        if kind == "request_repair":
            return self._authorize_repair(state, effect, deadline)
        if kind == "complete_goal":
            return self._set_goal(state, effect, "completed", ProjectEvent.GOAL_COMPLETED)
        if kind == "pause_goal":
            return self._set_goal(state, effect, "paused", ProjectEvent.GOAL_PAUSED)
        if kind == "resume_goal":
            return self._set_goal(state, effect, "active", ProjectEvent.GOAL_RESUMED)
        if kind in {"show_attention", "record_failure"}:
            self._artifacts.put_effect(state.project_id, effect.effect_id, kind, dict(effect.data))
            return EffectResult(state, None)
        if kind == "advance_stage":
            return self._advance_stage(state, effect)
        raise ProjectEffectError("UNKNOWN_PROJECT_EFFECT", f"unsupported effect kind: {kind}")

    def _intake(
        self, state: ProjectState, effect: PendingEffect, deadline: float
    ) -> EffectResult:
        request = self._base_request(state, Role.SUPERVISOR, "scene_task_eligibility")
        state, completed = self._run_structured(
            state,
            Role.SUPERVISOR,
            request,
            "hia-project-eligibility/1",
            deadline,
            local_image_paths=self._authoritative_image_paths(state),
        )
        payload = completed.payload
        if set(payload) != {"schema", "disposition", "reason"}:
            raise ProjectEffectError("INVALID_ELIGIBILITY_SCHEMA", "eligibility fields are invalid")
        disposition = payload.get("disposition")
        _text(payload.get("reason"), "eligibility reason")
        self._artifacts.put_effect(state.project_id, effect.effect_id, effect.kind, payload)
        if disposition == "eligible":
            event = LifecycleEvent(ProjectEvent.SCENE_ELIGIBLE)
        elif disposition == "ineligible":
            event = LifecycleEvent(ProjectEvent.SCENE_INELIGIBLE, {"reason": payload["reason"]})
        elif disposition == "unclear":
            event = LifecycleEvent(ProjectEvent.INTAKE_UNCLEAR, {"reason": payload["reason"]})
        else:
            raise ProjectEffectError("INVALID_ELIGIBILITY_SCHEMA", "eligibility disposition is invalid")
        return EffectResult(state, event)

    def _plan(
        self, state: ProjectState, effect: PendingEffect, deadline: float
    ) -> EffectResult:
        request = self._base_request(state, Role.PLANNING, "create_plan_and_stage_cards")
        state, completed = self._run_structured(
            state,
            Role.PLANNING,
            request,
            "hia-project-plan/1",
            deadline,
            local_image_paths=self._authoritative_image_paths(state),
        )
        payload = completed.payload
        capsule = self._authoritative_task_capsule(state)
        try:
            validate_plan_structure(
                payload,
                allowed_source_anchors=(
                    capsule["task_anchor"],
                    *(item["attachment_anchor"] for item in capsule["attachments"]),
                ),
            )
        except ValueError as exc:
            raise ProjectEffectError("INVALID_PLAN_SCHEMA", str(exc)) from exc
        raw_requirements = payload.get("requirements")
        raw_stages = payload.get("stages")
        if not isinstance(raw_requirements, list) or not raw_requirements:
            raise ProjectEffectError("INVALID_PLAN_SCHEMA", "requirements must be non-empty")
        if not isinstance(raw_stages, list) or not raw_stages:
            raise ProjectEffectError("INVALID_PLAN_SCHEMA", "stages must be non-empty")
        requirements: list[Requirement] = []
        seen: set[str] = set()
        for raw in raw_requirements:
            if not isinstance(raw, Mapping) or set(raw) != {
                "requirement_id",
                "kind",
                "description",
                "source_ref",
                "user_fact_ids",
            }:
                raise ProjectEffectError("INVALID_PLAN_SCHEMA", "requirement fields are invalid")
            requirement_id = _text(raw.get("requirement_id"), "requirement_id")
            if requirement_id in seen:
                raise ProjectEffectError("INVALID_PLAN_SCHEMA", "requirement IDs must be unique")
            seen.add(requirement_id)
            requirements.append(
                Requirement(
                    requirement_id=requirement_id,
                    kind=_text(raw.get("kind"), "requirement kind"),
                    status=RequirementStatus.ACTIVE,
                    source_ref=_text(raw.get("source_ref"), "source_ref"),
                )
            )
        cards = [parse_stage_card(raw) for raw in raw_stages if isinstance(raw, Mapping)]
        if len(cards) != len(raw_stages) or len({card.stage_id for card in cards}) != len(cards):
            raise ProjectEffectError("INVALID_PLAN_SCHEMA", "stage cards are malformed or duplicated")
        try:
            validate_blueprint_information(payload, tuple(cards))
        except ValueError as exc:
            raise ProjectEffectError(
                "INVALID_PLAN_SCHEMA",
                str(exc),
            ) from exc
        covered = tuple(item for card in cards for item in card.requirement_ids)
        validate_requirement_coverage(requirements, covered)
        state = replace(
            state,
            requirements=tuple(requirements),
            stage=StageState(stage_id=cards[0].stage_id, ordinal=1),
            revision=state.revision + 1,
        )
        self._artifacts.put_named(state.project_id, "plan", payload)
        self._artifacts.put_effect(
            state.project_id, effect.effect_id, effect.kind, {"stage_ids": [c.stage_id for c in cards]}
        )
        return EffectResult(state, LifecycleEvent(ProjectEvent.PLAN_READY))

    def _authorize(
        self, state: ProjectState, effect: PendingEffect, deadline: float
    ) -> EffectResult:
        plan = self._require_plan(state)
        request = self._base_request(state, Role.SUPERVISOR, "authorize_plan")
        request["plan"] = plan
        state, completed = self._run_structured(
            state,
            Role.SUPERVISOR,
            request,
            "hia-project-authorization/1",
            deadline,
            local_image_paths=self._authoritative_image_paths(state),
        )
        payload = completed.payload
        if set(payload) != {"schema", "authorized", "stage_ids"}:
            raise ProjectEffectError("INVALID_AUTHORIZATION_SCHEMA", "authorization fields are invalid")
        expected = [stage["stage_id"] for stage in plan["stages"]]
        if payload.get("authorized") is not True or payload.get("stage_ids") != expected:
            raise ProjectEffectError(
                "PLAN_NOT_AUTHORIZED", "Supervisor did not authorize the exact persisted stage set"
            )
        self._artifacts.put_effect(state.project_id, effect.effect_id, effect.kind, payload)
        return EffectResult(state, LifecycleEvent(ProjectEvent.PLAN_AUTHORIZED))

    def _execute_stage(
        self, state: ProjectState, effect: PendingEffect, deadline: float
    ) -> EffectResult:
        stage = self._current_stage(state)
        repair = bool(effect.data.get("repair", False))
        request = self._base_request(
            state, Role.EXECUTION, "execute_repair" if repair else "execute_stage"
        )
        request["stage_card"] = stage
        if repair:
            request["repair_card"] = self._require_named(state, "repair_card")
        state, completed = self._run_structured(
            state,
            Role.EXECUTION,
            request,
            "hia-project-execution/1",
            deadline,
            record_success=False,
        )
        if completed.native_subagents != 0:
            raise ProjectEffectError(
                "EXECUTION_SUBAGENT_FORBIDDEN",
                "Execution cannot delegate inherited scene-write capability",
            )
        payload = completed.payload
        if set(payload) != {"schema", "stage_id", "evidence_refs", "claims_visual_change"}:
            raise ProjectEffectError("INVALID_EXECUTION_SCHEMA", "execution fields are invalid")
        if payload.get("stage_id") != stage["stage_id"]:
            raise ProjectEffectError("STAGE_OWNERSHIP_MISMATCH", "Execution returned the wrong stage")
        references = payload.get("evidence_refs")
        if not isinstance(references, list) or not references:
            raise ProjectEffectError("MISSING_EXECUTION_EVIDENCE", "Execution evidence is required")
        current = state.turns.get(Role.EXECUTION)
        if current is None or current.turn_id != completed.turn_id:
            raise ProjectEffectError("TURN_OWNERSHIP_MISMATCH", "Execution Turn identity was lost")
        evidence = validate_evidence(
            references=references,
            tool_events=completed.events,
            execution_thread_id=completed.thread_id,
            execution_turn_id=completed.turn_id,
            allowed_roots=self._allowed_roots,
            existing_total_evidence_bytes=state.total_evidence_bytes,
            max_total_evidence_bytes=state.budget.max_total_evidence_bytes,
        )
        tools = {item.tool for item in evidence.evidence}
        if "hia_capture_viewport" not in tools or len(tools - {"hia_capture_viewport"}) < 1:
            raise ProjectEffectError(
                "INCOMPLETE_EXECUTION_EVIDENCE",
                "stage evidence requires a real viewport capture and technical HIA evidence",
            )
        screenshot_hash = _capture_hash(evidence)
        repair_card = self._artifacts.get_named(state.project_id, "repair_card") if repair else None
        previous_execution = (
            self._artifacts.get_named(state.project_id, "current_execution") if repair else None
        )
        observation = ProgressObservation(
            role=Role.EXECUTION,
            kind="repair" if repair else "execution",
            elapsed_seconds=0,
            evidence_bytes=evidence.added_evidence_bytes,
            evidence_ids=tuple(item.item_id for item in evidence.evidence),
            repair_hash=(repair_card or {}).get("repair_hash") if isinstance(repair_card, Mapping) else None,
            screenshot_hash=screenshot_hash,
            claims_visual_change=payload.get("claims_visual_change") is True,
        )
        state = self._record(state, observation)
        stored = {
            "stage_id": stage["stage_id"],
            "turn_id": completed.turn_id,
            "thread_id": completed.thread_id,
            "evidence": [
                {
                    "item_id": item.item_id,
                    "tool": item.tool,
                    "artifact_paths": list(item.artifact_paths),
                    "frame": item.capture_frame,
                    "view": item.capture_view,
                    "evidence_bytes": item.evidence_bytes,
                }
                for item in evidence.evidence
            ],
        }
        if repair:
            previous_ids = {
                item.get("item_id")
                for item in (previous_execution or {}).get("evidence", [])
                if isinstance(item, Mapping)
            }
            current_ids = {item["item_id"] for item in stored["evidence"]}
            progress = self._artifacts.get_named(state.project_id, "repair_progress")
            prior_rounds = (
                progress.get("no_progress_rounds", 0) if isinstance(progress, Mapping) else 0
            )
            no_progress_rounds = prior_rounds + 1 if current_ids <= previous_ids else 0
            progress = {
                "no_progress_rounds": no_progress_rounds,
                "latest_evidence_ids": sorted(current_ids),
                "repair_hash": (repair_card or {}).get("repair_hash"),
            }
            self._artifacts.put_named(state.project_id, "repair_progress", progress)
            state = replace(
                state,
                stage=replace(state.stage, no_progress_rounds=no_progress_rounds),
                revision=state.revision + 1,
            )
            if no_progress_rounds >= state.budget.max_no_progress_rounds:
                raise _BudgetStop(state, "repair_added_no_new_evidence")
        self._artifacts.put_named(state.project_id, "current_execution", stored)
        self._artifacts.put_effect(state.project_id, effect.effect_id, effect.kind, stored)
        return EffectResult(state, LifecycleEvent(ProjectEvent.STAGE_EXECUTED))

    def _advance_stage(
        self, state: ProjectState, effect: PendingEffect
    ) -> EffectResult:
        plan = self._require_plan(state)
        stages = plan.get("stages")
        if not isinstance(stages, list):
            raise ProjectEffectError("MISSING_PLAN_ARTIFACT", "plan stages are unavailable")
        current = next(
            (index for index, item in enumerate(stages) if item.get("stage_id") == state.stage.stage_id),
            None,
        )
        if current is None or current + 1 >= len(stages):
            raise ProjectEffectError("NO_NEXT_STAGE", "advance_stage has no exact next stage")
        next_stage = stages[current + 1]
        stage_id = next_stage.get("stage_id")
        if not isinstance(stage_id, str) or not stage_id:
            raise ProjectEffectError("MISSING_STAGE_ARTIFACT", "next stage identity is invalid")
        advanced = replace(
            state,
            stage=StageState(stage_id=stage_id, ordinal=state.stage.ordinal + 1),
            revision=state.revision + 1,
        )
        self._artifacts.put_effect(
            state.project_id,
            effect.effect_id,
            effect.kind,
            {"from_stage_id": state.stage.stage_id, "to_stage_id": stage_id},
        )
        return EffectResult(advanced, None)

    def _review_stage(
        self, state: ProjectState, effect: PendingEffect, deadline: float
    ) -> EffectResult:
        stage = self._current_stage(state)
        execution = self._require_named(state, "current_execution")
        state, reviews = self._parallel_reviews(state, stage, execution, deadline)
        decision_request = self._base_request(state, Role.SUPERVISOR, "decide_stage_review")
        decision_request.update({"stage_card": stage, "execution": execution, "reviews": reviews})
        state, completed = self._run_structured(
            state,
            Role.SUPERVISOR,
            decision_request,
            "hia-project-stage-decision/1",
            deadline,
        )
        payload = completed.payload
        if set(payload) != {"schema", "stage_id", "decision", "final_stage", "repair_card"}:
            raise ProjectEffectError("INVALID_STAGE_DECISION", "stage decision fields are invalid")
        if payload.get("stage_id") != stage["stage_id"]:
            raise ProjectEffectError("STAGE_OWNERSHIP_MISMATCH", "Supervisor decided the wrong stage")
        failed = any(
            claim["disposition"] in {"failed", "unverified"}
            for review in reviews
            for claim in review["claims"]
        )
        final_expected = self._is_final_stage(state)
        if payload.get("final_stage") is not final_expected:
            raise ProjectEffectError("INVALID_STAGE_DECISION", "final_stage does not match the plan")
        decision = payload.get("decision")
        if decision == "pass":
            if failed or payload.get("repair_card") is not None:
                raise ProjectEffectError("INVALID_STAGE_DECISION", "failed claims cannot be passed")
            event = LifecycleEvent(ProjectEvent.REVIEWS_PASSED, {"final_stage": final_expected})
        elif decision == "repair":
            card = _parse_repair_card(payload.get("repair_card"), stage["stage_id"], execution)
            if not failed:
                raise ProjectEffectError("INVALID_STAGE_DECISION", "repair requires a failed claim")
            self._artifacts.put_named(state.project_id, "repair_card", card)
            event = LifecycleEvent(ProjectEvent.REVIEWS_FAILED)
        else:
            raise ProjectEffectError("INVALID_STAGE_DECISION", "decision must be pass or repair")
        self._artifacts.put_named(state.project_id, "current_reviews", reviews)
        self._artifacts.put_effect(state.project_id, effect.effect_id, effect.kind, payload)
        return EffectResult(state, event)

    def _authorize_repair(
        self, state: ProjectState, effect: PendingEffect, deadline: float
    ) -> EffectResult:
        stage = self._current_stage(state)
        card = self._require_named(state, "repair_card")
        request = self._base_request(state, Role.SUPERVISOR, "authorize_minimum_repair")
        request.update({"stage_card": stage, "repair_card": card})
        state, completed = self._run_structured(
            state,
            Role.SUPERVISOR,
            request,
            "hia-project-repair-authorization/1",
            deadline,
        )
        payload = completed.payload
        if set(payload) != {"schema", "stage_id", "authorized", "repair_hash"}:
            raise ProjectEffectError("INVALID_REPAIR_AUTHORIZATION", "repair authorization fields are invalid")
        if (
            payload.get("stage_id") != stage["stage_id"]
            or payload.get("authorized") is not True
            or payload.get("repair_hash") != card["repair_hash"]
        ):
            raise ProjectEffectError("INVALID_REPAIR_AUTHORIZATION", "repair authorization mismatch")
        self._artifacts.put_effect(state.project_id, effect.effect_id, effect.kind, payload)
        return EffectResult(state, LifecycleEvent(ProjectEvent.REPAIR_READY))

    def _parallel_reviews(
        self,
        state: ProjectState,
        stage: Mapping[str, Any],
        execution: Mapping[str, Any],
        deadline: float,
    ) -> tuple[ProjectState, list[dict[str, Any]]]:
        calls: list[_TurnCall] = []
        visual_images = _capture_image_paths(execution)
        for role in (Role.VISUAL_REVIEW, Role.TECHNICAL_REVIEW):
            request = self._base_request(state, role, "review_stage")
            request.update({"stage_card": stage, "execution": execution})
            state, call = self._start_turn(
                state,
                role,
                request,
                deadline,
                local_image_paths=visual_images if role is Role.VISUAL_REVIEW else (),
            )
            calls.append(call)
        remaining = self._remaining(deadline)
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="hia-project-review") as pool:
            futures = {
                pool.submit(self._client.wait_for_turn, call.thread_id, call.turn_id, remaining): call
                for call in calls
            }
            completed_turns: dict[Role, CompletedTurn] = {}
            try:
                for future, call in futures.items():
                    completed_turns[call.role] = future.result(timeout=self._remaining(deadline))
            except (FutureTimeout, TimeoutError) as exc:
                raise _Interrupted(state, "project_role_turn_timeout") from exc
        reviews: list[dict[str, Any]] = []
        for call in calls:
            completed = completed_turns[call.role]
            state = self._finish_turn(state, call, completed)
            state = self._record(
                state,
                ProgressObservation(
                    role=call.role,
                    kind="turn",
                    elapsed_seconds=completed.elapsed_seconds,
                    native_subagents=completed.native_subagents,
                ),
            )
            payload = self._parse_review_payload(completed.payload, call.role, stage, execution)
            reviews.append(payload)
        state = self._refresh_guidance(state)
        for role in (Role.VISUAL_REVIEW, Role.TECHNICAL_REVIEW):
            if pending_guidance(state, role):
                request = self._base_request(state, role, "revise_stage_review")
                request.update({"stage_card": stage, "execution": execution, "prior_reviews": reviews})
                state, completed = self._run_structured(
                    state,
                    role,
                    request,
                    "hia-project-review/1",
                    deadline,
                    local_image_paths=(
                        visual_images if role is Role.VISUAL_REVIEW else ()
                    ),
                )
                revised = self._parse_review_payload(completed.payload, role, stage, execution)
                reviews = [item for item in reviews if item["reviewer"] != role.value]
                reviews.append(revised)
        reviews.sort(key=lambda item: item["reviewer"])
        return state, reviews

    def _parse_review_payload(
        self,
        payload: Mapping[str, Any],
        role: Role,
        stage: Mapping[str, Any],
        execution: Mapping[str, Any],
    ) -> dict[str, Any]:
        if set(payload) != {"schema", "stage_id", "reviewer", "claims"}:
            raise ProjectEffectError("INVALID_REVIEW_SCHEMA", "review fields are invalid")
        if payload.get("schema") != "hia-project-review/1":
            raise ProjectEffectError("INVALID_REVIEW_SCHEMA", "review schema is invalid")
        if payload.get("reviewer") != role.value or payload.get("stage_id") != stage["stage_id"]:
            raise ProjectEffectError("REVIEW_OWNERSHIP_MISMATCH", "review role or stage mismatch")
        raw_claims = payload.get("claims")
        if not isinstance(raw_claims, list) or not raw_claims:
            raise ProjectEffectError("INVALID_REVIEW_SCHEMA", "review claims must be non-empty")
        claims = [parse_review_claim(item) for item in raw_claims if isinstance(item, Mapping)]
        if len(claims) != len(raw_claims):
            raise ProjectEffectError("INVALID_REVIEW_SCHEMA", "review claim is malformed")
        available = {item["item_id"] for item in execution["evidence"]}
        capture_ids = {
            item["item_id"]
            for item in execution["evidence"]
            if item.get("tool") == "hia_capture_viewport"
            and item.get("artifact_paths")
        }
        technical_ids = available - capture_ids
        for claim in claims:
            refs = getattr(claim, "evidence_refs", ())
            if not set(refs).issubset(available):
                raise ProjectEffectError("UNKNOWN_REVIEW_EVIDENCE", "review cites unavailable evidence")
            if claim.disposition in {"verified", "failed"}:
                required = capture_ids if role is Role.VISUAL_REVIEW else technical_ids
                if not set(refs).intersection(required):
                    kind = "capture" if role is Role.VISUAL_REVIEW else "technical"
                    raise ProjectEffectError(
                        "REVIEW_EVIDENCE_KIND_MISMATCH",
                        f"{role.value} {claim.disposition} claim requires current {kind} evidence",
                    )
        return json.loads(json.dumps(payload, ensure_ascii=False))

    def _run_structured(
        self,
        state: ProjectState,
        role: Role,
        request: Mapping[str, Any],
        schema: str,
        deadline: float,
        *,
        record_success: bool = True,
        local_image_paths: Sequence[str] = (),
    ) -> tuple[ProjectState, CompletedTurn]:
        correction = 0
        current_request = dict(request)
        current_request["response_contract"] = _response_contract(schema)
        current_request["response_rules"] = {
            "format": "one JSON object only",
            "no_markdown_fence": True,
            "task_specific": True,
            "no_placeholder_or_filler": True,
        }
        while correction <= state.budget.max_schema_corrections:
            state, call = self._start_turn(
                state,
                role,
                current_request,
                deadline,
                local_image_paths=local_image_paths,
            )
            try:
                completed = self._client.wait_for_turn(
                    call.thread_id, call.turn_id, self._remaining(deadline)
                )
            except TimeoutError as exc:
                raise _Interrupted(state, "project_role_turn_timeout") from exc
            state = self._finish_turn(state, call, completed)
            state = self._refresh_guidance(state)
            if pending_guidance(state, role):
                state = self._record(
                    state,
                    ProgressObservation(
                        role=role,
                        kind="turn",
                        elapsed_seconds=completed.elapsed_seconds,
                        native_subagents=completed.native_subagents,
                    ),
                )
                current_request = dict(request)
                current_request["response_contract"] = _response_contract(schema)
                current_request["response_rules"] = {
                    "format": "one JSON object only",
                    "no_markdown_fence": True,
                    "task_specific": True,
                    "no_placeholder_or_filler": True,
                }
                current_request["revision_of_turn_id"] = completed.turn_id
                continue
            try:
                if completed.payload_error_code is not None:
                    raise ValueError(completed.payload_error_code)
                if completed.payload.get("schema") != schema:
                    raise ValueError(f"expected schema {schema}")
                _validate_payload_shape(schema, completed.payload)
                if record_success:
                    state = self._record(
                        state,
                        ProgressObservation(
                            role=role,
                            kind="turn",
                            elapsed_seconds=completed.elapsed_seconds,
                            native_subagents=completed.native_subagents,
                        ),
                    )
                return state, completed
            except (TypeError, ValueError) as exc:
                correction += 1
                state = self._record(
                    state,
                    ProgressObservation(role=role, kind="schema_correction"),
                )
                current_request = dict(request)
                current_request["response_contract"] = _response_contract(schema)
                current_request["response_rules"] = {
                    "format": "one JSON object only",
                    "no_markdown_fence": True,
                    "task_specific": True,
                    "no_placeholder_or_filler": True,
                }
                current_request["schema_correction"] = {
                    "attempt": correction,
                    "required_schema": schema,
                    "error": completed.payload_error_code or type(exc).__name__,
                }
        raise _BudgetStop(state, "max_schema_corrections")

    def _start_turn(
        self,
        state: ProjectState,
        role: Role,
        request: Mapping[str, Any],
        deadline: float,
        *,
        local_image_paths: Sequence[str] = (),
    ) -> tuple[ProjectState, _TurnCall]:
        state = self._refresh_guidance(state)
        guidance = pending_guidance(state, role)
        envelope = dict(request)
        action = str(envelope.get("action") or "")
        envelope["guidance"] = [
            {"guidance_id": item.guidance_id, "revision": item.revision, "text": item.text}
            for item in guidance
        ]
        request_id = hashlib.sha256(
            f"{state.project_id}\0{role.value}\0{state.revision}\0{self._clock()}".encode("utf-8")
        ).hexdigest()[:24]
        params = self._ledger.begin(state, role, request_id)
        params.update(
            {
                "input": [
                    {
                        "type": "text",
                        "text": json.dumps(envelope, ensure_ascii=False, separators=(",", ":")),
                        "text_elements": [],
                    },
                    *(
                        {"type": "localImage", "path": path}
                        for path in local_image_paths
                    ),
                ],
                "approvalPolicy": "on-request" if role is Role.EXECUTION else "never",
                "sandboxPolicy": (
                    {"type": "workspaceWrite", "networkAccess": False}
                    if role is Role.EXECUTION
                    else {"type": "readOnly", "networkAccess": False}
                ),
            }
        )
        if action == "scene_task_eligibility":
            params["effort"] = "low"
        self._remaining(deadline)
        try:
            result = self._client.request("turn/start", params)
            if not isinstance(result, Mapping):
                raise ValueError("turn/start ACK must be an object")
            state, _ = self._ledger.acknowledge(state, role, request_id, result)
        except Exception:
            self._ledger.fail_request(role, request_id)
            raise
        turn = state.turns[role]
        return state, _TurnCall(
            role,
            action,
            request_id,
            turn.thread_id or "",
            turn.turn_id or "",
            tuple(item.guidance_id for item in guidance),
        )

    def _finish_turn(
        self, state: ProjectState, call: _TurnCall, completed: CompletedTurn
    ) -> ProjectState:
        if (
            completed.thread_id != call.thread_id
            or completed.turn_id != call.turn_id
            or completed.status != "completed"
            or not isinstance(completed.payload, Mapping)
        ):
            raise ProjectEffectError(
                "TURN_OWNERSHIP_MISMATCH", "completed Turn does not match its acknowledged request"
            )
        if call.action == "scene_task_eligibility" and completed.native_subagents != 0:
            raise ProjectEffectError(
                "ELIGIBILITY_SUBAGENT_FORBIDDEN",
                "scene eligibility is a low-cost Supervisor-only Turn",
            )
        event = {
            "params": {
                "threadId": completed.thread_id,
                "turn": {"id": completed.turn_id},
            }
        }
        state = self._ledger.complete(state, call.role, event)
        state = mark_guidance_consumed(state, call.role, call.guidance_ids)
        return state

    def _record(self, state: ProjectState, observation: ProgressObservation) -> ProjectState:
        result = record_progress(state, observation)
        if result.violation:
            raise _BudgetStop(result.state, result.violation)
        return result.state

    def _refresh_guidance(self, state: ProjectState) -> ProjectState:
        latest = self._registry.get(state.project_id)
        if latest is None:
            return state
        external = latest.state
        known = {item.guidance_id for item in state.guidance}
        additions = tuple(item for item in external.guidance if item.guidance_id not in known)
        if not additions:
            return state
        if external.roles != state.roles or external.goal_thread_id != state.goal_thread_id:
            raise ProjectEffectError(
                "PROJECT_IDENTITY_CHANGED", "project identity changed while an effect was running"
            )
        return replace(
            state,
            guidance=(*state.guidance, *additions),
            requirements=external.requirements,
            revision=max(state.revision, external.revision) + 1,
        )

    def _base_request(self, state: ProjectState, role: Role, action: str) -> dict[str, Any]:
        return {
            "schema": "hia-project-role-request/1",
            "action": action,
            "project_id": state.project_id,
            "role": role.value,
            "authoritative_task_ref": {
                "task_id": state.authoritative_task_id,
                "sha256": state.authoritative_task_sha256,
            },
            "authoritative_task": self._authoritative_task_capsule(state),
            "requirements": [
                {
                    "requirement_id": item.requirement_id,
                    "kind": item.kind,
                    "status": item.status.value,
                    "source_ref": item.source_ref,
                }
                for item in state.requirements
            ],
            "native_subagent_budget": (
                0
                if role is Role.EXECUTION or action == "scene_task_eligibility"
                else state.budget.max_native_subagents_per_turn
            ),
        }

    def _authoritative_task_capsule(self, state: ProjectState) -> dict[str, Any]:
        """Return the sole persisted task record as a read-only Turn capsule."""

        record = self._registry.require(state.project_id)
        if (
            record.state.authoritative_task_id != state.authoritative_task_id
            or record.state.authoritative_task_sha256
            != state.authoritative_task_sha256
        ):
            raise ProjectEffectError(
                "AUTHORITATIVE_TASK_MISMATCH",
                "persisted authoritative task identity changed",
            )
        return {
            "schema": "hia-authoritative-task/1",
            "task_id": state.authoritative_task_id,
            "sha256": state.authoritative_task_sha256,
            "task_anchor": f"task:{state.authoritative_task_id}",
            "task_text": record.authoritative_task_text,
            "attachments": [
                {
                    "attachment_anchor": f"attachment:{item.sha256}",
                    "path": item.path,
                    "sha256": item.sha256,
                    "size_bytes": item.size_bytes,
                }
                for item in record.attachments
            ],
        }

    def _authoritative_image_paths(self, state: ProjectState) -> tuple[str, ...]:
        """Return only supported image attachments from the authoritative task."""

        record = self._registry.require(state.project_id)
        return tuple(
            item.path
            for item in record.attachments
            if Path(item.path).suffix.casefold() in {".png", ".jpg", ".jpeg", ".webp"}
        )

    def _set_goal(
        self,
        state: ProjectState,
        effect: PendingEffect,
        status: str,
        event: ProjectEvent | None,
    ) -> EffectResult:
        try:
            result = self._client.request(
                "thread/goal/set",
                {
                    "threadId": state.goal_thread_id,
                    "objective": f"Houdini project {state.authoritative_task_id}",
                    "status": status,
                    "tokenBudget": None,
                },
            )
        except TimeoutError:
            # A timeout does not prove whether the native Goal changed.  The
            # outer executor maps it to PROJECT_INTERRUPTED so the reducer first
            # obtains a confirmed pause before another recovery attempt.
            raise
        except Exception:
            return EffectResult(state, _goal_failure_event(status, "goal_rpc_failed"))
        goal = result.get("goal") if isinstance(result, Mapping) else None
        if (
            not isinstance(goal, Mapping)
            or goal.get("threadId") != state.goal_thread_id
            or goal.get("status") != status
        ):
            return EffectResult(
                state,
                _goal_failure_event(status, "goal_ack_mismatch"),
            )
        self._artifacts.put_effect(
            state.project_id, effect.effect_id, effect.kind, {"thread_id": state.goal_thread_id, "status": status}
        )
        return EffectResult(state, LifecycleEvent(event) if event is not None else None)

    def _require_plan(self, state: ProjectState) -> Mapping[str, Any]:
        value = self._artifacts.get_named(state.project_id, "plan")
        if not isinstance(value, Mapping):
            raise ProjectEffectError("MISSING_PLAN_ARTIFACT", "persisted plan is unavailable")
        return value

    def _current_stage(self, state: ProjectState) -> Mapping[str, Any]:
        plan = self._require_plan(state)
        for stage in plan.get("stages", []):
            if isinstance(stage, Mapping) and stage.get("stage_id") == state.stage.stage_id:
                return stage
        raise ProjectEffectError("MISSING_STAGE_ARTIFACT", "current stage card is unavailable")

    def _is_final_stage(self, state: ProjectState) -> bool:
        plan = self._require_plan(state)
        stages = plan.get("stages", [])
        return bool(stages) and stages[-1].get("stage_id") == state.stage.stage_id

    def _require_named(self, state: ProjectState, name: str) -> Mapping[str, Any]:
        value = self._artifacts.get_named(state.project_id, name)
        if not isinstance(value, Mapping):
            raise ProjectEffectError("MISSING_PROJECT_ARTIFACT", f"{name} is unavailable")
        return value

    def _remaining(self, deadline: float) -> float:
        value = deadline - self._clock()
        if value <= 0:
            raise TimeoutError("project effect deadline exceeded")
        return value


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProjectEffectError("INVALID_STRUCTURED_PAYLOAD", f"{name} must be non-empty")
    return value


def _goal_failure_event(status: str, error: str) -> LifecycleEvent:
    events = {
        "completed": ProjectEvent.GOAL_COMPLETION_FAILED,
        "paused": ProjectEvent.GOAL_PAUSE_FAILED,
        "active": ProjectEvent.GOAL_RESUME_FAILED,
    }
    try:
        event = events[status]
    except KeyError as exc:
        raise ProjectEffectError(
            "INVALID_GOAL_STATUS",
            f"unsupported native Goal status: {status}",
        ) from exc
    return LifecycleEvent(event, {"error": error})


def _validate_payload_shape(schema: str, payload: Mapping[str, Any]) -> None:
    """Validate structural response shape without judging natural-language quality."""

    fields = {
        "hia-project-eligibility/1": {"schema", "disposition", "reason"},
        "hia-project-plan/1": {
            "schema",
            "task_description",
            "user_facts",
            "blueprint_sections",
            "requirements",
            "stages",
        },
        "hia-project-authorization/1": {"schema", "authorized", "stage_ids"},
        "hia-project-execution/1": {
            "schema",
            "stage_id",
            "evidence_refs",
            "claims_visual_change",
        },
        "hia-project-review/1": {"schema", "stage_id", "reviewer", "claims"},
        "hia-project-stage-decision/1": {
            "schema",
            "stage_id",
            "decision",
            "final_stage",
            "repair_card",
        },
        "hia-project-repair-authorization/1": {
            "schema",
            "stage_id",
            "authorized",
            "repair_hash",
        },
    }
    expected = fields.get(schema)
    if expected is None or set(payload) != expected:
        raise ValueError("structured response fields do not match its schema")
    if schema == "hia-project-eligibility/1":
        if payload.get("disposition") not in {"eligible", "ineligible", "unclear"}:
            raise ValueError("eligibility disposition is invalid")
        _plain_text(payload.get("reason"))
    elif schema == "hia-project-plan/1":
        description = payload.get("task_description")
        facts = payload.get("user_facts")
        sections = payload.get("blueprint_sections")
        requirements = payload.get("requirements")
        stages = payload.get("stages")
        if not isinstance(description, Mapping) or not description:
            raise ValueError("task_description must be a non-empty object")
        if not isinstance(facts, list) or not facts:
            raise ValueError("user_facts must be non-empty")
        if not isinstance(sections, list) or not sections:
            raise ValueError("blueprint_sections must be non-empty")
        if not isinstance(requirements, list) or not requirements:
            raise ValueError("requirements must be non-empty")
        if not isinstance(stages, list) or not stages:
            raise ValueError("stages must be non-empty")
        for item in requirements:
            if not isinstance(item, Mapping) or set(item) != {
                "requirement_id",
                "kind",
                "description",
                "source_ref",
                "user_fact_ids",
            }:
                raise ValueError("requirement shape is invalid")
            for key in ("requirement_id", "kind", "description", "source_ref"):
                _plain_text(item.get(key))
            _text_list(item.get("user_fact_ids"))
        for item in stages:
            if not isinstance(item, Mapping):
                raise ValueError("stage card must be an object")
            parse_stage_card(item)
    elif schema == "hia-project-authorization/1":
        if not isinstance(payload.get("authorized"), bool):
            raise ValueError("authorized must be boolean")
        _text_list(payload.get("stage_ids"))
    elif schema == "hia-project-execution/1":
        _plain_text(payload.get("stage_id"))
        refs = payload.get("evidence_refs")
        if not isinstance(refs, list) or not refs or not all(isinstance(item, Mapping) for item in refs):
            raise ValueError("execution evidence references are invalid")
        if not isinstance(payload.get("claims_visual_change"), bool):
            raise ValueError("claims_visual_change must be boolean")
    elif schema == "hia-project-review/1":
        _plain_text(payload.get("stage_id"))
        _plain_text(payload.get("reviewer"))
        claims = payload.get("claims")
        if not isinstance(claims, list) or not claims:
            raise ValueError("review claims must be non-empty")
        for claim in claims:
            if not isinstance(claim, Mapping):
                raise ValueError("review claim must be an object")
            parse_review_claim(claim)
    elif schema == "hia-project-stage-decision/1":
        _plain_text(payload.get("stage_id"))
        if payload.get("decision") not in {"pass", "repair"}:
            raise ValueError("stage decision is invalid")
        if not isinstance(payload.get("final_stage"), bool):
            raise ValueError("final_stage must be boolean")
    elif schema == "hia-project-repair-authorization/1":
        _plain_text(payload.get("stage_id"))
        if not isinstance(payload.get("authorized"), bool):
            raise ValueError("authorized must be boolean")
        _plain_text(payload.get("repair_hash"))


def _plain_text(value: Any) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("required text is missing")


def _text_list(value: Any) -> None:
    if not isinstance(value, list) or not value or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise ValueError("required string list is invalid")


def _parse_repair_card(
    value: Any, stage_id: str, execution: Mapping[str, Any]
) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {
        "stage_id",
        "defect_hash",
        "instructions",
        "evidence_refs",
    }:
        raise ProjectEffectError("INVALID_REPAIR_CARD", "repair card fields are invalid")
    if value.get("stage_id") != stage_id:
        raise ProjectEffectError("INVALID_REPAIR_CARD", "repair card stage mismatch")
    defect_hash = _text(value.get("defect_hash"), "defect_hash")
    instructions = value.get("instructions")
    refs = value.get("evidence_refs")
    if not isinstance(instructions, list) or not instructions or not all(
        isinstance(item, Mapping) and item for item in instructions
    ):
        raise ProjectEffectError("INVALID_REPAIR_CARD", "repair instructions must be structured")
    if not isinstance(refs, list) or not refs or not all(isinstance(item, str) and item for item in refs):
        raise ProjectEffectError("INVALID_REPAIR_CARD", "repair evidence refs are invalid")
    available = {item["item_id"] for item in execution["evidence"]}
    if not set(refs).issubset(available):
        raise ProjectEffectError("INVALID_REPAIR_CARD", "repair cites unavailable evidence")
    result = json.loads(json.dumps(value, ensure_ascii=False))
    result["repair_hash"] = hashlib.sha256(
        json.dumps(result, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    result["defect_hash"] = defect_hash
    return result


def _capture_hash(evidence: EvidenceValidationResult) -> str | None:
    paths = [
        path
        for item in evidence.evidence
        if item.tool == "hia_capture_viewport"
        for path in item.artifact_paths
    ]
    if not paths:
        return None
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(Path(path).read_bytes())
    return digest.hexdigest()


def _capture_image_paths(execution: Mapping[str, Any]) -> tuple[str, ...]:
    """Return only current validated viewport artifacts, preserving evidence order."""

    paths: list[str] = []
    seen: set[str] = set()
    evidence = execution.get("evidence")
    if not isinstance(evidence, list):
        raise ProjectEffectError(
            "MISSING_EXECUTION_EVIDENCE", "validated execution evidence is unavailable"
        )
    for item in evidence:
        if not isinstance(item, Mapping) or item.get("tool") != "hia_capture_viewport":
            continue
        artifact_paths = item.get("artifact_paths")
        if not isinstance(artifact_paths, list):
            continue
        for path in artifact_paths:
            if isinstance(path, str) and path and path not in seen:
                seen.add(path)
                paths.append(path)
    if not paths:
        raise ProjectEffectError(
            "MISSING_VISUAL_REVIEW_IMAGE",
            "Visual Review requires current validated viewport image content",
        )
    return tuple(paths)


def _response_contract(schema: str) -> Mapping[str, Any]:
    contracts: dict[str, Mapping[str, Any]] = {
        "hia-project-eligibility/1": {
            "schema": schema,
            "disposition": "eligible|ineligible|unclear",
            "reason": "specific reason",
        },
        "hia-project-plan/1": {
            "schema": schema,
            "task_description": {
                "description": "complete task-specific production description",
                "source_anchors": ["task:<authoritative task ID>"],
            },
            "user_facts": [
                {
                    "fact_id": "stable user-fact ID",
                    "description": "one explicit user fact without invention",
                    "source_anchor": "task:<task ID> or attachment:<sha256>",
                }
            ],
            "blueprint_sections": [
                {
                    "section_id": section_id,
                    "title": "user-visible section title",
                    "description": "complete task-specific section content",
                    "source_anchors": ["authoritative source anchors"],
                    "user_fact_ids": ["covered user-fact IDs"],
                    "requirement_ids": ["covered requirement IDs"],
                    "stage_ids": ["covered stage IDs"],
                }
                for section_id in FULL_BLUEPRINT_SECTION_IDS
            ],
            "requirements": [
                {
                    "requirement_id": "stable task-specific ID",
                    "kind": "hard_constraint|structure|visual|material|behavior|delivery",
                    "description": "complete requirement meaning and observable consequence",
                    "source_ref": "authoritative task or attachment anchor",
                    "user_fact_ids": ["authoritative facts represented by this requirement"],
                }
            ],
            "stages": [
                {
                    "depth": "full",
                    "stage_id": "stable ordered ID",
                    "requirement_ids": ["covered requirement IDs"],
                    "ordered_steps": [
                        {
                            "step_id": "stable ID",
                            "dependencies": ["earlier step IDs only"],
                            "requirement_ids": ["requirements covered by this step"],
                            "user_fact_ids": ["authoritative facts implemented by this step"],
                            "target_network_region": {
                                "context": "OBJ/SOP/LOP/material context",
                                "target": "specific editable network region",
                            },
                            "native_operation_strategy": {
                                "native_nodes": ["specific native node types"],
                                "operation": "specific construction or validation operation",
                            },
                            "connections": [
                                {"from": "source/output", "to": "target/input", "purpose": "data-flow reason"}
                            ],
                            "parameter_dependencies": [
                                {"parameter": "path/name", "depends_on": "fact or upstream value", "effect": "observable dependency"}
                            ],
                            "expected_result": {
                                "visible": "specific visible result",
                                "editable": "specific procedural editability",
                            },
                            "evidence": {
                                "visual": "specific image content and view",
                                "technical": "specific HIA measurement or inspection",
                            },
                            "minimum_repair": {
                                "trigger": "specific failed observation",
                                "operation": "smallest evidence-backed repair",
                            },
                        }
                    ],
                    "evidence_contract": {
                        "visual": "required views/frames",
                        "technical": "required measurements or HIA tools",
                    },
                    "reviewers": ["visual_review", "technical_review"],
                    "failure_minimum_repair": "repair only evidence-backed deviations",
                }
            ],
        },
        "hia-project-authorization/1": {
            "schema": schema,
            "authorized": True,
            "stage_ids": ["exact persisted stage IDs in order"],
        },
        "hia-project-execution/1": {
            "schema": schema,
            "stage_id": "exact current stage ID",
            "evidence_refs": [
                {"item_id": "completed current-Turn HIA item ID"},
                {
                    "item_id": "current-Turn hia_capture_viewport item ID",
                    "frame": "exact numeric frame",
                    "path": "optional exact returned path",
                },
            ],
            "claims_visual_change": "boolean",
        },
        "hia-project-review/1": {
            "schema": schema,
            "stage_id": "exact current stage ID",
            "reviewer": "exact visual_review or technical_review role",
            "claims": [
                {
                    "disposition": "verified",
                    "claim_id": "stable requirement claim ID",
                    "evidence_refs": ["available evidence item IDs"],
                    "actual_evidence": "specific observed evidence",
                },
                {
                    "disposition": "failed",
                    "claim_id": "stable requirement claim ID",
                    "evidence_refs": ["available evidence item IDs"],
                    "deviation": "specific observed deviation",
                    "minimum_repair": "minimum evidence-backed repair",
                },
                {
                    "disposition": "unverified",
                    "claim_id": "stable requirement claim ID",
                    "missing_evidence": ["missing observation"],
                    "minimum_next_observation": "smallest next observation",
                },
            ],
        },
        "hia-project-stage-decision/1": {
            "schema": schema,
            "stage_id": "exact current stage ID",
            "decision": "pass|repair",
            "final_stage": "boolean derived from persisted plan",
            "repair_card": "null for pass; structured repair card for repair",
        },
        "hia-project-repair-authorization/1": {
            "schema": schema,
            "stage_id": "exact current stage ID",
            "authorized": True,
            "repair_hash": "exact persisted repair hash",
        },
    }
    contract = contracts.get(schema)
    if contract is None:
        raise ProjectEffectError(
            "UNKNOWN_RESPONSE_SCHEMA", f"no response contract for {schema}"
        )
    return contract
