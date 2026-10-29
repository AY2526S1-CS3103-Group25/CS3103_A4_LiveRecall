# Team XX - LiveRecall

## Team members

- Name / student ID / responsibility

## Ubuntu setup

Exact commands from a fresh Ubuntu environment.

## Install

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Required server

```bash
python solution_server.py --port 8765
```

## Browser client

Complete the installation steps above first, then open three terminals. In each
command block, replace `/path/to/CS3103_A4_LiveRecall` with the location of your
project folder. The commands use the `venv` environment created during installation.

### Terminal 1 - start the caption server

Enter the project folder, activate the virtual environment, then start the server:

```bash
cd "/path/to/CS3103_A4_LiveRecall"
source venv/bin/activate
python solution_server.py --port 8765
```

Leave this terminal running. It receives caption updates and sends them to
connected browser clients.

### Terminal 2 - serve the browser files

```bash
cd "/path/to/CS3103_A4_LiveRecall"
python3 -m http.server 8000 --directory client
```

Leave this terminal running too. Open **http://127.0.0.1:8000/** in two browser
tabs. Both should show **Connection: connected** before starting the source.

### Terminal 3 - send five caption segments

```bash
cd "/path/to/CS3103_A4_LiveRecall"
source venv/bin/activate
python caption_source.py --segments 5
```

The source sends five segments, then exits.

Optional: add `--revision-delay 1 --segment-gap 1` to slow the updates so each
revision is easier to see.

```text
Caption source -> caption server -> browser display
```

### Check the display

1. Each segment appears as **PROVISIONAL**, using muted italic text.
2. Newer revisions replace the text on the same line without adding duplicates.
3. The completed segment becomes **FINAL**, using darker normal text.
4. Immediately after the source finishes, both tabs should show exactly
   **five FINAL lines** and no browser console errors. Check before the live
   window expires.

The live pane scrolls internally (up to `min(24rem, 50vh)`, with its existing
6rem minimum height), keeping the newest accepted update visible while recall
buttons remain outside the pane. It is keyboard-focusable and labeled
**Live captions**; long caption text wraps. Initially, a separate status reads
**Waiting for captions.** Once accepted captions have all expired, it reads
**No recent captions. Older captions have left the live window.** The status
is hidden whenever caption rows exist.

Malformed JSON and non-object messages are ignored. Caption updates require
positive safe integers for `segment` and `revision` and a boolean `final`;
invalid updates are ignored before changing state. Unknown fields are ignored,
and caption text is rendered as plain text. Non-string text is normalized to an
empty string so malformed content cannot interrupt rendering. If browser session
storage is unavailable, the client keeps an in-memory numeric identifier and can
still send its connection hello. Invalid stored identifiers are replaced.

Caption rows stay in segment order, including captions restored by late updates.
The display ignores older revisions and repeated provisional updates. A final
update at the same revision can complete a provisional caption; accepted final
captions remain protected from later updates. Finalized captions remain visible
for about 70 seconds, then their rows and stored state are removed. Older
provisional captions are also removed if they receive no accepted updates for
70 seconds and the stream has moved to a later segment. The newest active
caption's text is preserved indefinitely. After 70 seconds without an accepted
update, it shows **PROVISIONAL — STALE** with an explanation that no recent
updates have arrived. The existing five-second cleanup timer detects this at
about 70–75 seconds. An accepted newer revision or final clears the stale label;
rejected updates do not refresh the timer. A late final or newer revision can
restore a removed provisional caption. Only the revision and final status of
removed captions are temporarily remembered to reject stale updates; these
records also expire after about 70 seconds. This guard does not implement
reconnect recovery or history
merging; the reconnect follow-up must send the actual latest finalized segment
and revision in `CLIENT_HELLO`.

### What did I miss? — local recall (issue #7)

The recall pane defaults to **Previous 30 seconds**. Use **15 s**, **60 s**, or
**What did I miss? (30 s)** to select a window; the main action always returns
to 30 seconds. Only accepted finalized captions appear, ordered by segment.
Provisional text stays in the live pane. The selected button has a visible
pressed state, and the recall pane supports keyboard scrolling and long text.

**Timing decision:** recall uses **client receipt time**, measured with the
monotonic `performance.now()` clock when a final caption is first accepted.
The user explicitly selected this basis for issue #7. `source_ts` is not used
to filter recall, so a delayed final receives its full local window. A
duplicate, stale revision, or conflicting update cannot reset that time.

The existing local caption state retains finals for 70 seconds, exceeding the
required 60-second history. Selecting any window filters this state at the
time of the click, without sending a WebSocket or HTTP request. The selected
view also refreshes when a final is accepted and on the existing five-second
cleanup timer, including while the source is idle. Recall does not force
scrolling to the newest row. During disconnection, previously received finals
remain available until their local windows expire. Reloading clears this
in-memory history.

To check the feature with the server and browser already running, use a longer
stream in Terminal 3:

```bash
python caption_source.py --duration 120
```

1. After more than 60 seconds, switch between 15, 30, and 60 seconds. The larger
   windows should include earlier finals, with no provisional or duplicate lines.
2. Select 60 seconds, then **What did I miss? (30 s)**; the heading and selected
   button should return to 30 seconds.
3. Stop the caption server with **Ctrl+C**. The locally received history should
   still be selectable while the connection status reads **disconnected**.
4. Leave the page idle. Recall should empty after its selected window expires,
   detected on the next cleanup tick; retained final state expires after about
   70–75 seconds.

Automatic reconnect, finalized resume positions, and merging recovered `HISTORY`
items remain separate follow-up work. The existing connection hello requests
initial recovery context, but recall selection itself never queries the server.

To repeat the test, reload both tabs, wait for **connected**, then run the
Terminal 3 command again. Reloading clears the previous captions. Press
**Ctrl+C** in Terminals 1 and 2 when finished.

### Browser troubleshooting

- **404: File not found:** stop Terminal 2 with **Ctrl+C**, check that it is
  inside `CS3103_A4_LiveRecall`, then rerun its command and refresh Chrome.
- **Not connected:** check that Terminal 1 is running on port 8765, then reload
  the browser tabs.
- **No captions:** open the tabs before starting the source. Reload both tabs,
  wait for **connected**, then rerun Terminal 3.

## Public deterministic tests

Exact commands and configuration used for T1-T5, including independent runs.

## Mandatory deterministic application demo

Exact commands/steps for `caption_source.py`, browser clients, revision replacement, recall, late join, and reconnect/recovery.

## Design notes

The local-recall timing and retention decision is documented in the issue #7
section above; include the client-receipt-time choice in the corresponding PR.

Briefly describe:

- revision/deduplication state;
- bounded server history;
- reconnect/late-join recovery;
- per-client buffering/backpressure policy;
- overload behavior and trade-offs.

## Optional ASR bonus

Only if attempted. Keep these dependencies separate from the mandatory server path.
