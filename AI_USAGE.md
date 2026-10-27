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
allowing late finals. Reconnect recovery remains separate work.

## Responsibility statement

We understand that the team remains responsible for the submitted code, experimental results, and claims, and that every member may be asked to explain or modify relevant code during the viva.
