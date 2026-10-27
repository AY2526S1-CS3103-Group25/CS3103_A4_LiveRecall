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
4. Both tabs should finish with exactly **five FINAL lines** and no browser
   console errors.

The display ignores older revisions and repeated provisional updates. A final
update at the same revision can complete a provisional caption; accepted final
captions remain protected from later updates. Finalized captions remain visible
for about 70 seconds, then their rows and stored state are removed. Older
provisional captions are also removed if they receive no accepted updates for
70 seconds and the stream has moved to a later segment. The newest active
caption is preserved, and a late final or newer revision can restore a removed
provisional caption. Only the revision and final status of removed captions are
temporarily remembered to reject stale updates; these records also expire after
about 70 seconds. This guard does not implement reconnect recovery or history
merging; the reconnect follow-up must send the actual latest finalized segment
and revision in `CLIENT_HELLO`.

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

Briefly describe:

- revision/deduplication state;
- bounded server history;
- reconnect/late-join recovery;
- per-client buffering/backpressure policy;
- overload behavior and trade-offs.

## Optional ASR bonus

Only if attempted. Keep these dependencies separate from the mandatory server path.
