"""Thin command runner for the pure project lifecycle reducer.

Each call persists a deterministic pending effect before external RPC work.  An
effect ACK is applied explicitly; this module contains no autonomous while-loop.
"""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from typing import Any, Mapping, Protocol

from .project_contracts import PendingEffect, ProjectState
from .project_effects import EffectResult
from .project_lifecycle import LifecycleEvent, reduce_project
from .project_registry import ProjectRecord, ProjectRegistry


class EffectExecutor(Protocol):
    def execute(self, state: ProjectState, effect: PendingEffect) -> EffectResult: ...


class ProjectRunner:
    def __init__(self, registry: ProjectRegistry) -> None:
        self._registry = registry

    def dispatch(self, project_id: str, event: LifecycleEvent) -> ProjectRecord:
        record = self._registry.require(project_id)
        if record.state.pending_effects:
            raise ValueError("project has an unacknowledged pending effect")
        next_state, commands = reduce_project(record.state, event)
        effects = tuple(
            _pending_effect(next_state, index, command.kind.value, command.data or {})
            for index, command in enumerate(commands)
        )
        next_state = replace(next_state, pending_effects=effects)
        updated = ProjectRecord(
            next_state, record.authoritative_task_text, record.attachments
        )
        self._registry.put(updated, expected_revision=record.state.revision)
        return updated

    def execute_next(self, project_id: str, executor: EffectExecutor) -> ProjectRecord:
        record = self._registry.require(project_id)
        if not record.state.pending_effects:
            raise ValueError("project has no pending effect")
        effect = record.state.pending_effects[0]
        outcome = executor.execute(record.state, effect)
        return self.acknowledge_effect(project_id, effect.effect_id, outcome)

    def acknowledge(
        self,
        project_id: str,
        effect_id: str,
        outcome: LifecycleEvent,
    ) -> ProjectRecord:
        record = self._registry.require(project_id)
        return self.acknowledge_effect(
            project_id,
            effect_id,
            EffectResult(record.state, outcome),
        )

    def acknowledge_effect(
        self,
        project_id: str,
        effect_id: str,
        outcome: EffectResult,
    ) -> ProjectRecord:
        record = self._registry.require(project_id)
        effects = record.state.pending_effects
        if not effects or effects[0].effect_id != effect_id:
            raise ValueError("effect ACK does not match the oldest pending effect")
        base = _reconcile_effect_state(record.state, outcome.state, effect_id)
        base = replace(
            base,
            pending_effects=effects[1:],
            revision=record.state.revision,
        )
        if outcome.event is None:
            next_state = replace(base, revision=record.state.revision + 1)
            commands = ()
        else:
            next_state, commands = reduce_project(base, outcome.event)
        appended = tuple(
            _pending_effect(next_state, index, command.kind.value, command.data or {})
            for index, command in enumerate(commands, start=len(base.pending_effects))
        )
        next_state = replace(
            next_state,
            pending_effects=(*base.pending_effects, *appended),
        )
        updated = ProjectRecord(
            next_state, record.authoritative_task_text, record.attachments
        )
        self._registry.put(updated, expected_revision=record.state.revision)
        return updated


def _reconcile_effect_state(
    current: ProjectState,
    outcome: ProjectState,
    effect_id: str,
) -> ProjectState:
    """Merge only concurrent guidance/runtime edits into an effect receipt."""

    stable_current = (
        current.project_id,
        current.goal_thread_id,
        current.authoritative_task_id,
        current.authoritative_task_sha256,
        current.status,
    )
    stable_outcome = (
        outcome.project_id,
        outcome.goal_thread_id,
        outcome.authoritative_task_id,
        outcome.authoritative_task_sha256,
        outcome.status,
    )
    if stable_outcome != stable_current:
        raise ValueError("effect outcome changed authoritative project identity or status")
    if (
        not outcome.pending_effects
        or outcome.pending_effects[0].effect_id != effect_id
    ):
        raise ValueError("effect outcome lost the oldest pending effect")

    roles = dict(outcome.roles)
    for role, latest in current.roles.items():
        produced = roles.get(role)
        if produced is None:
            roles[role] = latest
        elif produced.thread_id != latest.thread_id:
            raise ValueError("effect outcome changed an existing role Thread identity")
        else:
            # A user runtime edit applies to the role's next Turn.  Preserve the
            # latest explicit settings without changing the active Turn receipt.
            roles[role] = latest

    guidance = {item.guidance_id: item for item in outcome.guidance}
    for item in current.guidance:
        existing = guidance.get(item.guidance_id)
        if existing is not None and existing != item:
            raise ValueError("effect outcome conflicts with persisted guidance")
        guidance[item.guidance_id] = item
    ordered_guidance = tuple(sorted(guidance.values(), key=lambda item: item.revision))
    if len({item.revision for item in ordered_guidance}) != len(ordered_guidance):
        raise ValueError("effect outcome has duplicate guidance revisions")
    latest_current_revision = max(
        (item.revision for item in current.guidance), default=0
    )
    latest_outcome_revision = max(
        (item.revision for item in outcome.guidance), default=0
    )
    requirements = (
        current.requirements
        if latest_current_revision > latest_outcome_revision
        else outcome.requirements
    )
    consumed = dict(outcome.guidance_consumed)
    for role, revision in current.guidance_consumed.items():
        consumed[role] = max(consumed.get(role, 0), revision)
    return replace(
        outcome,
        roles=roles,
        guidance=ordered_guidance,
        guidance_consumed=consumed,
        requirements=requirements,
    )


def _pending_effect(
    state: ProjectState, index: int, kind: str, data: Mapping[str, Any]
) -> PendingEffect:
    encoded = json.dumps(
        {
            "project_id": state.project_id,
            "revision": state.revision,
            "index": index,
            "kind": kind,
            "data": dict(data),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    return PendingEffect(f"effect-{digest[:24]}", kind, data)
