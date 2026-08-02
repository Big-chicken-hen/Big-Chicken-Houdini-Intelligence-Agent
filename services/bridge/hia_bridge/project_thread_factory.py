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
    Role.PLANNING: (
        "维护 requirement 覆盖和当前阶段卡，只读；不得调用 HIA/HOM 或修改 HIP。"
        "必须按任务真实复杂度选择最小充分的 direct、focused 或 full 深度；"
        "只有实质性多阶段任务才使用 Full，禁止把单一确定操作扩写成万字蓝图。"
    ),
    Role.EXECUTION: "你是当前 HIP 的唯一写入者；一次只执行已授权的当前阶段或修复。",
    Role.VISUAL_REVIEW: "只读审查真实截图与参考一致性，不得调用 HIA/HOM 或修改 HIP。",
    Role.TECHNICAL_REVIEW: "只读审查结构、依赖和真实工具证据，不得调用 HIA/HOM 或修改 HIP。",
}

_PROJECT_PROTOCOL_INSTRUCTION = (
    " When the newest user message is a hia-project-role-request/1 envelope, "
    "it is the only current project action. Follow its response_contract "
    "exactly, return one JSON object "
    "only, and do not call update_goal. Do not start independent work outside "
    "the envelope. Do not create, fork, or delegate to internal subagents; the "
    "five native project-role Threads are the complete team. On the first "
    "project Turn, respond naturally to the user's task. If fulfilling it "
    "requires the live Houdini scene, hand it to Planning; otherwise answer it "
    "yourself and complete the project. Never expose an eligibility classifier "
    "or inspect repository files merely to choose that route."
    " When the newest user message is a hia-project-guidance/1 envelope, record "
    "it as user guidance and return exactly one JSON object with schema "
    "hia-project-guidance-recorded/1 and the same revision; do not start project "
    "work or call tools from that guidance-record Turn."
)

ROLE_INSTRUCTIONS = {
    role: instruction + _PROJECT_PROTOCOL_INSTRUCTION
    for role, instruction in ROLE_INSTRUCTIONS.items()
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
            raise ValueError(
                "thread/start observable profile mismatch; the returned Thread "
                f"may remain visible and must not be deleted automatically: {exc}"
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
        return replace(state, **changes)

    def start_supervisor(
        self,
        state: ProjectState,
        *,
        model: str | None = None,
        effort: str | None = None,
        service_tier: str | None = None,
    ) -> ProjectState:
        if state.status is not ProjectStatus.PLANNING:
            raise ValueError("Supervisor must be created while project planning starts")
        started = self.start_role(
            state,
            Role.SUPERVISOR,
            model=model,
            effort=effort,
            service_tier=service_tier,
        )
        return started

    def provision_workers(self, state: ProjectState) -> ProjectState:
        """Create the remaining four native roles immediately after Supervisor."""

        if set(state.roles) != {Role.SUPERVISOR}:
            raise ValueError("project role creation requires exactly one Supervisor")
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
