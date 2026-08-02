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
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Iterable

from .events import EventBuffer
from .project_effects import CompletedTurn

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
    ) -> None:
        self.code = code
        self.turn_terminal_no_hia = bool(turn_terminal_no_hia)
        super().__init__(message)


class ProjectTurnTimeout(TimeoutError):
    """The acknowledged native Turn did not terminate within its budget."""


@dataclass(frozen=True)
class _TurnCursor:
    cursor: int
    started_at: float


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
        self._max_message_bytes = int(max_agent_message_bytes)
        self._poll_interval = float(poll_interval_seconds)
        self._clock = clock
        self._lock = threading.Lock()
        self._idle = threading.Condition(self._lock)
        self._starting_threads: set[str] = set()
        self._turns: dict[tuple[str, str], _TurnCursor] = {}

    def request(self, method: str, params: Mapping[str, Any]) -> Any:
        if method != "turn/start":
            return self._client.request(method, params)
        return self._start_turn(params, timeout_seconds=None)

    def start_turn_when_idle(
        self, params: Mapping[str, Any], timeout_seconds: float
    ) -> Any:
        if timeout_seconds <= 0:
            raise ProjectTurnTimeout("project Thread idle wait budget is exhausted")
        return self._start_turn(params, timeout_seconds=timeout_seconds)

    def _start_turn(
        self,
        params: Mapping[str, Any],
        *,
        timeout_seconds: float | None,
    ) -> Any:
        thread_id = params.get("threadId")
        if not _identifier(thread_id):
            raise ProjectAppServerError(
                "INVALID_TURN_REQUEST", "turn/start requires a non-empty threadId"
            )
        thread_id = str(thread_id)
        with self._idle:
            deadline = (
                None
                if timeout_seconds is None
                else time.monotonic() + timeout_seconds
            )
            while thread_id in self._starting_threads or any(
                active_thread_id == thread_id for active_thread_id, _ in self._turns
            ):
                if deadline is None:
                    raise ProjectAppServerError(
                        "PROJECT_THREAD_BUSY",
                        "the project Thread already has an active Turn",
                    )
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ProjectTurnTimeout(
                        "project Thread did not become idle before its deadline"
                    )
                self._idle.wait(remaining)
            self._starting_threads.add(thread_id)
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
            with self._idle:
                if key in self._turns:
                    raise ProjectAppServerError(
                        "DUPLICATE_TURN_ACK", "the acknowledged Turn is already tracked"
                    )
                self._turns[key] = _TurnCursor(cursor=cursor, started_at=started_at)
        finally:
            with self._idle:
                self._starting_threads.discard(thread_id)
                self._idle.notify_all()
        return result

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
                sorted(key for key in self._turns if key[0] in allowed)
            )
        for thread_id, turn_id in active:
            self._client.request(
                "turn/interrupt",
                {"threadId": thread_id, "turnId": turn_id},
            )
        return active

    def has_active_thread(self, thread_id: str) -> bool:
        if not _identifier(thread_id):
            raise ValueError("thread_id must be non-empty")
        with self._lock:
            return thread_id in self._starting_threads or any(
                active_thread_id == thread_id for active_thread_id, _ in self._turns
            )

    def wait_until_thread_idle(self, thread_id: str, timeout_seconds: float) -> bool:
        if not _identifier(thread_id):
            raise ValueError("thread_id must be non-empty")
        if timeout_seconds <= 0:
            return False
        deadline = time.monotonic() + timeout_seconds
        with self._idle:
            while thread_id in self._starting_threads or any(
                active_thread_id == thread_id for active_thread_id, _ in self._turns
            ):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._idle.wait(remaining)
            return True

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
                "UNACKNOWLEDGED_TURN", "the project Turn was not acknowledged by this adapter"
            )

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
                                item_id = str(item.get("tool") or "hia-tool")
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
            with self._idle:
                self._turns.pop(key, None)
                self._idle.notify_all()

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
