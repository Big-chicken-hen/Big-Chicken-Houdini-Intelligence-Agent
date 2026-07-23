"""Deterministic stdio JSONL app-server used only by offline tests."""

from __future__ import annotations

import json
import ntpath
import os
import sys
from typing import Any


THREAD_ID = "thread-fake"
TURN_ID = "turn-fake"
APPROVAL_ID = "approval-fake"
SYSTEM_DRIVE = (
    ntpath.splitdrive(os.environ.get("SystemRoot", ""))[0]
    or os.environ.get("SystemDrive")
    or "C:"
)


_turn_counter = 0
_pending_approvals: dict[str, tuple[str, str]] = {}
_goals: dict[str, dict[str, Any]] = {}


def emit(message: dict[str, Any]) -> None:
    sys.stdout.write(
        json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n"
    )
    sys.stdout.flush()


def result(request_id: Any, value: Any) -> None:
    emit({"id": request_id, "result": value})


def thread_payload(thread_id: str = THREAD_ID) -> dict[str, Any]:
    return {"thread": {"id": thread_id, "turns": [], "preview": "fake thread"}}


def goal_payload(thread_id: str, params: dict[str, Any]) -> dict[str, Any]:
    previous = _goals.get(thread_id, {})
    return {
        "threadId": thread_id,
        "objective": params.get("objective", previous.get("objective", "Fake Goal")),
        "status": params.get("status", previous.get("status", "active")),
        "tokenBudget": params.get("tokenBudget", previous.get("tokenBudget")),
        "tokensUsed": previous.get("tokensUsed", 12),
        "timeUsedSeconds": previous.get("timeUsedSeconds", 3),
        "createdAt": previous.get("createdAt", 1_720_000_000),
        "updatedAt": previous.get("updatedAt", 1_720_000_001) + 1,
    }


def turn_payload(
    turn_id: str = TURN_ID,
    status: str = "inProgress",
) -> dict[str, Any]:
    return {"turn": {"id": turn_id, "status": status, "items": []}}


def next_turn() -> tuple[str, str]:
    global _turn_counter
    _turn_counter += 1
    if _turn_counter == 1:
        return TURN_ID, APPROVAL_ID
    return f"{TURN_ID}-{_turn_counter}", f"{APPROVAL_ID}-{_turn_counter}"


def model_payload(
    model: str,
    display_name: str,
    *,
    hidden: bool = False,
    is_default: bool = False,
) -> dict[str, Any]:
    return {
        "id": model,
        "model": model,
        "displayName": display_name,
        "description": f"Fake catalog entry for {display_name}",
        "hidden": hidden,
        "isDefault": is_default,
        "inputModalities": ["text", "image"],
        "supportedReasoningEfforts": [
            {"reasoningEffort": "low", "description": "Faster"},
            {"reasoningEffort": "high", "description": "More reasoning"},
        ],
        "defaultReasoningEffort": "high" if is_default else "low",
    }


def handle_request(message: dict[str, Any]) -> None:
    request_id = message.get("id")
    method = message.get("method")
    params = message.get("params") or {}
    if method == "initialize":
        result(
            request_id,
            {
                "codexHome": "E:\\houdini-intelligence-agent\\.runtime\\fake-codex-home",
                "platformFamily": "windows",
                "platformOs": "windows",
                "userAgent": "fake-codex/0.144.3",
            },
        )
    elif method == "account/read":
        result(
            request_id,
            {
                "requiresOpenaiAuth": False,
                "account": {
                    "type": "chatgpt",
                    "email": "fake@example.invalid",
                    "planType": "plus",
                },
            },
        )
    elif method == "model/list":
        cursor = params.get("cursor")
        if cursor is None:
            result(
                request_id,
                {
                    "data": [
                        model_payload(
                            "fake-default-model",
                            "Fake Default Model",
                            is_default=True,
                        ),
                        model_payload(
                            "fake-hidden-model",
                            "Fake Hidden Model",
                            hidden=True,
                        ),
                    ],
                    "nextCursor": "fake-model-page-2",
                },
            )
        elif cursor == "fake-model-page-2":
            result(
                request_id,
                {
                    "data": [
                        model_payload("fake-secondary-model", "Fake Secondary Model")
                    ],
                    "nextCursor": None,
                },
            )
        else:
            result(request_id, {"data": [], "nextCursor": None})
    elif method == "thread/list":
        result(
            request_id,
            {
                "data": [
                    {
                        "id": THREAD_ID,
                        "cwd": os.getcwd(),
                        "name": "Fake Thread",
                        "preview": "fake thread",
                        "updatedAt": 1_720_000_000,
                        "recencyAt": 1_720_000_001,
                    }
                ],
                "nextCursor": None,
                "receivedParams": params,
            },
        )
    elif method == "thread/name/set":
        result(request_id, {"receivedParams": params})
        emit(
            {
                "method": "thread/name/updated",
                "params": {
                    "threadId": params.get("threadId", THREAD_ID),
                    "threadName": params.get("name"),
                },
            }
        )
    elif method == "thread/goal/get":
        thread_id = params.get("threadId", THREAD_ID)
        result(request_id, {"goal": _goals.get(thread_id)})
    elif method == "thread/goal/set":
        thread_id = params.get("threadId", THREAD_ID)
        goal = goal_payload(thread_id, params)
        _goals[thread_id] = goal
        result(request_id, {"goal": goal})
        emit(
            {
                "method": "thread/goal/updated",
                "params": {"threadId": thread_id, "goal": goal, "turnId": None},
            }
        )
    elif method == "thread/goal/clear":
        thread_id = params.get("threadId", THREAD_ID)
        cleared = _goals.pop(thread_id, None) is not None
        result(request_id, {"cleared": cleared})
        if cleared:
            emit(
                {
                    "method": "thread/goal/cleared",
                    "params": {"threadId": thread_id},
                }
            )
    elif method == "thread/start":
        response = thread_payload()
        response["receivedParams"] = params
        result(request_id, response)
        emit(
            {
                "method": "thread/started",
                "params": {"thread": thread_payload()["thread"]},
            }
        )
    elif method == "thread/resume":
        result(request_id, thread_payload(params.get("threadId", THREAD_ID)))
    elif method == "thread/read":
        result(request_id, thread_payload(params.get("threadId", THREAD_ID)))
    elif method == "turn/start":
        thread_id = params.get("threadId", THREAD_ID)
        turn_id, approval_id = next_turn()
        _pending_approvals[approval_id] = (thread_id, turn_id)
        response = turn_payload(turn_id)
        response["receivedParams"] = params
        result(request_id, response)
        emit(
            {
                "method": "turn/started",
                "params": {
                    "threadId": thread_id,
                    "turn": turn_payload(turn_id)["turn"],
                },
            }
        )
        emit(
            {
                "method": "turn/plan/updated",
                "params": {
                    "threadId": thread_id,
                    "turnId": turn_id,
                    "explanation": "fake plan",
                    "plan": [{"step": "stream reply", "status": "inProgress"}],
                },
            }
        )
        for delta in ("Hello ", "from fake Codex"):
            emit(
                {
                    "method": "item/agentMessage/delta",
                    "params": {
                        "threadId": thread_id,
                        "turnId": turn_id,
                        "itemId": f"item-agent-{turn_id}",
                        "delta": delta,
                    },
                }
            )
        emit(
            {
                "id": approval_id,
                "method": "item/commandExecution/requestApproval",
                "params": {
                    "threadId": thread_id,
                    "turnId": turn_id,
                    "itemId": f"item-command-{turn_id}",
                    "startedAtMs": 1,
                    "command": (
                        "Set-Content -LiteralPath "
                        f"'{SYSTEM_DRIVE}\\HIA-Fake-Approval.txt' -Value test"
                    ),
                    "reason": "fake approval test",
                },
            }
        )
    elif method == "turn/steer":
        turn_id = params.get("expectedTurnId", TURN_ID)
        response = {"turnId": turn_id, "receivedParams": params}
        result(request_id, response)
    elif method == "turn/interrupt":
        thread_id = params.get("threadId", THREAD_ID)
        turn_id = params.get("turnId", TURN_ID)
        stale_approvals = [
            approval_id
            for approval_id, mapped in _pending_approvals.items()
            if mapped == (thread_id, turn_id)
        ]
        for approval_id in stale_approvals:
            _pending_approvals.pop(approval_id, None)
        result(request_id, {})
        emit(
            {
                "method": "turn/completed",
                "params": {
                    "threadId": thread_id,
                    "turn": turn_payload(turn_id, "interrupted")["turn"],
                },
            }
        )
    else:
        emit(
            {
                "id": request_id,
                "error": {"code": -32601, "message": f"unknown fake method: {method}"},
            }
        )


def main() -> None:
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        message = json.loads(line)
        if message.get("method") == "initialized" and "id" not in message:
            sys.stderr.write("fake app-server initialized\n")
            sys.stderr.flush()
            emit({"method": "future/unknownNotification", "params": {"ignored": True}})
        elif "method" in message:
            handle_request(message)
        elif message.get("id") in _pending_approvals and "result" in message:
            approval_id = message["id"]
            thread_id, turn_id = _pending_approvals.pop(approval_id)
            emit(
                {
                    "method": "serverRequest/resolved",
                    "params": {"threadId": thread_id, "requestId": approval_id},
                }
            )
            emit(
                {
                    "method": "turn/completed",
                    "params": {
                        "threadId": thread_id,
                        "turn": turn_payload(turn_id, "completed")["turn"],
                    },
                }
            )


if __name__ == "__main__":
    main()
