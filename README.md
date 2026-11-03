# Team 25 - LiveRecall

## Team members

| Name | Student ID | Main responsibility |
|---|---|---|
| Leong Wei Zhi (@LeongWZ) | TODO(team) | Server: protocol ingestion and revision dedup, backpressure checkpoints, reconnect backoff |
| Yong Quan (@yongqqqq) | TODO(team) | Server: bounded history and resume, delivery isolation, overload handling |
| Kai Ming (@CloudKai) | TODO(team) | Browser client: live display, validation, local recall |
| Zhan Wen (@panomia01) | TODO(team) | Experiments: public tests T1-T5, Ubuntu evidence, before/after comparison |

## Ubuntu setup

Ubuntu is required for the reproducible performance tests because T5 uses Linux
`tc/netem`. From a fresh Ubuntu environment (the public results below used
Ubuntu 24.04.1 LTS, kernel 6.8.0-41, Python 3.12.3, websockets 17.4):

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip iproute2
```

Verify the tools:

```bash
python3 --version
tc -V
```

On macOS or Windows hosts, an Ubuntu VM via
[Multipass](https://canonical.com/multipass/install) works:

```bash
multipass launch --name liverecall --cpus 2 --memory 4G --disk 15G
multipass shell liverecall
# then run the apt commands above inside the VM, and `multipass stop liverecall` when done
```

Run the server and the simulated clients inside the **same** Ubuntu
environment for the public performance experiments; the supplied impairment
profiles apply to `lo`. See `UBUNTU_SETUP.md` for details. `ffmpeg` and ASR
packages are **not** mandatory dependencies.

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
records also expire after about 70 seconds. On reconnect the client resumes by
sending its highest contiguous finalized segment and revision in `CLIENT_HELLO`,
and recovered `HISTORY` items merge through this same accept path, so the guard
above also deduplicates recovery bursts.

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
cleanup timer, including while the source is idle. When the visible finalized
entries are unchanged, their DOM rows are preserved so idle cleanup ticks and
equivalent window selections do not clear text selection. Headings and empty
messages still reflect the selected window. Recall does not force
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

Automatic reconnect (jittered exponential backoff), finalized resume positions
(the contiguous-final cursor sent in `CLIENT_HELLO`), and merging recovered
`HISTORY` items are all implemented in `client/app.js`; recall selection itself
never queries the server.

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

All five public tests were run on Ubuntu 24.04.1 LTS (kernel 6.8.0-41, Python
3.12.3, websockets 17.4) at commit `86b2bb1`, with **three independent runs
each** (`--runs 3` restarts the server per run); every reported value is the
median of the three runs. Exact commands, from the repo root with the venv
active:

```bash
mkdir -p results/ubuntu

# T1 - basic correctness
python run_demo.py --clients 20 --duration 20 --runs 3 --output-stem results/ubuntu/t1

# T2 - public scale
python run_demo.py --clients 100 --duration 40 --runs 3 --output-stem results/ubuntu/t2

# T3 - slow readers
python run_demo.py --clients 100 --duration 60 --runs 3 --slow-percent 10 --slow-ms 500 --output-stem results/ubuntu/t3

# T4 - reconnect
python run_demo.py --clients 100 --duration 60 --runs 3 --disconnect-percent 20 --disconnect-at 15 --disconnect-seconds 5 --output-stem results/ubuntu/t4
```

T5 applies the supplied impairment profile to loopback (`delay 120ms 30ms
distribution normal loss 3%`) and always resets it afterwards:

```bash
(
  set -e
  trap 'sudo bash ./network_profiles/reset.sh lo' EXIT
  sudo bash ./network_profiles/delay_loss.sh lo
  python run_demo.py --clients 100 --duration 40 --runs 3 --output-stem results/ubuntu/t5
)
```

Median results (Caption Age in milliseconds; full per-run data, metric
definitions and limits in [docs/PUBLIC_TESTS_GUIDE.md](docs/PUBLIC_TESTS_GUIDE.md),
committed verification summaries under `docs/evidence/`):

| Test | P50 | P95 | P99 | Fresh updates | Fresh finals | Final completeness | Live coverage |
|---|---:|---:|---:|---:|---:|---:|---:|
| T1 | 6.20 | 13.03 | 23.03 | 100.00% | 100.00% | 100.00% | 100.00% |
| T2 | 13.23 | 23.55 | 27.33 | 100.00% | 100.00% | 100.00% | 100.00% |
| T3 | 15.33 | 30.46 | 163.70 | 99.79% | 99.57% | 100.00% | 93.90% |
| T4 | 9.03 | 22.41 | 26.92 | 100.00% | 98.42% | 100.00% | 98.33% |
| T5 | 311.53 | 800.02 | 1102.49 | 78.61% | 76.16% | 100.00% | 89.00% |

All 15 runs recorded zero duplicate/stale deliveries and zero connection
errors. Two per-run exceptions stay visible even where the median is 100%:
T1 run 01 delivered 94.70% of provisional revisions (all finals arrived), and
in T5 run 03 one client lacked one final segment at collection end
(4899/4900 expected client-final deliveries). The guide discusses both.

## Mandatory deterministic application demo

No ASR setup is required; captions come from the deterministic
`caption_source.py`. The full walkthrough is in `DEMO_GUIDE.md`; summary:

1. Terminal 1: `python solution_server.py --port 8765`
2. Terminal 2: `python3 -m http.server 8000 --directory client`, then open
   **http://127.0.0.1:8000/** in at least two tabs.
3. Terminal 3: `python caption_source.py --duration 120` (long mode, so the
   60-second recall window is meaningful).

Demonstrate: provisional vs final styling, newer revisions replacing the same
row, the 15/30/60-second local recall windows, a late-joining tab receiving
recent context via `HISTORY` and then continuing live, and disconnect/recovery.

For disconnect/recovery, use `cut_proxy.py` so the server, its history, and the
source survive while only the browsers' path breaks:

```bash
python solution_server.py --port 8799                  # real server
python cut_proxy.py --listen 8765 --target 8799        # the cuttable path
python3 -m http.server 8000 --directory client
python caption_source.py --uri ws://127.0.0.1:8799     # source bypasses the proxy
```

Type `down` at the proxy, wait for finals to accumulate, then `up`: the tabs
reconnect with jittered exponential backoff and recover the missed finals
without duplicates. (`cut` alone only exercises reconnect, not recovery.)

## Design notes

The local-recall timing and retention decision is documented in the issue #7
section above (client receipt time, measured with the monotonic
`performance.now()` clock).

**Revision/deduplication state.** The server keeps one `(revision, final)`
watermark per segment (`SessionState` in `solution_server.py`), dropping
stale, duplicate and post-final updates with per-reason counters. Identity
fields (`segment`, `revision`, `final`) are validated strictly, while
recoverable fields (`source_ts`, `text`) are defaulted rather than dropped —
a dropped final is unrecoverable. The browser mirrors this with its own
revision guard, so recovery bursts deduplicate through the same accept path.

**Bounded server history.** Recent finalized captions are retained for
`HISTORY_RETENTION_SECONDS = 70` (the required 60-second window plus margin)
in a receipt-time-ordered deque: O(1) expiry, immune to source clock skew and
out-of-order finals. Tracked segment metadata is capped
(`MAX_TRACKED_SEGMENTS = 1024`) so a never-finalizing source cannot grow
state forever.

**Reconnect/late-join recovery.** `CLIENT_HELLO` carries the client's highest
contiguous finalized segment/revision; the server replies with ordered
`HISTORY` batches of at most 1 MiB each (a caption too large for its wrapper
falls back to a plain `CAPTION_UPDATE`), then live updates. The browser's
resume cursor only advances over a contiguous run of finals, so a recovery
batch starting above a gap cannot skip a missed final. Reconnects use
jittered exponential backoff (1 s doubling to a 10 s cap, reset only after a
connection survives longer than the cap).

**Per-client buffering/backpressure.** Each client has one bounded queue
(`MAX_PENDING = 128` messages / 256 KiB) drained by a sole sender task, so a
blocked client can never stall ingestion or other clients. Because a
successful `send()` is not proof of application consumption, the sender
issues ping/pong delivery checkpoints (every 1 s / 8 messages / 16 KiB, with
a 10 s timeout and a bounded in-flight window) to detect receiver congestion
hiding below the transport buffers.

**Overload behavior and trade-offs.** Under observed lag the server coalesces
semantically: obsolete provisional revisions are dropped, while finals and
the newest active revision are always preserved, and a probation window
restores full-update delivery once the client sustains healthy checkpoints.
Measured on Ubuntu (controlled before/after comparison, T3 workload): final
completeness 95.13% -> 100.00% and slow-client P95 28,109.67 ms -> 594.17 ms,
at the cost of +13.50 ms normal-client P95 — more useful final state for slow
readers in exchange for protocol/framing overhead. Details in
`docs/PUBLIC_TESTS_GUIDE.md` (controlled comparison) and
`docs/safe_cleanup_audit.md`.

## Optional ASR bonus

Not attempted. The mandatory path has no ASR dependencies; `bonus_asr/`
contains guidance only, and model weights are gitignored.
