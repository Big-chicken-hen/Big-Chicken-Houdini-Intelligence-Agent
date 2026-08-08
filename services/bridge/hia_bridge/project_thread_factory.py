"""Create native project Threads with explicit persisted role identities."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol

from .project_contracts import ProjectState, Role, RoleThread
from .project_permissions import (
    permission_profile,
    require_complete_project_roles,
    validate_observable_role_response,
    validate_role_permissions,
)
from .thread_rotation import ThreadRotationProfile


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
    "the envelope. The five native project role Threads remain the baseline team. On the first "
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
    role: instruction
    + _PROJECT_PROTOCOL_INSTRUCTION
    + (
        " Do not create, fork, or delegate to internal subagents; Execution must remain the sole serialized scene writer."
        if role is Role.EXECUTION
        else " If native subagent tools are actually available, use them only for genuinely parallel, non-overlapping read-only research or review; they never replace a project role and must not call HIA/HOM."
    )
    for role, instruction in ROLE_INSTRUCTIONS.items()
}


class ProjectRoleCreationError(RuntimeError):
    def __init__(self, message: str, orphan_thread_ids: tuple[str, ...] = ()) -> None:
        self.orphan_thread_ids = orphan_thread_ids
        super().__init__(message)


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

    def rotation_profile(
        self, project_id: str, binding: RoleThread
    ) -> ThreadRotationProfile | None:
        if not isinstance(binding.model, str) or not binding.model:
            return None
        permissions = permission_profile(
            binding.role,
            self._selected_backend,
            self._server_transports,
        )
        read_only = permissions.sandbox == "read-only"
        return ThreadRotationProfile(
            cwd=str(self._project_root),
            developer_instructions=ROLE_INSTRUCTIONS[binding.role],
            ephemeral=False,
            thread_source=f"hia-project/{project_id}/{binding.role.value}",
            model=binding.model,
            reasoning_effort=binding.effort,
            service_tier=binding.service_tier,
            fork_sandbox=permissions.sandbox,
            response_sandbox={
                "type": "readOnly" if read_only else "workspaceWrite",
                "networkAccess": bool(
                    permissions.config.get(
                        "sandbox_workspace_write.network_access", False
                    )
                ),
            },
            config=dict(permissions.config),
        )

    def validate_rotation_response(
        self,
        project_id: str,
        binding: RoleThread,
        result: Mapping[str, Any],
    ) -> None:
        stored = self.rotation_profile(project_id, binding)
        if stored is None:
            raise ValueError("project role rotation profile is missing")
        validate_role_permissions(
            binding.role,
            {
                "sandbox": stored.fork_sandbox,
                "approvalPolicy": "never",
                "config": stored.config,
            },
            self._selected_backend,
            self._server_transports,
        )
        validate_observable_role_response(
            binding.role,
            result,
            expected_source=f"hia-project/{project_id}/{binding.role.value}",
            expected_model=binding.model,
        )

    def _start_role(
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
            raise ProjectRoleCreationError(
                f"thread/start observable profile mismatch: {exc}",
                (thread_id,),
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

    def create_all_roles(
        self,
        state: ProjectState,
        *,
        model: str | None = None,
        effort: str | None = None,
        service_tier: str | None = None,
    ) -> ProjectState:
        """Create exactly five roles or delete every newly created Thread."""

        if state.roles:
            raise ValueError("project role creation requires an empty role set")
        created: list[str] = []
        try:
            for role in Role:
                state = self._start_role(
                    state,
                    role,
                    model=model,
                    effort=effort,
                    service_tier=service_tier,
                )
                created.append(state.roles[role].thread_id)
            require_complete_project_roles(state.roles)
            return state
        except Exception as exc:
            candidates = list(created)
            if isinstance(exc, ProjectRoleCreationError):
                candidates.extend(exc.orphan_thread_ids)
            orphans = self.cleanup_thread_ids(candidates)
            raise ProjectRoleCreationError(
                "project role creation failed; all known role Threads were cleaned"
                if not orphans
                else "project role creation failed and cleanup was incomplete",
                tuple(orphans),
            ) from exc

    def cleanup_roles(self, state: ProjectState) -> tuple[str, ...]:
        """Delete only role Threads created for a not-yet-persisted project."""

        return self.cleanup_thread_ids(
            binding.thread_id for binding in state.roles.values()
        )

    def cleanup_thread_ids(self, thread_ids: Iterable[str]) -> tuple[str, ...]:
        orphans: list[str] = []
        for thread_id in dict.fromkeys(thread_ids):
            if not isinstance(thread_id, str) or not thread_id:
                continue
            try:
                self._client.request("thread/delete", {"threadId": thread_id})
            except Exception:
                orphans.append(thread_id)
        return tuple(orphans)
