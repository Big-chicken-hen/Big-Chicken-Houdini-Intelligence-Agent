"""Create native project Threads with explicit persisted role identities."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping, Protocol

from .project_contracts import ProjectState, ProjectStatus, Role, RoleThread
from .project_permissions import permission_profile, require_complete_project_roles


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
    def __init__(self, client: AppServerClient, project_root: Path) -> None:
        self._client = client
        self._project_root = project_root.resolve()

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
        if role is not Role.SUPERVISOR and state.status is not ProjectStatus.PLANNING:
            raise ValueError("worker roles are provisioned only after eligible intake")
        profile = permission_profile(role)
        params: dict[str, Any] = {
            "cwd": str(self._project_root),
            "approvalPolicy": profile.approval_policy,
            "approvalsReviewer": "user",
            "sandbox": profile.sandbox,
            "ephemeral": False,
            "developerInstructions": ROLE_INSTRUCTIONS[role],
            "threadSource": f"hia-project/{state.project_id}/{role.value}",
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
        return replace(state, roles=roles, revision=state.revision + 1)

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
        supervisor_id = started.roles[Role.SUPERVISOR].thread_id
        return replace(started, goal_thread_id=supervisor_id)

    def provision_workers(self, state: ProjectState) -> ProjectState:
        if set(state.roles) != {Role.SUPERVISOR}:
            raise ValueError("eligible provisioning requires exactly one Supervisor")
        supervisor = state.roles[Role.SUPERVISOR]
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
        require_complete_project_roles(state.roles)
        return state
