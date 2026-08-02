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
from .project_lifecycle import LifecycleEvent, reduce_project
from .project_registry import ProjectRecord, ProjectRegistry


class EffectExecutor(Protocol):
    def execute(self, state: ProjectState, effect: PendingEffect) -> LifecycleEvent: ...


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
        updated = ProjectRecord(next_state, record.authoritative_task_text)
        self._registry.put(updated, expected_revision=record.state.revision)
        return updated

    def execute_next(self, project_id: str, executor: EffectExecutor) -> ProjectRecord:
        record = self._registry.require(project_id)
        if not record.state.pending_effects:
            raise ValueError("project has no pending effect")
        effect = record.state.pending_effects[0]
        outcome = executor.execute(record.state, effect)
        return self.acknowledge(project_id, effect.effect_id, outcome)

    def acknowledge(
        self,
        project_id: str,
        effect_id: str,
        outcome: LifecycleEvent,
    ) -> ProjectRecord:
        record = self._registry.require(project_id)
        effects = record.state.pending_effects
        if not effects or effects[0].effect_id != effect_id:
            raise ValueError("effect ACK does not match the oldest pending effect")
        base = replace(record.state, pending_effects=effects[1:])
        next_state, commands = reduce_project(base, outcome)
        appended = tuple(
            _pending_effect(next_state, index, command.kind.value, command.data or {})
            for index, command in enumerate(commands, start=len(base.pending_effects))
        )
        next_state = replace(
            next_state,
            pending_effects=(*base.pending_effects, *appended),
        )
        updated = ProjectRecord(next_state, record.authoritative_task_text)
        self._registry.put(updated, expected_revision=record.state.revision)
        return updated


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
