# AI / Coding-Assistant Usage

## How AI was used

### Issue #3 - live caption display

AI was used as an implementation and review aid to discuss design options,
draft parts of the caption display logic, and identify edge cases. Proposed
changes were evaluated against the assignment's revision rules, bounded-state
requirements, and need for simple, understandable code.

## One useful suggestion

Keeping one caption row per segment was a useful suggestion because it allows
newer revisions to replace existing text without duplicate lines. The approach
was adopted with explicit checks for older revisions and finalized captions.
Browser checks verified these behaviors.

## One suggestion that required modification

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

During review with AI, I examined cases where the display could look correct
while giving the viewer misleading information. I evaluated the suggested fixes
against caption preservation, revision correctness, and the existing live window.

- **An unfinished caption could appear current indefinitely.** The review
  suggested expiring it, but that would remove the viewer's last available
  context. I chose to preserve the newest provisional text and label it
  "PROVISIONAL — STALE" after 70 seconds without an accepted update. I also
  distinguished receiving a message from accepting useful progress: rejected
  revisions must not refresh the timer or clear the label. An accepted newer
  revision or final clears it. Reusing the five-second cleanup timer gives
  detection at about 70–75 seconds without another timer.
- **The existing auto-scroll did not constrain the growing pane.** I traced
  this to the lack of a height limit and scrollable overflow. I adopted an
  internal scroll area capped at `min(24rem, 50vh)`, keeping the 6rem minimum
  height, wrapping, and recall controls outside it. Keyboard focus and a
  "Live captions" label make the pane accessible beyond mouse scrolling.
- **Revision comparisons assumed valid input.** I considered how a missing
  revision or a string such as `"false"` could undermine those comparisons.
  I added validation before state changes: positive safe integers for segment
  and revision, and a boolean final flag. Malformed JSON and non-object messages
  are ignored; unknown fields remain allowed for protocol compatibility, and
  caption text still renders as plain text.
- **An empty pane did not explain what had happened.** I separated initial
  waiting from expiry with two status messages. I placed the status outside
  the caption container to preserve segment ordering and avoided rewriting
  unchanged status text on every cleanup tick to prevent repeated announcements.

I kept finals and superseded provisionals on the existing roughly 70-second
retention window, with pruned revision/finality guards for one extra 70-second
window. Only the newest provisional text is preserved indefinitely; duplicate
protection remains temporary. Lecture restarts, reconnect, history/recall, and
multi-session behavior remain separate work, with no server or protocol changes.

Verification used six Node.js regressions running the actual client script with
a controlled clock, plus Chromium checks of the actual HTML/script with
intercepted WebSocket messages at desktop and mobile sizes. The UI detector
reported no findings. The submission sanity check found the required files,
but its server startup and the real two-browser demo could not be verified in
this sandbox because binding the server port was denied.

## Responsibility statement

We understand that the team remains responsible for the submitted code, experimental results, and claims, and that every member may be asked to explain or modify relevant code during the viva.
