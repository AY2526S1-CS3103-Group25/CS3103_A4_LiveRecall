# Mandatory Deterministic Application Demo

No ASR or speech-recognition setup is required.

## Suggested setup

Terminal 1:

```bash
python solution_server.py --port 8765
```

Terminal 2 (serve the browser files):

```bash
python -m http.server 8000 --directory client
```

Open `http://127.0.0.1:8000` in at least two browser tabs/windows.

Terminal 3, use long/demo mode so the 60-second recall view is meaningful:

```bash
python caption_source.py --duration 120
```

## Demonstrate

- provisional versus final captions are visually distinguishable;
- newer revisions replace older revisions of the same segment;
- `What did I miss?` defaults to 30 seconds and supports 15/30/60 seconds using local state under normal operation;
- one client temporarily disconnects, reconnects automatically, and recovers recent missed finalized captions without duplicate finals;
- a newly opened late client receives recent context and then continues with live updates.

## Disconnect / recovery demo (cut_proxy.py)

Ctrl-C on the server is a poor disconnect: it also kills `caption_source.py` and
discards the history a reconnecting client would recover. `cut_proxy.py` breaks
only the browser clients' path; the server, its history, and the source survive.
It takes over the port the browser already dials, so `client/app.js` needs no change:

```bash
python solution_server.py --port 8799                  # real server
python cut_proxy.py --listen 8765 --target 8799        # the cuttable path
python -m http.server 8000 --directory client
python caption_source.py --uri ws://127.0.0.1:8799     # source bypasses the proxy
# browser -> http://127.0.0.1:8000  (dials ws://127.0.0.1:8765)
```

Commands typed at the proxy's terminal:

| Command  | Effect |
| -------- | ------ |
| `cut`    | close live connections; clients reconnect immediately |
| `down`   | close them and stop listening, so redials are refused as well |
| `up`     | listen again; clients reconnect and recover what they missed |
| `freeze` | keep sockets open but relay nothing — a silently dead path: the tab still says "connected" because no close arrives |
| `thaw`   | resume relaying |
| `status` | print mode and live connection count |
| `quit`   | exit |

Use `down`, wait, then `up` for an outage long enough that finalized captions
accumulate; `cut` alone only exercises the reconnect, not the recovery. Once
stdin reaches end of input (nohup, a closed pipe, CI) the proxy just relays
until it is killed.

## Where to tweak during the viva

| Likely request | Where |
| -------------- | ----- |
| History retention window | `SessionState.HISTORY_RETENTION_SECONDS` (solution_server.py) and `CAPTION_RETENTION_MS` (client/app.js) — the client keeps 70 s to exceed the required 60 s |
| Hello history default / clamp | `DEFAULT_HISTORY_SECONDS` / `MAX_HISTORY_SECONDS` (solution_server.py) and `history_seconds` in the hello (client/app.js) |
| Recall windows (15/30/60) | `data-seconds` buttons in client/index.html + the guard in `selectRecallWindow` (client/app.js) |
| Coalescing / overload policy | `ClientState._observe_lag` and the overflow branch in `offer` (solution_server.py) |
| Reconnect backoff | `RECONNECT_BASE_MS` / `RECONNECT_MAX_MS` and the close handler (client/app.js) |
| Checkpoint cadence / lag thresholds | the `ClientState` constants block: `CHECKPOINT_*`, `LAG_SECONDS`, `MAX_PENDING*` (solution_server.py) |
