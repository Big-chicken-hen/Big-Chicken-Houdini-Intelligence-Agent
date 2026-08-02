"""Thin project-team facade for Session, HTTP, and Panel integration."""

from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import re
import threading
from typing import Any, Callable, Mapping
from typing import Protocol
import uuid

from .errors import BridgeError
from .project_contracts import (
    ProjectState,
    ProjectStatus,
    Role,
    RoleThread,
    authoritative_task_identity,
)
from .project_guidance import RequirementDelta, publish_guidance
from .project_identity import parse_project_thread_source
from .project_lifecycle import LifecycleEvent, ProjectEvent
from .project_registry import ProjectRecord, ProjectRegistry
from .project_runner import ProjectRunner
from .project_thread_factory import (
    AppServerClient,
    ProjectRoleCreationError,
    ProjectThreadFactory,
    ROLE_TITLES,
)


PROJECT_TEAM_SCHEMA = "hia-project-team/2"
SETTINGS_SCHEMA = "hia-project-team-settings/3"
PROJECT_MODES = frozenset({"single", "team"})
_TERMINAL = frozenset(
    {ProjectStatus.COMPLETED, ProjectStatus.FAILED}
)
_GUIDANCE_INACTIVE = frozenset(
    {*_TERMINAL, ProjectStatus.STOPPED}
)


class ProjectGuidanceUnavailable(ValueError):
    """Structured rejection for guidance that no live workflow can consume."""

    code = "PROJECT_GUIDANCE_INACTIVE"

    def __init__(self, project_id: str, status: ProjectStatus) -> None:
        self.project_id = project_id
        self.status = status.value
        self.recoverable = False
        super().__init__("project is not running and cannot accept guidance")


class ProjectRuntimeSelectionError(ValueError):
    """A role runtime selection is absent from the live Codex catalog."""

    code = "PROJECT_RUNTIME_SELECTION_INVALID"

    def __init__(
        self,
        message: str,
        *,
        field: str,
        model: str | None,
        allowed: list[str],
    ) -> None:
        self.field = field
        self.model = model
        self.allowed = allowed
        super().__init__(message)


class ProjectWorkflowControl(Protocol):
    def start(self, project_id: str) -> bool: ...

    def stop(self, project_id: str) -> bool: ...

    def resume(self, project_id: str) -> bool: ...


class ProjectServiceClient(AppServerClient, Protocol):
    def wait_for_turn(
        self, thread_id: str, turn_id: str, timeout_seconds: float
    ) -> Any: ...

    def has_active_thread(self, thread_id: str) -> bool: ...


class ProjectGuidanceRecordError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class ProjectTeamSettings:
    def __init__(self, path: Path) -> None:
        self._path = path.resolve()
        self._lock = threading.RLock()

    def get(self) -> str:
        with self._lock:
            if not self._path.exists():
                return "single"
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, ValueError) as exc:
                raise BridgeError(
                    "SETTINGS_CORRUPTED",
                    "Project team settings exist but cannot be read",
                    http_status=500,
                ) from exc
            mode = raw.get("mode") if isinstance(raw, Mapping) else None
            if not isinstance(raw, Mapping) or raw.get("schema") != SETTINGS_SCHEMA:
                raise BridgeError(
                    "SETTINGS_CORRUPTED",
                    "Project team settings use an unsupported schema",
                    http_status=500,
                )
            if mode not in PROJECT_MODES:
                raise BridgeError(
                    "SETTINGS_CORRUPTED",
                    "Project team settings contain an invalid mode",
                    http_status=500,
                )
            return mode

    def set(self, mode: str) -> None:
        if mode not in PROJECT_MODES:
            raise ValueError("mode must be single or team")
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self._path.with_name(f".{self._path.name}.{os.getpid()}.tmp")
            temporary.write_text(
                json.dumps({"schema": SETTINGS_SCHEMA, "mode": mode}) + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, self._path)


class ProjectTeamService:
    def __init__(
        self,
        *,
        client: ProjectServiceClient,
        project_root: Path,
        registry: ProjectRegistry,
        settings: ProjectTeamSettings,
        thread_factory: ProjectThreadFactory,
        runner: ProjectRunner,
        workflow: ProjectWorkflowControl,
        model_catalog: Callable[[], Mapping[str, Any]],
    ) -> None:
        if any(
            dependency is None
            for dependency in (
                client,
                registry,
                settings,
                thread_factory,
                runner,
                workflow,
                model_catalog,
            )
        ):
            raise ValueError("project service dependencies must be configured")
        for method_name in ("request", "wait_for_turn", "has_active_thread"):
            if not callable(getattr(client, method_name, None)):
                raise ValueError(
                    f"project service client must implement {method_name}"
                )
        self._client = client
        self._project_root = project_root.resolve()
        self._registry = registry
        self._settings = settings
        self._factory = thread_factory
        self._model_catalog = model_catalog
        self._runner = runner
        self._workflow = workflow
        self._lock = threading.RLock()

    def set_mode(self, mode: str) -> dict[str, Any]:
        self._settings.set(mode)
        return self.snapshot()

    def role_identity_for_thread(self, thread_id: str) -> tuple[str, Role] | None:
        """Return only an exact persisted Project/Role identity for a Thread."""

        if not isinstance(thread_id, str) or not thread_id:
            return None
        with self._lock:
            for record in self._registry.list():
                matches = [
                    role
                    for role, binding in record.state.roles.items()
                    if binding.thread_id == thread_id
                ]
                if len(matches) > 1:
                    raise ValueError("project registry reuses a Thread within a project")
                if matches:
                    return record.state.project_id, matches[0]
        return None

    def read_role_thread(self, thread_id: str) -> dict[str, Any]:
        """Read one project role without resuming it or changing ordinary Session."""

        identity = self.role_identity_for_thread(thread_id)
        if identity is None:
            raise KeyError(thread_id)
        project_id, role = identity
        result = self._client.request(
            "thread/read",
            {"threadId": thread_id, "includeTurns": True},
        )
        thread = result.get("thread") if isinstance(result, Mapping) else None
        native_identity = parse_project_thread_source(
            thread.get("threadSource") if isinstance(thread, Mapping) else None
        )
        if (
            native_identity is None
            or native_identity.project_id != project_id
            or native_identity.role is not role
        ):
            raise ValueError("native project Thread source does not match the registry")
        return {
            "project_id": project_id,
            "role": role.value,
            "thread_id": thread_id,
            "read": self._project_role_messages(result, thread_id),
        }

    def start_team_project(
        self,
        *,
        task_text: str,
        model: str | None = None,
        effort: str | None = None,
        service_tier: str | None = None,
        local_image_paths: list[str] | None = None,
    ) -> dict[str, Any]:
        task_id, digest = authoritative_task_identity(task_text)
        project_id = f"project-{uuid.uuid4()}"
        image_paths = self._validated_image_paths(local_image_paths or [])
        state = ProjectState(
            project_id=project_id,
            authoritative_task_id=task_id,
            authoritative_task_sha256=digest,
        )
        try:
            state = self._factory.create_all_roles(
                state,
                model=model,
                effort=effort,
                service_tier=service_tier,
            )
        except ProjectRoleCreationError as exc:
            raise BridgeError(
                "PROJECT_ROLE_CREATION_FAILED",
                str(exc),
                500,
                {"orphan_thread_ids": list(exc.orphan_thread_ids)},
            ) from exc
        supervisor_id = state.supervisor_thread_id
        record = ProjectRecord(state, task_text)
        self._registry.put(record)
        record = self._runner.dispatch(
            project_id,
            LifecycleEvent(
                ProjectEvent.PROJECT_STARTED,
                {"local_image_paths": list(image_paths)},
            ),
        )
        if not self._workflow.start(project_id):
            self._runner.cancel_and_dispatch(
                project_id,
                LifecycleEvent(
                    ProjectEvent.PROJECT_FAILED,
                    {"error": "project workflow did not start"},
                ),
            )
            raise RuntimeError("project workflow did not start")
        return {
            "project_id": project_id,
            "root_thread_id": supervisor_id,
            "routing": "team",
            "project_team": self.snapshot(),
            "next_action": (
                self._runner.next_action(project_id).kind
                if self._runner.next_action(project_id) is not None
                else None
            ),
        }

    def append_guidance(
        self,
        *,
        project_id: str,
        text: str,
        thread_id: str | None = None,
        requirement_delta: RequirementDelta | None = None,
    ) -> dict[str, Any]:
        if not isinstance(text, str) or not text.strip():
            raise ValueError("guidance text must be non-empty")
        with self._lock:
            record = self._registry.require(project_id)
            if record.state.status in _GUIDANCE_INACTIVE:
                raise ProjectGuidanceUnavailable(project_id, record.state.status)
            target_role = None
            if thread_id is not None:
                matches = [
                    role
                    for role, binding in record.state.roles.items()
                    if binding.thread_id == thread_id
                ]
                if len(matches) != 1:
                    raise ValueError("thread_id is not an explicit member of this project")
                target_role = matches[0]
            # Validate the requirement delta before writing the sole guidance body
            # to native Thread history.
            publish_guidance(
                record.state,
                text,
                target_role=target_role,
                requirement_delta=requirement_delta,
            )
            self._record_native_guidance(
                record,
                text=text,
                target_role=target_role,
            )
            self._runner.merge_guidance(
                project_id,
                text,
                target_role=target_role,
                requirement_delta=requirement_delta,
            )
        # Guidance is consumed by the next explicit role stage.  Never restart
        # a completed role Turn in the background.
        return self.snapshot()

    def _record_native_guidance(
        self,
        record: ProjectRecord,
        *,
        text: str,
        target_role: Role | None,
    ) -> None:
        supervisor = record.state.roles.get(Role.SUPERVISOR)
        if supervisor is None:
            raise ProjectGuidanceRecordError(
                "PROJECT_GUIDANCE_HISTORY_UNAVAILABLE",
                "project has no native Supervisor Thread for guidance history",
            )
        if self._client.has_active_thread(supervisor.thread_id):
            raise ProjectGuidanceRecordError(
                "PROJECT_GUIDANCE_BUSY",
                "Supervisor is running a project Turn; retry guidance after it finishes",
            )
        revision = record.state.guidance_revision + 1
        envelope = {
            "schema": "hia-project-guidance/1",
            "project_id": record.state.project_id,
            "revision": revision,
            "target_role": target_role.value if target_role is not None else None,
            "text": text,
        }
        params: dict[str, Any] = {
            "threadId": supervisor.thread_id,
            "input": [
                {
                    "type": "text",
                    "text": json.dumps(
                        envelope, ensure_ascii=False, separators=(",", ":")
                    ),
                    "text_elements": [],
                }
            ],
            "approvalPolicy": "never",
            "sandboxPolicy": {"type": "readOnly", "networkAccess": False},
        }
        if supervisor.model is not None:
            params["model"] = supervisor.model
        if supervisor.effort is not None:
            params["effort"] = supervisor.effort
        if supervisor.service_tier is not None:
            params["serviceTier"] = supervisor.service_tier
        try:
            result = self._client.request("turn/start", params)
            turn = result.get("turn") if isinstance(result, Mapping) else None
            turn_id = turn.get("id") if isinstance(turn, Mapping) else None
            if not isinstance(turn_id, str) or not turn_id:
                raise ValueError("turn/start did not acknowledge a guidance Turn")
            completed = self._client.wait_for_turn(
                supervisor.thread_id,
                turn_id,
                120.0,
            )
            payload = getattr(completed, "payload", None)
            if (
                getattr(completed, "status", None) != "completed"
                or not isinstance(payload, Mapping)
                or set(payload) != {"schema", "revision"}
                or payload.get("schema") != "hia-project-guidance-recorded/1"
                or payload.get("revision") != revision
            ):
                raise ValueError("Supervisor did not confirm the exact guidance revision")
        except ProjectGuidanceRecordError:
            raise
        except Exception as exc:
            raise ProjectGuidanceRecordError(
                "PROJECT_GUIDANCE_RECORD_FAILED",
                "guidance was not confirmed in native Supervisor Thread history",
            ) from exc

    def continue_project(self, *, project_id: str) -> dict[str, Any]:
        with self._lock:
            record = self._registry.require(project_id)
            if record.state.status not in {
                ProjectStatus.WAITING_USER,
                ProjectStatus.STOPPED,
            }:
                raise ValueError("only a waiting or stopped project can continue")
        continuation = self._resolve_continuation(record)
        with self._lock:
            current = self._registry.require(project_id)
            if current.state.revision != record.state.revision:
                raise ValueError("project changed while continuation was inspected")
            if not self._workflow.resume(project_id, continuation):
                raise RuntimeError("project workflow did not resume")
        return self.snapshot()

    def stop_project(self, *, project_id: str) -> dict[str, Any]:
        with self._lock:
            record = self._registry.require(project_id)
            if record.state.status in _TERMINAL:
                raise ValueError("terminal project cannot be stopped")
            if record.state.status is ProjectStatus.STOPPED:
                return self.snapshot()
            self._workflow.stop(project_id)
        return self.snapshot()

    def set_role_runtime(
        self,
        *,
        project_id: str,
        thread_id: str,
        model: str | None,
        effort: str | None,
        service_tier: str | None,
    ) -> dict[str, Any]:
        with self._lock:
            record = self._registry.require(project_id)
            if record.state.status in _TERMINAL:
                raise ValueError("terminal project role runtime cannot be changed")
            matches = [
                role
                for role, binding in record.state.roles.items()
                if binding.thread_id == thread_id
            ]
            if len(matches) != 1:
                raise ValueError("thread_id is not an explicit member of this project")
            role = matches[0]
            expected_revision = record.state.revision
        model, effort, service_tier = self._validated_runtime_selection(
            model,
            effort,
            service_tier,
        )
        with self._lock:
            record = self._registry.require(project_id)
            binding = record.state.roles.get(role)
            if (
                record.state.revision != expected_revision
                or binding is None
                or binding.thread_id != thread_id
                or record.state.status in _TERMINAL
            ):
                raise ValueError(
                    "project role changed while its runtime catalog was validated; retry"
                )
            roles = dict(record.state.roles)
            roles[role] = replace(
                roles[role],
                model=model,
                effort=effort,
                service_tier=service_tier,
            )
            state = replace(record.state, roles=roles, revision=record.state.revision + 1)
            self._registry.put(
                ProjectRecord(
                    state, record.authoritative_task_text
                ),
                expected_revision=record.state.revision,
            )
        return self.snapshot()

    def _validated_runtime_selection(
        self,
        model: str | None,
        effort: str | None,
        service_tier: str | None,
    ) -> tuple[str, str | None, str | None]:
        for field, value in (
            ("model", model),
            ("effort", effort),
            ("service_tier", service_tier),
        ):
            if value is not None and (
                not isinstance(value, str)
                or not value.strip()
                or any(ord(character) < 32 for character in value)
            ):
                raise ProjectRuntimeSelectionError(
                    f"{field} must be selected from the live Codex model catalog",
                    field=field,
                    model=model if isinstance(model, str) else None,
                    allowed=[],
                )
        payload = self._model_catalog()
        raw_models = payload.get("models") if isinstance(payload, Mapping) else None
        if not isinstance(raw_models, list):
            raise ProjectRuntimeSelectionError(
                "The live Codex model catalog is invalid; refresh models and retry",
                field="model",
                model=model,
                allowed=[],
            )
        models = {
            item.get("model"): item
            for item in raw_models
            if isinstance(item, Mapping)
            and isinstance(item.get("model"), str)
            and item.get("model")
        }
        selected = models.get(model) if model is not None else next(
            (item for item in models.values() if item.get("isDefault") is True),
            None,
        )
        if selected is None:
            raise ProjectRuntimeSelectionError(
                "Choose a model from the refreshed Codex model catalog",
                field="model",
                model=model,
                allowed=sorted(models),
            )
        selected_model = selected.get("model")
        modalities = selected.get("inputModalities")
        if not isinstance(modalities, list) or "text" not in modalities:
            raise ProjectRuntimeSelectionError(
                "The selected model cannot accept project role text instructions",
                field="model",
                model=str(selected_model),
                allowed=sorted(
                    key
                    for key, item in models.items()
                    if isinstance(item.get("inputModalities"), list)
                    and "text" in item["inputModalities"]
                ),
            )
        allowed_efforts = [
            item.get("reasoningEffort")
            for item in selected.get("supportedReasoningEfforts", [])
            if isinstance(item, Mapping)
            and isinstance(item.get("reasoningEffort"), str)
        ]
        if effort is not None and effort not in allowed_efforts:
            raise ProjectRuntimeSelectionError(
                "Choose a reasoning effort supported by the selected model",
                field="effort",
                model=str(selected_model),
                allowed=allowed_efforts,
            )
        allowed_tiers = [
            item.get("id")
            for item in selected.get("serviceTiers", [])
            if isinstance(item, Mapping) and isinstance(item.get("id"), str)
        ]
        if service_tier is not None and service_tier not in allowed_tiers:
            raise ProjectRuntimeSelectionError(
                "Choose a service tier supported by the selected model",
                field="service_tier",
                model=str(selected_model),
                allowed=allowed_tiers,
            )
        return str(selected_model), effort, service_tier

    def snapshot(self) -> dict[str, Any]:
        records = self._registry.list()
        projects = [self._public_project(record) for record in records]
        return {
            "schema": PROJECT_TEAM_SCHEMA,
            "revision": max((record.state.revision for record in records), default=0),
            "state_status": "ready",
            "settings": {"mode": self._settings.get(), "writable": True},
            "projects": projects,
        }

    def _public_project(self, record: ProjectRecord) -> dict[str, Any]:
        state = record.state
        runtime_allowed = state.status not in _TERMINAL
        guidance_allowed = state.status not in _GUIDANCE_INACTIVE
        threads = []
        for role in Role:
            binding = state.roles.get(role)
            if binding is None:
                continue
            threads.append(
                {
                    "role": role.value,
                    "role_title": ROLE_TITLES[role],
                    "thread_id": binding.thread_id,
                    "model": binding.model,
                    "effort": binding.effort,
                    "service_tier": binding.service_tier,
                    "status": (
                        "running"
                        if state.turns.get(role) and state.turns[role].active
                        else "waiting"
                    ),
                    "actions": {
                        "open_thread": True,
                        "append_guidance": guidance_allowed,
                        "set_role_runtime": runtime_allowed,
                    },
                }
            )
        return {
            "project_id": state.project_id,
            "title": record.authoritative_task_text.strip().splitlines()[0][:160],
            "status": state.status.value,
            "stage": state.stage.stage_id,
            "root_thread_id": state.supervisor_thread_id,
            "attention_reason": state.attention_reason,
            "consumed_turns": sum(item.consumed_turns for item in state.turns.values()),
            "last_error": state.last_error,
            "latest_evidence_ids": list(state.stage.latest_evidence_ids),
            "attachment_count": 0,
            "requirements": [
                {
                    "requirement_id": item.requirement_id,
                    "kind": item.kind,
                    "status": item.status.value,
                }
                for item in state.requirements
            ],
            "actions": {
                "append_guidance": guidance_allowed,
                "continue": state.status in {
                    ProjectStatus.WAITING_USER,
                    ProjectStatus.STOPPED,
                },
                "stop": state.status not in _TERMINAL,
            },
            "threads": threads,
        }

    def _validated_image_paths(self, values: list[str]) -> tuple[str, ...]:
        if len(values) > 16:
            raise ValueError("a project task accepts at most 16 images")
        paths: list[str] = []
        total = 0
        for value in values:
            if not isinstance(value, str) or not value:
                raise ValueError("image path must be a non-empty string")
            path = Path(value).resolve(strict=True)
            try:
                path.relative_to(self._project_root)
            except ValueError as exc:
                raise ValueError("project image must stay inside the project") from exc
            if not path.is_file():
                raise ValueError("project image path must reference a file")
            total += path.stat().st_size
            if total > 128 * 1024 * 1024:
                raise ValueError("project images exceed 128 MiB")
            paths.append(str(path))
        return tuple(paths)

    def _resolve_continuation(self, record: ProjectRecord) -> dict[str, object]:
        """Choose one new role Turn from native history; ambiguity stays user-visible."""

        plan = self._latest_role_payload(
            record,
            Role.PLANNING,
            "hia-project-plan/1",
        )
        if plan is None:
            return {"command": "request_plan"}
        authorization = self._latest_role_payload(
            record,
            Role.SUPERVISOR,
            "hia-project-authorization/1",
        )
        if authorization is None or authorization.get("authorized") is not True:
            return {"command": "request_authorization"}
        stage_id = record.state.stage.stage_id
        stages = plan.get("stages")
        if not isinstance(stage_id, str) or not isinstance(stages, list):
            return {}
        stage = next(
            (
                item
                for item in stages
                if isinstance(item, Mapping) and item.get("stage_id") == stage_id
            ),
            None,
        )
        if stage is None:
            return {}
        execution = self._latest_role_payload(
            record,
            Role.EXECUTION,
            "hia-project-execution/1",
            stage_id=stage_id,
        )
        if execution is None:
            return {"command": "start_execution"}
        decision = self._latest_role_payload(
            record,
            Role.SUPERVISOR,
            "hia-project-stage-decision/1",
            stage_id=stage_id,
        )
        if decision is None:
            return {"command": "start_reviews"}
        if decision.get("decision") == "repair":
            return {
                "command": "start_execution",
                "action_data": {"repair": True},
            }
        return {}

    def _latest_role_payload(
        self,
        record: ProjectRecord,
        role: Role,
        schema: str,
        *,
        stage_id: str | None = None,
    ) -> Mapping[str, Any] | None:
        binding = record.state.roles.get(role)
        if binding is None:
            return None
        projected = self.read_role_thread(binding.thread_id).get("read")
        thread = projected.get("thread") if isinstance(projected, Mapping) else None
        turns = thread.get("turns") if isinstance(thread, Mapping) else None
        if not isinstance(turns, list):
            return None
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
                candidate = text.strip()
                if candidate.startswith("```") and candidate.endswith("```"):
                    candidate = re.sub(
                        r"\A```(?:json)?\s*|\s*```\Z",
                        "",
                        candidate,
                        flags=re.IGNORECASE,
                    )
                try:
                    payload = json.loads(candidate)
                except (TypeError, ValueError):
                    continue
                if not isinstance(payload, Mapping) or payload.get("schema") != schema:
                    continue
                if stage_id is not None and payload.get("stage_id") != stage_id:
                    continue
                return payload
        return None

    @staticmethod
    def _project_role_messages(result: Any, expected_thread_id: str) -> dict[str, Any]:
        """Project-only read projection without importing the ordinary Session."""

        thread = result.get("thread") if isinstance(result, Mapping) else None
        if not isinstance(thread, Mapping) or thread.get("id") != expected_thread_id:
            raise ValueError("project Thread response identity is invalid")
        turns = thread.get("turns")
        if not isinstance(turns, list):
            raise ValueError("project Thread response has no turns array")
        projected_turns: list[dict[str, Any]] = []
        for turn in turns:
            if not isinstance(turn, Mapping) or not isinstance(turn.get("items"), list):
                raise ValueError("project Thread response contains an invalid turn")
            projected_items: list[dict[str, Any]] = []
            for item in turn["items"]:
                if not isinstance(item, Mapping):
                    continue
                if item.get("type") == "userMessage":
                    content = item.get("content")
                    if not isinstance(content, list):
                        continue
                    safe_content = [
                        {"type": entry["type"], **({"text": entry["text"]} if entry.get("type") == "text" else {"path": entry["path"]})}
                        for entry in content
                        if isinstance(entry, Mapping)
                        and (
                            (entry.get("type") == "text" and isinstance(entry.get("text"), str))
                            or (
                                entry.get("type") == "localImage"
                                and isinstance(entry.get("path"), str)
                            )
                        )
                    ]
                    projected_items.append(
                        {"type": "userMessage", "content": safe_content}
                    )
                elif item.get("type") == "agentMessage" and isinstance(
                    item.get("text"), str
                ):
                    markers = {
                        str(item.get(name) or "").strip().casefold()
                        for name in ("channel", "phase")
                    }
                    if markers & {"analysis", "commentary", "internal", "reasoning"}:
                        continue
                    projected_items.append(
                        {"type": "agentMessage", "text": item["text"]}
                    )
            projected_turns.append({"items": projected_items})
        return {"thread": {"id": expected_thread_id, "turns": projected_turns}}
