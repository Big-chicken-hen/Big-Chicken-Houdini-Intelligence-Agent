"""Lifecycle-safe coordinator for :mod:`hia_panel.project_team_view`.

The controller owns no widget layout and no Bridge transport.  A host injects a
small gateway and callbacks for opening/creating central conversations.  View
signals are connected exactly once at construction; Bridge response signals are
detached on close and reattached once on show.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from typing import Any, Protocol


class ProjectTeamGateway(Protocol):
    """Minimal async Bridge surface required by the standalone controller.

    Every method returns immediately after queueing a request.  Results arrive
    through ``actionCompleted(context, payload)`` or
    ``requestFailed(context, payload)``.  The existing BridgeClient needs only
    a thin set of methods with these exact signatures during host integration.
    """

    actionCompleted: Any
    requestFailed: Any

    def get_project_team(self, *, context: str) -> str | None: ...

    def get_models(self, *, context: str) -> str | None: ...

    def append_project_guidance(
        self,
        *,
        project_id: str,
        thread_id: str | None,
        text: str,
        requirement_delta: Mapping[str, Any] | None = None,
        context: str,
    ) -> str | None: ...

    def set_project_role_runtime(
        self,
        *,
        project_id: str,
        thread_id: str,
        model: str | None,
        effort: str | None,
        service_tier: str | None,
        context: str,
    ) -> str | None: ...

    def continue_project(self, *, project_id: str, context: str) -> str | None: ...

    def stop_project(self, *, project_id: str, context: str) -> str | None: ...

class ProjectTeamController:
    def __init__(
        self,
        view: Any,
        gateway: ProjectTeamGateway,
        *,
        on_new_task: Callable[[str], None],
        on_open_thread: Callable[[str], None],
        on_refresh_ordinary: Callable[[], None] | None = None,
        on_delete_thread: Callable[[str], None] | None = None,
        on_rename_thread: Callable[[str, str], None] | None = None,
        on_copy_thread_id: Callable[[str], None] | None = None,
        on_error: Callable[[str], None] | None = None,
    ) -> None:
        self.view = view
        self.gateway = gateway
        self._on_new_task = on_new_task
        self._on_open_thread = on_open_thread
        self._on_refresh_ordinary = on_refresh_ordinary or (lambda: None)
        self._on_delete_thread = on_delete_thread or (lambda _thread_id: None)
        self._on_rename_thread = on_rename_thread or (
            lambda _thread_id, _name: None
        )
        self._on_copy_thread_id = on_copy_thread_id or (lambda _thread_id: None)
        self._on_error = on_error or (lambda _message: None)
        self._gateway_connected = False
        self._closed = True
        self._ordinary_threads: Any = None
        self._project_snapshot: Any = None
        self._pending_ordinary_selection: str | None = None
        self._pending_guidance: dict[
            str, tuple[str, str | None, str, Mapping[str, Any] | None, bool]
        ] = {}
        self._connect_view_once()

    @property
    def active(self) -> bool:
        return not self._closed and self._gateway_connected

    def show(self) -> None:
        if self.active:
            return
        self._closed = False
        self._connect_gateway()
        self.refresh()

    def close(self) -> None:
        if self._closed and not self._gateway_connected:
            return
        self._closed = True
        self._disconnect_gateway()

    def dispose(self) -> None:
        self.close()

    def refresh(self) -> None:
        if not self.active:
            return
        self.gateway.get_project_team(context="project_team_refresh")
        self._on_refresh_ordinary()
        self.gateway.get_models(context="project_model_catalog")

    def refresh_models(self) -> None:
        if self.active:
            self.gateway.get_models(context="project_model_catalog")

    def consume_ordinary_threads(self, threads: Any) -> None:
        """Apply the Panel's single authoritative ordinary-Thread history."""

        if not self.active:
            return
        candidates = _ordinary_thread_candidates(threads)
        self._ordinary_threads = candidates
        self._render_if_available()

    def select_ordinary_thread_when_available(self, thread_id: str) -> None:
        """Select a newly started ordinary Thread after history publishes it."""

        if not self.active or not isinstance(thread_id, str) or not thread_id:
            return
        self._pending_ordinary_selection = thread_id

    def consume_project_team_update(self, event: Any) -> bool:
        """Apply one trusted Bridge event without disturbing ordinary history."""

        if not self.active or not isinstance(event, Mapping):
            return False
        if event.get("type") != "project_team_updated":
            return False
        snapshot = event.get("project_team")
        if not isinstance(snapshot, Mapping):
            return False
        self._project_snapshot = snapshot
        self._render_if_available()
        return True

    def _connect_view_once(self) -> None:
        self.view.refreshRequested.connect(self.refresh)
        self.view.newTaskRequested.connect(self._new_task)
        self.view.openThreadRequested.connect(self._open_thread)
        self.view.deleteThreadRequested.connect(self._delete_thread)
        self.view.renameThreadRequested.connect(self._rename_thread)
        self.view.copyThreadIdRequested.connect(self._copy_thread_id)
        self.view.appendGuidanceRequested.connect(self._append_guidance)
        self.view.roleRuntimeRequested.connect(self._set_role_runtime)
        self.view.modelCatalogRefreshRequested.connect(self.refresh_models)
        self.view.continueProjectRequested.connect(self._continue_project)
        self.view.stopProjectRequested.connect(self._stop_project)

    def _new_task(self, route: str) -> None:
        if self.active and route in {"single", "team"}:
            self._on_new_task(route)

    def _open_thread(self, thread_id: str) -> None:
        if self.active and isinstance(thread_id, str) and thread_id:
            self._on_open_thread(thread_id)

    def _delete_thread(self, thread_id: str) -> None:
        if self.active and isinstance(thread_id, str) and thread_id:
            self._on_delete_thread(thread_id)

    def _rename_thread(self, thread_id: str, name: str) -> None:
        if (
            self.active
            and isinstance(thread_id, str)
            and thread_id
            and isinstance(name, str)
            and name.strip()
        ):
            self._on_rename_thread(thread_id, name.strip())

    def _copy_thread_id(self, thread_id: str) -> None:
        if self.active and isinstance(thread_id, str) and thread_id:
            self._on_copy_thread_id(thread_id)

    def _connect_gateway(self) -> None:
        if self._gateway_connected:
            return
        self.gateway.actionCompleted.connect(self._action_completed)
        self.gateway.requestFailed.connect(self._request_failed)
        self._gateway_connected = True

    def _disconnect_gateway(self) -> None:
        if not self._gateway_connected:
            return
        _disconnect(self.gateway.actionCompleted, self._action_completed)
        _disconnect(self.gateway.requestFailed, self._request_failed)
        self._gateway_connected = False

    def _append_guidance(
        self,
        project_id: str,
        thread_id: str | None,
        text: str,
        requirement_change: Mapping[str, Any] | None = None,
        acknowledge_view: bool = True,
    ) -> bool:
        if not self.active:
            return False
        context = f"project_guidance:{uuid.uuid4().hex}"
        requirement_delta = _requirement_delta(requirement_change, context)
        self._pending_guidance[context] = (
            project_id,
            thread_id,
            text,
            requirement_change,
            acknowledge_view,
        )
        request_id = self.gateway.append_project_guidance(
            project_id=project_id,
            thread_id=thread_id,
            text=text,
            requirement_delta=requirement_delta,
            context=context,
        )
        if request_id is None:
            self._pending_guidance.pop(context, None)
            return False
        return True

    def _set_role_runtime(
        self,
        project_id: str,
        thread_id: str,
        model: str | None,
        effort: str | None,
        service_tier: str | None,
    ) -> None:
        if not self.active:
            return
        self.gateway.set_project_role_runtime(
            project_id=project_id,
            thread_id=thread_id,
            model=model,
            effort=effort,
            service_tier=service_tier,
            context=f"project_runtime:{project_id}:{thread_id}",
        )

    def _continue_project(self, project_id: str) -> None:
        if self.active:
            self.gateway.continue_project(
                project_id=project_id,
                context=f"project_continue:{project_id}",
            )

    def _stop_project(self, project_id: str) -> None:
        if self.active:
            self.gateway.stop_project(
                project_id=project_id,
                context=f"project_stop:{project_id}",
            )

    def _action_completed(self, context: str, payload: Any) -> None:
        if not self.active or not isinstance(payload, Mapping):
            return
        project_snapshot_received = False
        if context == "project_team_refresh" or context.startswith("project_"):
            snapshot = payload.get("project_team")
            if isinstance(snapshot, Mapping):
                self._project_snapshot = snapshot
                project_snapshot_received = True
        if context == "project_model_catalog":
            self.view.set_model_catalog(payload.get("models", []))
        if context.startswith("project_guidance:") and project_snapshot_received:
            pending = self._pending_guidance.pop(context, None)
            if pending is not None:
                (
                    _project_id,
                    _thread_id,
                    submitted_text,
                    requirement_change,
                    acknowledge_view,
                ) = pending
                if acknowledge_view and not self.view.acknowledge_guidance(
                    submitted_text, requirement_change
                ):
                    self._on_error(
                        "先前版本的追加指导已发送；当前正在编辑的内容已保留。"
                    )
        self._render_if_available()

    def _request_failed(self, context: str, payload: Any) -> None:
        if not self.active:
            return
        is_guidance = context.startswith("project_guidance:")
        is_runtime = context.startswith("project_runtime:")
        pending = self._pending_guidance.pop(context, None) if is_guidance else None
        message = "项目请求失败"
        if isinstance(payload, Mapping):
            error = payload.get("structured_error")
            if not isinstance(error, Mapping):
                error = payload.get("error")
            if isinstance(error, Mapping) and isinstance(error.get("message"), str):
                message = error["message"]
                details = error.get("details")
                if is_runtime and error.get("code") == "PROJECT_RUNTIME_SELECTION_INVALID":
                    field = details.get("field") if isinstance(details, Mapping) else None
                    message = (
                        f"角色运行设置未保存：{field or '模型组合'}不受当前模型支持。"
                        "已刷新实时模型目录，请重新选择后保存。"
                    )
                    self.gateway.get_models(context="project_model_catalog")
                if is_guidance and error.get("code") == "PROJECT_GUIDANCE_INACTIVE":
                    recoverable = (
                        isinstance(details, Mapping)
                        and details.get("recoverable") is True
                    )
                    message = (
                        "项目当前暂停，追加指导没有发送。请先点击“继续项目”，"
                        "恢复后重新发送；当前文字已保留。"
                        if recoverable
                        else "项目已经停止或结束，追加指导没有发送。请新建项目继续；"
                        "当前文字已保留。"
                    )
            elif isinstance(payload.get("message"), str):
                message = payload["message"]
        if is_guidance and pending is not None:
            self.gateway.get_project_team(context="project_team_refresh")
        self._on_error(
            message if is_guidance or is_runtime else f"{context}: {message}"
        )

    def _render_if_available(self) -> None:
        self.view.state.apply_snapshot(
            self._project_snapshot,
            self._ordinary_threads,
        )
        pending_thread_id = self._pending_ordinary_selection
        pending_key = (
            f"thread:{pending_thread_id}"
            if isinstance(pending_thread_id, str)
            else None
        )
        if pending_key is not None:
            available = any(
                thread.stable_key == pending_key
                for thread in self.view.state.tree.ordinary_threads
            )
            if available:
                self.view.state.select(pending_key)
                self._pending_ordinary_selection = None
        self.view.refresh_view()


def _ordinary_thread_candidates(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, Mapping):
        value = value.get("threads")
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _disconnect(signal: Any, callback: Callable[..., Any]) -> None:
    try:
        signal.disconnect(callback)
    except (RuntimeError, TypeError):
        # Qt raises when an already-destroyed signal or an absent connection is
        # disconnected.  The controller's own flag remains authoritative.
        pass


def _requirement_delta(
    change: Mapping[str, Any] | None, context: str
) -> Mapping[str, Any] | None:
    if change is None:
        return None
    operation = change.get("operation")
    target = change.get("target_requirement_id")
    if operation not in {"add", "replace", "remove"}:
        raise ValueError("requirement change operation is invalid")
    if operation in {"replace", "remove"} and not isinstance(target, str):
        raise ValueError("requirement change target is required")
    if operation == "remove":
        return {"remove": [target]}
    new_id = f"REQ-user-{uuid.uuid5(uuid.NAMESPACE_URL, context).hex[:16]}"
    result: dict[str, Any] = {
        "add": [
            {
                "requirement_id": new_id,
                "kind": "user_scope",
                "source_ref": f"guidance:{context.rsplit(':', 1)[-1]}",
            }
        ]
    }
    if operation == "replace":
        result["supersede"] = {target: new_id}
    return result
