"""Create native project Threads with explicit persisted role identities."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping, Protocol

from .project_contracts import ProjectState, ProjectStatus, Role, RoleThread
from .project_permissions import (
    permission_profile,
    require_complete_project_roles,
    validate_observable_role_response,
)


class AppServerClient(Protocol):
    def request(self, method: str, params: Mapping[str, Any]) -> Any: ...


ROLE_TITLES: Mapping[Role, str] = {
    Role.SUPERVISOR: "监督 AI",
    Role.PLANNING: "方案 AI",
    Role.EXECUTION: "执行 AI",
    Role.VISUAL_REVIEW: "视觉审查 AI",
    Role.TECHNICAL_REVIEW: "技术审查 AI",
}


ROLE_INSTRUCTIONS: Mapping[Role, str] = {
    Role.SUPERVISOR: "监督项目约束和证据，只读；不得调用 HIA/HOM 或修改 HIP。",
    Role.PLANNING: "维护 requirement 覆盖和当前阶段卡，只读；不得调用 HIA/HOM 或修改 HIP。",
    Role.EXECUTION: "你是当前 HIP 的唯一写入者；一次只执行已授权的当前阶段或修复。",
    Role.VISUAL_REVIEW: "只读审查真实截图与参考一致性，不得调用 HIA/HOM 或修改 HIP。",
    Role.TECHNICAL_REVIEW: "只读审查结构、依赖和真实工具证据，不得调用 HIA/HOM 或修改 HIP。",
}


class ProjectThreadFactory:
    def __init__(
        self,
        client: AppServerClient,
        project_root: Path,
        selected_backend: str,
        server_transports: Mapping[str, Mapping[str, Any]],
    ) -> None:
        self._client = client
        self._project_root = project_root.resolve()
        self._selected_backend = selected_backend
        self._server_transports = server_transports
        permission_profile(
            Role.EXECUTION,
            selected_backend,
            server_transports,
        )

    def start_role(
        self,
        state: ProjectState,
        role: Role,
        *,
        model: str | None = None,
        effort: str | None = None,
        service_tier: str | None = None,
    ) -> ProjectState:
        if role in state.roles:
            raise ValueError(f"project already has a {role.value} Thread")
        if role is not Role.SUPERVISOR and state.status is not ProjectStatus.PROVISIONING_ROLES:
            raise ValueError("worker roles are provisioned only after eligible intake")
        profile = permission_profile(
            role,
            self._selected_backend,
            self._server_transports,
        )
        source = f"hia-project/{state.project_id}/{role.value}"
        params: dict[str, Any] = {
            "cwd": str(self._project_root),
            "approvalPolicy": profile.approval_policy,
            "approvalsReviewer": "user",
            "sandbox": profile.sandbox,
            "ephemeral": False,
            "developerInstructions": ROLE_INSTRUCTIONS[role],
            "threadSource": source,
            "config": dict(profile.config),
        }
        if model is not None:
            params["model"] = model
        if service_tier is not None:
            params["serviceTier"] = service_tier
        result = self._client.request("thread/start", params)
        thread = result.get("thread") if isinstance(result, Mapping) else None
        thread_id = thread.get("id") if isinstance(thread, Mapping) else None
        if not isinstance(thread_id, str) or not thread_id.strip():
            raise ValueError("thread/start did not return a valid Thread id")
        try:
            validate_observable_role_response(
                role,
                result,
                expected_source=source,
                expected_model=model,
            )
        except Exception as exc:
            cleanup_error = self._delete_thread(thread_id)
            suffix = f"; cleanup failed: {cleanup_error}" if cleanup_error else ""
            raise ValueError(
                f"thread/start observable profile mismatch: {exc}{suffix}"
            ) from exc
        actual_model = result.get("model", model) if isinstance(result, Mapping) else model
        actual_tier = (
            result.get("serviceTier", service_tier)
            if isinstance(result, Mapping)
            else service_tier
        )
        actual_effort = effort
        if actual_effort is None and isinstance(result, Mapping):
            candidate = result.get("reasoningEffort")
            actual_effort = candidate if isinstance(candidate, str) else None
        binding = RoleThread(
            role=role,
            thread_id=thread_id,
            model=actual_model if isinstance(actual_model, str) else None,
            effort=actual_effort,
            service_tier=actual_tier if isinstance(actual_tier, str) else None,
        )
        roles = dict(state.roles)
        roles[role] = binding
        changes: dict[str, Any] = {
            "roles": roles,
            "revision": state.revision + 1,
        }
        if role is Role.SUPERVISOR:
            changes["goal_thread_id"] = thread_id
        return replace(state, **changes)

    def _delete_thread(self, thread_id: str) -> str | None:
        try:
            self._client.request("thread/delete", {"threadId": thread_id})
        except Exception as exc:
            return str(exc)
        return None

    def start_supervisor(
        self,
        state: ProjectState,
        *,
        model: str | None = None,
        effort: str | None = None,
        service_tier: str | None = None,
    ) -> ProjectState:
        if state.status is not ProjectStatus.PROVISIONING:
            raise ValueError("Supervisor must be created during provisioning")
        started = self.start_role(
            state,
            Role.SUPERVISOR,
            model=model,
            effort=effort,
            service_tier=service_tier,
        )
        return started

    def provision_workers(self, state: ProjectState) -> ProjectState:
        if set(state.roles) != {Role.SUPERVISOR}:
            raise ValueError("eligible provisioning requires exactly one Supervisor")
        supervisor = state.roles[Role.SUPERVISOR]
        created: list[str] = []
        try:
            for role in (
                Role.PLANNING,
                Role.EXECUTION,
                Role.VISUAL_REVIEW,
                Role.TECHNICAL_REVIEW,
            ):
                state = self.start_role(
                    state,
                    role,
                    model=supervisor.model,
                    effort=supervisor.effort,
                    service_tier=supervisor.service_tier,
                )
                created.append(state.roles[role].thread_id)
        except Exception:
            cleanup_errors: list[str] = []
            for thread_id in reversed(created):
                try:
                    self._client.request("thread/delete", {"threadId": thread_id})
                except Exception as cleanup_error:
                    cleanup_errors.append(f"{thread_id}: {cleanup_error}")
            if cleanup_errors:
                raise RuntimeError(
                    "role provisioning failed and precise cleanup was incomplete: "
                    + "; ".join(cleanup_errors)
                )
            raise
        require_complete_project_roles(state.roles)
        return state

    def validate_recovery_identity(self, state: ProjectState) -> None:
        """Verify persisted native identities without depending on chat history.

        The project registry is the authority for the original task text and
        its digest.  Native history can be paged, compacted, or replaced by a
        verified Thread migration, so recovery deliberately requests no Turns
        and never searches user messages for another copy of the task.
        """

        supervisor = state.roles.get(Role.SUPERVISOR)
        if supervisor is None or supervisor.thread_id != state.goal_thread_id:
            raise ValueError("persisted Supervisor/Goal owner identity is invalid")

        for role, binding in state.roles.items():
            expected_source = f"hia-project/{state.project_id}/{role.value}"
            result = self._client.request(
                "thread/read",
                {"threadId": binding.thread_id, "includeTurns": False},
            )
            thread = result.get("thread") if isinstance(result, Mapping) else None
            if not isinstance(thread, Mapping) or thread.get("id") != binding.thread_id:
                raise ValueError(f"persisted {role.value} Thread identity is missing")
            source = thread.get("threadSource", thread.get("source"))
            if source != expected_source:
                raise ValueError(f"persisted {role.value} Thread source is invalid")
            status = thread.get("status")
            status_type = status.get("type") if isinstance(status, Mapping) else None
            if status_type == "active":
                raise ValueError(f"persisted {role.value} Thread still has an active Turn")
            session_source = thread.get("source")
            if (
                thread.get("nativeSubagent") is True
                or thread.get("parentThreadId")
                or (
                    isinstance(session_source, Mapping)
                    and "subAgent" in session_source
                )
            ):
                raise ValueError(f"persisted {role.value} identity is a native subagent")

            goal_result = self._client.request(
                "thread/goal/get", {"threadId": binding.thread_id}
            )
            goal = goal_result.get("goal") if isinstance(goal_result, Mapping) else None
            if role is Role.SUPERVISOR:
                if (
                    not isinstance(goal, Mapping)
                    or goal.get("threadId") != state.goal_thread_id
                ):
                    raise ValueError("persisted Supervisor no longer owns the native Goal")
            elif goal is not None:
                raise ValueError(f"persisted {role.value} unexpectedly owns a native Goal")
