# AI / Coding-Assistant Usage

## Tools used

### Issue #3 - live caption display and revision replacement (28 October 2026)

OpenAI Codex inspected GitHub issue #3, the assignment PDF (requirements 1-2
and protocol sections 4.1-4.2), and the existing browser skeleton. It helped
plan and implement the segment-to-row map, revision checks, provisional/final
styling, explanatory comments, and issue-specific browser instructions.
Impeccable guided the limited styling change, and Playwright CLI was used for
controlled browser checks and a desktop screenshot. No server or
instructor test-harness files were changed for this issue.

## One useful suggestion

Codex suggested keeping the latest accepted caption and its existing DOM row
together in a map keyed by segment. Updating the same row avoids duplicate
lines and preserves its position. Separate checks reject equal/older revisions
and protect finalized captions, including against an unexpected newer update.

Verification used the actual client HTML/JavaScript in two browser tabs, with
WebSocket events supplied by a test double. The checks covered in-place
replacement, stale/equal revisions, final durability, independent segments,
revision gaps, more than three revisions, a final arriving without a provisional,
and literal display of HTML-like text. The browser checks passed without script
or console errors. The desktop screenshot was inspected. JavaScript syntax,
diff whitespace, and the Impeccable mechanical UI scan also passed.

## One suggestion that was wrong, inefficient, or required modification

Codex initially proposed verifying this change by starting the existing server,
serving the client over HTTP, and running the deterministic caption source with
two browser tabs. That verification plan required modification: this execution
environment rejected both local server socket binds with `PermissionError`.
The corrected check intercepted browser requests to serve the real client files
and supplied controlled WebSocket events without opening a listening port.

This verifies browser revision handling and rendering, but does not establish
end-to-end network delivery or public/hidden benchmark performance. The real
source-to-server-to-browser smoke test remains to be run using the commands in
README.md in an environment that permits local servers.

Issue #3 intentionally retains caption lines for the page session. Recall,
rolling-history retention, HISTORY merging, and automatic reconnect remain
separate work; this change must not be presented as implementing those features.

## Responsibility statement

We understand that the team remains responsible for the submitted code, experimental results, and claims, and that every member may be asked to explain or modify relevant code during the viva.
