"""Pure Python Turn state used by the Houdini Panel."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum


class TurnPhase(str, Enum):
    """Panel-visible lifecycle for at most one selected Thread's active Turn."""

    IDLE = "idle"
    STARTING = "starting"
    IN_PROGRESS = "in_progress"
    RECONCILING = "reconciling"


@dataclass(frozen=True)
class ControlAvailability:
    """Derived enabled state for the four Turn-related Panel actions."""

    new_thread: bool
    resume_thread: bool
    send: bool
    stop: bool


@dataclass(frozen=True)
class TurnStateToken:
    """Immutable correlation token for one observed Panel Turn state."""

    generation: int
    revision: int
    thread_id: str | None
    turn_id: str | None


class PanelTurnState:
    """Deny concurrent Turns and unlock only on authoritative evidence.

    ``turn/start`` acknowledgement transitions to ``IN_PROGRESS``.  It never
    transitions to ``IDLE``.  A matching ``turn/completed`` notification, or
    explicit reconciliation proving no active Turn exists, is required before
    another Turn can begin.
    """

    def __init__(self) -> None:
        self._phase = TurnPhase.IDLE
        self._thread_id: str | None = None
        self._turn_id: str | None = None
        self._generation = 0
        self._revision = 0
        self._reconciliation_claims: set[tuple[int, str]] = set()

    @property
    def phase(self) -> TurnPhase:
        return self._phase

    @property
    def thread_id(self) -> str | None:
        return self._thread_id

    @property
    def turn_id(self) -> str | None:
        return self._turn_id

    @property
    def busy(self) -> bool:
        return self._phase is not TurnPhase.IDLE

    def capture_token(self) -> TurnStateToken:
        """Capture the exact generation and revision for an async operation."""

        return TurnStateToken(
            generation=self._generation,
            revision=self._revision,
            thread_id=self._thread_id,
            turn_id=self._turn_id,
        )

    def token_is_current(self, token: TurnStateToken | None) -> bool:
        return isinstance(token, TurnStateToken) and token == self.capture_token()

    def token_generation_is_current(self, token: TurnStateToken | None) -> bool:
        """Return whether an async operation belongs to the active generation."""

        return (
            isinstance(token, TurnStateToken)
            and token.generation == self._generation
            and token.thread_id == self._thread_id
        )

    def claim_reconciliation(self, reason: str = "unspecified") -> TurnStateToken | None:
        """Allow one session reconciliation per reason and Turn generation."""

        normalized_reason = reason.strip() if isinstance(reason, str) else ""
        if not normalized_reason:
            normalized_reason = "unspecified"
        claim = (self._generation, normalized_reason)
        if claim in self._reconciliation_claims:
            return None
        self._reconciliation_claims.add(claim)
        return self.capture_token()

    def begin_start(self, thread_id: str) -> bool:
        """Reserve the single Turn slot before sending ``turn/start``."""

        normalized_thread_id = self._identifier(thread_id)
        if normalized_thread_id is None or self.busy:
            return False
        self._generation += 1
        self._reconciliation_claims.clear()
        self._thread_id = normalized_thread_id
        self._turn_id = None
        self._phase = TurnPhase.STARTING
        self._touch()
        return True

    def acknowledge_start(
        self,
        token: TurnStateToken,
        thread_id: str,
        turn_id: str,
    ) -> bool:
        """Record a correlated ``turn/start`` acknowledgement.

        ``turn/started`` may legitimately advance the state revision before
        the acknowledgement arrives.  The generation must still be the one
        for which the request was issued, preventing an old acknowledgement
        from attaching itself to a newer ``STARTING`` state.
        """

        if not self.token_generation_is_current(token):
            return False
        return self._record_started(thread_id, turn_id)

    def observe_started(self, thread_id: str, turn_id: str) -> bool:
        """Apply an allowlisted ``turn/started`` notification idempotently."""

        return self._record_started(thread_id, turn_id)

    def mark_start_uncertain(self, thread_id: str) -> bool:
        """Keep controls locked after an ambiguous ``turn/start`` transport error."""

        normalized_thread_id = self._identifier(thread_id)
        if normalized_thread_id is None or normalized_thread_id != self._thread_id:
            return False
        if self._phase is TurnPhase.STARTING:
            self._phase = TurnPhase.RECONCILING
            self._touch()
            return True
        return self._phase in {TurnPhase.RECONCILING, TurnPhase.IN_PROGRESS}

    def confirm_start_not_created(
        self,
        thread_id: str,
        *,
        no_active_turn: bool,
    ) -> bool:
        """Unlock only when an authoritative snapshot proves no Turn is active."""

        normalized_thread_id = self._identifier(thread_id)
        if (
            not no_active_turn
            or normalized_thread_id is None
            or normalized_thread_id != self._thread_id
            or self._turn_id is not None
            or self._phase not in {TurnPhase.STARTING, TurnPhase.RECONCILING}
        ):
            return False
        self._phase = TurnPhase.IDLE
        self._touch()
        return True

    def observe_completed(self, thread_id: str, turn_id: str) -> bool:
        """Unlock only for the exact active Thread and Turn identifiers."""

        normalized_thread_id = self._identifier(thread_id)
        normalized_turn_id = self._identifier(turn_id)
        if (
            self._phase is not TurnPhase.IN_PROGRESS
            or normalized_thread_id is None
            or normalized_turn_id is None
            or normalized_thread_id != self._thread_id
            or normalized_turn_id != self._turn_id
        ):
            return False
        self._phase = TurnPhase.IDLE
        self._turn_id = None
        self._touch()
        return True

    def reconcile_snapshot(
        self,
        token: TurnStateToken,
        thread_id: str,
        turn_id: str | None,
        turn_status: str | None,
        *,
        turn_active: bool,
    ) -> bool:
        """Apply a post-failure/session snapshot without trusting stale replies.

        The snapshot is applied only if the generation, state revision, Thread,
        and Turn still equal the values captured when the GET was issued.  A
        late response therefore cannot unlock or re-lock a newer state.
        """

        if not self.token_is_current(token):
            return False
        normalized_thread_id = self._identifier(thread_id)
        normalized_turn_id = self._identifier(turn_id)
        if normalized_thread_id is None:
            return False

        if turn_active:
            if self._phase is TurnPhase.IDLE:
                self._thread_id = normalized_thread_id
                self._turn_id = normalized_turn_id
                self._phase = (
                    TurnPhase.IN_PROGRESS
                    if normalized_turn_id is not None
                    else TurnPhase.RECONCILING
                )
                self._touch()
                return True
            if normalized_thread_id != self._thread_id:
                return False
            if normalized_turn_id is None:
                return self._phase in {TurnPhase.STARTING, TurnPhase.RECONCILING}
            return self.observe_started(normalized_thread_id, normalized_turn_id)

        # ``turn_active == false`` is the authoritative fact.  The status is
        # retained for the UI, but older Bridge builds may omit it.  Exact
        # token and identifier matching still prevents a stale snapshot from
        # unlocking a newer Turn.
        if normalized_turn_id is not None:
            if (
                self._phase in {TurnPhase.STARTING, TurnPhase.RECONCILING}
                and self._turn_id is None
            ):
                # A session snapshot cannot prove that a terminal Turn ID is
                # the start request currently in flight; it may describe the
                # previous Turn on the same Thread.  Only its correlated
                # turn/start acknowledgement may bind that ID.
                return False
            if self._phase is TurnPhase.IN_PROGRESS:
                return self.observe_completed(normalized_thread_id, normalized_turn_id)
            return self._phase is TurnPhase.IDLE
        if self._phase in {TurnPhase.STARTING, TurnPhase.RECONCILING}:
            return self.confirm_start_not_created(
                normalized_thread_id,
                no_active_turn=True,
            )
        return self._phase is TurnPhase.IDLE

    def reconcile_no_active_error(
        self,
        token: TurnStateToken,
        details: Mapping[str, object],
    ) -> bool:
        """Apply the authoritative terminal snapshot from ``NO_ACTIVE_TURN``."""

        if details.get("turn_active") is not False:
            return False
        thread_id = details.get("thread_id")
        turn_id = details.get("turn_id")
        turn_status = details.get("turn_status")
        if not isinstance(thread_id, str):
            return False
        return self.reconcile_snapshot(
            token,
            thread_id,
            turn_id if isinstance(turn_id, str) else None,
            turn_status if isinstance(turn_status, str) else None,
            turn_active=False,
        )

    def reconcile_steer_snapshot(
        self,
        token: TurnStateToken,
        thread_id: str,
        turn_id: str | None,
        *,
        turn_active: bool,
    ) -> bool:
        """Apply the one authoritative snapshot requested after a stale steer."""

        normalized_thread_id = self._identifier(thread_id)
        normalized_turn_id = self._identifier(turn_id)
        if (
            not self.token_is_current(token)
            or self._phase is not TurnPhase.IN_PROGRESS
            or normalized_thread_id != self._thread_id
        ):
            return False
        if not turn_active:
            self._phase = TurnPhase.IDLE
            self._turn_id = None
            self._touch()
            return True
        if normalized_turn_id is None:
            return False
        if normalized_turn_id != self._turn_id:
            self._generation += 1
            self._reconciliation_claims.clear()
            self._turn_id = normalized_turn_id
            self._touch()
        return True

    def derive_controls(
        self,
        *,
        connected: bool,
        authenticated: bool,
        selected_thread_id: str | None,
    ) -> ControlAvailability:
        """Derive buttons without relying on their previous UI enabled state."""

        base_enabled = bool(connected and authenticated)
        idle = self._phase is TurnPhase.IDLE
        selected_thread = self._identifier(selected_thread_id) is not None
        interruptible = (
            connected
            and self._phase is TurnPhase.IN_PROGRESS
            and self._identifier(self._thread_id) is not None
            and self._identifier(self._turn_id) is not None
        )
        return ControlAvailability(
            new_thread=base_enabled and idle,
            resume_thread=base_enabled and idle,
            send=base_enabled and idle and selected_thread,
            stop=bool(interruptible),
        )

    def _record_started(self, thread_id: str, turn_id: str) -> bool:
        normalized_thread_id = self._identifier(thread_id)
        normalized_turn_id = self._identifier(turn_id)
        if normalized_thread_id is None or normalized_turn_id is None:
            return False
        if self._phase in {TurnPhase.STARTING, TurnPhase.RECONCILING}:
            if normalized_thread_id != self._thread_id or self._turn_id is not None:
                return False
            self._turn_id = normalized_turn_id
            self._phase = TurnPhase.IN_PROGRESS
            self._touch()
            return True
        if self._phase is TurnPhase.IN_PROGRESS:
            return (
                normalized_thread_id == self._thread_id
                and normalized_turn_id == self._turn_id
            )
        return False

    def _touch(self) -> None:
        self._revision += 1

    @staticmethod
    def _identifier(value: object) -> str | None:
        if not isinstance(value, str):
            return None
        value = value.strip()
        return value or None
