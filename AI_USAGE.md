# AI / Coding-Assistant Usage

## Tools used

- **Claude Code (Anthropic, Fable 5 model)** — interactive CLI agent used to plan and implement the server-side protocol ingestion (issue #1 / PR #20): the `CaptionUpdate` validation layer in `solution_server.py`, ad-hoc protocol probe scripts used during testing, and the PR/issue write-ups. All generated code was reviewed by the team and verified against the public test harness before merging.
- **Claude Code automated PR review (GitHub Actions, `claude-sonnet-5-5`)** — the workflow in `.github/workflows/` reviews every PR and leaves inline comments; its findings on PR #20 were triaged by the team and either applied or argued against in the review threads.

(To be extended by teammates as further issues are worked on.)

## One useful suggestion

The automated reviewer flagged that our first ingestion implementation dropped any `CAPTION_UPDATE` failing strict validation — including a `final=true` update with a missing or non-numeric `source_ts`. Since a dropped final can never be recovered, one malformed timestamp would have permanently cost Final Caption Completeness in the graded tests. We changed `source_ts` to be lenient (defaulting to the server's receive time and patching the forwarded payload) while keeping the identity fields (`segment`, `revision`, `final`, `text`) strict.

**Verification:** a probe script sent updates with missing and `null` `source_ts` plus a battery of genuinely malformed frames; the client received the timestamp-defaulted updates (extra fields intact) while malformed frames were still dropped, and `run_demo.py --clients 20 --duration 20` reported 100% final-caption completeness with 0 duplicates/stale messages.

## One suggestion that was wrong, inefficient, or required modification

The AI's initial implementation added a per-update session check: any `CAPTION_UPDATE` whose `session` field differed from the `SOURCE_HELLO` session was dropped:

```python
elif isinstance(msg_session, str) and msg_session != session:
    reason = f"session {msg_session!r} does not match {session!r}"
```

**Problem:** the assignment spec (Section 4.1) assigns no semantics to the update's `session` field beyond the five required fields and explicitly requires ignoring fields outside the minimum interface. The check was extra strictness with a catastrophic failure mode: if the hidden test source ever tagged updates with a different session value than its hello, every caption — finals included — would be silently dropped and Final Caption Completeness would go to 0.

**Correction:** removed the check entirely (commit `0f19fa5`); the session is identified by the connection's `SOURCE_HELLO`, and the update's `session` field is tolerated and forwarded unmodified like any other extra field. Verified by probe (an update tagged `session: "L2"` after an `"L1"` hello is now forwarded) and a clean T1 run.

## Responsibility statement

We understand that the team remains responsible for the submitted code, experimental results, and claims, and that every member may be asked to explain or modify relevant code during the viva.
