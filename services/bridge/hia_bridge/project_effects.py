"""Bounded executor for explicit project-role actions.

The executor performs one native role Turn and returns the resulting lifecycle
event. It does not persist plans, reviews, repairs, or execution bodies; those
remain authoritative in the five native Codex Thread histories.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass, replace
import hashlib
import json
from pathlib import Path
import time
from typing import Any, Callable, Mapping, Protocol, Sequence

from .project_budget import ProgressObservation, record_progress
from .project_contracts import (
    ProjectState,
    Requirement,
    RequirementStatus,
    Role,
    StageState,
)
from .project_evidence import validate_evidence
from .project_guidance import validate_requirement_coverage
from .project_lifecycle import LifecycleEvent, ProjectEvent
from .project_payloads import (
    parse_review_claim,
    parse_stage_card,
    validate_blueprint_information,
    validate_plan_structure,
)
from .project_registry import ProjectRegistry
from .scene_writer import SceneWriterOwnership
from .project_runner import ProjectAction, ProjectActionResult
from .project_turns import TurnOwnershipLedger


class ProjectRoleClient(Protocol):
    def request(self, method: str, params: Mapping[str, Any]) -> Any: ...

    def wait_for_turn(
        self, thread_id: str, turn_id: str, timeout_seconds: float
    ) -> "CompletedTurn": ...

    def wait_until_thread_idle(
        self, thread_id: str, timeout_seconds: float
    ) -> bool: ...

    def start_turn_when_idle(
        self, params: Mapping[str, Any], timeout_seconds: float
    ) -> Any: ...


@dataclass(frozen=True)
class CompletedTurn:
    thread_id: str
    turn_id: str
    status: str
    payload: Mapping[str, Any]
    events: tuple[Mapping[str, Any], ...] = ()
    elapsed_seconds: int = 0
    payload_error_code: str | None = None
    payload_error_message: str | None = None


@dataclass(frozen=True)
class _TurnCall:
    role: Role
    action: str
    request_id: str
    thread_id: str
    turn_id: str
    guidance_ids: tuple[str, ...]


class ProjectRoleError(RuntimeError):
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


class ProjectRoleExecutor:
    """Execute one explicit role action with finite waiting."""

    def __init__(
        self,
        *,
        client: ProjectRoleClient,
        registry: ProjectRegistry,
        scene_writer: SceneWriterOwnership,
        allowed_evidence_roots: Sequence[str | Path],
        total_timeout_seconds: float = 300.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if total_timeout_seconds <= 0:
            raise ValueError("total_timeout_seconds must be positive")
        if scene_writer is None:
            raise ValueError("scene_writer is required")
        self._client = client
        self._registry = registry
        self._scene_writer = scene_writer
        self._allowed_roots = tuple(allowed_evidence_roots)
        self._timeout = float(total_timeout_seconds)
        self._clock = clock
        self._ledger = TurnOwnershipLedger()

    def execute(self, state: ProjectState, action: ProjectAction) -> ProjectActionResult:
        deadline = min(
            self._clock() + self._timeout,
            self._clock() + max(0, state.budget.max_elapsed_seconds - state.elapsed_seconds),
        )
        try:
            return self._execute(state, action, deadline)
        except _BudgetStop as stop:
            return ProjectActionResult(
                stop.state,
                LifecycleEvent(ProjectEvent.BUDGET_EXHAUSTED, {"reason": stop.reason}),
            )
        except _Interrupted as stop:
            return ProjectActionResult(
                stop.state,
                LifecycleEvent(ProjectEvent.PROJECT_INTERRUPTED, {"reason": stop.reason}),
            )
        except TimeoutError:
            return ProjectActionResult(
                state,
                LifecycleEvent(
                    ProjectEvent.PROJECT_INTERRUPTED,
                    {"reason": "project_role_timeout"},
                ),
            )

    def _execute(
        self, state: ProjectState, action: ProjectAction, deadline: float
    ) -> ProjectActionResult:
        kind = action.kind
        if kind == "start_supervisor":
            return self._supervisor_start(state, action, deadline)
        if kind == "request_plan":
            return self._plan(state, action, deadline)
        if kind == "request_authorization":
            return self._authorize(state, action, deadline)
        if kind == "start_execution":
            return self._execute_stage(state, action, deadline)
        if kind == "start_reviews":
            return self._review_stage(state, action, deadline)
        raise ProjectRoleError("UNKNOWN_PROJECT_ACTION", f"unsupported action kind: {kind}")

    def _supervisor_start(
        self, state: ProjectState, action: ProjectAction, deadline: float
    ) -> ProjectActionResult:
        request = self._base_request(state, Role.SUPERVISOR, "start_project")
        request["authoritative_task"] = self._authoritative_task_capsule(state)
        state, completed = self._run_structured(
            state,
            Role.SUPERVISOR,
            request,
            "hia-project-start/1",
            deadline,
            local_image_paths=tuple(action.data.get("local_image_paths", ())),
        )
        payload = completed.payload
        if set(payload) != {"schema", "route", "reply"}:
            raise ProjectRoleError("INVALID_PROJECT_START", "project start fields are invalid")
        route = payload.get("route")
        _text(payload.get("reply"), "Supervisor reply")
        if route == "planning":
            event = LifecycleEvent(ProjectEvent.PROJECT_ACCEPTED)
        elif route == "answered":
            event = LifecycleEvent(ProjectEvent.PROJECT_ANSWERED)
        else:
            raise ProjectRoleError("INVALID_PROJECT_START", "route must be planning or answered")
        return ProjectActionResult(state, event)

    def _plan(
        self, state: ProjectState, action: ProjectAction, deadline: float
    ) -> ProjectActionResult:
        request = self._base_request(state, Role.PLANNING, "create_plan_and_stage_cards")
        request["authoritative_task"] = self._authoritative_task_capsule(state)
        state, completed = self._run_structured(
            state,
            Role.PLANNING,
            request,
            "hia-project-plan/1",
            deadline,
            local_image_paths=self._read_latest_images(state, Role.SUPERVISOR),
        )
        payload = completed.payload
        capsule = self._authoritative_task_capsule(state)
        try:
            validate_plan_structure(
                payload,
                allowed_source_anchors=(
                    capsule["task_anchor"],
                    *(item["attachment_anchor"] for item in capsule["attachments"]),
                    *(f"guidance:{revision}" for revision in range(1, state.guidance_revision + 1)),
                ),
            )
        except ValueError as exc:
            raise ProjectRoleError("INVALID_PLAN_SCHEMA", str(exc)) from exc
        raw_requirements = payload.get("requirements")
        raw_stages = payload.get("stages")
        if not isinstance(raw_requirements, list) or not raw_requirements:
            raise ProjectRoleError("INVALID_PLAN_SCHEMA", "requirements must be non-empty")
        if not isinstance(raw_stages, list) or not raw_stages:
            raise ProjectRoleError("INVALID_PLAN_SCHEMA", "stages must be non-empty")
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
                raise ProjectRoleError("INVALID_PLAN_SCHEMA", "requirement fields are invalid")
            requirement_id = _text(raw.get("requirement_id"), "requirement_id")
            if requirement_id in seen:
                raise ProjectRoleError("INVALID_PLAN_SCHEMA", "requirement IDs must be unique")
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
            raise ProjectRoleError("INVALID_PLAN_SCHEMA", "stage cards are malformed or duplicated")
        try:
            validate_blueprint_information(payload, tuple(cards))
        except ValueError as exc:
            raise ProjectRoleError(
                "INVALID_PLAN_SCHEMA",
                str(exc),
            ) from exc
        covered = tuple(item for card in cards for item in card.requirement_ids)
        validate_requirement_coverage(requirements, covered)
        if state.requirements:
            validate_requirement_coverage(state.requirements, (item.requirement_id for item in requirements))
        inactive_requirements = tuple(
            item
            for item in state.requirements
            if item.status
            in {
                RequirementStatus.REMOVED_BY_USER,
                RequirementStatus.SUPERSEDED_BY_USER,
            }
            and item.requirement_id not in {current.requirement_id for current in requirements}
        )
        state = replace(
            state,
            requirements=tuple((*requirements, *inactive_requirements)),
            stage=StageState(stage_id=cards[0].stage_id, ordinal=1),
            revision=state.revision + 1,
        )
        return ProjectActionResult(state, LifecycleEvent(ProjectEvent.PLAN_READY))

    def _authorize(
        self, state: ProjectState, action: ProjectAction, deadline: float
    ) -> ProjectActionResult:
        plan = self._action_plan(state, action)
        request = self._base_request(state, Role.SUPERVISOR, "authorize_plan")
        request["authoritative_task"] = self._authoritative_task_capsule(state)
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
        if set(payload) != {
            "schema",
            "authorized",
            "stage_ids",
            "semantic_review",
        }:
            raise ProjectRoleError("INVALID_AUTHORIZATION_SCHEMA", "authorization fields are invalid")
        expected = [stage["stage_id"] for stage in plan["stages"]]
        try:
            _validate_semantic_authorization(payload["semantic_review"], plan)
        except ValueError as exc:
            raise ProjectRoleError("INVALID_AUTHORIZATION_SCHEMA", str(exc)) from exc
        all_pass = all(
            item["status"] == "pass"
            for item in payload["semantic_review"].values()
        )
        if (
            payload.get("authorized") is not True
            or not all_pass
            or payload.get("stage_ids") != expected
        ):
            raise ProjectRoleError(
                "PLAN_NOT_AUTHORIZED",
                "Supervisor did not pass every semantic item for the exact blueprint revision",
            )
        return ProjectActionResult(state, LifecycleEvent(ProjectEvent.PLAN_AUTHORIZED))

    def _execute_stage(
        self, state: ProjectState, action: ProjectAction, deadline: float
    ) -> ProjectActionResult:
        return self._execute_stage_owned(state, action, deadline)

    def _execute_stage_owned(
        self, state: ProjectState, action: ProjectAction, deadline: float
    ) -> ProjectActionResult:
        plan = self._action_plan(state, action)
        if (
            state.status.value != "executing"
        ):
            raise ProjectRoleError("INVALID_EXECUTION_PHASE", "Execution requires the executing phase")
        stage = self._current_stage(state, plan)
        repair = bool(action.data.get("repair", False))
        request = self._base_request(
            state, Role.EXECUTION, "execute_repair" if repair else "execute_stage"
        )
        request["stage_card"] = stage
        if repair:
            decision = self._read_latest_payload(
                state, Role.SUPERVISOR, "hia-project-stage-decision/1"
            )
            repair_card = decision.get("repair_card")
            if not isinstance(repair_card, Mapping):
                raise ProjectRoleError(
                    "MISSING_REPAIR_CARD",
                    "Supervisor native Thread has no repair card for this stage",
                )
            request["repair_card"] = repair_card
        state, completed = self._run_structured(
            state,
            Role.EXECUTION,
            request,
            "hia-project-execution/1",
            deadline,
            record_success=False,
        )
        payload = completed.payload
        if set(payload) != {"schema", "stage_id", "evidence_refs", "claims_visual_change"}:
            raise ProjectRoleError("INVALID_EXECUTION_SCHEMA", "execution fields are invalid")
        if payload.get("stage_id") != stage["stage_id"]:
            raise ProjectRoleError("STAGE_OWNERSHIP_MISMATCH", "Execution returned the wrong stage")
        references = payload.get("evidence_refs")
        if not isinstance(references, list):
            raise ProjectRoleError("INVALID_EXECUTION_SCHEMA", "evidence_refs must be a list")
        current = state.turns.get(Role.EXECUTION)
        if current is None or current.turn_id != completed.turn_id:
            raise ProjectRoleError("TURN_OWNERSHIP_MISMATCH", "Execution Turn identity was lost")
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
        capture_required, technical_required = _stage_evidence_needs(stage)
        if capture_required and "hia_capture_viewport" not in tools:
            raise ProjectRoleError(
                "MISSING_VISUAL_EVIDENCE",
                "the Full stage contract requires a current viewport capture",
            )
        if technical_required and not (tools - {"hia_capture_viewport"}):
            raise ProjectRoleError(
                "MISSING_TECHNICAL_EVIDENCE",
                "the Full stage contract requires current technical evidence",
            )
        observation = ProgressObservation(
            role=Role.EXECUTION,
            kind="repair" if repair else "execution",
            elapsed_seconds=0,
            evidence_bytes=evidence.added_evidence_bytes,
            evidence_ids=tuple(item.item_id for item in evidence.evidence),
        )
        state = self._record(state, observation)
        return ProjectActionResult(
            state,
            LifecycleEvent(ProjectEvent.STAGE_EXECUTED),
        )

    def _review_stage(
        self, state: ProjectState, action: ProjectAction, deadline: float
    ) -> ProjectActionResult:
        plan = self._action_plan(state, action)
        stage = self._current_stage(state, plan)
        execution = self._read_execution_history(state, stage)
        state, reviews = self._parallel_reviews(state, stage, execution, deadline)
        coverage_proof = _review_coverage_proof(stage, reviews)
        decision_request = self._base_request(state, Role.SUPERVISOR, "decide_stage_review")
        decision_request.update(
            {
                "stage_card": stage,
                "execution": execution,
                "reviews": reviews,
                "review_coverage": coverage_proof,
            }
        )
        state, completed = self._run_structured(
            state,
            Role.SUPERVISOR,
            decision_request,
            "hia-project-stage-decision/1",
            deadline,
        )
        payload = completed.payload
        if set(payload) != {
            "schema",
            "stage_id",
            "decision",
            "final_stage",
            "repair_card",
            "approved_exemptions",
        }:
            raise ProjectRoleError("INVALID_STAGE_DECISION", "stage decision fields are invalid")
        if payload.get("stage_id") != stage["stage_id"]:
            raise ProjectRoleError("STAGE_OWNERSHIP_MISMATCH", "Supervisor decided the wrong stage")
        failed = any(
            claim["disposition"] in {"failed", "unverified"}
            for review in reviews
            for claim in review["claims"]
        )
        required_exemptions = _review_exemptions(reviews)
        approved_exemptions = _approved_review_exemptions(
            payload.get("approved_exemptions")
        )
        if not set(approved_exemptions).issubset(required_exemptions):
            raise ProjectRoleError(
                "INVALID_STAGE_DECISION",
                "approved exemptions must exactly match evidence-bound review exemptions",
            )
        exemptions_match = approved_exemptions == required_exemptions
        final_expected = self._is_final_stage(state, plan)
        if payload.get("final_stage") is not final_expected:
            raise ProjectRoleError("INVALID_STAGE_DECISION", "final_stage does not match the plan")
        decision = payload.get("decision")
        if decision == "pass":
            if failed or not exemptions_match or payload.get("repair_card") is not None:
                raise ProjectRoleError(
                    "INVALID_STAGE_DECISION",
                    "failed, unverified, or unapproved exemption claims cannot be passed",
                )
            stages = plan.get("stages")
            next_stage_id = None
            if not final_expected and isinstance(stages, list):
                index = next(
                    (
                        offset
                        for offset, item in enumerate(stages)
                        if isinstance(item, Mapping)
                        and item.get("stage_id") == state.stage.stage_id
                    ),
                    None,
                )
                if index is not None and index + 1 < len(stages):
                    candidate = stages[index + 1]
                    next_stage_id = (
                        candidate.get("stage_id")
                        if isinstance(candidate, Mapping)
                        else None
                    )
            event = LifecycleEvent(
                ProjectEvent.REVIEWS_PASSED,
                {
                    "final_stage": final_expected,
                    "next_stage_id": next_stage_id,
                },
            )
        elif decision == "repair":
            card = _parse_repair_card(payload.get("repair_card"), stage["stage_id"], execution)
            if not failed and exemptions_match:
                raise ProjectRoleError(
                    "INVALID_STAGE_DECISION",
                    "repair requires a failed, unverified, or unapproved exemption claim",
                )
            event = LifecycleEvent(
                ProjectEvent.REVIEWS_FAILED,
                {},
            )
        else:
            raise ProjectRoleError("INVALID_STAGE_DECISION", "decision must be pass or repair")
        return ProjectActionResult(state, event)

    def _parallel_reviews(
        self,
        state: ProjectState,
        stage: Mapping[str, Any],
        execution: Mapping[str, Any],
        deadline: float,
    ) -> tuple[ProjectState, list[dict[str, Any]]]:
        calls: list[_TurnCall] = []
        capture_required, _ = _stage_evidence_needs(stage)
        visual_images = (
            _capture_image_paths(execution) if capture_required else ()
        )
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
                ),
            )
            try:
                payload = self._parse_review_payload(
                    completed.payload, call.role, stage, execution
                )
            except ProjectRoleError as exc:
                state = self._record(
                    state,
                    ProgressObservation(role=call.role, kind="schema_correction"),
                )
                correction_request = self._base_request(
                    state, call.role, "correct_stage_review"
                )
                correction_request.update(
                    {
                        "stage_card": stage,
                        "execution": execution,
                        "invalid_review": completed.payload,
                        "review_error": {"code": exc.code, "message": str(exc)},
                    }
                )
                state, completed = self._run_structured(
                    state,
                    call.role,
                    correction_request,
                    "hia-project-review/1",
                    deadline,
                    local_image_paths=(
                        visual_images if call.role is Role.VISUAL_REVIEW else ()
                    ),
                    payload_validator=lambda candidate, review_role=call.role: (
                        self._validate_review_correction(
                            candidate, review_role, stage, execution
                        )
                    ),
                    allow_correction=False,
                )
                payload = self._parse_review_payload(
                    completed.payload, call.role, stage, execution
                )
            reviews.append(payload)
        state = self._refresh_guidance(state)
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
            raise ProjectRoleError("INVALID_REVIEW_SCHEMA", "review fields are invalid")
        if payload.get("schema") != "hia-project-review/1":
            raise ProjectRoleError("INVALID_REVIEW_SCHEMA", "review schema is invalid")
        if payload.get("reviewer") != role.value or payload.get("stage_id") != stage["stage_id"]:
            raise ProjectRoleError("REVIEW_OWNERSHIP_MISMATCH", "review role or stage mismatch")
        raw_claims = payload.get("claims")
        if not isinstance(raw_claims, list) or not raw_claims:
            raise ProjectRoleError("INVALID_REVIEW_SCHEMA", "review claims must be non-empty")
        try:
            claims = [
                parse_review_claim(item)
                for item in raw_claims
                if isinstance(item, Mapping)
            ]
        except (TypeError, ValueError) as exc:
            raise ProjectRoleError(
                "INVALID_REVIEW_SCHEMA", "review claim is malformed"
            ) from exc
        if len(claims) != len(raw_claims):
            raise ProjectRoleError("INVALID_REVIEW_SCHEMA", "review claim is malformed")
        expected_ids = tuple(stage.get("requirement_ids") or ())
        if not expected_ids or any(not isinstance(item, str) or not item for item in expected_ids):
            raise ProjectRoleError(
                "INVALID_REVIEW_REQUIREMENTS", "stage requirement IDs are malformed"
            )
        claim_ids = tuple(claim.claim_id for claim in claims)
        if len(set(claim_ids)) != len(claim_ids):
            raise ProjectRoleError(
                "DUPLICATE_REVIEW_CLAIM", "each stage requirement must be claimed exactly once"
            )
        unknown = sorted(set(claim_ids) - set(expected_ids))
        missing = sorted(set(expected_ids) - set(claim_ids))
        if unknown:
            raise ProjectRoleError(
                "UNKNOWN_REVIEW_CLAIM", f"unknown stage requirement claims: {unknown}"
            )
        if missing:
            raise ProjectRoleError(
                "MISSING_REVIEW_CLAIM", f"missing stage requirement claims: {missing}"
            )
        available = {item["item_id"] for item in execution["evidence"]}
        capture_ids = {
            item["item_id"]
            for item in execution["evidence"]
            if item.get("tool") == "hia_capture_viewport"
            and item.get("artifact_paths")
        }
        technical_ids = available - capture_ids
        capture_required, technical_required = _stage_evidence_needs(stage)
        for claim in claims:
            refs = getattr(claim, "evidence_refs", ())
            if not set(refs).issubset(available):
                raise ProjectRoleError("UNKNOWN_REVIEW_EVIDENCE", "review cites unavailable evidence")
            if claim.disposition in {"verified", "failed", "not_applicable"}:
                required = capture_ids if role is Role.VISUAL_REVIEW else technical_ids
                kind_required = (
                    capture_required
                    if role is Role.VISUAL_REVIEW
                    else technical_required
                )
                if kind_required and not set(refs).intersection(required):
                    kind = "capture" if role is Role.VISUAL_REVIEW else "technical"
                    raise ProjectRoleError(
                        "REVIEW_EVIDENCE_KIND_MISMATCH",
                        f"{role.value} {claim.disposition} claim requires current {kind} evidence",
                    )
        return json.loads(json.dumps(payload, ensure_ascii=False))

    def _validate_review_correction(
        self,
        payload: Mapping[str, Any],
        role: Role,
        stage: Mapping[str, Any],
        execution: Mapping[str, Any],
    ) -> None:
        try:
            self._parse_review_payload(payload, role, stage, execution)
        except ProjectRoleError as exc:
            raise ValueError(f"{exc.code}: {exc}") from exc

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
        payload_validator: Callable[[Mapping[str, Any]], None] | None = None,
        allow_correction: bool = True,
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
        if schema == "hia-project-plan/1":
            current_request["response_rules"]["depth_policy"] = _plan_depth_policy()
        while True:
            reservation = None
            scene_owner = None
            if role is Role.EXECUTION:
                reservation = self._scene_writer.reserve("project", state.project_id)
            try:
                state, call = self._start_turn(
                    state,
                    role,
                    current_request,
                    deadline,
                    local_image_paths=local_image_paths,
                )
                if reservation is not None:
                    scene_owner = self._scene_writer.bind(reservation, call.turn_id)
            except Exception:
                if reservation is not None:
                    self._scene_writer.abandon_uncreated(reservation)
                raise
            try:
                completed = self._client.wait_for_turn(
                    call.thread_id, call.turn_id, self._remaining(deadline)
                )
            except TimeoutError as exc:
                raise _Interrupted(state, "project_role_turn_timeout") from exc
            if scene_owner is not None:
                if not self._scene_writer.turn_terminal(scene_owner):
                    raise ProjectRoleError(
                        "SCENE_WRITER_STILL_ACTIVE",
                        "Execution ended while an HIA scene write was still active",
                    )
            state = self._finish_turn(state, call, completed)
            state = self._refresh_guidance(state)
            try:
                if completed.payload_error_code is not None:
                    raise ValueError(completed.payload_error_code)
                if completed.payload.get("schema") != schema:
                    raise ValueError(f"expected schema {schema}")
                _validate_payload_shape(schema, completed.payload)
                if payload_validator is not None:
                    payload_validator(completed.payload)
                if record_success:
                    state = self._record(
                        state,
                        ProgressObservation(
                            role=role,
                            kind="turn",
                            elapsed_seconds=completed.elapsed_seconds,
                        ),
                    )
                return state, completed
            except (TypeError, ValueError) as exc:
                if correction >= (1 if allow_correction else 0):
                    raise ProjectRoleError(
                        "INVALID_STRUCTURED_OUTPUT",
                        f"{role.value} returned invalid {schema} twice",
                    ) from exc
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
                if schema == "hia-project-plan/1":
                    current_request["response_rules"]["depth_policy"] = _plan_depth_policy()
                current_request["schema_correction"] = {
                    "attempt": correction,
                    "required_schema": schema,
                    "error": completed.payload_error_code or type(exc).__name__,
                }
    def _start_turn(
        self,
        state: ProjectState,
        role: Role,
        request: Mapping[str, Any],
        deadline: float,
        *,
        local_image_paths: Sequence[str] = (),
    ) -> tuple[ProjectState, _TurnCall]:
        envelope = dict(request)
        action = str(envelope.get("action") or "")
        envelope["guidance_revision"] = state.guidance_revision
        envelope["guidance_source"] = "native Thread history"
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
        self._remaining(deadline)
        try:
            result = self._client.start_turn_when_idle(
                params, self._remaining(deadline)
            )
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
            (),
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
            raise ProjectRoleError(
                "TURN_OWNERSHIP_MISMATCH", "completed Turn does not match its acknowledged request"
            )
        event = {
            "params": {
                "threadId": completed.thread_id,
                "turn": {"id": completed.turn_id},
            }
        }
        state = self._ledger.complete(state, call.role, event)
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
        if external.guidance_revision <= state.guidance_revision:
            return state
        if external.roles != state.roles:
            raise ProjectRoleError(
                "PROJECT_IDENTITY_CHANGED", "project identity changed while a role action was running"
            )
        return replace(
            state,
            guidance_revision=external.guidance_revision,
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
            "requirements": [
                {
                    "requirement_id": item.requirement_id,
                    "kind": item.kind,
                    "status": item.status.value,
                    "source_ref": item.source_ref,
                }
                for item in state.requirements
            ],
            "guidance": self._native_guidance(state, role),
        }

    def _native_guidance(
        self, state: ProjectState, role: Role
    ) -> list[dict[str, Any]]:
        if state.guidance_revision == 0:
            return []
        by_revision: dict[int, dict[str, Any]] = {}
        for turn in self._read_thread_turns(state, Role.SUPERVISOR):
            items = turn.get("items")
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, Mapping) or item.get("type") != "userMessage":
                    continue
                content = item.get("content")
                if not isinstance(content, list):
                    continue
                for entry in content:
                    if not isinstance(entry, Mapping) or entry.get("type") != "text":
                        continue
                    raw_text = entry.get("text")
                    if not isinstance(raw_text, str):
                        continue
                    try:
                        payload = json.loads(raw_text)
                    except ValueError:
                        continue
                    if not isinstance(payload, Mapping) or payload.get("schema") != "hia-project-guidance/1":
                        continue
                    if payload.get("project_id") != state.project_id:
                        raise ProjectRoleError(
                            "INVALID_GUIDANCE_HISTORY",
                            "native Supervisor guidance references another project",
                        )
                    revision = payload.get("revision")
                    target = payload.get("target_role")
                    text = payload.get("text")
                    if (
                        not isinstance(revision, int)
                        or isinstance(revision, bool)
                        or revision < 1
                        or target not in {None, *(role.value for role in Role)}
                        or not isinstance(text, str)
                        or not text.strip()
                    ):
                        raise ProjectRoleError(
                            "INVALID_GUIDANCE_HISTORY",
                            "native Supervisor guidance is malformed",
                        )
                    normalized = {
                        "revision": revision,
                        "target_role": target,
                        "text": text,
                    }
                    existing = by_revision.get(revision)
                    if existing is not None and existing != normalized:
                        raise ProjectRoleError(
                            "INVALID_GUIDANCE_HISTORY",
                            "native Supervisor guidance revisions conflict",
                        )
                    by_revision[revision] = normalized
        expected = set(range(1, state.guidance_revision + 1))
        observed = set(by_revision).intersection(expected)
        if observed != expected:
            raise ProjectRoleError(
                "MISSING_GUIDANCE_HISTORY",
                "registry guidance revision has no complete native Supervisor history",
            )
        return [
            by_revision[revision]
            for revision in sorted(expected)
            if role is Role.SUPERVISOR
            or by_revision[revision]["target_role"] in {None, role.value}
        ]

    def _authoritative_task_capsule(self, state: ProjectState) -> dict[str, Any]:
        """Return the sole persisted task record as a read-only Turn capsule."""

        record = self._registry.require(state.project_id)
        if (
            record.state.authoritative_task_id != state.authoritative_task_id
            or record.state.authoritative_task_sha256
            != state.authoritative_task_sha256
        ):
            raise ProjectRoleError(
                "AUTHORITATIVE_TASK_MISMATCH",
                "persisted authoritative task identity changed",
            )
        return {
            "schema": "hia-authoritative-task/1",
            "task_id": state.authoritative_task_id,
            "sha256": state.authoritative_task_sha256,
            "task_anchor": f"task:{state.authoritative_task_id}",
            "task_text": record.authoritative_task_text,
            "attachments": [],
        }

    def _action_plan(
        self, state: ProjectState, action: ProjectAction
    ) -> Mapping[str, Any]:
        return self._read_latest_payload(state, Role.PLANNING, "hia-project-plan/1")

    def _read_latest_payload(
        self, state: ProjectState, role: Role, schema: str
    ) -> Mapping[str, Any]:
        turns = self._read_thread_turns(state, role)
        for turn in reversed(turns):
            items = turn.get("items") if isinstance(turn, Mapping) else None
            if not isinstance(items, list):
                continue
            for item in reversed(items):
                if not isinstance(item, Mapping) or item.get("type") != "agentMessage":
                    continue
                text = item.get("text")
                if not isinstance(text, str):
                    continue
                try:
                    payload = json.loads(text)
                except ValueError:
                    continue
                if isinstance(payload, Mapping) and payload.get("schema") == schema:
                    return payload
        raise ProjectRoleError(
            "MISSING_NATIVE_ROLE_OUTPUT",
            f"{role.value} native Thread has no {schema} output",
        )

    def _read_thread_turns(
        self, state: ProjectState, role: Role
    ) -> list[Mapping[str, Any]]:
        thread_id = state.roles[role].thread_id
        result = self._client.request(
            "thread/read",
            {"threadId": thread_id, "includeTurns": True},
        )
        thread = result.get("thread") if isinstance(result, Mapping) else None
        turns = thread.get("turns") if isinstance(thread, Mapping) else None
        if (
            not isinstance(thread, Mapping)
            or thread.get("id") != thread_id
            or not isinstance(turns, list)
        ):
            raise ProjectRoleError("INVALID_THREAD_HISTORY", "native Thread history is unavailable")
        return [turn for turn in turns if isinstance(turn, Mapping)]

    def _read_latest_images(
        self, state: ProjectState, role: Role
    ) -> tuple[str, ...]:
        paths: list[str] = []
        for turn in self._read_thread_turns(state, role):
            items = turn.get("items")
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, Mapping):
                    continue
                content = item.get("content")
                if item.get("type") != "userMessage" or not isinstance(content, list):
                    continue
                for entry in content:
                    if not isinstance(entry, Mapping):
                        continue
                    path = entry.get("path")
                    if entry.get("type") == "localImage" and isinstance(path, str):
                        paths.append(path)
        return tuple(dict.fromkeys(paths))

    def _read_execution_history(
        self, state: ProjectState, stage: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        thread_id = state.roles[Role.EXECUTION].thread_id
        for turn in reversed(self._read_thread_turns(state, Role.EXECUTION)):
            turn_id = turn.get("id")
            items = turn.get("items")
            if not isinstance(turn_id, str) or not isinstance(items, list):
                continue
            payload: Mapping[str, Any] | None = None
            events: list[Mapping[str, Any]] = []
            for item in items:
                if not isinstance(item, Mapping):
                    continue
                if item.get("type") == "agentMessage" and isinstance(item.get("text"), str):
                    try:
                        candidate = json.loads(item["text"])
                    except ValueError:
                        candidate = None
                    if (
                        isinstance(candidate, Mapping)
                        and candidate.get("schema") == "hia-project-execution/1"
                        and candidate.get("stage_id") == stage.get("stage_id")
                    ):
                        payload = candidate
                elif item.get("type") == "mcpToolCall":
                    events.append(
                        {
                            "method": "item/completed",
                            "params": {
                                "threadId": thread_id,
                                "turnId": turn_id,
                                "item": item,
                            },
                        }
                    )
            if payload is None:
                continue
            references = payload.get("evidence_refs")
            if not isinstance(references, list):
                raise ProjectRoleError("INVALID_EXECUTION_SCHEMA", "evidence_refs must be a list")
            evidence = validate_evidence(
                references=references,
                tool_events=events,
                execution_thread_id=thread_id,
                execution_turn_id=turn_id,
                allowed_roots=self._allowed_roots,
                existing_total_evidence_bytes=0,
                max_total_evidence_bytes=state.budget.max_total_evidence_bytes,
            )
            return {
                "stage_id": stage["stage_id"],
                "turn_id": turn_id,
                "thread_id": thread_id,
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
        raise ProjectRoleError(
            "MISSING_NATIVE_ROLE_OUTPUT",
            "Execution native Thread has no output for the current stage",
        )

    def _current_stage(
        self, state: ProjectState, plan: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        for stage in plan.get("stages", []):
            if isinstance(stage, Mapping) and stage.get("stage_id") == state.stage.stage_id:
                return stage
        raise ProjectRoleError("MISSING_STAGE_ARTIFACT", "current stage card is unavailable")

    def _is_final_stage(
        self, state: ProjectState, plan: Mapping[str, Any]
    ) -> bool:
        stages = plan.get("stages", [])
        return bool(stages) and stages[-1].get("stage_id") == state.stage.stage_id

    def _remaining(self, deadline: float) -> float:
        value = deadline - self._clock()
        if value <= 0:
            raise TimeoutError("project role deadline exceeded")
        return value


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProjectRoleError("INVALID_STRUCTURED_PAYLOAD", f"{name} must be non-empty")
    return value


def _validate_payload_shape(schema: str, payload: Mapping[str, Any]) -> None:
    """Validate structural response shape without judging natural-language quality."""

    fields = {
        "hia-project-start/1": {"schema", "route", "reply"},
        "hia-project-plan/1": {
            "schema",
            "task_description",
            "user_facts",
            "blueprint_sections",
            "requirements",
            "stages",
        },
        "hia-project-authorization/1": {
            "schema",
            "authorized",
            "stage_ids",
            "semantic_review",
        },
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
            "approved_exemptions",
        },
    }
    expected = fields.get(schema)
    if expected is None or set(payload) != expected:
        raise ValueError("structured response fields do not match its schema")
    if schema == "hia-project-start/1":
        if payload.get("route") not in {"planning", "answered"}:
            raise ValueError("project route is invalid")
        _plain_text(payload.get("reply"))
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
        _validate_semantic_review_shape(payload.get("semantic_review"))
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
        _approved_review_exemptions(payload.get("approved_exemptions"))


def _plain_text(value: Any) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("required text is missing")


def _text_list(value: Any) -> None:
    if not isinstance(value, list) or not value or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise ValueError("required string list is invalid")


_SEMANTIC_REVIEW_KEYS = (
    "task_anchors",
    "blueprint_sections",
    "requirements",
    "stages_and_steps",
    "anti_filler",
    "native_strategy_and_dependencies",
    "evidence_contracts",
    "minimum_repairs",
)


def _validate_semantic_review_shape(value: Any) -> None:
    if not isinstance(value, Mapping) or set(value) != set(_SEMANTIC_REVIEW_KEYS):
        raise ValueError("semantic_review must contain every fixed review item")
    for name in _SEMANTIC_REVIEW_KEYS:
        item = value[name]
        if not isinstance(item, Mapping) or set(item) != {
            "status",
            "referenced_ids",
            "findings",
        }:
            raise ValueError(f"semantic review item {name} has invalid fields")
        if item.get("status") not in {"pass", "fail"}:
            raise ValueError(f"semantic review item {name} has invalid status")
        _text_list(item.get("referenced_ids"))
        findings = item.get("findings")
        if not isinstance(findings, list) or not all(
            isinstance(finding, str) and finding.strip() for finding in findings
        ):
            raise ValueError(f"semantic review item {name} findings are invalid")
        if item.get("status") == "fail" and not findings:
            raise ValueError(f"failed semantic review item {name} requires findings")


def _validate_semantic_authorization(value: Any, plan: Mapping[str, Any]) -> None:
    _validate_semantic_review_shape(value)
    stage_ids = [str(stage["stage_id"]) for stage in plan["stages"]]
    step_ids = [
        str(step["step_id"])
        for stage in plan["stages"]
        for step in stage["ordered_steps"]
    ]
    anchors = set(plan["task_description"]["source_anchors"])
    anchors.update(str(item["source_anchor"]) for item in plan["user_facts"])
    expected = {
        "task_anchors": anchors,
        "blueprint_sections": {
            str(item["section_id"]) for item in plan["blueprint_sections"]
        },
        "requirements": {
            str(item["requirement_id"]) for item in plan["requirements"]
        },
        "stages_and_steps": set((*stage_ids, *step_ids)),
        "anti_filler": set((*stage_ids, *step_ids)),
        "native_strategy_and_dependencies": set(step_ids),
        "evidence_contracts": set((*stage_ids, *step_ids)),
        "minimum_repairs": set((*stage_ids, *step_ids)),
    }
    for name, identifiers in expected.items():
        actual = set(value[name]["referenced_ids"])
        if actual != identifiers:
            raise ValueError(
                f"semantic review item {name} must reference its complete actual ID set"
            )


def _parse_repair_card(
    value: Any, stage_id: str, execution: Mapping[str, Any]
) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {
        "stage_id",
        "instructions",
        "evidence_refs",
    }:
        raise ProjectRoleError("INVALID_REPAIR_CARD", "repair card fields are invalid")
    if value.get("stage_id") != stage_id:
        raise ProjectRoleError("INVALID_REPAIR_CARD", "repair card stage mismatch")
    instructions = value.get("instructions")
    refs = value.get("evidence_refs")
    if not isinstance(instructions, list) or not instructions or not all(
        isinstance(item, Mapping) and item for item in instructions
    ):
        raise ProjectRoleError("INVALID_REPAIR_CARD", "repair instructions must be structured")
    if not isinstance(refs, list) or not refs or not all(isinstance(item, str) and item for item in refs):
        raise ProjectRoleError("INVALID_REPAIR_CARD", "repair evidence refs are invalid")
    available = {item["item_id"] for item in execution["evidence"]}
    if not set(refs).issubset(available):
        raise ProjectRoleError("INVALID_REPAIR_CARD", "repair cites unavailable evidence")
    return json.loads(json.dumps(value, ensure_ascii=False))


def _capture_image_paths(execution: Mapping[str, Any]) -> tuple[str, ...]:
    """Return only current validated viewport artifacts, preserving evidence order."""

    paths: list[str] = []
    seen: set[str] = set()
    evidence = execution.get("evidence")
    if not isinstance(evidence, list):
        raise ProjectRoleError(
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
        raise ProjectRoleError(
            "MISSING_VISUAL_REVIEW_IMAGE",
            "Visual Review requires current validated viewport image content",
        )
    return tuple(paths)


def _stage_evidence_needs(stage: Mapping[str, Any]) -> tuple[bool, bool]:
    """Return only evidence kinds explicitly requested by a Full stage card."""

    if stage.get("depth") != "full":
        return False, False
    contract = stage.get("evidence_contract")
    if not isinstance(contract, Mapping):
        return False, False
    return bool(contract.get("capture")), bool(contract.get("technical"))


def _review_exemptions(
    reviews: Sequence[Mapping[str, Any]],
) -> tuple[tuple[str, str, str, tuple[str, ...]], ...]:
    exemptions: list[tuple[str, str, str, tuple[str, ...]]] = []
    for review in reviews:
        reviewer = str(review.get("reviewer") or "")
        for claim in review.get("claims", ()):
            if not isinstance(claim, Mapping) or claim.get("disposition") != "not_applicable":
                continue
            exemption = claim.get("exemption")
            if not isinstance(exemption, Mapping):
                raise ProjectRoleError(
                    "INVALID_REVIEW_EXEMPTION", "review exemption is malformed"
                )
            exemptions.append(
                (
                    reviewer,
                    str(claim.get("claim_id") or ""),
                    str(exemption.get("reason") or ""),
                    tuple(str(item) for item in exemption.get("evidence_refs", ())),
                )
            )
    return tuple(sorted(exemptions))


def _approved_review_exemptions(
    value: Any,
) -> tuple[tuple[str, str, str, tuple[str, ...]], ...]:
    if not isinstance(value, list):
        raise ValueError("approved_exemptions must be a list")
    approvals: list[tuple[str, str, str, tuple[str, ...]]] = []
    expected = {"reviewer", "requirement_id", "reason", "evidence_refs"}
    for item in value:
        if not isinstance(item, Mapping) or set(item) != expected:
            raise ValueError("approved exemption shape is invalid")
        reviewer = item.get("reviewer")
        if reviewer not in {
            Role.VISUAL_REVIEW.value,
            Role.TECHNICAL_REVIEW.value,
        }:
            raise ValueError("approved exemption reviewer is invalid")
        requirement_id = item.get("requirement_id")
        reason = item.get("reason")
        evidence_refs = item.get("evidence_refs")
        if not isinstance(requirement_id, str) or not requirement_id.strip():
            raise ValueError("approved exemption requirement_id is invalid")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("approved exemption reason is invalid")
        if (
            not isinstance(evidence_refs, list)
            or not evidence_refs
            or any(not isinstance(ref, str) or not ref for ref in evidence_refs)
        ):
            raise ValueError("approved exemption evidence_refs are invalid")
        approvals.append((reviewer, requirement_id, reason, tuple(evidence_refs)))
    if len(set(approvals)) != len(approvals):
        raise ValueError("approved exemptions must be unique")
    return tuple(sorted(approvals))


def _review_coverage_proof(
    stage: Mapping[str, Any], reviews: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    requirement_ids = tuple(stage.get("requirement_ids") or ())
    expected_reviewers = {
        Role.VISUAL_REVIEW.value,
        Role.TECHNICAL_REVIEW.value,
    }
    by_reviewer: dict[str, list[str]] = {}
    for review in reviews:
        reviewer = str(review.get("reviewer") or "")
        if reviewer in by_reviewer:
            raise ProjectRoleError(
                "DUPLICATE_STAGE_REVIEW", "each required reviewer may report only once"
            )
        by_reviewer[reviewer] = [
            str(claim.get("claim_id") or "")
            for claim in review.get("claims", ())
            if isinstance(claim, Mapping)
        ]
    if set(by_reviewer) != expected_reviewers:
        raise ProjectRoleError(
            "MISSING_STAGE_REVIEW", "both independent stage reviews are required"
        )
    if any(set(claim_ids) != set(requirement_ids) for claim_ids in by_reviewer.values()):
        raise ProjectRoleError(
            "INCOMPLETE_REVIEW_COVERAGE",
            "both reviewers must cover every stage requirement",
        )
    return {
        "stage_id": stage.get("stage_id"),
        "requirement_ids": list(requirement_ids),
        "reviewers": {
            reviewer: {"claim_ids": list(requirement_ids), "complete": True}
            for reviewer in sorted(by_reviewer)
        },
    }


def _plan_depth_policy() -> Mapping[str, Any]:
    return {
        "selection": "choose the smallest depth that fully covers the current task",
        "direct": (
            "one known deterministic scene operation; return one compact stage and "
            "omit evidence_contract, reviewers, and failure_minimum_repair"
        ),
        "focused": (
            "a bounded subsystem or short multi-step change; keep the blueprint "
            "proportional and omit Full-only stage fields"
        ),
        "full": (
            "a substantial multi-stage asset or consequential workflow only; use "
            "all fourteen stable blueprint sections, both reviewers, every Full-only "
            "stage field, and the 10000/2500/350 information floors"
        ),
    }


def _response_contract(schema: str) -> Mapping[str, Any]:
    contracts: dict[str, Mapping[str, Any]] = {
        "hia-project-start/1": {
            "schema": schema,
            "route": "planning|answered",
            "reply": "natural answer or concise handoff acknowledgement",
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
                    "source_anchor": "task:<ID>, attachment:<sha256>, or guidance:<ID>",
                }
            ],
            "blueprint_sections": [
                {
                    "section_id": "task-specific ID; Full uses all fourteen stable IDs",
                    "title": "user-visible section title",
                    "description": "complete task-specific section content",
                    "source_anchors": ["authoritative source anchors"],
                    "user_fact_ids": ["covered user-fact IDs"],
                    "requirement_ids": ["covered requirement IDs"],
                    "stage_ids": ["covered stage IDs"],
                }
            ],
            "requirements": [
                {
                    "requirement_id": "stable task-specific ID",
                    "kind": "hard_constraint|structure|visual|material|behavior|delivery",
                    "description": "complete requirement meaning and observable consequence",
                    "source_ref": "authoritative task, attachment, or guidance anchor",
                    "user_fact_ids": ["authoritative facts represented by this requirement"],
                }
            ],
            "stages": [
                {
                    "depth": "direct|focused|full",
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
                }
            ],
        },
        "hia-project-authorization/1": {
            "schema": schema,
            "authorized": True,
            "stage_ids": ["exact plan stage IDs in order"],
            "semantic_review": {
                name: {
                    "status": "pass|fail",
                    "referenced_ids": ["complete actual IDs from the supplied plan"],
                    "findings": ["specific finding; empty list is allowed only for pass"],
                }
                for name in _SEMANTIC_REVIEW_KEYS
            },
        },
        "hia-project-execution/1": {
            "schema": schema,
            "stage_id": "exact current stage ID",
            "evidence_refs": [
                {
                    "item_id": "completed current-Turn HIA item required by the stage evidence contract",
                    "frame": "include only for a requested capture",
                    "path": "include only when returned by that item",
                }
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
                    "claim_id": "exact current stage requirement_id",
                    "evidence_refs": ["available evidence item IDs"],
                    "actual_evidence": "specific observed evidence",
                },
                {
                    "disposition": "failed",
                    "claim_id": "exact current stage requirement_id",
                    "evidence_refs": ["available evidence item IDs"],
                    "deviation": "specific observed deviation",
                    "minimum_repair": "minimum evidence-backed repair",
                },
                {
                    "disposition": "unverified",
                    "claim_id": "exact current stage requirement_id",
                    "missing_evidence": ["missing observation"],
                    "minimum_next_observation": "smallest next observation",
                },
                {
                    "disposition": "not_applicable",
                    "claim_id": "exact current stage requirement_id",
                    "exemption": {
                        "reason": "specific reason the requirement cannot apply",
                        "evidence_refs": ["available evidence item IDs proving the reason"],
                    },
                },
            ],
        },
        "hia-project-stage-decision/1": {
            "schema": schema,
            "stage_id": "exact current stage ID",
            "decision": "pass|repair",
            "final_stage": "boolean derived from persisted plan",
            "repair_card": "null for pass; structured repair card for repair",
            "approved_exemptions": [
                {
                    "reviewer": "exact reviewer owning the not_applicable claim",
                    "requirement_id": "exact current stage requirement_id",
                    "reason": "exact structured exemption reason from the claim",
                    "evidence_refs": ["exact available evidence IDs from the claim"],
                }
            ],
        },
    }
    contract = contracts.get(schema)
    if contract is None:
        raise ProjectRoleError(
            "UNKNOWN_RESPONSE_SCHEMA", f"no response contract for {schema}"
        )
    return contract
