"""Lifecycle-safe coordinator for :mod:`hia_panel.project_team_view`.

The controller owns no widget layout and no Bridge transport.  A host injects a
small gateway and callbacks for opening/creating central conversations.  View
signals are connected exactly once at construction; Bridge response signals are
detached on close and reattached once on show.
"""

from __future__ import annotations

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

    def get_threads(self, *, context: str) -> str | None: ...

    def append_project_guidance(
        self,
        *,
        project_id: str,
        thread_id: str | None,
        text: str,
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
        on_error: Callable[[str], None] | None = None,
    ) -> None:
        self.view = view
        self.gateway = gateway
        self._on_new_task = on_new_task
        self._on_open_thread = on_open_thread
        self._on_error = on_error or (lambda _message: None)
        self._gateway_connected = False
        self._closed = True
        self._ordinary_threads: Any = None
        self._project_snapshot: Any = None
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
        self.gateway.get_threads(context="project_history_refresh")

    def _connect_view_once(self) -> None:
        self.view.refreshRequested.connect(self.refresh)
        self.view.newTaskRequested.connect(self._new_task)
        self.view.openThreadRequested.connect(self._open_thread)
        self.view.appendGuidanceRequested.connect(self._append_guidance)
        self.view.roleRuntimeRequested.connect(self._set_role_runtime)
        self.view.continueProjectRequested.connect(self._continue_project)
        self.view.stopProjectRequested.connect(self._stop_project)

    def _new_task(self, route: str) -> None:
        if self.active and route in {"single", "team"}:
            self._on_new_task(route)

    def _open_thread(self, thread_id: str) -> None:
        if self.active and isinstance(thread_id, str) and thread_id:
            self._on_open_thread(thread_id)

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
    ) -> None:
        if not self.active:
            return
        self.gateway.append_project_guidance(
            project_id=project_id,
            thread_id=thread_id,
            text=text,
            context=f"project_guidance:{project_id}",
        )

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
        if context == "project_history_refresh":
            self._ordinary_threads = payload.get("threads")
        if context.startswith("project_guidance:") and project_snapshot_received:
            self.view.acknowledge_guidance()
        self._render_if_available()

    def _request_failed(self, context: str, payload: Any) -> None:
        if not self.active:
            return
        message = "项目请求失败"
        if isinstance(payload, Mapping):
            error = payload.get("error")
            if isinstance(error, Mapping) and isinstance(error.get("message"), str):
                message = error["message"]
            elif isinstance(payload.get("message"), str):
                message = payload["message"]
        self._on_error(f"{context}: {message}")

    def _render_if_available(self) -> None:
        if self._project_snapshot is None:
            return
        self.view.state.apply_snapshot(
            self._project_snapshot,
            self._ordinary_threads,
        )
        self.view.render()


def _disconnect(signal: Any, callback: Callable[..., Any]) -> None:
    try:
        signal.disconnect(callback)
    except (RuntimeError, TypeError):
        # Qt raises when an already-destroyed signal or an absent connection is
        # disconnected.  The controller's own flag remains authoritative.
        pass
