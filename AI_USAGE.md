# AI / Coding-Assistant Usage

## Tools used

- **Claude Code (Anthropic, Fable 5 model)** — interactive CLI agent used to plan and implement the server-side protocol ingestion (issue #1 / PR #20): the `CaptionUpdate` validation layer in `solution_server.py`, ad-hoc protocol probe scripts used during testing, and the PR/issue write-ups. All generated code was reviewed by the team and verified against the public test harness before merging.
- **Claude Code automated PR review (GitHub Actions, `claude-sonnet-5-5`)** — the workflow in `.github/workflows/` reviews every PR and leaves inline comments; its findings on PR #20 were triaged by the team and either applied or argued against in the review threads.

(To be extended by teammates as further issues are worked on.)

## One useful suggestion

The automated reviewer flagged that our first ingestion implementation dropped any `CAPTION_UPDATE` failing strict validation — including a `final=true` update with a missing or non-numeric `source_ts`, and later the same problem for a missing or non-string `text`. Since a dropped final can never be recovered (the protocol has no retransmit, and Final Caption Completeness counts unique finals delivered), one malformed field would have permanently cost score in the graded tests.

We split the fields by what a wrong guess costs. An invalid `source_ts` is replaced with the server's receive time (commit `9ed84a2`) and an invalid `text` with an empty string (commit `3430a12`), in both cases patching the value into the forwarded payload so clients still parse it. The identity fields `segment`, `revision` and `final` stay strict, because a guessed value there would place the caption at the wrong position in the stream or deduplicate it against the wrong segment — corrupting session state, which is worse than dropping one frame. The assignment does not require these replacements: they keep an update from being dropped, but they cannot restore its original timestamp or its missing text.

**Verification:** a probe script sent updates with missing and `null` `source_ts`, a final with `text: 42`, plus a battery of genuinely malformed frames; the client received the defaulted updates (extra fields intact, the untouched field unchanged) while malformed frames were still dropped, and `run_demo.py --clients 20 --duration 20` reported 100% final-caption completeness with 0 duplicates/stale messages.

## One suggestion that was wrong, inefficient, or required modification

The AI's initial implementation added a per-update session check: any `CAPTION_UPDATE` whose `session` field differed from the `SOURCE_HELLO` session was dropped:

```python
elif isinstance(msg_session, str) and msg_session != session:
    reason = f"session {msg_session!r} does not match {session!r}"
```

**Problem:** the spec (Section 4.1) lists `session` among the `CAPTION_UPDATE` fields, but assigns it no meaning beyond identifying the lecture in `SOURCE_HELLO`. So the check was neither required nor forbidden by the assignment — removing it was our risk judgement, not the correction of a rules violation. The objection is that the comparison conveys no information while carrying a total failure mode: one source connection already *is* one session, so a matching `session` confirms nothing the connection had not already established, but if the hidden test source ever tagged its updates with a value differing from its hello, every caption — finals included — would be silently dropped and Final Caption Completeness would go to 0.

**Correction:** removed the check entirely (commit `0f19fa5`); the session is identified by the connection's `SOURCE_HELLO`, and the update's own `session` field is now read by nothing and forwarded unmodified in the raw payload. Verified by probe (an update tagged `session: "L2"` after an `"L1"` hello is now forwarded) and a clean T1 run.

## Responsibility statement

We understand that the team remains responsible for the submitted code, experimental results, and claims, and that every member may be asked to explain or modify relevant code during the viva.
