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

With the server running, serve the browser files in a second terminal:

```bash
python3 -m http.server 8000 --directory client
```

Open `http://127.0.0.1:8000` in two browser tabs, then start the supplied source
in another terminal (with the virtual environment active):

```bash
python caption_source.py --segments 5
```

Issue #3 implements live display: each segment has one line, newer revisions
replace that line, and older/equal revisions are ignored. PROVISIONAL lines are
muted and italic; FINAL lines are darker and upright. Once final, a line cannot
be overwritten. Lines remain visible for the page session.

For a focused check, reload both tabs before starting the source. Watch each
segment change from provisional to final without adding duplicate lines. After
the source finishes, each tab should contain five FINAL lines and no browser
console errors. Recall, history merging, and automatic reconnect remain TODOs.

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
