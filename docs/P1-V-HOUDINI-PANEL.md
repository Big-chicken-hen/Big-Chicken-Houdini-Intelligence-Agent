# P1-V Houdini-visible vertical slice

## Scope

P1-V provides a Houdini 21.0.440 Python Panel connected to the pinned Codex 0.144.3 app-server through an authenticated, loopback-only standard-library Bridge. It provides conversation display, native Qt input-method support for natural-language input, dynamic model and reasoning-effort selectors, send/stop, Thread start/resume, approval allow/deny, stable plan events, lifecycle state, and streaming agent-message deltas.

P1-V never imports `hou`, creates or modifies a node, saves a HIP file, or exposes a scene tool. Scene work such as “generate a table” remains P2-V.

Codex remains the only intelligent component. The Bridge forwards fixed protocol operations and transient events. It does not rewrite prompts, plan, remember conversations, or persist chat content.

## Process and trust boundary

```text
Houdini Python Panel (PySide6 UI + standard-library HTTP daemon workers)
    -> thread-safe bounded result queue
    -> main-thread QTimer drain
    -> Bearer-authenticated HTTP long polling
    -> 127.0.0.1:<random port> standard-library Bridge
    -> stdio JSONL
    -> E:\houdini-intelligence-agent\.runtime\toolchains\codex\0.144.3\codex.exe app-server
```

The Bridge generates a random 32-byte URL-safe token and asks the operating system for port `0`, producing a random available loopback port. Every endpoint, including health, requires the Bearer token. The app-server is never bound to a network socket.

The launcher passes the URL and token only through the Houdini child-process environment. It does not call `setx`, change global PATH, set HOME or USERPROFILE, or modify user/machine environment variables. Houdini preferences and temporary data are redirected to a unique project-local `.runtime/launcher-sessions/<uuid>` directory.

## Frozen protocol surface

The Bridge loads and validates `contracts/codex-app-server/0.144.3/core-allowlist.json` before launch. P1-V uses only:

- `initialize` followed by the `initialized` client notification.
- Read-only `account/read` to report `authenticated`, `login_required`, or `account_error`. P1-V does not initiate login.
- `thread/start`, `thread/resume`, and `thread/read`.
- `turn/start` and `turn/interrupt`.
- Read-only `model/list` for the Panel model catalog.
- Three stable approval requests and the stable streaming/lifecycle notifications frozen by P0-C2B.
- Receive-only observation of `account/rateLimits/updated`, `mcpServer/startupStatus/updated`, and `remoteControl/status/changed`.

P1-V explicitly adds stable, non-experimental `account/read` to the frozen allowlist because authentication visibility is required by the Panel. It also authorizes stable, non-experimental `model/list` as a read-only P1 extension; the Bridge follows its cursor with strict page and item limits and returns only the display fields needed by the selectors. The three status notifications are passive observations only; admitting them does not enable remote-control requests, screen takeover, or additional MCP tools. Unknown client methods are rejected before writing to stdin. Unknown server requests receive a JSON-RPC method-not-allowed error. Truly unknown notifications are recorded as protocol warnings and ignored.

No experimental Schema, app-server WebSocket, `dynamicTools`, process API, or `thread/shellCommand` is used.

## Bridge endpoints

All endpoints are under `/v1` and require `Authorization: Bearer <session-token>`.

| Method and path | Purpose |
|---|---|
| `GET /v1/health` | Bridge, app-server, authentication, Thread, and Turn state |
| `GET /v1/session` | Current transient connection identifiers |
| `GET /v1/models` | Bounded, read-only catalog of non-hidden Codex models |
| `POST /v1/session` | `start`, `resume`, or `read` a Codex Thread |
| `POST /v1/turn` | Forward the exact user text through `turn/start` |
| `GET /v1/events` | Bounded long polling; never blocks the Houdini UI thread |
| `POST /v1/interrupt` | Interrupt the selected active Turn |
| `POST /v1/approval` | Return one allow/deny decision for a pending approval |
| `POST /v1/shutdown` | Launcher-only: gracefully stop the Bridge and its owned app-server after Houdini exits |

The launcher owns the shared Bridge process. Closing or reopening a Python
Panel only disposes that Panel's local polling, timer, pending requests, and
HTTP workers; it never requests process-wide Bridge shutdown.

The in-memory event ring is bounded and process-local. It is not a chat database and is discarded when the Bridge exits. Codex Thread history remains authoritative and is read through `thread/read` when a Thread is resumed.

The Panel correlates every Turn start, interrupt, and recovery read with an immutable Turn-generation token. A `turn/start` acknowledgement never unlocks the controls; only a matching completion or authoritative Bridge state with `turn_active=false` does. Unmatched lifecycle events, late acknowledgements, event gaps, and terminal conflicts may trigger one `GET /v1/session` per reason and Turn generation. Each recovery read has a five-second absolute monotonic deadline, does not retry itself, and disables session-selection actions while in flight. A stale response cannot unlock or re-lock a newer Turn.

The model selector is populated only from `model/list`; no model identifier is hard-coded in the Panel. Starting a new Thread forwards the chosen model through `thread/start.model`. The next Turn forwards the current model and reasoning effort through `turn/start.model` and `turn/start.effort`, so changing either selector on an existing Thread takes effect on the next Turn. Both selectors are locked while a Turn is active. If catalog loading fails, the Panel keeps a **Codex 默认** choice and chat remains available.

The natural-language editor is a stock `QTextEdit` with no IME or focus overrides, selected after an offline Houdini IME A/B check found stock `QPlainTextEdit` intermittently unreliable. It adds no `WA_InputMethodEnabled`, input-method-hint, focus-policy, event-filter, key-handler, input-method-handler, viewport, or automatic-focus override. The read-only conversation and approval displays remain `QPlainTextEdit`. Network callbacks update text and enabled state without moving focus to another widget. JSON transport remains UTF-8 and preserves Unicode without ASCII escaping.

## Launch and open the Panel

The launcher is generated but is not executed as part of P1-V verification.

1. Open PowerShell.
2. Run:

   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File E:\houdini-intelligence-agent\scripts\launch-houdini-21.0.440.ps1
   ```

3. In Houdini, open the pane-tab type menu in any pane.
4. Choose **New Pane Tab Type → Python Panel → Houdini Intelligence**. If Houdini opens a generic Python Panel first, choose **Houdini Intelligence** from that pane's Python Panel interface menu.
5. Confirm the top row reports the Codex connection and authentication state.

If the isolated project-local Codex home has no account, the Panel deliberately displays **认证：需要登录** and disables conversation actions. It never pretends to be authenticated and P1-V performs no login.

### Manual Houdini input-method acceptance

Headless unit tests prove exact Unicode string forwarding but cannot prove that the operating-system candidate window behaves correctly inside Houdini. Perform this GUI-only acceptance after launching Houdini manually:

1. Open **Houdini Intelligence**, click the natural-language input editor, and switch to a Chinese IME.
2. Type several pinyin syllables, verify the candidate window remains visible, select candidates, and verify composition text is committed in place without duplicated or missing characters.
3. Enter exactly `中文输入测试：请生成一张四条腿的桌子，尺寸为 120×60×75 厘米。`.
4. Change **模型** and **推理强度**, then start or resume a Thread as appropriate and send the text.
5. Verify the displayed `You:` text is byte-for-byte equivalent when encoded as UTF-8, the selected model/effort remain locked for the active Turn, and they unlock only after terminal state reconciliation.
6. Repeat with a second Turn to confirm network events and button refreshes do not steal focus from an in-progress IME composition.

Closing the Panel performs only an idempotent local dispose and never requests global Bridge shutdown. After the launcher confirms that Houdini has exited, it sends the sole authenticated shutdown request and waits for the Bridge. If graceful shutdown fails, the launcher terminates only the exact Bridge and app-server process IDs it created; it never kills Houdini as part of Bridge cleanup.

## Verification boundary

Offline tests use `tests/fakes/fake_app_server.py` and cover initialization, authentication state, streaming deltas, stable plan updates, resume/read, interrupt, approval response, unknown-method rejection, unknown-notification recording and ignore behavior, bounded model-list pagination and fallback, exact Unicode forwarding, loopback binding, Bearer rejection, launcher syntax, Panel/package structure, and child-process reaping.

The finite real smoke test runs only `initialize`, `initialized`, and `account/read`, then closes stdin and verifies the app-server process exits. It starts no Bridge network listener, creates no Thread or Turn, and performs no login.

## Recorded finite smoke result

On 2026-07-14, the project-local Codex 0.144.3 executable returned:

- `initialize`: exit path succeeded; user agent identified Codex Desktop/0.144.3 on Windows.
- `account/read`: `requiresOpenaiAuth` was `true` and `account` was absent.
- Shutdown: stdin was closed normally and the exact app-server process was reaped.

No login, Thread, Turn, network listener, or Houdini process was started by this smoke test.
