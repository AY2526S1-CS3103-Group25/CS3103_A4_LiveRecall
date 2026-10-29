# AI / Coding-Assistant Usage

## How AI helped

Claude Code and Codex were used to explain networking concepts, discuss design
options, draft implementation changes, suggest tests, and improve documentation.
Automated Claude Code reviews also helped identify validation problems and edge
cases that needed further investigation.

The assistance covered caption ingestion, revision replacement, client cleanup,
local recall, and interface behavior. AI was useful for identifying interactions
between these areas, such as how rejecting an update can affect final-caption
completeness or how retaining every caption can cause memory growth.

Suggestions were evaluated against the assignment requirements and existing code.
We revised proposals when their assumptions, resource use, or behavior did not
fit the project.

## Useful suggestions

Keeping one display row per caption segment made revision replacement easier to
understand and prevented every provisional update from appearing as another
line. This approach still needed explicit checks to reject older revisions and
protect accepted final captions.

Reusing the client's existing recent-caption state for recall avoided maintaining
a second transcript. The recall controls filter locally stored finalized captions
for the selected window, so changing the view does not require a server request
and previously received captions remain accessible during a disconnection.

AI also helped identify malformed-input cases and draft controlled-clock checks.
These checks made it practical to examine expiry boundaries and delayed updates
without waiting through a full lecture each time.

## Critical evaluation and changes

### Balancing validation with caption delivery

An early implementation rejected updates whenever any field was invalid. Review
showed that this could discard an otherwise usable final caption because its
timestamp or text was missing. We kept strict validation for segment identity,
revision, and final status, while allowing recoverable timestamp and text errors
to receive defaults.

This was a trade-off: a replacement timestamp or empty text can preserve delivery,
but cannot recover the original information. We therefore distinguish accepting
an update from guaranteeing that its original content or timing was preserved.

### Bounding memory while preserving revision correctness

An initial client design retained captions for the entire page session. We changed
it to expire finalized captions and older inactive provisionals after a bounded
retention period. Recently removed captions retain temporary revision guards so
stale updates cannot immediately restore outdated text.

We also rejected a revision-handling exception that could accept an older final.
The accepted rule allows an equal-revision final to complete a provisional, while
rejecting older revisions. Blocking every segment below a single cutoff was also
too broad because it could reject unseen captions; temporary records of the
segments actually removed were used instead. These guards expire, so they do not
provide permanent duplicate protection or complete reconnect recovery.

### Preserving useful context without presenting it as current

Deleting an inactive unfinished caption would remove potentially useful context.
Keeping it unchanged could make old text appear current. We chose to preserve the
newest unfinished caption and label it stale after prolonged inactivity. Accepted
updates clear that label; rejected updates do not reset the inactivity timer.

### Choosing and applying recall timing deliberately

We chose client receipt time for recall, measured with a monotonic clock when a
final caption is first accepted. The assignment specifies the recall durations
but does not prescribe this timing basis. The choice gives delayed finals their
full local window and avoids depending on the source clock. It also means recall
represents recently received finals rather than their exact lecture event times.

Retaining enough history and displaying the selected window are separate concerns.
The client keeps finals for 70 seconds, but recall independently filters the
previous 15, 30, or 60 seconds. Duplicate or rejected updates must not extend a
final's window, and the visible view must refresh even when the source is idle.

### Checking failures beyond the normal demonstration

Additional AI-assisted review exposed cases that ordinary caption playback did
not cover. Malformed text could throw during browser rendering, and blocked
session storage could prevent the connection hello. We normalized invalid text
and kept an in-memory identifier when storage is unavailable. Review also found
that passing every recalled row as a separate function argument could fail for
an unusually large window; a document fragment avoids that argument limit.

On the server, a numeric value that overflowed Python's floating-point range
could become invalid JSON when another field was repaired. Non-finite numbers
are now normalized before re-serialization, including inside unknown extra
fields. Binary UTF-8 source frames are decoded to text before forwarding so the
browser receives JSON text rather than binary objects. These changes preserve
valid caption content and were checked against the original failure cases.

## Verification and limitations

Earlier development records include server probes, a basic public test run, and
browser checks. Controlled-clock client checks covered revision replacement,
final protection, malformed messages, recall boundaries, misleading source
timestamps, expiry, and local selection while disconnected. The development test
folder was later removed from the submission folder at the user's request.

A subsequent review ran 17 client checks and eight server checks from temporary
files outside the submission folder; all passed. The checks included malformed
values, blocked storage, numeric overflow, binary frames, and a large recall
window. They exercise application code with controlled DOM and socket adapters.

These checks do not establish complete networking correctness or performance.
Verification of the latest recall interface with the real server and browser
remains unfinished because of port-binding and browser-automation restrictions.
AI-generated explanations and passing isolated checks are not substitutes for
the required application demonstration and Ubuntu experiments.

## Responsibility statement

The team remains responsible for the submitted code, experimental results, and
claims. We must be able to explain the design choices, identify their limitations,
and modify the implementation during the viva.
