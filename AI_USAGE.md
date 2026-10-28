# AI / Coding-Assistant Usage

## Tools used

- **Claude Code (Anthropic, Fable 5 model)** — interactive CLI agent used to plan and implement the server-side protocol work in `solution_server.py`: the `CaptionUpdate` ingestion/validation layer (issue #1 / PR #20) and the `SessionState` revision-deduplication gate (issue #2 / PR #22), plus the ad-hoc protocol probe scripts used during testing and the PR/issue write-ups. All generated code was reviewed by the team and verified against the public test harness before merging.
- **Claude Code automated PR review (GitHub Actions, `claude-sonnet-5-5`)** — the workflow in `.github/workflows/` reviews every PR and leaves inline comments; its findings were triaged by the team and either applied or argued against in the review threads.
- **Codex (OpenAI)** — used to review the assignment specification and PR #22, plan and implement bounded server history and resume (issue #6), bounded client delivery and semantic overload handling (issues #9/#10), and write controlled-clock and WebSocket regression tests.

(To be extended by teammates as further issues are worked on.)

## One useful suggestion

The automated reviewer flagged that our first ingestion implementation dropped any `CAPTION_UPDATE` failing strict validation — including a `final=true` update with a missing or non-numeric `source_ts`, and later the same problem for a missing or non-string `text`. Since a dropped final can never be recovered (the protocol has no retransmit, and Final Caption Completeness counts unique finals delivered), one malformed field would have permanently cost score in the graded tests.

We split the fields by what a wrong guess costs. An invalid `source_ts` is replaced with the server's receive time (commit `9ed84a2`) and an invalid `text` with an empty string (commit `3430a12`), in both cases patching the value into the forwarded payload so clients still parse it. The identity fields `segment`, `revision` and `final` stay strict, because a guessed value there would place the caption at the wrong position in the stream or deduplicate it against the wrong segment — corrupting session state, which is worse than dropping one frame. The assignment does not require these replacements: they keep an update from being dropped, but they cannot restore its original timestamp or its missing text.

**Verification:** a probe script sent updates with missing and `null` `source_ts`, a final with `text: 42`, plus a battery of genuinely malformed frames; the client received the defaulted updates (extra fields intact, the untouched field unchanged) while malformed frames were still dropped, and `run_demo.py --clients 20 --duration 20` reported 100% final-caption completeness with 0 duplicates/stale messages.

Codex suggested keeping recent finalized payloads separate from the revision-state watermark. This preserves recoverable text when revision metadata is compacted. The server retains 70 seconds of finals using monotonic receipt time for storage expiry and original `source_ts` for requested context. We avoided a fixed count cap that could discard required finals inside the 60-second recovery window.

**Verification:** all 13 history regressions and the startup check passed on macOS. Three independent runs each of T1 and T4 achieved 100% final completeness with zero duplicate/stale messages; each T4 run recovered 120 missed finals. Raw CSV/JSON outputs remain locally under `results/issue6/`. Ubuntu performance validation remains outstanding.

Codex suggested sharing the existing finalized history between overloaded clients rather than retaining a separate unbounded final backlog for each one. A receipt-order final cursor advances only after successful sends, so catch-up also handles finals accepted out of segment order. Queue limits are 128 messages and 256 KiB; frames larger than the byte budget use the single-frame catch-up path. Coalescing starts on 100 ms of pending/send lag or 75% queue occupancy, and full revision delivery resumes below the lower queue/50 ms thresholds. These are explicit starting limits, not assumptions about hidden workloads. The initial HISTORY snapshot and one in-flight frame are additional bounded storage; TCP/WebSocket buffers are separate from the application queue.

**Verification:** 27 regressions passed, including blocked HISTORY/live sends, final-only overflow, byte limits, oversized UTF-8 frames, recovery boundaries, expiry, stream reset and bursty input. The burst regression preserves all 100 finals without duplicates or revision regression while respecting queue limits. If a receiver falls behind the shared recovery window, the server closes it with an explicit reason rather than silently skipping expired finals. This fallback may reduce completeness; it does not promise unlimited recovery.

**Public measurements:** three independent runs each of T1–T4 on macOS arm64, Python 3.11.9 and websockets 16.1.1 had zero duplicate/stale messages and zero connection errors. Median final completeness was 100% for T1/T2/T4 and 95.13% for T3. Median P95 Caption Age was respectively 6.19/16.71/2253.69/16.15 ms. T3's normal clients retained 100% freshness and a 16.70 ms median P95; its slow clients account for the poor aggregate result. Raw outputs and an aggregate validation summary are local ignored artifacts under `results/issue9-10/`. The startup check passed. Ubuntu/netem validation remains outstanding; these results do not establish full hidden-performance correctness.

## One suggestion that was wrong, inefficient, or required modification

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

Codex's first history implementation held a per-client send lock while the client was registered in the shared broadcaster.

**Problem:** review of PR #26 correctly identified that HISTORY ordering was preserved but a blocked HISTORY send stalled the broadcaster's gather and therefore subsequent source ingestion. Passing T4 alone did not establish slow-client isolation.

**Correction:** replaced the shared gather with synchronous queue offers and one sender per client. The same sender owns HISTORY and live delivery. A regression holds one HISTORY send indefinitely and proves a normal client receives subsequent finals; another holds an in-flight live final and proves overflow catch-up neither duplicates it nor stalls normal clients. Cancellation is cleanup after closing the connection, rather than the mechanism for interrupting a blocked send.

Public T3 also challenged the assumption that a slow application necessarily produces observable server send backpressure.

**Problem:** a separate local WebSocket diagnostic generated 22 finals while a client read one message every 500 ms. At source completion the client application had consumed 11 finals, but the server had zero pending messages/bytes, zero transport write-buffer bytes, no blocked send and no coalescing/catch-up transitions. The remaining data was buffered below the server application queue; successful `send()` is not proof of application consumption.

**Evaluation:** bounded queues and blocked-send isolation are verified, but the implementation does not fully solve T3's slow-consumer completeness result. We did not silently drop normal provisional updates or modify the instructor harness to improve that number. Receiver-progress feedback or a separately measured transport policy would be needed to address this blind spot; the current measurements must be reported as a limitation, not a successful completeness result.

## Responsibility statement

We understand that the team remains responsible for the submitted code, experimental results, and claims, and that every member may be asked to explain or modify relevant code during the viva.
