"""Exact app-server event adapter for project-team effects.

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
from typing import TYPE_CHECKING, Any, Callable

from .events import EventBuffer
from .project_effects import CompletedTurn

if TYPE_CHECKING:
    from .codex_stdio import CodexStdioClient


class ProjectAppServerError(RuntimeError):
    """A deterministic failure while correlating a native project Turn."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class ProjectTurnTimeout(TimeoutError):
    """The acknowledged native Turn did not terminate within its budget."""


@dataclass(frozen=True)
class _TurnCursor:
    cursor: int
    started_at: float


class ProjectEffectClient:
    """Adapt ``CodexStdioClient`` and ``EventBuffer`` to project effects.

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
        self._turns: dict[tuple[str, str], _TurnCursor] = {}

    def request(self, method: str, params: Mapping[str, Any]) -> Any:
        if method != "turn/start":
            return self._client.request(method, params)

        thread_id = params.get("threadId")
        if not _identifier(thread_id):
            raise ProjectAppServerError(
                "INVALID_TURN_REQUEST", "turn/start requires a non-empty threadId"
            )
        cursor = self._events.cursor()
        started_at = self._clock()
        result = self._client.request(method, params)
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
        key = (str(thread_id), str(turn_id))
        with self._lock:
            if key in self._turns:
                raise ProjectAppServerError(
                    "DUPLICATE_TURN_ACK", "the acknowledged Turn is already tracked"
                )
            self._turns[key] = _TurnCursor(cursor=cursor, started_at=started_at)
        return result

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
        subagents: set[str] = set()

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
                        if status != "completed":
                            raise ProjectAppServerError(
                                "PROJECT_TURN_NOT_COMPLETED",
                                f"project Turn ended with status {status!r}",
                            )
                        payload = self._parse_payload(deltas, completed_texts)
                        return CompletedTurn(
                            thread_id=thread_id,
                            turn_id=turn_id,
                            status="completed",
                            payload=payload,
                            events=tuple(hia_events),
                            elapsed_seconds=max(0, int(self._clock() - tracked.started_at)),
                            native_subagents=len(subagents),
                        )

                    if not _belongs_to_turn(params, thread_id, turn_id):
                        continue
                    if method_name == "item/agentMessage/delta":
                        delta = params.get("delta")
                        if not isinstance(delta, str):
                            raise ProjectAppServerError(
                                "INVALID_AGENT_MESSAGE", "agentMessage delta must be text"
                            )
                        delta_bytes += len(delta.encode("utf-8"))
                        if delta_bytes > self._max_message_bytes:
                            raise ProjectAppServerError(
                                "AGENT_MESSAGE_TOO_LARGE", "agentMessage exceeded its byte budget"
                            )
                        deltas.append(delta)
                    elif method_name in {"item/started", "item/completed"}:
                        item = params.get("item")
                        if not isinstance(item, Mapping):
                            continue
                        if method_name == "item/completed" and item.get("type") == "agentMessage":
                            text = item.get("text")
                            if not isinstance(text, str):
                                raise ProjectAppServerError(
                                    "INVALID_AGENT_MESSAGE", "completed agentMessage must contain text"
                                )
                            if len(text.encode("utf-8")) > self._max_message_bytes:
                                raise ProjectAppServerError(
                                    "AGENT_MESSAGE_TOO_LARGE", "agentMessage exceeded its byte budget"
                                )
                            if completed_texts and text != completed_texts[0]:
                                raise ProjectAppServerError(
                                    "CONFLICTING_AGENT_MESSAGES",
                                    "the Turn emitted contradictory final agent messages",
                                )
                            completed_texts.append(text)
                        if method_name == "item/completed" and _is_hia_item(item):
                            hia_events.append(dict(event))
                        _collect_subagents(item, subagents)

                if not raw_events and not self._client.is_running:
                    raise ProjectAppServerError(
                        "CODEX_PROCESS_EXITED",
                        "Codex app-server exited while a project Turn was active",
                    )
        finally:
            with self._lock:
                self._turns.pop(key, None)

    def _parse_payload(
        self, deltas: list[str], completed_texts: list[str]
    ) -> Mapping[str, Any]:
        streamed = "".join(deltas)
        completed = completed_texts[0] if completed_texts else ""
        if streamed and completed and streamed != completed:
            raise ProjectAppServerError(
                "CONFLICTING_AGENT_MESSAGES",
                "streamed and completed agent messages disagree",
            )
        text = completed or streamed
        if not text:
            raise ProjectAppServerError(
                "MISSING_AGENT_MESSAGE", "completed Turn has no structured agent message"
            )
        if len(text.encode("utf-8")) > self._max_message_bytes:
            raise ProjectAppServerError(
                "AGENT_MESSAGE_TOO_LARGE", "agentMessage exceeded its byte budget"
            )
        try:
            payload = json.loads(text)
        except (TypeError, ValueError) as exc:
            raise ProjectAppServerError(
                "INVALID_AGENT_JSON", "agentMessage is not valid JSON"
            ) from exc
        if not isinstance(payload, Mapping):
            raise ProjectAppServerError(
                "INVALID_AGENT_PAYLOAD", "agentMessage JSON must be an object"
            )
        return dict(payload)

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


def _collect_subagents(item: Mapping[str, Any], found: set[str]) -> None:
    item_type = item.get("type")
    if item_type == "subAgentActivity":
        identity = item.get("agentThreadId")
        if not _identifier(identity):
            identity = item.get("id")
        if _identifier(identity):
            found.add(str(identity))
    elif item_type == "collabAgentToolCall":
        identities: set[str] = set()
        receivers = item.get("receiverThreadIds")
        if isinstance(receivers, list):
            identities.update(str(value) for value in receivers if _identifier(value))
        states = item.get("agentsStates")
        if isinstance(states, Mapping):
            identities.update(str(value) for value in states if _identifier(value))
        if not identities and _identifier(item.get("id")):
            identities.add(str(item["id"]))
        found.update(identities)
