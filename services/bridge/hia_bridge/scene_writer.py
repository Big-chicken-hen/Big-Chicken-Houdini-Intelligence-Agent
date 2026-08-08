"""One process-local owner for the live Houdini scene writer."""

from __future__ import annotations

from dataclasses import dataclass
import threading
import uuid

from .errors import BridgeError


@dataclass(frozen=True)
class SceneWriterReservation:
    token: str
    thread_id: str


class SceneWriterOwnership:
    """Non-persistent, non-queuing ownership for one live scene writer."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._reservation: SceneWriterReservation | None = None
        self._owner: str | None = None
        self._owner_thread_id: str | None = None
        self._owner_turn_id: str | None = None
        self._turn_terminal = False
        self._active_hia_items: set[str] = set()
        self._anonymous_hia_items = 0

    def reserve(self, thread_id: str) -> SceneWriterReservation:
        if not thread_id:
            raise ValueError("scene writer reservation identity is invalid")
        with self._lock:
            if self._reservation is not None or self._owner is not None:
                raise BridgeError(
                    "SCENE_WRITER_BUSY",
                    "Another Turn already owns the live Houdini scene writer",
                    409,
                    {"owner": self._owner, "starting": self._owner is None},
                )
            reservation = SceneWriterReservation(
                token=uuid.uuid4().hex,
                thread_id=thread_id,
            )
            self._reservation = reservation
            self._turn_terminal = False
            self._active_hia_items.clear()
            self._anonymous_hia_items = 0
            return reservation

    def bind(
        self,
        reservation: SceneWriterReservation,
        thread_id: str,
        turn_id: str,
    ) -> str:
        if not thread_id or not turn_id:
            raise ValueError("thread_id and turn_id are required")
        with self._lock:
            self._require_reservation(reservation)
            if reservation.thread_id != thread_id:
                raise ValueError("scene writer reservation must match thread_id")
            owner = f"ordinary:{thread_id}:{turn_id}"
            self._owner = owner
            self._owner_thread_id = thread_id
            self._owner_turn_id = turn_id
            return owner

    def abandon_uncreated(self, reservation: SceneWriterReservation) -> None:
        with self._lock:
            self._require_reservation(reservation)
            if self._owner is not None:
                raise RuntimeError("cannot abandon a bound scene writer")
            self._clear_locked()

    def hia_started(self, owner: str, item_id: str) -> None:
        if not item_id:
            return
        with self._lock:
            if owner != self._owner:
                return
            self._active_hia_items.add(item_id)

    def hia_finished(self, owner: str, item_id: str) -> bool:
        with self._lock:
            if owner != self._owner:
                return False
            self._active_hia_items.discard(item_id)
            return self._release_if_safe_locked()

    def hia_anonymous_started(self, owner: str) -> None:
        with self._lock:
            if owner == self._owner:
                self._anonymous_hia_items += 1

    def hia_anonymous_finished(self, owner: str) -> bool:
        with self._lock:
            if owner != self._owner:
                return False
            if self._anonymous_hia_items > 0:
                self._anonymous_hia_items -= 1
            return self._release_if_safe_locked()

    def turn_terminal(self, owner: str) -> bool:
        with self._lock:
            if owner != self._owner:
                return False
            self._turn_terminal = True
            return self._release_if_safe_locked()

    def retained_after_terminal(self, owner: str) -> bool:
        with self._lock:
            return (
                owner == self._owner
                and self._turn_terminal
                and (
                    bool(self._active_hia_items)
                    or self._anonymous_hia_items > 0
                )
            )

    def is_current(self, owner: str) -> bool:
        with self._lock:
            return owner == self._owner

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return {
                "owner": self._owner,
                "thread_id": self._owner_thread_id,
                "turn_id": self._owner_turn_id,
                "starting": self._reservation is not None and self._owner is None,
                "turn_terminal": self._turn_terminal,
                "active_hia_items": len(self._active_hia_items),
                "anonymous_hia_items": self._anonymous_hia_items,
            }

    def _release_if_safe_locked(self) -> bool:
        if (
            not self._turn_terminal
            or self._active_hia_items
            or self._anonymous_hia_items > 0
        ):
            return False
        self._clear_locked()
        return True

    def _clear_locked(self) -> None:
        self._reservation = None
        self._owner = None
        self._owner_thread_id = None
        self._owner_turn_id = None
        self._turn_terminal = False
        self._active_hia_items.clear()
        self._anonymous_hia_items = 0

    def _require_reservation(self, reservation: SceneWriterReservation) -> None:
        if (
            self._reservation is None
            or self._reservation.token != reservation.token
        ):
            raise RuntimeError("scene writer reservation is no longer current")


__all__ = ["SceneWriterOwnership", "SceneWriterReservation"]
