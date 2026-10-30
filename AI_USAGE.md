# AI / Coding-Assistant Usage

## How AI was used

### Server protocol ingestion (issue #1)

- **Claude Code (Anthropic, Fable 5 model)** — interactive CLI agent used to plan and implement the server-side protocol work in `solution_server.py`: the `CaptionUpdate` ingestion/validation layer (issue #1 / PR #20) and the `SessionState` revision-deduplication gate (issue #2 / PR #22), plus the ad-hoc protocol probe scripts used during testing and the PR/issue write-ups. All generated code was reviewed by the team and verified against the public test harness before merging.
- **Claude Code automated PR review (GitHub Actions, `claude-sonnet-5-5`)** — the workflow in `.github/workflows/` reviews every PR and leaves inline comments; its findings were triaged by the team and either applied or argued against in the review threads.
- **Codex (OpenAI)** — used to review the assignment specification and PR #22, plan and implement bounded server history and resume (issue #6), bounded client delivery and semantic overload handling (issues #9/#10), and write controlled-clock and WebSocket regression tests.

### Live caption display (issue #3)

AI was used as an implementation and review aid to discuss design options,
draft parts of the caption display logic, and identify edge cases. Proposed
changes were evaluated against the assignment's revision rules, bounded-state
requirements, and need for simple, understandable code.

## Useful suggestions

### Preserving server updates with recoverable field errors

The automated reviewer flagged that our first ingestion implementation dropped any `CAPTION_UPDATE` failing strict validation — including a `final=true` update with a missing or non-numeric `source_ts`, and later the same problem for a missing or non-string `text`. Since a dropped final can never be recovered (the protocol has no retransmit, and Final Caption Completeness counts unique finals delivered), one malformed field would have permanently cost score in the graded tests.

We split the fields by what a wrong guess costs. An invalid `source_ts` is replaced with the server's receive time (commit `9ed84a2`) and an invalid `text` with an empty string (commit `3430a12`), in both cases patching the value into the forwarded payload so clients still parse it. The identity fields `segment`, `revision` and `final` stay strict, because a guessed value there would place the caption at the wrong position in the stream or deduplicate it against the wrong segment — corrupting session state, which is worse than dropping one frame. The assignment does not require these replacements: they keep an update from being dropped, but they cannot restore its original timestamp or its missing text.

**Verification:** a probe script sent updates with missing and `null` `source_ts`, a final with `text: 42`, plus a battery of genuinely malformed frames; the client received the defaulted updates (extra fields intact, the untouched field unchanged) while malformed frames were still dropped, and `run_demo.py --clients 20 --duration 20` reported 100% final-caption completeness with 0 duplicates/stale messages.

Codex suggested keeping recent finalized payloads separate from the revision-state watermark. This preserves recoverable text when revision metadata is compacted. The server retains 70 seconds of finals using monotonic receipt time for both storage expiry and requested context; original `source_ts` values remain unmodified for caption-age measurements. We avoided a fixed count cap that could discard required finals inside the 60-second recovery window.

**Verification:** all 13 history regressions and the startup check passed on macOS. Three independent runs each of T1 and T4 achieved 100% final completeness with zero duplicate/stale messages; each T4 run recovered 120 missed finals. Raw CSV/JSON outputs remain locally under `results/issue6/`. Ubuntu performance validation remains outstanding.

Codex suggested sharing the existing finalized history between overloaded clients rather than retaining a separate unbounded final backlog for each one. A receipt-order final cursor advances only after successful sends, so catch-up also handles finals accepted out of segment order. Queue limits are 128 messages and 256 KiB; frames larger than the byte budget use the single-frame catch-up path. Coalescing starts on 100 ms of pending/send lag or 75% queue occupancy, and full revision delivery resumes below the lower queue/50 ms thresholds. These are explicit starting limits, not assumptions about hidden workloads. The initial HISTORY snapshot and one in-flight frame are additional bounded storage; TCP/WebSocket buffers are separate from the application queue.

**Verification:** 27 regressions passed, including blocked HISTORY/live sends, final-only overflow, byte limits, oversized UTF-8 frames, recovery boundaries, expiry, stream reset and bursty input. The burst regression preserves all 100 finals without duplicates or revision regression while respecting queue limits. If a receiver falls behind the shared recovery window, the server closes it with an explicit reason rather than silently skipping expired finals. This fallback may reduce completeness; it does not promise unlimited recovery.

**Public measurements:** three independent runs each of T1–T4 on macOS arm64, Python 3.11.9 and websockets 16.1.1 had zero duplicate/stale messages and zero connection errors. Median final completeness was 100% for T1/T2/T4 and 95.13% for T3. Median P95 Caption Age was respectively 6.19/16.71/2253.69/16.15 ms. T3's normal clients retained 100% freshness and a 16.70 ms median P95; its slow clients account for the poor aggregate result. Raw outputs and an aggregate validation summary are local ignored artifacts under `results/issue9-10/`. The startup check passed. Ubuntu/netem validation remains outstanding; these results do not establish full hidden-performance correctness.

### Replacing caption revisions in one row

Keeping one caption row per segment was a useful suggestion because it allows
newer revisions to replace existing text without duplicate lines. The approach
was adopted with explicit checks for older revisions and finalized captions.
Browser checks verified these behaviors.

## Suggestions that required modification

### Server session validation

The AI's initial implementation added a per-update session check: any `CAPTION_UPDATE` whose `session` field differed from the `SOURCE_HELLO` session was dropped:

```python
elif isinstance(msg_session, str) and msg_session != session:
    reason = f"session {msg_session!r} does not match {session!r}"
```

**Problem:** the spec (Section 4.1) lists `session` among the `CAPTION_UPDATE` fields, but assigns it no meaning beyond identifying the lecture in `SOURCE_HELLO`. So the check was neither required nor forbidden by the assignment — removing it was our risk judgement, not the correction of a rules violation. The objection is that the comparison conveys no information while carrying a total failure mode: one source connection already *is* one session, so a matching `session` confirms nothing the connection had not already established, but if the hidden test source ever tagged its updates with a value differing from its hello, every caption — finals included — would be silently dropped and Final Caption Completeness would go to 0.

**Correction:** removed the check entirely (commit `0f19fa5`); the session is identified by the connection's `SOURCE_HELLO`, and the update's own `session` field is now read by nothing and forwarded unmodified in the raw payload. Verified by probe (an update tagged `session: "L2"` after an `"L1"` hello is now forwarded) and a clean T1 run.

Codex initially recommended requiring a contiguous finalized cursor rather than the latest finalized state held by the client.

**Problem:** this overstated the protocol requirement. Section 4.2 specifies the latest finalized segment/revision, and the starter source completes each segment before beginning the next. Requiring a different cursor would expand scope without establishing a need for it.

**Correction:** kept the specified cursor semantics and filtered HISTORY by segment/revision. Out-of-order gaps cannot be inferred from that cursor alone and would require a separate protocol decision. Tests cover same-segment revision boundaries, late join, and incremental reconnect.
### Bounded client state and revision protection

The initial AI-assisted design kept every caption for the page session. Review
identified that this would allow memory usage to grow throughout a lecture.
The design was revised to remove finalized captions and their rows after about
70 seconds, preserving the required recent window while limiting growth.

An initial exception to the revision check was rejected because it could accept
older finals. A refined condition was adopted: equal-revision finals may complete
provisional captions, while older revisions remain rejected. Review also showed
that blocking all IDs below the highest pruned segment could reject unseen
captions. This was replaced with expiring records of only the IDs actually pruned.

Further review identified abandoned provisional captions as another source of
growth. Cleanup now removes older, superseded provisionals after 70 seconds
without an accepted update, while preserving the newest active caption and
allowing late finals. Restored rows are inserted by segment number to preserve
display order. Reconnect recovery remains separate work.

## Issue #3 — evaluating caption reliability and usability

During review with AI, I found that an abandoned provisional caption could look
current indefinitely. The suggestion was to delete it, but I questioned whether
that would remove useful context. I chose to preserve the newest unfinished text
and mark it "PROVISIONAL — STALE" after 70 seconds without an accepted update.
Rejected revisions do not reset the timer; an accepted newer revision or final
clears the label. The existing five-second cleanup timer detects staleness at
about 70–75 seconds. Finals and older provisionals still expire after roughly
70 seconds, with revision/finality guards retained for one extra 70-second
window, so duplicate protection remains temporary.

I also evaluated AI suggestions for internal scrolling, input validation, and
empty-state messages. I adopted them while keeping recall controls outside the
scrolling pane, validating updates before state changes, allowing unknown fields,
and placing the empty status outside the caption rows to preserve their order.
Six controlled-clock regressions and desktop/mobile Chromium checks with mocked
WebSocket messages passed, and the UI detector reported no findings. The real
server demo and submission startup check remained unverified because the sandbox
blocked port binding.

## Additional reflection

Codex helped me explore the local recall design and check edge cases. I chose to reuse locally stored finalized captions and measure recall from client receipt time, so switching windows needs no server request and delayed captions get a full local window. I also questioned rebuilding unchanged content because it could interrupt text selection, and changed the renderer to preserve those rows.

Review by LeongWZ exposed a clock-skew assumption in the original HISTORY selection: server wall time was compared with source event time. Codex changed selection to monotonic receipt age and added a regression with past/future source clocks and a server wall-clock jump. All 14 history regressions passed. The oversized HISTORY finding was valid, but dropping oldest finals to fit a frame would sacrifice the required recovery window. The correction batches recovery into ordered messages of at most 1 MiB, retaining the complete selected snapshot. A single caption that fits alone but not inside a HISTORY wrapper uses CAPTION_UPDATE after an initial HISTORY reply. PROTOCOL_EXAMPLES.md records this extension; the 70-second history retention is unchanged. Decoded records are cached once per retained final and shared by snapshots, avoiding repeated parsing and per-client decoded copies.

**Verification:** all 21 server regressions and 11 current-main client regressions passed, and the startup check passed. A real WebSocket receiver with max_size=1 MiB recovered all 20,000 finals in order and then received a live final; tests also cover UTF-8 byte budgets, the single-caption wrapper boundary, multi-batch handoff and failed-send closure. An isolated probe of the actual #8 client at 7b32381 merged server-generated HISTORY batches and the standalone-caption fallback without duplicate rows and resumed from segment 9. The browser on main still defers HISTORY merging to #8. Failed broadcast sends now close their sockets so handlers do not remain open without delivery; bounded slow-client isolation remains #9.

Three independent T4 runs on the completed #6 branch each recovered 120 missed finals with 100% final completeness and zero duplicate/stale messages or connection errors. Median P95 caption age was 16.48 ms and HISTORY response P95 was 3.43 ms on macOS. Raw results remain ignored under `results/issue6-closure/`; Ubuntu measurements are still outstanding.

Codex's first history implementation held a per-client send lock while the client was registered in the shared broadcaster.

**Problem:** review of PR #26 correctly identified that HISTORY ordering was preserved but a blocked HISTORY send stalled the broadcaster's gather and therefore subsequent source ingestion. Passing T4 alone did not establish slow-client isolation.

**Correction:** replaced the shared gather with synchronous queue offers and one sender per client. The same sender owns HISTORY and live delivery. A regression holds one HISTORY send indefinitely and proves a normal client receives subsequent finals; another holds an in-flight live final and proves overflow catch-up neither duplicates it nor stalls normal clients. Cancellation is cleanup after closing the connection, rather than the mechanism for interrupting a blocked send.

Public T3 also challenged the assumption that a slow application necessarily produces observable server send backpressure.

**Problem:** a separate local WebSocket diagnostic generated 22 finals while a client read one message every 500 ms. At source completion the client application had consumed 11 finals, but the server had zero pending messages/bytes, zero transport write-buffer bytes, no blocked send and no coalescing/catch-up transitions. The remaining data was buffered below the server application queue; successful `send()` is not proof of application consumption.

**Evaluation:** bounded queues and blocked-send isolation are verified, but the implementation does not fully solve T3's slow-consumer completeness result. We did not silently drop normal provisional updates or modify the instructor harness to improve that number. Receiver-progress feedback or a separately measured transport policy would be needed to address this blind spot; the current measurements must be reported as a limitation, not a successful completeness result.

Following LeongWZ’s delivery review, Codex consolidated coalescing into one pass, exposed a small sender-facing history API, and preserved the first failure reason. Expiry tests now advance receipt clocks rather than mutating shared history internals. All 29 server regressions and the six current-main client tests passed; the development startup check passed, while the final-file check still reports missing `report.pdf`. The repository-root regression command is recorded in AGENTS.md.

Stream-reset disconnects remain deliberate: adopting a new stream in place cannot reset an unmodified client’s revision map, so keeping its socket open does not establish correct new-lecture display. Oversized updates still trigger shared catch-up to preserve the hard private-queue byte bound; treating them as already in flight from synchronous `offer` would require another staging slot or bypassing the sole sender. T3’s read budget suggests that earlier selective coalescing may help, but its trigger and timing must be measured on Ubuntu before claiming a solution.

After integrating main and these review corrections, three new T4 runs each recovered 120 finals with 100% final completeness and zero duplicate/stale messages or connection errors. Median P95 caption age was 16.44 ms and HISTORY response P95 was 3.72 ms on macOS; raw outputs remain ignored under `results/review-update/`. Three fresh T1 runs also achieved 100% final completeness, freshness and revision coverage with zero duplicate/stale/error counts; median P95 caption age was 6.22 ms. These runs validate normal delivery and reconnect behavior after the receipt-clock change, not the later large-HISTORY batching change or T3 trigger.

The final delivery integration sends all recovery batches through the same per-client sender before live updates, releases the snapshot after sending, and keeps ingestion free of client-I/O awaits. All 37 server regressions passed. A new test blocks the second HISTORY batch, floods live finals past the private queue limit, proves a normal client continues receiving, then verifies the slow client receives finals 1–40 once in order through shared catch-up. T3 receiver-progress detection remains tracked separately under #10.

Three independent T4 runs on the final integrated delivery branch also achieved 100% final completeness, 120 recovered items per run and zero duplicate/stale/error counts. Median P95 caption age was 16.25 ms and recovery P95 was 3.27 ms on macOS. Raw results remain ignored under `results/issue9-closure/`. These validate #9’s isolation/recovery integration; #10 remains open for the T3 detection limitation.

## Responsibility statement

We understand that the team remains responsible for the submitted code, experimental results, and claims, and that every member may be asked to explain or modify relevant code during the viva.
