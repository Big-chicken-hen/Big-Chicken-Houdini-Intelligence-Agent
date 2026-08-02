"""Bounded native Thread transfer after repeated automatic compaction."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import threading
import uuid
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from .errors import BridgeError


TRANSFER_SCHEMA = "hia-thread-transfer/1"
TRANSFER_COMPACTION_LIMIT = 3
_MAX_THREADS = 512
_MAX_SEEN = 96
_MAX_PENDING_DELETES = 64
_MAX_DESCRIPTOR_CONFIG_BYTES = 16_384


class TransferClient(Protocol):
    def request(self, method: str, params: Mapping[str, Any]) -> Any: ...


class TransferEvents(Protocol):
    def publish(self, event_type: str, **fields: Any) -> Mapping[str, Any]: ...


class ThreadTransferManager:
    """Count native compactions and perform one verified fork/swap/delete."""

    def __init__(
        self,
        client: TransferClient,
        events: TransferEvents,
        *,
        descriptor: Callable[[str], Mapping[str, Any] | None],
        rehydrate_descriptor: Callable[[Mapping[str, Any]], Mapping[str, Any]],
        begin: Callable[[str], bool],
        commit: Callable[[str, str, Mapping[str, Any]], Mapping[str, Any]],
        rollback: Callable[[str, str, Mapping[str, Any]], None],
        finish: Callable[..., None],
        state_path: Path | None = None,
    ) -> None:
        self._client, self._events = client, events
        self._descriptor, self._rehydrate_descriptor = descriptor, rehydrate_descriptor
        self._begin = begin
        self._commit, self._rollback, self._finish = commit, rollback, finish
        self._state_path = state_path
        self._lock = threading.RLock()
        self._threads: dict[str, dict[str, Any]] = {}
        self._lineage: list[dict[str, Any]] = []
        self._pending_deletes: list[dict[str, Any]] = []
        self._transferring: set[str] = set()
        self._recovering: set[tuple[str, str]] = set()
        self._load()

    def observe(self, event: Mapping[str, Any]) -> None:
        parsed = self._compaction(event)
        if parsed is None:
            return
        thread_id, turn_id, item_id, kind = parsed
        if self._descriptor(thread_id) is None:
            return
        try:
            with self._lock:
                state = self._threads.setdefault(thread_id, self._new_state())
                if state["pending"]:
                    return
                before = self._copy_state(state)
                changed = self._count(state, thread_id, turn_id, item_id, kind)
                if not changed:
                    return
                if state["count"] >= TRANSFER_COMPACTION_LIMIT:
                    state["pending"] = True
                try:
                    self._write()
                except Exception:
                    self._threads[thread_id] = before
                    raise
        except Exception as exc:
            self._failure(thread_id, exc)

    def after_event(self, event: Mapping[str, Any]) -> None:
        if event.get("type") != "codex_notification":
            return
        if event.get("method") in {
            "turn/completed",
            "thread/compacted",
            "item/started",
            "item/completed",
        }:
            self.maybe_schedule_all()

    def maybe_schedule_all(self) -> None:
        with self._lock:
            candidates = [
                thread_id
                for thread_id, state in self._threads.items()
                if state.get("pending") and thread_id not in self._transferring
            ]
        for thread_id in candidates:
            self._schedule(thread_id)

    def recover_pending_deletes(self) -> None:
        """Finish only persisted, re-verified fork deletions after a restart."""

        with self._lock:
            pending = copy.deepcopy(self._pending_deletes)
        for marker in pending:
            key = (marker["old_thread_id"], marker["new_thread_id"])
            with self._lock:
                if key in self._recovering:
                    continue
                self._recovering.add(key)
            try:
                self._recover_pending_delete(marker)
            finally:
                with self._lock:
                    self._recovering.discard(key)

    def snapshot(self, thread_id: str) -> dict[str, Any]:
        with self._lock:
            state = self._threads.get(thread_id, self._new_state())
            return {
                "generation": state["generation"],
                "compactions": state["count"],
                "pending": state["pending"],
            }

    def _schedule(self, thread_id: str) -> None:
        if not self._begin(thread_id):
            return
        with self._lock:
            state = self._threads.get(thread_id)
            if state is None or not state.get("pending") or thread_id in self._transferring:
                self._finish(thread_id)
                return
            self._transferring.add(thread_id)
        worker = threading.Thread(
            target=self._transfer,
            args=(thread_id,),
            name=f"hia-thread-transfer-{thread_id[-8:]}",
            daemon=True,
        )
        try:
            worker.start()
        except RuntimeError as exc:
            with self._lock:
                self._transferring.discard(thread_id)
            self._finish(thread_id)
            self._failure(thread_id, exc)

    def _transfer(self, old_thread_id: str) -> None:
        new_thread_id: str | None = None
        metadata: Mapping[str, Any] = {}
        try:
            descriptor = self._descriptor(old_thread_id)
            if descriptor is None:
                raise BridgeError(
                    "THREAD_TRANSFER_UNMANAGED",
                    "The compacted Thread is no longer managed by this Bridge",
                    http_status=409,
                )
            old = self._read_thread(old_thread_id)
            last_turn_id, expected_context = self._fork_context(old)
            goal = self._read_goal(old_thread_id)
            old_profile = self._client.request(
                "thread/resume",
                self._resume_params(old_thread_id, descriptor),
            )
            self._response_thread(old_profile, "thread/resume", old_thread_id)
            self._verify_settings(old_profile, descriptor)
            title = descriptor.get("title") or old.get("name")
            source = descriptor.get("thread_source", old.get("threadSource"))
            params = self._fork_params(
                old_thread_id, last_turn_id, descriptor, source
            )
            forked = self._client.request("thread/fork", params)
            new_thread = self._response_thread(forked, "thread/fork")
            new_thread_id = self._identifier(new_thread.get("id"), "new_thread_id")
            if new_thread_id == old_thread_id:
                raise BridgeError(
                    "THREAD_TRANSFER_ID_REUSED",
                    "Codex thread/fork returned the original Thread id",
                    http_status=502,
                )
            if new_thread.get("forkedFromId") != old_thread_id:
                raise BridgeError(
                    "THREAD_TRANSFER_FORK_INVALID",
                    "Fork lineage does not identify the original Thread",
                    http_status=502,
                )
            self._require_context(new_thread, expected_context)
            self._verify_settings(forked, descriptor, baseline=old_profile)
            if isinstance(title, str) and title.strip():
                self._client.request(
                    "thread/name/set",
                    {"threadId": new_thread_id, "name": title},
                )
            resumed = self._client.request(
                "thread/resume",
                self._resume_params(new_thread_id, descriptor),
            )
            self._response_thread(resumed, "thread/resume", new_thread_id)
            self._verify_settings(resumed, descriptor, baseline=old_profile)
            verified = self._read_thread(new_thread_id)
            self._require_context(verified, expected_context)
            if isinstance(title, str) and verified.get("name") != title:
                raise BridgeError(
                    "THREAD_TRANSFER_TITLE_MISMATCH",
                    "Forked Thread title did not survive verification",
                    http_status=502,
                )
            if source is not None and verified.get("threadSource") != source:
                raise BridgeError(
                    "THREAD_TRANSFER_SOURCE_MISMATCH",
                    "Forked Thread source does not match its managed role",
                    http_status=502,
                )
            self._restore_goal(new_thread_id, goal)
            metadata = {
                key: descriptor[key]
                for key in ("project_id", "role")
                if isinstance(descriptor.get(key), str)
            }
            self._prepare_delete(
                old_thread_id,
                new_thread_id,
                last_turn_id,
                metadata,
                descriptor,
                old_profile,
            )
        except Exception as exc:
            self._failure(old_thread_id, exc, metadata)
            self._reset_failed_count(old_thread_id)
            self._finish_transfer(old_thread_id, new_thread_id)
            return

        try:
            committed_metadata = self._commit(
                old_thread_id, new_thread_id, descriptor
            )
            metadata = {
                **metadata,
                **{
                    key: committed_metadata[key]
                    for key in ("project_id", "role")
                    if isinstance(committed_metadata.get(key), str)
                },
            }
        except Exception as exc:
            try:
                self._rollback_prepared_delete(old_thread_id, new_thread_id)
            except Exception:
                pass
            self._failure(old_thread_id, exc, metadata)
            self._finish_transfer(old_thread_id, new_thread_id)
            return

        deleted = False
        try:
            self._client.request("thread/delete", {"threadId": old_thread_id})
            deleted = True
        except Exception as exc:
            if self._is_not_found(exc):
                deleted = True
            elif self._thread_exists(old_thread_id) is True:
                try:
                    self._rollback(new_thread_id, old_thread_id, descriptor)
                    self._rollback_prepared_delete(old_thread_id, new_thread_id)
                except Exception as rollback_error:
                    self._failure(
                        old_thread_id,
                        BridgeError(
                            "THREAD_TRANSFER_RECOVERY_PENDING",
                            f"Delete failed and ownership rollback is pending: {rollback_error}",
                            http_status=503,
                        ),
                        metadata,
                    )
                else:
                    self._failure(old_thread_id, exc, metadata)
                self._finish_transfer(old_thread_id, new_thread_id)
                return
            else:
                # The delete outcome is unknown.  Keep the verified new owner and
                # the durable marker; a restart will re-verify before retrying.
                self._failure(
                    old_thread_id,
                    BridgeError(
                        "THREAD_TRANSFER_RECOVERY_PENDING",
                        f"Old Thread delete outcome is unknown and will be recovered: {exc}",
                        http_status=503,
                    ),
                    metadata,
                )
                self._finish_transfer(old_thread_id, new_thread_id)
                return

        if deleted:
            try:
                self._finalize_delete(old_thread_id, new_thread_id)
            except Exception:
                # The irreversible delete already succeeded.  Preserve the marker
                # for idempotent startup cleanup and never roll ownership back.
                pass
            self._success(old_thread_id, new_thread_id, metadata)
        self._finish_transfer(old_thread_id, new_thread_id)

    def _finish_transfer(
        self, old_thread_id: str, new_thread_id: str | None
    ) -> None:
        with self._lock:
            self._transferring.discard(old_thread_id)
        self._finish(
            old_thread_id,
            *(tuple([new_thread_id]) if new_thread_id is not None else ()),
        )
        self.maybe_schedule_all()

    def _recover_pending_delete(self, marker: Mapping[str, Any]) -> None:
        old_thread_id = marker["old_thread_id"]
        new_thread_id = marker["new_thread_id"]
        last_turn_id = marker["last_turn_id"]
        metadata = marker.get("metadata")
        metadata = metadata if isinstance(metadata, Mapping) else {}
        try:
            persisted = marker.get("descriptor")
            if not isinstance(persisted, Mapping):
                raise BridgeError(
                    "THREAD_TRANSFER_RECOVERY_INVALID",
                    "Pending-delete runtime descriptor is missing",
                    http_status=502,
                )
            descriptor = dict(self._rehydrate_descriptor(persisted))
            self._verify_rehydrated_descriptor(persisted, descriptor)
            new_thread = self._read_thread(new_thread_id)
            if new_thread.get("forkedFromId") != old_thread_id:
                raise BridgeError(
                    "THREAD_TRANSFER_RECOVERY_INVALID",
                    "Pending-delete fork lineage no longer matches the old Thread",
                    http_status=502,
                )
            old_exists = self._thread_exists(old_thread_id)
            old_goal: Mapping[str, Any] | None = None
            expected_context: list[Any] | None = None
            effective_profile = marker.get("effective_profile")
            if not isinstance(effective_profile, Mapping):
                raise BridgeError(
                    "THREAD_TRANSFER_RECOVERY_INVALID",
                    "Pending-delete effective Thread profile is missing",
                    http_status=502,
                )
            old_profile: Mapping[str, Any] = effective_profile
            if old_exists is True:
                old_thread = self._read_thread(old_thread_id)
                current_last_turn_id, expected_context = self._fork_context(old_thread)
                if current_last_turn_id != last_turn_id:
                    raise BridgeError(
                        "THREAD_TRANSFER_RECOVERY_HISTORY_CHANGED",
                        "Old Thread gained a different terminal Turn after transfer preparation",
                        http_status=409,
                    )
                old_goal = self._read_goal(old_thread_id)
                resumed_old = self._client.request(
                    "thread/resume",
                    self._resume_params(old_thread_id, descriptor),
                )
                self._response_thread(
                    resumed_old, "thread/resume", old_thread_id
                )
                self._verify_settings(resumed_old, descriptor)
                self._verify_settings(
                    resumed_old, descriptor, baseline=effective_profile
                )
                old_profile = resumed_old
            elif old_exists is None:
                raise BridgeError(
                    "THREAD_TRANSFER_RECOVERY_UNAVAILABLE",
                    "Old Thread could not be read for recovery verification",
                    http_status=503,
                )
            if expected_context is None:
                self._require_turn(new_thread, last_turn_id)
            else:
                self._require_context(new_thread, expected_context)
            resumed = self._client.request(
                "thread/resume",
                self._resume_params(new_thread_id, descriptor),
            )
            self._response_thread(resumed, "thread/resume", new_thread_id)
            self._verify_settings(resumed, descriptor, baseline=old_profile)
            verified = self._read_thread(new_thread_id)
            if expected_context is None:
                self._require_turn(verified, last_turn_id)
            else:
                self._require_context(verified, expected_context)
            title = descriptor.get("title")
            if isinstance(title, str) and title.strip() and verified.get("name") != title:
                raise BridgeError(
                    "THREAD_TRANSFER_TITLE_MISMATCH",
                    "Recovered fork title does not match its runtime descriptor",
                    http_status=502,
                )
            source = descriptor.get("thread_source")
            if source is not None and verified.get("threadSource") != source:
                raise BridgeError(
                    "THREAD_TRANSFER_SOURCE_MISMATCH",
                    "Recovered fork source does not match its runtime descriptor",
                    http_status=502,
                )
            if old_exists is True:
                self._restore_goal(new_thread_id, old_goal)
            else:
                # Delete already completed before the previous process stopped.
                # The new native Goal must still be readable before marker cleanup.
                self._read_goal(new_thread_id)
            for current_id in (old_thread_id, new_thread_id):
                current = self._descriptor(current_id)
                if current is None:
                    continue
                for key in ("project_id", "role"):
                    expected = metadata.get(key)
                    if isinstance(expected, str) and current.get(key) != expected:
                        raise BridgeError(
                            "THREAD_TRANSFER_RECOVERY_INVALID",
                            "Pending-delete project ownership does not match the verified fork",
                            http_status=502,
                        )
            committed = self._commit(old_thread_id, new_thread_id, descriptor)
            metadata = {
                **metadata,
                **{
                    key: committed[key]
                    for key in ("project_id", "role")
                    if isinstance(committed.get(key), str)
                },
            }
            if old_exists is False:
                try:
                    self._finalize_delete(old_thread_id, new_thread_id)
                except Exception:
                    pass
                self._success(old_thread_id, new_thread_id, metadata)
                return
            try:
                self._client.request("thread/delete", {"threadId": old_thread_id})
            except Exception as exc:
                if not self._is_not_found(exc):
                    if self._thread_exists(old_thread_id) is True:
                        try:
                            self._rollback(
                                new_thread_id, old_thread_id, descriptor
                            )
                            self._rollback_prepared_delete(
                                old_thread_id, new_thread_id
                            )
                        except Exception:
                            pass
                    raise
            try:
                self._finalize_delete(old_thread_id, new_thread_id)
            except Exception:
                pass
            self._success(old_thread_id, new_thread_id, metadata)
        except Exception as exc:
            self._failure(old_thread_id, exc, metadata)

    def _prepare_delete(
        self,
        old_thread_id: str,
        new_thread_id: str,
        last_turn_id: str,
        metadata: Mapping[str, Any],
        descriptor: Mapping[str, Any],
        effective_profile: Mapping[str, Any],
    ) -> None:
        with self._lock:
            before = self._state_backup()
            previous = self._copy_state(
                self._threads.get(old_thread_id, self._new_state())
            )
            generation = int(previous["generation"]) + 1
            marker = {
                "old_thread_id": old_thread_id,
                "new_thread_id": new_thread_id,
                "last_turn_id": last_turn_id,
                "generation": generation,
                "metadata": {
                    key: metadata[key]
                    for key in ("project_id", "role")
                    if isinstance(metadata.get(key), str)
                },
                "descriptor": self._persist_descriptor(descriptor),
                "effective_profile": self._persist_effective_profile(
                    effective_profile
                ),
                "old_state": previous,
            }
            self._threads.pop(old_thread_id, None)
            self._threads[new_thread_id] = {
                **self._new_state(),
                "generation": generation,
            }
            self._pending_deletes = [
                value
                for value in self._pending_deletes
                if value.get("old_thread_id") != old_thread_id
            ]
            self._pending_deletes.append(marker)
            self._pending_deletes = self._pending_deletes[-_MAX_PENDING_DELETES:]
            try:
                self._write()
            except Exception:
                self._restore_backup(before)
                raise

    def _rollback_prepared_delete(
        self, old_thread_id: str, new_thread_id: str
    ) -> None:
        with self._lock:
            before = self._state_backup()
            marker = self._pending_marker(old_thread_id, new_thread_id)
            old_state = self._copy_state(marker.get("old_state", {}))
            old_state["count"] = TRANSFER_COMPACTION_LIMIT - 1
            old_state["pending"] = False
            self._threads.pop(new_thread_id, None)
            self._threads[old_thread_id] = old_state
            self._pending_deletes.remove(marker)
            try:
                self._write()
            except Exception:
                self._restore_backup(before)
                raise

    def _finalize_delete(self, old_thread_id: str, new_thread_id: str) -> None:
        with self._lock:
            before = self._state_backup()
            marker = self._pending_marker(old_thread_id, new_thread_id)
            self._pending_deletes.remove(marker)
            self._lineage.append(
                {
                    "old_thread_id": old_thread_id,
                    "new_thread_id": new_thread_id,
                    "generation": marker["generation"],
                }
            )
            self._lineage = self._lineage[-256:]
            try:
                self._write()
            except Exception:
                self._restore_backup(before)
                raise

    def _pending_marker(
        self, old_thread_id: str, new_thread_id: str
    ) -> dict[str, Any]:
        for marker in self._pending_deletes:
            if (
                marker.get("old_thread_id") == old_thread_id
                and marker.get("new_thread_id") == new_thread_id
            ):
                return marker
        raise BridgeError(
            "THREAD_TRANSFER_MARKER_MISSING",
            "Verified Thread pending-delete marker is missing",
            http_status=503,
        )

    def _state_backup(self) -> tuple[Any, Any, Any]:
        return (
            copy.deepcopy(self._threads),
            copy.deepcopy(self._lineage),
            copy.deepcopy(self._pending_deletes),
        )

    def _restore_backup(self, backup: tuple[Any, Any, Any]) -> None:
        self._threads, self._lineage, self._pending_deletes = backup

    def _reset_failed_count(self, thread_id: str) -> None:
        with self._lock:
            state = self._threads.get(thread_id)
            if state is None:
                return
            before = self._copy_state(state)
            state["count"] = TRANSFER_COMPACTION_LIMIT - 1
            state["pending"] = False
            try:
                self._write()
            except Exception:
                self._threads[thread_id] = before

    def _read_thread(self, thread_id: str) -> Mapping[str, Any]:
        result = self._client.request(
            "thread/read", {"threadId": thread_id, "includeTurns": True}
        )
        return self._response_thread(result, "thread/read", thread_id)

    def _read_goal(self, thread_id: str) -> Mapping[str, Any] | None:
        result = self._client.request("thread/goal/get", {"threadId": thread_id})
        goal = result.get("goal") if isinstance(result, Mapping) else object()
        if goal is None:
            return None
        if not isinstance(goal, Mapping) or goal.get("threadId") != thread_id:
            raise BridgeError(
                "THREAD_TRANSFER_GOAL_INVALID",
                "Native Goal read did not match the transferred Thread",
                http_status=502,
            )
        return dict(goal)

    def _restore_goal(
        self, thread_id: str, expected: Mapping[str, Any] | None
    ) -> None:
        current = self._read_goal(thread_id)
        stable = ("objective", "status", "tokenBudget")
        if expected is None:
            if current is not None:
                raise BridgeError(
                    "THREAD_TRANSFER_GOAL_MISMATCH",
                    "Forked Thread unexpectedly acquired a native Goal",
                    http_status=502,
                )
            return
        if current is None or any(
            current.get(key) != expected.get(key) for key in stable
        ):
            self._client.request(
                "thread/goal/set",
                {
                    "threadId": thread_id,
                    **{key: expected.get(key) for key in stable},
                },
            )
            current = self._read_goal(thread_id)
        if current is None or any(
            current.get(key) != expected.get(key) for key in stable
        ):
            raise BridgeError(
                "THREAD_TRANSFER_GOAL_MISMATCH",
                "Forked Thread native Goal could not be restored exactly",
                http_status=502,
            )

    def _thread_exists(self, thread_id: str) -> bool | None:
        try:
            self._read_thread(thread_id)
            return True
        except Exception as exc:
            return False if self._is_not_found(exc) else None

    @staticmethod
    def _is_not_found(error: Any) -> bool:
        values = [str(error)]
        for field in ("code", "message", "details"):
            value = getattr(error, field, None)
            if value is not None:
                values.append(str(value))
        normalized = " ".join(values).casefold().replace("_", " ")
        return any(
            marker in normalized
            for marker in ("not found", "does not exist", "unknown thread")
        )

    @staticmethod
    def _fork_params(
        thread_id: str,
        last_turn_id: str,
        descriptor: Mapping[str, Any],
        source: Any,
    ) -> dict[str, Any]:
        params = ThreadTransferManager._profile_params(descriptor)
        params.update(
            threadId=thread_id,
            lastTurnId=last_turn_id,
            ephemeral=False,
        )
        if source is not None:
            params["threadSource"] = source
        return params

    @staticmethod
    def _resume_params(
        thread_id: str, descriptor: Mapping[str, Any]
    ) -> dict[str, Any]:
        return {"threadId": thread_id, **ThreadTransferManager._profile_params(descriptor)}

    @staticmethod
    def _profile_params(descriptor: Mapping[str, Any]) -> dict[str, Any]:
        params = {
            "cwd": descriptor["cwd"],
            "approvalPolicy": descriptor["approval_policy"],
            "approvalsReviewer": descriptor.get("approvals_reviewer", "user"),
            "sandbox": descriptor["sandbox"],
            "developerInstructions": descriptor["developer_instructions"],
        }
        for source, target in (
            ("model", "model"),
            ("service_tier", "serviceTier"),
            ("config", "config"),
        ):
            value = descriptor.get(source)
            if value is not None:
                params[target] = value
        return params

    @staticmethod
    def _response_thread(
        result: Any, method: str, expected_id: str | None = None
    ) -> Mapping[str, Any]:
        thread = result.get("thread") if isinstance(result, Mapping) else None
        if not isinstance(thread, Mapping):
            raise BridgeError(
                "THREAD_TRANSFER_RESPONSE_INVALID",
                f"Codex {method} returned no Thread",
                http_status=502,
            )
        if expected_id is not None and thread.get("id") != expected_id:
            raise BridgeError(
                "THREAD_TRANSFER_RESPONSE_INVALID",
                f"Codex {method} returned a different Thread",
                http_status=502,
            )
        return thread

    @staticmethod
    def _fork_context(thread: Mapping[str, Any]) -> tuple[str, list[Any]]:
        turns = thread.get("turns")
        if not isinstance(turns, list):
            turns = []
        for index in range(len(turns) - 1, -1, -1):
            turn = turns[index]
            if (
                isinstance(turn, Mapping)
                and turn.get("status") in {"completed", "failed", "interrupted"}
            ):
                turn_id = ThreadTransferManager._identifier(
                    turn.get("id"), "last_turn_id"
                )
                return turn_id, copy.deepcopy(turns[: index + 1])
        raise BridgeError(
            "THREAD_TRANSFER_NO_TERMINAL_TURN",
            "Thread transfer requires a latest terminal Turn",
            http_status=409,
        )

    @staticmethod
    def _require_context(thread: Mapping[str, Any], expected: list[Any]) -> None:
        turns = thread.get("turns")
        if not isinstance(turns, list) or turns != expected:
            raise BridgeError(
                "THREAD_TRANSFER_HISTORY_MISMATCH",
                "Forked Thread does not preserve the complete verified Turn context",
                http_status=502,
            )

    @staticmethod
    def _require_turn(thread: Mapping[str, Any], turn_id: str) -> None:
        turns = thread.get("turns")
        if not isinstance(turns, list) or not any(
            isinstance(turn, Mapping)
            and turn.get("id") == turn_id
            and turn.get("status") in {"completed", "failed", "interrupted"}
            for turn in turns
        ):
            raise BridgeError(
                "THREAD_TRANSFER_HISTORY_MISSING",
                "Forked Thread does not contain the verified terminal Turn",
                http_status=502,
            )

    @staticmethod
    def _verify_settings(
        result: Any,
        descriptor: Mapping[str, Any],
        *,
        baseline: Mapping[str, Any] | None = None,
    ) -> None:
        if not isinstance(result, Mapping):
            raise BridgeError(
                "THREAD_TRANSFER_RESPONSE_INVALID",
                "Thread configuration verification response is invalid",
                http_status=502,
            )
        for response_key, descriptor_key in (
            ("model", "model"),
            ("serviceTier", "service_tier"),
            ("approvalPolicy", "approval_policy"),
            ("approvalsReviewer", "approvals_reviewer"),
        ):
            expected = (
                baseline.get(response_key)
                if baseline is not None
                else descriptor.get(descriptor_key)
            )
            if expected is not None and result.get(response_key) != expected:
                raise BridgeError(
                    "THREAD_TRANSFER_PERMISSION_MISMATCH",
                    f"Forked Thread did not preserve {response_key}",
                    http_status=502,
                )
        expected_sandbox = (
            baseline.get("sandbox")
            if baseline is not None
            else descriptor.get("sandbox")
        )
        actual_sandbox = result.get("sandbox")
        actual_type = ThreadTransferManager._sandbox_type(actual_sandbox)
        expected_type = ThreadTransferManager._sandbox_type(expected_sandbox)
        allowed = {expected_type}
        if baseline is None and expected_type == "workspaceWrite":
            # Windows may resolve requested workspace-write to read-only when
            # its native sandbox is unavailable.  Preserve that effective
            # boundary exactly across the fork; never accept a broader one.
            allowed.add("readOnly")
        if expected_sandbox is not None and actual_type not in allowed:
            raise BridgeError(
                "THREAD_TRANSFER_PERMISSION_MISMATCH",
                "Forked Thread did not preserve its sandbox",
                http_status=502,
            )
        expected_cwd = (
            baseline.get("cwd")
            if baseline is not None
            else descriptor.get("cwd")
        )
        actual_cwd = result.get("cwd")
        if expected_cwd is not None and (
            not isinstance(actual_cwd, str)
            or ThreadTransferManager._normalized_cwd(actual_cwd)
            != ThreadTransferManager._normalized_cwd(str(expected_cwd))
        ):
            raise BridgeError(
                "THREAD_TRANSFER_CWD_MISMATCH",
                "Forked Thread did not preserve its normalized project cwd",
                http_status=502,
            )

    @staticmethod
    def _sandbox_type(value: Any) -> Any:
        kind = value.get("type") if isinstance(value, Mapping) else value
        return {
            "read-only": "readOnly",
            "workspace-write": "workspaceWrite",
            "danger-full-access": "dangerFullAccess",
        }.get(kind, kind)

    @staticmethod
    def _normalized_cwd(value: str) -> str:
        return os.path.normcase(os.path.normpath(os.path.abspath(value)))

    @classmethod
    def _persist_descriptor(cls, value: Mapping[str, Any]) -> dict[str, Any]:
        """Persist bounded runtime/profile metadata, never instructions or chat."""

        if not isinstance(value, Mapping):
            raise BridgeError(
                "THREAD_TRANSFER_DESCRIPTOR_INVALID",
                "Thread transfer runtime descriptor is invalid",
                http_status=502,
            )
        result: dict[str, Any] = {}
        limits = {
            "title": 1_024,
            "model": 256,
            "effort": 64,
            "service_tier": 128,
            "cwd": 4_096,
            "approval_policy": 64,
            "approvals_reviewer": 64,
            "sandbox": 64,
            "profile": 64,
            "project_id": 256,
            "role": 64,
            "focus_binding": 128,
        }
        for key, limit in limits.items():
            raw = value.get(key)
            if raw is None:
                continue
            if not isinstance(raw, str) or not raw or len(raw) > limit or "\x00" in raw:
                raise BridgeError(
                    "THREAD_TRANSFER_DESCRIPTOR_INVALID",
                    f"Thread transfer descriptor field {key} is invalid",
                    http_status=502,
                )
            result[key] = raw
        cwd = result.get("cwd")
        if not isinstance(cwd, str) or not Path(cwd).is_absolute():
            raise BridgeError(
                "THREAD_TRANSFER_DESCRIPTOR_INVALID",
                "Thread transfer descriptor cwd must be absolute",
                http_status=502,
            )
        for key in ("selected", "focus_enabled"):
            raw = value.get(key)
            if raw is not None:
                if not isinstance(raw, bool):
                    raise BridgeError(
                        "THREAD_TRANSFER_DESCRIPTOR_INVALID",
                        f"Thread transfer descriptor field {key} is invalid",
                        http_status=502,
                    )
                result[key] = raw
        source = value.get("thread_source")
        if source is not None:
            result["thread_source"] = cls._bounded_json_value(source)
        config = value.get("config")
        if config is not None:
            bounded = cls._bounded_json_value(config)
            encoded = json.dumps(
                bounded, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
            if len(encoded) > _MAX_DESCRIPTOR_CONFIG_BYTES:
                raise BridgeError(
                    "THREAD_TRANSFER_DESCRIPTOR_INVALID",
                    "Thread transfer config is too large",
                    http_status=502,
                )
            result["config"] = bounded
        return result

    @classmethod
    def _persist_effective_profile(cls, value: Any) -> dict[str, str]:
        """Persist only the effective native boundary needed after old deletion."""

        if not isinstance(value, Mapping):
            raise BridgeError(
                "THREAD_TRANSFER_DESCRIPTOR_INVALID",
                "Thread transfer effective profile is invalid",
                http_status=502,
            )
        result: dict[str, str] = {}
        limits = {
            "model": 256,
            "serviceTier": 128,
            "approvalPolicy": 64,
            "approvalsReviewer": 64,
            "cwd": 4_096,
        }
        for key, limit in limits.items():
            raw = value.get(key)
            if raw is None:
                continue
            if not isinstance(raw, str) or not raw or len(raw) > limit or "\x00" in raw:
                raise BridgeError(
                    "THREAD_TRANSFER_DESCRIPTOR_INVALID",
                    f"Thread transfer effective profile field {key} is invalid",
                    http_status=502,
                )
            result[key] = raw
        sandbox = cls._sandbox_type(value.get("sandbox"))
        if sandbox not in {"readOnly", "workspaceWrite", "dangerFullAccess"}:
            raise BridgeError(
                "THREAD_TRANSFER_DESCRIPTOR_INVALID",
                "Thread transfer effective sandbox is invalid",
                http_status=502,
            )
        result["sandbox"] = sandbox
        return result

    @classmethod
    def _bounded_json_value(cls, value: Any, *, depth: int = 0) -> Any:
        if depth > 5:
            raise BridgeError(
                "THREAD_TRANSFER_DESCRIPTOR_INVALID",
                "Thread transfer descriptor nesting is too deep",
                http_status=502,
            )
        if value is None or isinstance(value, (bool, int, float)):
            return value
        if isinstance(value, str):
            if len(value) > 4_096 or "\x00" in value:
                raise BridgeError(
                    "THREAD_TRANSFER_DESCRIPTOR_INVALID",
                    "Thread transfer descriptor contains an invalid string",
                    http_status=502,
                )
            return value
        if isinstance(value, Mapping):
            if len(value) > 64:
                raise BridgeError(
                    "THREAD_TRANSFER_DESCRIPTOR_INVALID",
                    "Thread transfer descriptor contains too many config fields",
                    http_status=502,
                )
            return {
                str(key)[:256]: cls._bounded_json_value(child, depth=depth + 1)
                for key, child in value.items()
            }
        if isinstance(value, (list, tuple)) and len(value) <= 64:
            return [
                cls._bounded_json_value(child, depth=depth + 1) for child in value
            ]
        raise BridgeError(
            "THREAD_TRANSFER_DESCRIPTOR_INVALID",
            "Thread transfer descriptor contains an unsupported config value",
            http_status=502,
        )

    @classmethod
    def _verify_rehydrated_descriptor(
        cls, persisted: Mapping[str, Any], restored: Mapping[str, Any]
    ) -> None:
        if not isinstance(restored.get("developer_instructions"), str):
            raise BridgeError(
                "THREAD_TRANSFER_RECOVERY_INVALID",
                "Static Thread instructions could not be rebuilt for recovery",
                http_status=502,
            )
        if cls._persist_descriptor(restored) != dict(persisted):
            raise BridgeError(
                "THREAD_TRANSFER_RECOVERY_INVALID",
                "Rebuilt Thread profile differs from its persisted runtime descriptor",
                http_status=502,
            )

    @staticmethod
    def _identifier(value: Any, field: str) -> str:
        if not isinstance(value, str) or not value or len(value) > 256 or "\x00" in value:
            raise BridgeError(
                "THREAD_TRANSFER_RESPONSE_INVALID",
                f"Thread transfer returned an invalid {field}",
                http_status=502,
            )
        return value

    @staticmethod
    def _compaction(
        event: Mapping[str, Any]
    ) -> tuple[str, str, str | None, str] | None:
        if event.get("type") != "codex_notification":
            return None
        method = event.get("method")
        params = event.get("params")
        if not isinstance(params, Mapping):
            return None
        thread_id, turn_id = params.get("threadId"), params.get("turnId")
        if not isinstance(thread_id, str) or not isinstance(turn_id, str):
            return None
        if method == "thread/compacted":
            return thread_id, turn_id, None, "legacy"
        item = params.get("item")
        if (
            method in {"item/started", "item/completed"}
            and isinstance(item, Mapping)
            and item.get("type") == "contextCompaction"
            and isinstance(item.get("id"), str)
        ):
            return thread_id, turn_id, item["id"], "item"
        return None

    @staticmethod
    def _count(
        state: dict[str, Any],
        thread_id: str,
        turn_id: str,
        item_id: str | None,
        kind: str,
    ) -> bool:
        turn_key = ThreadTransferManager._hash(thread_id, turn_id)
        seen = state["seen"]
        legacy = state["legacy_unpaired"]
        items = state["item_unpaired"]
        if kind == "legacy":
            key = "l:" + turn_key
            if key in seen:
                return False
            ThreadTransferManager._remember(seen, key)
            if turn_key in items:
                items.remove(turn_key)
                return True
            ThreadTransferManager._remember(legacy, turn_key)
        else:
            key = "i:" + ThreadTransferManager._hash(thread_id, turn_id, item_id or "")
            if key in seen:
                return False
            ThreadTransferManager._remember(seen, key)
            if turn_key in legacy:
                legacy.remove(turn_key)
                return True
            if turn_key not in items:
                ThreadTransferManager._remember(items, turn_key)
        state["count"] = min(TRANSFER_COMPACTION_LIMIT, int(state["count"]) + 1)
        return True

    @staticmethod
    def _remember(values: list[str], value: str) -> None:
        values.append(value)
        if len(values) > _MAX_SEEN:
            del values[: len(values) - _MAX_SEEN]

    @staticmethod
    def _hash(*values: str) -> str:
        return hashlib.sha256("\x1f".join(values).encode("utf-8")).hexdigest()[:32]

    @staticmethod
    def _new_state() -> dict[str, Any]:
        return {
            "generation": 0,
            "count": 0,
            "pending": False,
            "seen": [],
            "legacy_unpaired": [],
            "item_unpaired": [],
        }

    @staticmethod
    def _copy_state(state: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "generation": int(state.get("generation", 0)),
            "count": int(state.get("count", 0)),
            "pending": state.get("pending") is True,
            "seen": list(state.get("seen", [])),
            "legacy_unpaired": list(state.get("legacy_unpaired", [])),
            "item_unpaired": list(state.get("item_unpaired", [])),
        }

    def _load(self) -> None:
        path = self._state_path
        if path is None or not path.is_file():
            return
        try:
            if path.stat().st_size > 262_144:
                raise ValueError
            value = json.loads(path.read_text(encoding="utf-8"))
            if value.get("schema") != TRANSFER_SCHEMA:
                raise ValueError
            for thread_id, raw in list(value.get("threads", {}).items())[:_MAX_THREADS]:
                if isinstance(thread_id, str) and isinstance(raw, Mapping):
                    self._threads[thread_id] = self._copy_state(raw)
            lineage = value.get("lineage")
            self._lineage = list(lineage[-256:]) if isinstance(lineage, list) else []
            pending = value.get("pending_deletes")
            if isinstance(pending, list):
                for raw in pending[-_MAX_PENDING_DELETES:]:
                    marker = self._loaded_marker(raw)
                    if marker is not None:
                        self._pending_deletes.append(marker)
        except (OSError, UnicodeError, ValueError, TypeError, AttributeError):
            self._threads, self._lineage, self._pending_deletes = {}, [], []

    @classmethod
    def _loaded_marker(cls, raw: Any) -> dict[str, Any] | None:
        if not isinstance(raw, Mapping):
            return None
        old_thread_id = raw.get("old_thread_id")
        new_thread_id = raw.get("new_thread_id")
        last_turn_id = raw.get("last_turn_id")
        generation = raw.get("generation")
        if (
            not all(
                isinstance(value, str) and value and len(value) <= 256
                for value in (old_thread_id, new_thread_id, last_turn_id)
            )
            or old_thread_id == new_thread_id
            or not isinstance(generation, int)
            or isinstance(generation, bool)
            or generation < 1
        ):
            return None
        metadata = raw.get("metadata")
        metadata = metadata if isinstance(metadata, Mapping) else {}
        descriptor = raw.get("descriptor")
        effective_profile = raw.get("effective_profile")
        if not isinstance(descriptor, Mapping):
            return None
        try:
            persisted_descriptor = cls._persist_descriptor(descriptor)
            persisted_effective_profile = cls._persist_effective_profile(
                effective_profile
            )
        except BridgeError:
            return None
        old_state = raw.get("old_state")
        return {
            "old_thread_id": old_thread_id,
            "new_thread_id": new_thread_id,
            "last_turn_id": last_turn_id,
            "generation": generation,
            "metadata": {
                key: metadata[key]
                for key in ("project_id", "role")
                if isinstance(metadata.get(key), str)
                and metadata[key]
                and len(metadata[key]) <= 256
            },
            "descriptor": persisted_descriptor,
            "effective_profile": persisted_effective_profile,
            "old_state": cls._copy_state(
                old_state if isinstance(old_state, Mapping) else {}
            ),
        }

    def _write(self) -> None:
        path = self._state_path
        if path is None:
            return
        payload = {
            "schema": TRANSFER_SCHEMA,
            "threads": dict(list(self._threads.items())[-_MAX_THREADS:]),
            "lineage": self._lineage[-256:],
            "pending_deletes": self._pending_deletes[-_MAX_PENDING_DELETES:],
        }
        encoded = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ) + "\n"
        if len(encoded.encode("utf-8")) > 262_144:
            raise BridgeError(
                "THREAD_TRANSFER_STATE_TOO_LARGE",
                "Bounded Thread transfer metadata is full",
                http_status=503,
            )
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(encoded, encoding="utf-8")
            os.replace(temporary, path)
        except OSError as exc:
            raise BridgeError(
                "THREAD_TRANSFER_STATE_UNAVAILABLE",
                "Thread transfer metadata could not be persisted",
                http_status=503,
            ) from exc
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def _failure(
        self,
        old_thread_id: str,
        error: Any,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        fields = {
            key: metadata[key]
            for key in ("project_id", "role")
            if metadata is not None and isinstance(metadata.get(key), str)
        }
        self._publish(
            "thread_transferred",
            old_thread_id=old_thread_id,
            new_thread_id=None,
            error=str(error).replace("\x00", "")[:2_000],
            **fields,
        )

    def _success(
        self,
        old_thread_id: str,
        new_thread_id: str,
        metadata: Mapping[str, Any],
    ) -> None:
        self._publish(
            "thread_transferred",
            old_thread_id=old_thread_id,
            new_thread_id=new_thread_id,
            **{
                key: metadata[key]
                for key in ("project_id", "role")
                if isinstance(metadata.get(key), str)
            },
        )

    def _publish(self, event_type: str, **fields: Any) -> None:
        # Notification delivery is downstream of the irreversible old-Thread
        # delete and therefore must never trigger ownership rollback.
        try:
            self._events.publish(event_type, **fields)
        except Exception:
            pass
