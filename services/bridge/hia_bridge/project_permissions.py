"""Non-bypassable app-server permission profiles for project roles."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .project_contracts import READ_ONLY_ROLES, Role


HIA_SERVER_KEYS = (
    "mcp_servers.hia_mcp_v2.enabled",
    "mcp_servers.houdini_intelligence.enabled",
)
HIA_SERVER_IDS = ("hia_mcp_v2", "houdini_intelligence")


@dataclass(frozen=True)
class RolePermissionProfile:
    role: Role
    sandbox: str
    approval_policy: str
    config: Mapping[str, Any]

    @property
    def scene_write(self) -> bool:
        return self.role is Role.EXECUTION


def permission_profile(
    role: Role,
    selected_backend: str,
    server_transports: Mapping[str, Mapping[str, Any]],
) -> RolePermissionProfile:
    if selected_backend not in {"hia_mcp_v2", "houdini_intelligence"}:
        raise ValueError("selected_backend is invalid")
    read_only = role in READ_ONLY_ROLES
    config: dict[str, Any] = {}
    _validate_server_transports(server_transports)
    for server_id in HIA_SERVER_IDS:
        descriptor = server_transports[server_id]
        prefix = f"mcp_servers.{server_id}"
        for name, value in descriptor.items():
            if name in {"enabled", "required"}:
                continue
            config[f"{prefix}.{name}"] = value
        selected = not read_only and server_id == selected_backend
        config[f"{prefix}.enabled"] = selected
        config[f"{prefix}.required"] = selected
    config["multi_agent_mode"] = (
        "explicitRequestOnly" if role is Role.EXECUTION else "proactive"
    )
    return RolePermissionProfile(
        role=role,
        sandbox="read-only" if read_only else "workspace-write",
        approval_policy="never" if read_only else "on-request",
        config=config,
    )


def validate_role_permissions(
    role: Role,
    descriptor: Mapping[str, Any],
    selected_backend: str,
    server_transports: Mapping[str, Mapping[str, Any]],
) -> None:
    """Reject permission drift after start, resume, fork, or restart."""

    expected = permission_profile(role, selected_backend, server_transports)
    if descriptor.get("sandbox") != expected.sandbox:
        raise ValueError(f"{role.value} sandbox permission drift")
    if descriptor.get("approvalPolicy") != expected.approval_policy:
        raise ValueError(f"{role.value} approval policy drift")
    config = descriptor.get("config")
    if not isinstance(config, Mapping):
        raise ValueError(f"{role.value} config is missing")
    if set(config) != set(expected.config):
        raise ValueError(f"{role.value} config contains unexpected permission keys")
    for key, value in expected.config.items():
        if config.get(key) != value:
            raise ValueError(f"{role.value} config permission drift: {key}")


def validate_observable_role_response(
    role: Role,
    descriptor: Mapping[str, Any],
    *,
    expected_source: str,
    expected_model: str | None = None,
) -> None:
    """Validate only fields exposed by pinned app-server start/fork responses.

    Codex 0.144.3 does not return the effective per-Thread ``config`` or MCP
    inventory.  Those capabilities must be proven by a real Turn/tool smoke;
    request echoing is deliberately not treated as runtime evidence here.
    """

    read_only = role in READ_ONLY_ROLES
    allowed_sandboxes = {"readOnly"} if read_only else {"readOnly", "workspaceWrite"}
    expected_approval = "never" if read_only else "on-request"
    sandbox = descriptor.get("sandbox")
    sandbox_type = sandbox.get("type") if isinstance(sandbox, Mapping) else None
    if sandbox_type not in allowed_sandboxes:
        raise ValueError(f"{role.value} observable sandbox permission drift")
    if descriptor.get("approvalPolicy") != expected_approval:
        raise ValueError(f"{role.value} observable approval policy drift")
    thread = descriptor.get("thread")
    if not isinstance(thread, Mapping):
        raise ValueError(f"{role.value} response Thread is missing")
    if thread.get("threadSource") != expected_source:
        raise ValueError(f"{role.value} response Thread source drift")
    actual_model = descriptor.get("model")
    if not isinstance(actual_model, str) or not actual_model:
        raise ValueError(f"{role.value} response model is missing")
    if expected_model is not None and actual_model != expected_model:
        raise ValueError(f"{role.value} response model drift")
def _validate_server_transports(
    value: Mapping[str, Mapping[str, Any]],
) -> None:
    if set(value) != set(HIA_SERVER_IDS):
        raise ValueError("both exact HIA MCP server transports are required")
    for server_id in HIA_SERVER_IDS:
        descriptor = value.get(server_id)
        if not isinstance(descriptor, Mapping):
            raise ValueError(f"{server_id} transport is missing")
        command = descriptor.get("command")
        args = descriptor.get("args")
        if not isinstance(command, str) or not command.strip():
            raise ValueError(f"{server_id} transport command is invalid")
        if args is not None and (
            not isinstance(args, (list, tuple))
            or any(not isinstance(item, str) for item in args)
        ):
            raise ValueError(f"{server_id} transport args are invalid")


def require_complete_project_roles(roles: Mapping[Role, Any]) -> None:
    expected = set(Role)
    actual = set(roles)
    if actual != expected:
        missing = sorted(role.value for role in expected - actual)
        extra = sorted(str(role) for role in actual - expected)
        raise ValueError(
            f"scene execution requires exactly five roles; missing={missing}, extra={extra}"
        )
