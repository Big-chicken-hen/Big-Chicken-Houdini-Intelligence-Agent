"""Safe automatic migration for ordinary native Codex Threads.

Only compact-event identities and the last user-selected runtime profile are
persisted.  Chat bodies remain authoritative in Codex and are never copied to
local storage.  Native requests run on a worker thread so the app-server reader
thread is never blocked waiting for its own response.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import threading
import time
from typing import Any, Callable, Mapping, Protocol


LEDGER_SCHEMA = "hia-ordinary-thread-transfer/1"
LEDGER_MAX_BYTES = 2 * 1024 * 1024


def _normalized_cwd(value: str) -> str | None:
    candidate = value.strip()
    if candidate.startswith("\\\\?\\UNC\\"):
        candidate = "\\\\" + candidate[8:]
    elif candidate.startswith("\\\\?\\"):
        candidate = candidate[4:]
    try:
        return os.path.normcase(str(Path(candidate).resolve(strict=False)))
    except (OSError, RuntimeError, ValueError):
        return None


class AppServerClient(Protocol):
    def request(self, method: str, params: Mapping[str, Any]) -> Any: ...


class OrdinaryTransferError(RuntimeError):
    """A replacement was rejected before the old Thread was changed."""


class OrdinaryThreadTransfer:
    """Count real compactions and replace an idle ordinary Thread at three."""

    def __init__(
        self,
        client: AppServerClient,
        *,
        project_root: Path,
        ledger_path: Path,
        publish: Callable[..., Any],
        rebind: Callable[[str, str], bool],
        idle_timeout_seconds: float = 30.0,
    ) -> None:
        self._client = client
        self._project_root = project_root.resolve()
        self._ledger_path = ledger_path.resolve()
        self._publish = publish
        self._rebind = rebind
        self._idle_timeout_seconds = idle_timeout_seconds
        self._lock = threading.RLock()
        self._inflight: set[str] = set()
        self._closed = False
        self._workers: set[threading.Thread] = set()

    def record_profile(
        self,
        thread_id: str,
        *,
        model: str | None,
        effort: str | None,
        service_tier: str | None,
    ) -> None:
        """Remember non-chat selections needed to verify a later native fork."""

        if not isinstance(thread_id, str) or not thread_id:
            return
        with self._lock:
            entries = self._read_entries()
            entry = dict(entries.get(thread_id, {}))
            previous_model = entry.get("model")
            previous_effort = entry.get("effort")
            previous_tier = entry.get("service_tier")
            entry.update(
                {
                    "thread_id": thread_id,
                    "model": model if model is not None else previous_model,
                    "effort": effort if effort is not None else previous_effort,
                    "service_tier": (
                        service_tier if service_tier is not None else previous_tier
                    ),
                    "compaction_event_ids": list(
                        entry.get("compaction_event_ids", [])
                    ),
                }
            )
            entries[thread_id] = entry
            self._write_entries(entries)

    def observe_codex_event(self, event: Mapping[str, object]) -> bool:
        """Persist one real compaction; return quickly on the reader thread."""

        if event.get("type") != "codex_notification":
            return False
        method = event.get("method")
        params = event.get("params")
        if not isinstance(params, Mapping):
            return False
        item: Mapping[str, object] | None = None
        if method == "thread/compacted":
            pass
        elif method == "item/completed":
            candidate = params.get("item")
            if not isinstance(candidate, Mapping) or candidate.get("type") != "contextCompaction":
                return False
            item = candidate
        else:
            return False
        thread_id = params.get("threadId")
        turn_id = params.get("turnId")
        if not isinstance(thread_id, str) or not isinstance(turn_id, str):
            return False
        item_id = item.get("id") if item is not None else None
        event_id = (
            f"item:{turn_id}:{item_id}"
            if isinstance(item_id, str) and item_id
            else f"legacy:{turn_id}"
        )
        with self._lock:
            if self._closed:
                return False
            entries = self._read_entries()
            entry = dict(entries.get(thread_id, {}))
            identities = list(entry.get("compaction_event_ids", []))
            legacy_id = f"legacy:{turn_id}"
            item_prefix = f"item:{turn_id}:"
            if event_id in identities:
                return True
            if event_id == legacy_id and any(value.startswith(item_prefix) for value in identities):
                return True
            if event_id.startswith(item_prefix) and legacy_id in identities:
                identities[identities.index(legacy_id)] = event_id
            else:
                identities.append(event_id)
            entry.update(
                {
                    "thread_id": thread_id,
                    "model": entry.get("model"),
                    "effort": entry.get("effort"),
                    "service_tier": entry.get("service_tier"),
                    "compaction_event_ids": identities,
                }
            )
            entries[thread_id] = entry
            self._write_entries(entries)
            self._publish(
                "ordinary_thread_compaction_recorded",
                thread_id=thread_id,
                turn_id=turn_id,
                compaction_count=len(identities),
            )
            if len(identities) >= 3:
                self._schedule_locked(thread_id)
        return True

    def close(self, timeout_seconds: float = 5.0) -> bool:
        with self._lock:
            self._closed = True
            workers = tuple(self._workers)
        deadline = time.monotonic() + max(0.0, timeout_seconds)
        for worker in workers:
            worker.join(max(0.0, deadline - time.monotonic()))
        return not any(worker.is_alive() for worker in workers)

    def recover(self) -> tuple[str, ...]:
        """Resume only persisted threshold transfers after a Bridge restart."""

        with self._lock:
            scheduled: list[str] = []
            for thread_id, entry in self._read_entries().items():
                if (
                    len(entry.get("compaction_event_ids", [])) >= 3
                    and self._schedule_locked(thread_id)
                ):
                    scheduled.append(thread_id)
            return tuple(scheduled)

    def _schedule_locked(self, thread_id: str) -> bool:
        if thread_id in self._inflight:
            return False
        self._inflight.add(thread_id)
        worker = threading.Thread(
            target=self._transfer_worker,
            args=(thread_id,),
            name="hia-transfer-ordinary",
            daemon=True,
        )
        self._workers.add(worker)
        worker.start()
        return True

    def _transfer_worker(self, old_thread_id: str) -> None:
        new_thread_id: str | None = None
        original_entry: dict[str, Any] | None = None
        ledger_replaced = False
        old_delete_confirmed = False
        try:
            with self._lock:
                entry = dict(self._read_entries().get(old_thread_id, {}))
            original_entry = entry
            if len(entry.get("compaction_event_ids", [])) < 3:
                return
            old_read = self._wait_for_idle(old_thread_id)
            old_thread = self._validate_ordinary_thread(old_read, old_thread_id)
            old_turns = self._turns(old_thread)
            old_goal = self._goal(old_thread_id)
            model = entry.get("model")
            if not isinstance(model, str) or not model:
                raise OrdinaryTransferError(
                    "ordinary Thread model is not known well enough to verify a fork"
                )
            params: dict[str, Any] = {
                "threadId": old_thread_id,
                "cwd": str(self._project_root),
                "approvalPolicy": "on-request",
                "approvalsReviewer": "user",
                "sandbox": "workspace-write",
                "ephemeral": False,
            }
            if isinstance(model, str) and model:
                params["model"] = model
            service_tier = entry.get("service_tier")
            if isinstance(service_tier, str) and service_tier:
                params["serviceTier"] = service_tier
            fork = self._client.request("thread/fork", params)
            new_thread_id = self._fork_id(fork)
            if new_thread_id == old_thread_id:
                raise OrdinaryTransferError("thread/fork returned the old identity")
            self._validate_observable_profile(fork, entry)
            new_read = self._client.request(
                "thread/read", {"threadId": new_thread_id, "includeTurns": True}
            )
            new_thread = self._validate_ordinary_thread(new_read, new_thread_id)
            if new_thread.get("forkedFromId") not in {None, old_thread_id}:
                raise OrdinaryTransferError("fork lineage does not match the old Thread")
            if self._turns(new_thread) != old_turns:
                raise OrdinaryTransferError("forked context does not match the old Thread")
            if self._goal(new_thread_id) != old_goal:
                raise OrdinaryTransferError("forked native Goal does not match the old Thread")

            with self._lock:
                entries = self._read_entries()
                current = entries.get(old_thread_id)
                if current != entry:
                    raise OrdinaryTransferError("ordinary transfer ledger changed")
                replacement = dict(entry)
                replacement["thread_id"] = new_thread_id
                replacement["compaction_event_ids"] = []
                replacement["replaced_thread_id"] = old_thread_id
                entries.pop(old_thread_id, None)
                entries[new_thread_id] = replacement
                self._write_entries(entries)
                ledger_replaced = True
            selected = self._rebind(old_thread_id, new_thread_id)
            cleanup_error = self._delete(old_thread_id)
            if cleanup_error is not None:
                old_still_exists = False
                try:
                    self._client.request(
                        "thread/read",
                        {"threadId": old_thread_id, "includeTurns": False},
                    )
                    old_still_exists = True
                except Exception:
                    pass
                if old_still_exists:
                    with self._lock:
                        entries = self._read_entries()
                        entries.pop(new_thread_id, None)
                        entries[old_thread_id] = entry
                        self._write_entries(entries)
                    self._rebind(new_thread_id, old_thread_id)
                    self._delete(new_thread_id)
                    ledger_replaced = False
                    new_thread_id = None
                    self._publish(
                        "thread_transferred",
                        old_thread_id=old_thread_id,
                        new_thread_id=None,
                        project_id=None,
                        role=None,
                        error=f"old Thread delete failed; old identity retained: {cleanup_error}",
                    )
                else:
                    self._publish(
                        "thread_transfer_cleanup_failed",
                        old_thread_id=old_thread_id,
                        new_thread_id=new_thread_id,
                        project_id=None,
                        role=None,
                        selected=selected,
                        error=cleanup_error,
                    )
            else:
                old_delete_confirmed = True
                self._publish(
                    "thread_transferred",
                    old_thread_id=old_thread_id,
                    new_thread_id=new_thread_id,
                    project_id=None,
                    role=None,
                    selected=selected,
                )
        except Exception as exc:
            if (
                ledger_replaced
                and not old_delete_confirmed
                and original_entry is not None
                and new_thread_id is not None
            ):
                try:
                    with self._lock:
                        entries = self._read_entries()
                        entries.pop(new_thread_id, None)
                        entries[old_thread_id] = original_entry
                        self._write_entries(entries)
                    self._rebind(new_thread_id, old_thread_id)
                except Exception as rollback_exc:
                    exc = OrdinaryTransferError(
                        f"{exc}; identity rollback failed: {rollback_exc}"
                    )
            if new_thread_id is not None:
                self._delete(new_thread_id)
            self._publish(
                "thread_transferred",
                old_thread_id=old_thread_id,
                new_thread_id=None,
                project_id=None,
                role=None,
                error=str(exc),
            )
        finally:
            with self._lock:
                self._inflight.discard(old_thread_id)
                self._workers.discard(threading.current_thread())

    def _wait_for_idle(self, thread_id: str) -> Mapping[str, Any]:
        deadline = time.monotonic() + self._idle_timeout_seconds
        while True:
            result = self._client.request(
                "thread/read", {"threadId": thread_id, "includeTurns": True}
            )
            thread = self._thread(result)
            status = thread.get("status")
            status_type = status.get("type") if isinstance(status, Mapping) else None
            if status_type in {"idle", "notLoaded"}:
                return result
            if time.monotonic() >= deadline:
                raise OrdinaryTransferError("ordinary Thread did not become idle")
            time.sleep(0.1)

    def _validate_ordinary_thread(
        self, result: Any, thread_id: str
    ) -> Mapping[str, Any]:
        thread = self._thread(result)
        if thread.get("id") != thread_id:
            raise OrdinaryTransferError("thread/read identity mismatch")
        source = thread.get("threadSource")
        if isinstance(source, str) and source.startswith("hia-project/"):
            raise OrdinaryTransferError("project role Threads use the project transfer")
        if thread.get("nativeSubagent") is True or thread.get("parentThreadId"):
            raise OrdinaryTransferError("native subagent Threads cannot be migrated")
        native_source = thread.get("source")
        if isinstance(native_source, Mapping) and "subAgent" in native_source:
            raise OrdinaryTransferError("native subagent Threads cannot be migrated")
        cwd = thread.get("cwd")
        if (
            not isinstance(cwd, str)
            or _normalized_cwd(cwd) != _normalized_cwd(str(self._project_root))
        ):
            raise OrdinaryTransferError("ordinary Thread belongs to a different project root")
        return thread

    @staticmethod
    def _thread(result: Any) -> Mapping[str, Any]:
        thread = result.get("thread") if isinstance(result, Mapping) else None
        if not isinstance(thread, Mapping):
            raise OrdinaryTransferError("thread descriptor is missing")
        return thread

    @staticmethod
    def _turns(thread: Mapping[str, Any]) -> list[Any]:
        turns = thread.get("turns")
        if not isinstance(turns, list):
            raise OrdinaryTransferError("full Thread context was not returned")
        return turns

    @staticmethod
    def _fork_id(result: Any) -> str:
        thread = OrdinaryThreadTransfer._thread(result)
        thread_id = thread.get("id")
        if not isinstance(thread_id, str) or not thread_id:
            raise OrdinaryTransferError("thread/fork returned no identity")
        return thread_id

    @staticmethod
    def _validate_observable_profile(result: Any, entry: Mapping[str, Any]) -> None:
        if not isinstance(result, Mapping):
            raise OrdinaryTransferError("thread/fork returned an invalid profile")
        if result.get("approvalPolicy") != "on-request":
            raise OrdinaryTransferError("forked approval policy changed")
        sandbox = result.get("sandbox")
        sandbox_type = sandbox.get("type") if isinstance(sandbox, Mapping) else None
        if sandbox_type not in {"workspaceWrite", "readOnly"}:
            raise OrdinaryTransferError("forked sandbox profile is invalid")
        expected_model = entry.get("model")
        if isinstance(expected_model, str) and expected_model and result.get("model") != expected_model:
            raise OrdinaryTransferError("forked model changed")
        expected_tier = entry.get("service_tier")
        if expected_tier is not None and result.get("serviceTier") != expected_tier:
            raise OrdinaryTransferError("forked service tier changed")
        expected_effort = entry.get("effort")
        if expected_effort is not None and result.get("reasoningEffort") != expected_effort:
            raise OrdinaryTransferError("forked reasoning effort changed")

    def _goal(self, thread_id: str) -> Any:
        result = self._client.request("thread/goal/get", {"threadId": thread_id})
        if not isinstance(result, Mapping):
            raise OrdinaryTransferError("thread/goal/get returned an invalid response")
        return result.get("goal")

    def _delete(self, thread_id: str) -> str | None:
        try:
            self._client.request("thread/delete", {"threadId": thread_id})
        except Exception as exc:
            return str(exc)
        return None

    def _read_entries(self) -> dict[str, dict[str, Any]]:
        if not self._ledger_path.exists():
            return {}
        if self._ledger_path.stat().st_size > LEDGER_MAX_BYTES:
            raise OrdinaryTransferError("ordinary transfer ledger is too large")
        raw = json.loads(self._ledger_path.read_text(encoding="utf-8"))
        if not isinstance(raw, Mapping) or raw.get("schema") != LEDGER_SCHEMA:
            raise OrdinaryTransferError("ordinary transfer ledger schema is invalid")
        entries = raw.get("threads")
        if not isinstance(entries, list):
            raise OrdinaryTransferError("ordinary transfer ledger is malformed")
        parsed: dict[str, dict[str, Any]] = {}
        for item in entries:
            if not isinstance(item, dict) or not isinstance(item.get("thread_id"), str):
                raise OrdinaryTransferError("ordinary transfer entry is malformed")
            identities = item.get("compaction_event_ids", [])
            if not isinstance(identities, list) or any(not isinstance(value, str) for value in identities):
                raise OrdinaryTransferError("ordinary compaction identities are malformed")
            if len(set(identities)) != len(identities):
                raise OrdinaryTransferError("ordinary compaction identities are duplicated")
            parsed[item["thread_id"]] = dict(item)
        return parsed

    def _write_entries(self, entries: Mapping[str, Mapping[str, Any]]) -> None:
        self._ledger_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema": LEDGER_SCHEMA,
            "threads": [entries[key] for key in sorted(entries)],
        }
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > LEDGER_MAX_BYTES:
            raise OrdinaryTransferError("ordinary transfer ledger is too large")
        temporary = self._ledger_path.with_name(f".{self._ledger_path.name}.{os.getpid()}.tmp")
        temporary.write_text(encoded, encoding="utf-8")
        os.replace(temporary, self._ledger_path)
