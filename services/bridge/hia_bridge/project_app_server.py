"""Exact app-server event adapter for project-role Turns.

The adapter owns no project lifecycle state and never persists chat text.  It
only correlates a ``turn/start`` acknowledgement with the transient Bridge
event cursor that existed immediately before the request was sent.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Iterable

from .events import EventBuffer
from .project_effects import CompletedTurn
from .scene_writer import SceneWriterOwnership

if TYPE_CHECKING:
    from .codex_stdio import CodexStdioClient


class ProjectAppServerError(RuntimeError):
    """A deterministic failure while correlating a native project Turn."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        turn_terminal_no_hia: bool = False,
        turn_created: bool | None = None,
    ) -> None:
        self.code = code
        self.turn_terminal_no_hia = bool(turn_terminal_no_hia)
        self.turn_created = turn_created
        super().__init__(message)


class ProjectTurnTimeout(TimeoutError):
    """The acknowledged native Turn did not terminate within its budget."""


@dataclass
class _TurnCursor:
    cursor: int
    started_at: float
    wait_started: bool = False
    wait_finished: bool = False
    terminal_status: str | None = None
    active_hia_items: set[str] = field(default_factory=set)
    scene_owner: str | None = None
    invalid_hia_item_id: bool = False
    interrupt_requested: bool = False


class ProjectRoleClient:
    """Adapt ``CodexStdioClient`` and ``EventBuffer`` to project-role Turns.

    A separate cursor is retained for every acknowledged Thread/Turn pair, so
    Visual and Technical review Turns may be awaited concurrently without
    consuming or borrowing one another's notifications.
    """

    def __init__(
        self,
        client: CodexStdioClient,
        events: EventBuffer,
        *,
        scene_writer: SceneWriterOwnership | None = None,
        max_agent_message_bytes: int = 1_048_576,
        poll_interval_seconds: float = 0.25,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_agent_message_bytes < 2:
            raise ValueError("max_agent_message_bytes must be at least 2")
        if poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive")
        self._client = client
        self._events = events
        self._scene_writer = scene_writer
        self._max_message_bytes = int(max_agent_message_bytes)
        self._poll_interval = float(poll_interval_seconds)
        self._clock = clock
        self._lock = threading.Lock()
        self._starting_threads: set[str] = set()
        self._starting_notifications: dict[
            str, list[tuple[str, dict[str, Any], str]]
        ] = {}
        self._turns: dict[tuple[str, str], _TurnCursor] = {}
        add_observer = getattr(client, "add_notification_observer", None)
        if callable(add_observer):
            add_observer(self.observe_notification)

    def request(self, method: str, params: Mapping[str, Any]) -> Any:
        if method != "turn/start":
            return self._client.request(method, params)
        return self._start_turn(params)

    def bind_scene_writer(self, thread_id: str, turn_id: str, owner: str) -> None:
        """Bind one exact Execution Turn to synchronous native notifications."""

        if not all(_identifier(value) for value in (thread_id, turn_id, owner)):
            raise ProjectAppServerError(
                "INVALID_SCENE_WRITER_IDENTITY",
                "scene writer binding requires exact non-empty identities",
            )
        if self._scene_writer is None:
            raise ProjectAppServerError(
                "SCENE_WRITER_TRACKING_UNAVAILABLE",
                "project Execution has no synchronous scene writer tracker",
            )
        key = (thread_id, turn_id)
        with self._lock:
            tracked = self._turns.get(key)
            if tracked is None:
                raise ProjectAppServerError(
                    "UNACKNOWLEDGED_TURN",
                    "scene writer binding requires an acknowledged project Turn",
                )
            tracked.scene_owner = owner
            active_items = tuple(tracked.active_hia_items)
            invalid_item_id = tracked.invalid_hia_item_id
            terminal = tracked.terminal_status is not None
        for item_id in active_items:
            self._scene_writer.hia_started(owner, item_id)
        if invalid_item_id:
            self._scene_writer.fail_closed(owner)
        if terminal:
            self._scene_writer.turn_terminal(owner)

    def observe_notification(
        self, method: str, params: Mapping[str, Any]
    ) -> None:
        """Synchronously retain exact Turn/HIA lifecycle independent of the ring."""

        if method == "turn/completed":
            turn = params.get("turn")
            thread_id = params.get("threadId")
            turn_id = turn.get("id") if isinstance(turn, Mapping) else None
        elif method in {"item/started", "item/completed"}:
            thread_id = params.get("threadId")
            turn_id = params.get("turnId")
        else:
            return
        if not _identifier(thread_id) or not _identifier(turn_id):
            return
        thread_id = str(thread_id)
        turn_id = str(turn_id)
        with self._lock:
            if thread_id in self._starting_threads:
                self._starting_notifications[thread_id].append(
                    (method, dict(params), turn_id)
                )
                return
        self._observe_exact_event(method, params, thread_id, turn_id)

    def _start_turn(
        self,
        params: Mapping[str, Any],
    ) -> Any:
        thread_id = params.get("threadId")
        if not _identifier(thread_id):
            raise ProjectAppServerError(
                "INVALID_TURN_REQUEST",
                "turn/start requires a non-empty threadId",
                turn_created=False,
            )
        thread_id = str(thread_id)
        with self._lock:
            if thread_id in self._starting_threads or any(
                active_thread_id == thread_id for active_thread_id, _ in self._turns
            ):
                raise ProjectAppServerError(
                    "PROJECT_THREAD_BUSY",
                    "the project Thread already has an active Turn",
                    turn_created=False,
                )
            self._starting_threads.add(thread_id)
            self._starting_notifications[thread_id] = []
        try:
            cursor = self._events.cursor()
            started_at = self._clock()
            result = self._client.request("turn/start", params)
            turn = result.get("turn") if isinstance(result, Mapping) else None
            ack_thread_id = result.get("threadId") if isinstance(result, Mapping) else None
            turn_id = turn.get("id") if isinstance(turn, Mapping) else None
            if ack_thread_id is not None and ack_thread_id != thread_id:
                raise ProjectAppServerError(
                    "TURN_ACK_MISMATCH", "turn/start acknowledged a different Thread"
                )
            if not _identifier(turn_id):
                raise ProjectAppServerError(
                    "INVALID_TURN_ACK", "turn/start did not acknowledge a valid Turn"
                )
            key = (thread_id, str(turn_id))
            with self._lock:
                if key in self._turns:
                    raise ProjectAppServerError(
                        "DUPLICATE_TURN_ACK", "the acknowledged Turn is already tracked"
                    )
                self._turns[key] = _TurnCursor(cursor=cursor, started_at=started_at)
            self._replay_starting_notifications(thread_id, str(turn_id))
        finally:
            with self._lock:
                self._starting_threads.discard(thread_id)
                self._starting_notifications.pop(thread_id, None)
        return result

    def _replay_starting_notifications(
        self, thread_id: str, turn_id: str
    ) -> None:
        while True:
            with self._lock:
                notifications = tuple(
                    self._starting_notifications.get(thread_id, ())
                )
                self._starting_notifications[thread_id] = []
                if not notifications:
                    self._starting_threads.discard(thread_id)
                    self._starting_notifications.pop(thread_id, None)
                    return
            for method, params, notification_turn_id in notifications:
                if notification_turn_id == turn_id:
                    self._observe_exact_event(
                        method, params, thread_id, turn_id
                    )

    def interrupt_threads(
        self, thread_ids: Iterable[str]
    ) -> tuple[tuple[str, str], ...]:
        """Request interruption for exact Turns currently tracked by this adapter.

        A successful RPC acknowledgement is only an interruption request.  The
        caller must not claim that an in-progress UI-thread or HIA write has
        already stopped; ``wait_for_turn`` remains the completion authority.
        """

        allowed = {
            thread_id
            for thread_id in thread_ids
            if isinstance(thread_id, str) and thread_id.strip()
        }
        with self._lock:
            active = tuple(
                sorted(
                    key
                    for key, tracked in self._turns.items()
                    if key[0] in allowed
                    and tracked.terminal_status is None
                    and not tracked.interrupt_requested
                )
            )
            for key in active:
                self._turns[key].interrupt_requested = True
        first_error: Exception | None = None
        for thread_id, turn_id in active:
            try:
                self._client.request(
                    "turn/interrupt",
                    {"threadId": thread_id, "turnId": turn_id},
                )
            except Exception as exc:
                first_error = first_error or exc
        if first_error is not None:
            raise first_error
        return active

    def has_active_thread(self, thread_id: str) -> bool:
        if not _identifier(thread_id):
            raise ValueError("thread_id must be non-empty")
        with self._lock:
            return thread_id in self._starting_threads or any(
                active_thread_id == thread_id for active_thread_id, _ in self._turns
            )

    def wait_for_turn(
        self, thread_id: str, turn_id: str, timeout_seconds: float
    ) -> CompletedTurn:
        if not _identifier(thread_id) or not _identifier(turn_id):
            raise ProjectAppServerError(
                "INVALID_TURN_IDENTITY", "wait_for_turn requires exact identities"
            )
        if timeout_seconds <= 0:
            raise ProjectTurnTimeout("project Turn wait budget is exhausted")
        key = (thread_id, turn_id)
        with self._lock:
            tracked = self._turns.get(key)
            if tracked is None:
                raise ProjectAppServerError(
                    "UNACKNOWLEDGED_TURN",
                    "the project Turn was not acknowledged by this adapter",
                )
            if tracked.wait_started:
                raise ProjectAppServerError(
                    "PROJECT_TURN_WAIT_CLOSED",
                    "the project Turn already used its single bounded wait",
                )
            tracked.wait_started = True

        deadline = self._clock() + float(timeout_seconds)
        cursor = tracked.cursor
        deltas: list[str] = []
        delta_bytes = 0
        completed_texts: list[str] = []
        hia_events: list[Mapping[str, Any]] = []
        active_hia_items: set[str] = set()
        terminal_status: str | None = None
        payload_error: tuple[str, str] | None = None

        try:
            while True:
                with self._lock:
                    if tracked.invalid_hia_item_id:
                        raise ProjectAppServerError(
                            "INVALID_HIA_ITEM_ID",
                            "an HIA item notification omitted its exact item id",
                        )
                remaining = deadline - self._clock()
                if remaining <= 0:
                    raise ProjectTurnTimeout(
                        f"timed out waiting for project Turn {thread_id}/{turn_id}"
                    )
                batch = self._events.poll(
                    cursor,
                    timeout=min(remaining, self._poll_interval),
                    limit=512,
                )
                if batch.get("gap") is True:
                    raise ProjectAppServerError(
                        "PROJECT_EVENT_GAP",
                        "the transient event ring overwrote events for this Turn",
                    )
                raw_events = batch.get("events")
                if not isinstance(raw_events, list):
                    raise ProjectAppServerError(
                        "INVALID_EVENT_BATCH", "EventBuffer returned an invalid event batch"
                    )
                first_seq = next(
                    (
                        event.get("seq")
                        for event in raw_events
                        if isinstance(event, Mapping) and isinstance(event.get("seq"), int)
                    ),
                    None,
                )
                # EventBuffer treats after=0 as a request for whatever remains,
                # so supplement its gap bit with exact sequence continuity.
                if first_seq is not None and first_seq > cursor + 1:
                    raise ProjectAppServerError(
                        "PROJECT_EVENT_GAP",
                        "the transient event ring overwrote events for this Turn",
                    )
                for event in raw_events:
                    if not isinstance(event, Mapping):
                        continue
                    seq = event.get("seq")
                    if isinstance(seq, int) and seq > cursor:
                        cursor = seq
                    if event.get("type") == "process_exit":
                        raise ProjectAppServerError(
                            "CODEX_PROCESS_EXITED",
                            "Codex app-server exited while a project Turn was active",
                        )
                    if event.get("type") != "codex_notification":
                        continue
                    params = event.get("params")
                    if not isinstance(params, Mapping):
                        continue
                    method_name = event.get("method")
                    if method_name == "turn/completed":
                        turn = params.get("turn")
                        event_turn_id = turn.get("id") if isinstance(turn, Mapping) else None
                        if params.get("threadId") != thread_id or event_turn_id != turn_id:
                            continue
                        self._observe_exact_event(
                            method_name, params, thread_id, turn_id
                        )
                        status = turn.get("status") if isinstance(turn, Mapping) else None
                        terminal_status = status if isinstance(status, str) else "completed"
                        continue

                    if not _belongs_to_turn(params, thread_id, turn_id):
                        continue
                    if method_name == "item/agentMessage/delta":
                        delta = params.get("delta")
                        if not isinstance(delta, str):
                            payload_error = payload_error or (
                                "INVALID_AGENT_MESSAGE",
                                "agentMessage delta must be text",
                            )
                            continue
                        delta_bytes += len(delta.encode("utf-8"))
                        if delta_bytes > self._max_message_bytes:
                            payload_error = payload_error or (
                                "AGENT_MESSAGE_TOO_LARGE",
                                "agentMessage exceeded its byte budget",
                            )
                            continue
                        if payload_error is None:
                            deltas.append(delta)
                    elif method_name in {"item/started", "item/completed"}:
                        item = params.get("item")
                        if not isinstance(item, Mapping):
                            continue
                        if _is_hia_item(item):
                            item_id = item.get("id")
                            if not isinstance(item_id, str) or not item_id:
                                self._observe_exact_event(
                                    method_name, params, thread_id, turn_id
                                )
                                raise ProjectAppServerError(
                                    "INVALID_HIA_ITEM_ID",
                                    "an HIA item notification omitted its exact item id",
                                )
                            self._observe_exact_event(
                                method_name, params, thread_id, turn_id
                            )
                            if method_name == "item/started":
                                active_hia_items.add(item_id)
                            else:
                                active_hia_items.discard(item_id)
                        if method_name == "item/completed" and item.get("type") == "agentMessage":
                            text = item.get("text")
                            if not isinstance(text, str):
                                payload_error = payload_error or (
                                    "INVALID_AGENT_MESSAGE",
                                    "completed agentMessage must contain text",
                                )
                                continue
                            if len(text.encode("utf-8")) > self._max_message_bytes:
                                payload_error = payload_error or (
                                    "AGENT_MESSAGE_TOO_LARGE",
                                    "agentMessage exceeded its byte budget",
                                )
                                continue
                            if completed_texts and text != completed_texts[0]:
                                payload_error = payload_error or (
                                    "CONFLICTING_AGENT_MESSAGES",
                                    "the Turn emitted contradictory final agent messages",
                                )
                                continue
                            if payload_error is None:
                                completed_texts.append(text)
                        if method_name == "item/completed" and _is_hia_item(item):
                            hia_events.append(dict(event))

                if terminal_status is not None and not active_hia_items:
                    if terminal_status != "completed":
                        raise ProjectAppServerError(
                            "PROJECT_TURN_NOT_COMPLETED",
                            f"project Turn ended with status {terminal_status!r}",
                            turn_terminal_no_hia=True,
                        )
                    payload, terminal_payload_error = self._parse_payload(
                        deltas,
                        completed_texts,
                        payload_error,
                    )
                    return CompletedTurn(
                        thread_id=thread_id,
                        turn_id=turn_id,
                        status="completed",
                        payload=payload,
                        events=tuple(hia_events),
                        elapsed_seconds=max(0, int(self._clock() - tracked.started_at)),
                        payload_error_code=(
                            terminal_payload_error[0]
                            if terminal_payload_error is not None
                            else None
                        ),
                        payload_error_message=(
                            terminal_payload_error[1]
                            if terminal_payload_error is not None
                            else None
                        ),
                    )

                if not raw_events and not self._client.is_running:
                    raise ProjectAppServerError(
                        "CODEX_PROCESS_EXITED",
                        "Codex app-server exited while a project Turn was active",
                    )
        finally:
            with self._lock:
                current = self._turns.get(key)
                if current is tracked:
                    tracked.wait_finished = True
                    self._drop_terminal_turn_locked(key, tracked)

    def _observe_exact_event(
        self,
        method: str,
        params: Mapping[str, Any],
        thread_id: str,
        turn_id: str,
    ) -> None:
        key = (thread_id, turn_id)
        scene_action: tuple[str, str] | None = None
        owner: str | None = None
        invalid_identity = False
        with self._lock:
            tracked = self._turns.get(key)
            if tracked is None:
                return
            owner = tracked.scene_owner
            if method == "turn/completed":
                turn = params.get("turn")
                status = turn.get("status") if isinstance(turn, Mapping) else None
                tracked.terminal_status = (
                    status if isinstance(status, str) else "completed"
                )
                scene_action = ("terminal", "")
            elif method in {"item/started", "item/completed"}:
                item = params.get("item")
                if not isinstance(item, Mapping) or not _is_hia_item(item):
                    return
                item_id = item.get("id")
                if not isinstance(item_id, str) or not item_id:
                    tracked.invalid_hia_item_id = True
                    invalid_identity = True
                elif method == "item/started":
                    tracked.active_hia_items.add(item_id)
                    scene_action = ("started", item_id)
                else:
                    tracked.active_hia_items.discard(item_id)
                    scene_action = ("finished", item_id)
            self._drop_terminal_turn_locked(key, tracked)
        if owner is not None and self._scene_writer is not None and invalid_identity:
            self._scene_writer.fail_closed(owner)
            return
        if owner is None or self._scene_writer is None or scene_action is None:
            return
        action, item_id = scene_action
        if action == "started":
            self._scene_writer.hia_started(owner, item_id)
        elif action == "finished":
            self._scene_writer.hia_finished(owner, item_id)
        else:
            self._scene_writer.turn_terminal(owner)

    def _drop_terminal_turn_locked(
        self, key: tuple[str, str], tracked: _TurnCursor
    ) -> None:
        if (
            tracked.wait_finished
            and tracked.terminal_status is not None
            and not tracked.active_hia_items
            and not tracked.invalid_hia_item_id
            and self._turns.get(key) is tracked
        ):
            self._turns.pop(key, None)

    def _parse_payload(
        self,
        deltas: list[str],
        completed_texts: list[str],
        observed_error: tuple[str, str] | None = None,
    ) -> tuple[Mapping[str, Any], tuple[str, str] | None]:
        if observed_error is not None:
            return {}, observed_error
        streamed = "".join(deltas)
        completed = completed_texts[0] if completed_texts else ""
        if streamed and completed and streamed != completed:
            return {}, (
                "CONFLICTING_AGENT_MESSAGES",
                "streamed and completed agent messages disagree",
            )
        text = completed or streamed
        if not text:
            return {}, (
                "MISSING_AGENT_MESSAGE",
                "completed Turn has no structured agent message",
            )
        if len(text.encode("utf-8")) > self._max_message_bytes:
            return {}, (
                "AGENT_MESSAGE_TOO_LARGE",
                "agentMessage exceeded its byte budget",
            )
        try:
            payload = json.loads(text)
        except (TypeError, ValueError):
            return {}, (
                "INVALID_AGENT_JSON",
                "agentMessage is not valid JSON",
            )
        if not isinstance(payload, Mapping):
            return {}, (
                "INVALID_AGENT_PAYLOAD",
                "agentMessage JSON must be an object",
            )
        return dict(payload), None

def _identifier(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _belongs_to_turn(params: Mapping[str, Any], thread_id: str, turn_id: str) -> bool:
    return params.get("threadId") == thread_id and params.get("turnId") == turn_id


def _is_hia_item(item: Mapping[str, Any]) -> bool:
    if item.get("type") != "mcpToolCall":
        return False
    server = item.get("server")
    tool = item.get("tool")
    return server in {"hia_mcp_v2", "houdini_intelligence"} or (
        isinstance(tool, str) and tool.startswith("hia_")
    )
