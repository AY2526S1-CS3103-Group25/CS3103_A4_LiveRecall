# AI / Coding-Assistant Usage

## How AI was used

### Server protocol ingestion (issue #1)

- **Claude Code (Anthropic, Fable 5 model)** — interactive CLI agent used to plan and implement the server-side protocol work in `solution_server.py`: the `CaptionUpdate` ingestion/validation layer (issue #1 / PR #20) and the `SessionState` revision-deduplication gate (issue #2 / PR #22), plus the ad-hoc protocol probe scripts used during testing and the PR/issue write-ups. All generated code was reviewed by the team and verified against the public test harness before merging.
- **Claude Code automated PR review (GitHub Actions, `claude-sonnet-5-5`)** — the workflow in `.github/workflows/` reviews every PR and leaves inline comments; its findings were triaged by the team and either applied or argued against in the review threads.
- **Codex (OpenAI)** — used to review the assignment specification and PR #22, plan and implement bounded server history and resume (issue #6), and write controlled-clock and WebSocket regression tests.

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

Review by LeongWZ exposed a clock-skew assumption in the original HISTORY selection: server wall time was compared with source event time. Codex changed selection to monotonic receipt age and added a regression with past/future source clocks and a server wall-clock jump. All 14 history regressions passed. The oversized HISTORY finding was valid, but dropping oldest finals to fit a frame would sacrifice the required recovery window. The correction batches recovery into ordered messages of at most 1 MiB, retaining the complete selected snapshot. A single caption that fits alone but not inside a HISTORY wrapper uses CAPTION_UPDATE after an initial HISTORY reply. PROTOCOL_EXAMPLES.md records this extension; the 70-second history retention is unchanged. Decoded records are cached once per retained final and shared by snapshots, avoiding repeated parsing and per-client decoded copies.

**Verification:** all 21 server regressions and 11 current-main client regressions passed, and the startup check passed. A real WebSocket receiver with max_size=1 MiB recovered all 20,000 finals in order and then received a live final; tests also cover UTF-8 byte budgets, the single-caption wrapper boundary, multi-batch handoff and failed-send closure. An isolated probe of the actual #8 client at 7b32381 merged server-generated HISTORY batches and the standalone-caption fallback without duplicate rows and resumed from segment 9. The browser on main still defers HISTORY merging to #8. Failed broadcast sends now close their sockets so handlers do not remain open without delivery; bounded slow-client isolation remains #9.

## Responsibility statement

We understand that the team remains responsible for the submitted code, experimental results, and claims, and that every member may be asked to explain or modify relevant code during the viva.
