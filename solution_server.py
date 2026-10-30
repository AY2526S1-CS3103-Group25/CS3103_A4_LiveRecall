#!/usr/bin/env python3
"""
CS3103 Assignment 4 - LiveRecall revision-aware caption server.

Bounded recent history and per-client delivery preserve finalized captions while
coalescing obsolete provisional revisions only when a client falls behind.

Required evaluator command:
    python solution_server.py --port 8765

Teams should replace or substantially extend the implementation while keeping
this command-line interface and the protocol in the assignment specification.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import random
import re
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Iterator

import websockets
from websockets.exceptions import ConnectionClosed

# Each connection owns its bounded pending work and its sole network sender.
CLIENTS: dict[Any, ClientState] = {}

# Match the mandatory load client's receive limit, including the JSON wrapper.
MAX_HISTORY_MESSAGE_BYTES = 2**20


@dataclass(frozen=True, slots=True)
class CaptionUpdate:
    """A validated CAPTION_UPDATE.

    `payload` is the JSON string forwarded to clients: the original frame
    as received (extra fields preserved) unless a lenient field or non-finite
    number had to be normalized. Binary UTF-8 frames are decoded to text so
    browser clients receive JSON text frames rather than Blob objects.
    """

    segment: int
    revision: int
    source_ts: float
    final: bool
    text: str
    payload: str


class SessionState:
    """Revision metadata and recent finalized captions for the single session.

    Module-level lifetime, independent of any connection: state persists
    across a mid-stream source disconnect/reconnect so replayed frames stay
    deduplicated, but resets when a new source connects after the previous
    stream completed with SOURCE_DONE (a fresh lecture restarting at
    segment 1 must be deliverable without a server restart). SOURCE_DONE is
    not guaranteed — a source killed mid-stream never sends it — so there is
    a fallback: a provisional segment-1 revision-1 frame arriving as the
    first frame after a SOURCE_HELLO, while segment 1 is already closed,
    also resets, since a conforming source numbers segments monotonically
    from 1 and a new stream's s1r1 can only sit in that position (see
    apply()).

    Memory: the contiguous finalized prefix is compacted to the
    `closed_up_to` watermark and pruned from `segments`; durability for
    everything at or below the watermark is enforced by the single int, so
    late replays of old finals are still rejected after pruning. Correctness
    does not depend on finalization order — the watermark only advances over
    a contiguous finalized prefix, and out-of-order segments simply stay in
    the dict until the gap closes. The O(1) size (just the active segment)
    relies on in-order finalization, which the spec guarantees only for the
    starter-kit source; for any other ordering the dict grows with the
    source's unfinalized window, hard-capped at MAX_TRACKED_SEGMENTS by
    force-closing the oldest tracked segments. A force-closed segment that
    never finalized stays eligible for exactly one late final (reopenable),
    so capping never costs a final for any segment the server actually saw;
    the only finals forfeited are for segment numbers first appearing > 1024
    segments behind the frontier, or gap numbers never seen before a forced
    watermark advance.
    """

    # Hard cap on tracked (unpruned) segments. The instructor source keeps the
    # window at ~1 active segment; the cap only matters if one segment never
    # finalizes (e.g. its final frame was malformed and dropped), which would
    # otherwise stall the watermark and let later entries accumulate forever.
    MAX_TRACKED_SEGMENTS = 1024

    # Hard cap on remembered force-closed-but-never-finalized segments (the
    # ones still eligible for one late final). Only reachable under an
    # adversarial flood of never-finalized segments; beyond it, the oldest
    # lose their late-final eligibility.
    MAX_REOPENABLE_SEGMENTS = 1024

    # Cover the required 60-second recovery window plus a small margin.
    HISTORY_RETENTION_SECONDS = 70

    def __init__(self) -> None:
        self._closed_up_to = 0  # every segment <= this is closed and pruned
        self._segments: dict[int, tuple[int, bool]] = {}
        # Force-closed segments that never finalized: their (single) late
        # final is still accepted, so capping doesn't cost tracked finals.
        self._reopenable: set[int] = set()
        # Receipt order permits O(1) expiry, independently of source clock skew
        # or out-of-order finals. Revision compaction must not erase payloads.
        # Decode once per retained final; snapshots share these read-only records.
        self._history: deque[tuple[float, CaptionUpdate, dict[str, Any]]] = deque()
        # Receipt-order final positions let senders recover even out-of-order
        # segment finals, without a growing per-client set of delivered IDs.
        self._final_sequence = 0
        self._expired_sequence = 0
        self._active: dict[int, CaptionUpdate] = {}
        self.generation = object()
        self._done = False  # previous source completed with SOURCE_DONE
        # A CAPTION_UPDATE arrived since the last SOURCE_HELLO: the fallback
        # reset in apply() may only fire before this flips (a new stream's
        # s1r1 can only ever be a connection's first frame).
        self._stream_started = False
        self._dropped_stale = 0
        self._dropped_duplicate = 0
        self._dropped_post_final = 0
        self._force_closed = 0

    def on_source_connect(self) -> None:
        """Reset for a fresh stream iff the previous one completed.

        A new SOURCE_HELLO after a SOURCE_DONE is a new lecture whose segments
        restart at 1 — without a reset every frame would be rejected as
        post-final. A reconnect WITHOUT a prior SOURCE_DONE (crash, network
        drop) keeps state, so frames the source replays stay deduplicated; if
        that connection turns out to be a new stream instead of a replaying
        one, the segment-1 fallback in apply() catches it.
        """
        if self._done:
            print(
                f"new source after SOURCE_DONE: resetting ({self.drop_summary()})",
                flush=True,
            )
            self.__init__()
        self._stream_started = False

    def on_source_done(self) -> None:
        self._done = True

    def apply(self, update: CaptionUpdate) -> bool:
        """Record the update; return True if it should be delivered to clients.

        Synchronous on purpose: the check-and-set must not be interrupted by an
        await, so concurrent ingests on the event loop cannot double-accept.

        New-stream fallback (before the ladder): a provisional segment-1
        revision-1 frame that is the FIRST frame after a SOURCE_HELLO, while
        segment 1 is already closed, resets the session. A conforming source
        numbers segments monotonically from 1, so a new stream's s1r1 can
        only sit in that exact position; it means the previous stream's
        source was killed before sending SOURCE_DONE (e.g. Ctrl-C, then
        rerun against the same server), and without the reset the whole new
        lecture would be silently dropped as post-final. Deliberately
        narrow: once any frame has arrived on the connection the fallback is
        disarmed (a mid-stream or mid-replay s1r1 retransmit can never wipe
        state), a FINAL s1r1 never triggers it (so a replayed reopenable
        late final can't either), and a reopenable segment 1 is excluded
        (its late final must stay deliverable). The one case this misreads —
        a reconnecting source that replays its whole stream from s1r1 — is
        informationally indistinguishable from a new lecture, and treating
        it as one re-broadcasts duplicates instead of silencing a real
        lecture; no harness component replays at all.

        Reject ladder, top to bottom:
        - closed segment (<= watermark): dead, except the single late final
          of a force-closed, never-finalized segment (`_reopenable`);
        - already-final segment: finals are durable, nothing may follow;
        - lower revision: stale, even if marked final — finalizing on it
          would durably bind the segment to older text than clients already
          displayed;
        - same revision, provisional: duplicate.
        Everything else is stored and delivered: a first-seen segment, a
        strictly newer revision, or a final at exactly the stored provisional
        revision — a source may legally promote the last provisional in
        place, so that final is delivered (once) and the segment closes.
        """
        seg = update.segment
        if (
            not self._stream_started
            and seg == 1
            and update.revision == 1
            and not update.final
            and self._closed_up_to >= 1
            and 1 not in self._reopenable
        ):
            print(
                "stream opens at segment 1 revision 1 but segment 1 is closed: "
                f"treating as a new stream, resetting ({self.drop_summary()})",
                flush=True,
            )
            self.__init__()
        self._stream_started = True
        if seg <= self._closed_up_to:
            if update.final and seg in self._reopenable:
                self._reopenable.discard(seg)
                self._remember_final(update)
                return True
            self._dropped_post_final += 1
            return False
        stored = self._segments.get(seg)
        if stored is not None:
            revision, final = stored
            if final:
                self._dropped_post_final += 1
                return False
            if update.revision < revision:
                self._dropped_stale += 1
                return False
            if update.revision == revision and not update.final:
                self._dropped_duplicate += 1
                return False
        self._segments[seg] = (update.revision, update.final)
        if not update.final:
            self._active[seg] = update
        if update.final:
            self._remember_final(update)
            self._compact()
        if len(self._segments) > self.MAX_TRACKED_SEGMENTS:
            self._force_close_oldest()
        return True

    def _remember_final(self, update: CaptionUpdate) -> None:
        self.prune_history()
        self._active.pop(update.segment, None)
        self._final_sequence += 1
        self._history.append((time.monotonic(), update, json.loads(update.payload)))

    def prune_history(self) -> None:
        cutoff = time.monotonic() - self.HISTORY_RETENTION_SECONDS
        while self._history and self._history[0][0] <= cutoff:
            self._history.popleft()
            self._expired_sequence += 1

    def history_items(
        self, last_segment: int, last_revision: int, history_seconds: float
    ) -> list[dict[str, Any]]:
        """Snapshot missed finals in segment order, within receipt time.

        The cursor reports the latest finalized state held by the client.
        Use the same monotonic receipt clock for retention and selection: source
        and server wall clocks need not agree. Original timestamps and extra
        fields remain unchanged in the response.
        """
        self.prune_history()
        if history_seconds <= 0:
            return []
        cutoff = time.monotonic() - history_seconds
        updates = [
            (update, record) for received_at, update, record in self._history
            if received_at >= cutoff
            and (update.segment, update.revision) > (last_segment, last_revision)
        ]
        return [record for _, record in sorted(
            updates, key=lambda entry: entry[0].segment
        )]

    @property
    def final_sequence(self) -> int:
        return self._final_sequence

    @property
    def expired_sequence(self) -> int:
        return self._expired_sequence

    def final_after(self, sequence: int) -> CaptionUpdate | None:
        """Read the next retained final; callers first check expiry."""
        if sequence < self._final_sequence:
            return self._history[sequence - self._expired_sequence][1]
        return None

    def active_updates(self) -> list[CaptionUpdate]:
        return [self._active[segment] for segment in sorted(self._active)]

    def _compact(self) -> None:
        # Advance the watermark over the contiguous finalized prefix and drop
        # those entries; their durability is now carried by _closed_up_to.
        nxt = self._closed_up_to + 1
        entry = self._segments.get(nxt)
        while entry is not None and entry[1]:
            del self._segments[nxt]
            self._closed_up_to = nxt
            nxt += 1
            entry = self._segments.get(nxt)

    def _force_close_oldest(self) -> None:
        # A never-finalized segment stalls the watermark; cap memory by
        # force-closing the oldest tracked segments. A closed segment that
        # never finalized stays acceptable for exactly one late final (via
        # `_reopenable`), so the cap bounds state without losing finals; only
        # its provisional revision history is forgotten, which risks at most
        # re-relaying a replayed provisional, never a duplicate final.
        for seg in sorted(self._segments):
            if len(self._segments) <= self.MAX_TRACKED_SEGMENTS:
                break
            _, final = self._segments.pop(seg)
            self._active.pop(seg, None)
            self._force_closed += 1
            if not final:
                self._reopenable.add(seg)
            if seg > self._closed_up_to:
                self._closed_up_to = seg
        # Bound the reopen set too (relevant only under adversarial floods of
        # never-finalized segments); oldest entries are the ones given up.
        while len(self._reopenable) > self.MAX_REOPENABLE_SEGMENTS:
            self._reopenable.discard(min(self._reopenable))
        self._compact()

    def drop_summary(self) -> str:
        return (
            f"dropped(stale={self._dropped_stale} "
            f"dup={self._dropped_duplicate} "
            f"post_final={self._dropped_post_final}) "
            f"force_closed={self._force_closed}"
        )


SESSION = SessionState()


def _is_positive_int(value: Any) -> bool:
    # bool is a subclass of int, so it must be rejected explicitly.
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def _finite_float(value: Any) -> float | None:
    """Coerce a JSON number to a finite float, or None if it is not one.

    JSON integers have arbitrary precision, so the conversion to a C double
    can itself overflow (`float(10 ** 400)` raises OverflowError, and so does
    `math.isfinite` on the same value). The guard keeps an absurd timestamp a
    dropped field rather than an exception that would kill the connection.
    """
    # bool is a subclass of int, so it must be rejected explicitly.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) else None


def _decode_source_frame(raw: str | bytes) -> tuple[Any, str]:
    """Decode a source frame into (message, payload to forward).

    `json.loads` accepts the JavaScript-only `NaN`/`Infinity`/`-Infinity`
    literals and `json.dumps` re-emits them, but they are not valid JSON:
    a browser's `JSON.parse` rejects the entire frame, so a caption carrying
    one in any field — including an unknown extra one we never look at —
    would be lost. Large exponents such as 1e400 also decode to infinity in
    Python and can become invalid JSON when another field is patched. Both
    cases are replaced with null before re-serialization. Ordinary text frames
    retain their original payload.
    """
    if isinstance(raw, bytes):
        # Keep the decoder's existing acceptance of a UTF-8 BOM.
        raw = raw.decode("utf-8-sig")
    seen: list[str] = []

    def parse_float(token: str) -> float | None:
        number = float(token)
        if math.isfinite(number):
            return number
        seen.append("overflowing number")
        return None

    msg = json.loads(
        raw,
        parse_constant=lambda name: seen.append(name) or None,
        parse_float=parse_float,
    )
    if not seen:
        return msg, raw
    print(f"rewrote non-finite number(s) to null: {', '.join(seen)}", flush=True)
    return msg, json.dumps(msg, allow_nan=False)


def parse_caption_update(msg: dict[str, Any], raw: str) -> CaptionUpdate | None:
    """Validate a decoded CAPTION_UPDATE; return None (and log) if malformed.

    Shape validation only: segment/revision monotonicity is session state,
    not ingestion. Extra/unknown fields are ignored. Never raises.

    The identity fields (segment, revision, final) are strict: without them
    the update cannot be displayed or deduplicated. source_ts (only used for
    age metrics) and text (display content) are lenient — a missing/invalid
    value is defaulted and the payload patched, because dropping a possibly
    final caption over a non-identity field is unrecoverable. The update's
    `session` field is not checked: the session is identified by the source
    connection's SOURCE_HELLO, and the spec doesn't require per-update
    session matching.
    """
    reason = None
    segment = msg.get("segment")
    revision = msg.get("revision")
    final = msg.get("final")
    text = msg.get("text")

    if not _is_positive_int(segment):
        reason = f"segment must be a positive int, got {segment!r}"
    elif not _is_positive_int(revision):
        reason = f"revision must be a positive int, got {revision!r}"
    elif not isinstance(final, bool):
        reason = f"final must be a bool, got {final!r}"

    if reason is not None:
        print(f"dropping malformed CAPTION_UPDATE: {reason}", flush=True)
        return None

    source_ts = _finite_float(msg.get("source_ts"))
    patched: dict[str, Any] = {}
    if source_ts is None:
        source_ts = time.time()
        patched["source_ts"] = source_ts
    if not isinstance(text, str):
        text = ""
        patched["text"] = text

    payload = raw
    if patched:
        payload = json.dumps({**msg, **patched})
        print(
            f"CAPTION_UPDATE segment={segment} revision={revision}: "
            f"defaulted missing/invalid {', '.join(patched)}",
            flush=True,
        )

    return CaptionUpdate(
        segment=segment,
        revision=revision,
        source_ts=source_ts,
        final=final,
        text=text,
        payload=payload,
    )


def history_messages(items: list[dict[str, Any]]) -> Iterator[str]:
    """Emit lossless recovery batches before live delivery, one message at a time.

    The 70-second retention window is unchanged. Batching bounds wire messages
    rather than throwing away finals to make the whole window fit one message.
    An item that fits alone but not inside HISTORY uses CAPTION_UPDATE after an
    initial HISTORY reply; the existing client accept path handles either form.
    """
    prefix, suffix = '{"type":"HISTORY","items":[', ']}'
    empty_size = len(prefix) + len(suffix)
    batch: list[str] = []
    size = empty_size
    replied = False
    for item in items:
        # Escape lone surrogates, but retain compact UTF-8 for normal captions.
        encoded = json.dumps(item, ensure_ascii=False, separators=(',', ':'))
        raw = encoded.encode('utf-8', errors='backslashreplace')
        encoded = raw.decode('utf-8')
        if batch and size + 1 + len(raw) > MAX_HISTORY_MESSAGE_BYTES:
            yield prefix + ','.join(batch) + suffix
            replied = True
            batch = []
            size = empty_size
        if empty_size + len(raw) > MAX_HISTORY_MESSAGE_BYTES:
            if not replied:
                yield prefix + suffix
                replied = True
            if len(raw) > MAX_HISTORY_MESSAGE_BYTES:
                raise ValueError('A caption exceeds the receiver message limit')
            yield encoded
        else:
            size += len(raw) + bool(batch)
            batch.append(encoded)
    if batch or not replied:
        yield prefix + ','.join(batch) + suffix


class ClientState:
    # Live pending work is capped by count and UTF-8 bytes. The initial recovery
    # snapshot shares retained records; it is encoded one bounded batch at a time.
    # The snapshot references and one in-flight frame are outside the live budget.
    # Oversized valid frames use catch-up's single-frame path, never the queue.
    MAX_PENDING = 128
    MAX_PENDING_BYTES = 256 * 1024
    LAG_SECONDS = 0.100
    RECOVER_SECONDS = 0.050
    CHECKPOINT_SECONDS = 1.0
    CHECKPOINT_MESSAGES = 8
    CHECKPOINT_BYTES = 16 * 1024
    MAX_PROVISIONAL_HOLD = 1.0
    MAX_RECOVERY_FRAGMENTS = 32

    def __init__(self, ws, *, fragment_checkpoints: bool = False) -> None:
        self.ws = ws
        self.fragment_checkpoints = fragment_checkpoints
        self.pending: deque[tuple[CaptionUpdate, int, float, int]] = deque()
        self.pending_bytes = 0
        self.ready = asyncio.Event()
        self.failed = asyncio.Event()
        self.failure_reason = ""
        self.generation = SESSION.generation
        # Start at the snapshot/live boundary, not the HELLO cursor: context
        # excluded by history_seconds must not reappear during overload recovery.
        self.final_cursor = SESSION.final_sequence
        self.sent_active: dict[int, int] = {}
        self.lagging = False
        self.catch_up = False
        self.send_started: float | None = None
        self.in_flight_sequence = 0
        self.last_send_seconds = 0.0
        self.coalesced = 0
        self.catch_ups = 0
        self.next_checkpoint = 0.0
        self.checkpoint_messages = 0
        self.checkpoint_bytes = 0
        self.checkpoint_baseline: float | None = None
        self.provisional_hold = 0.0
        self.next_provisional_at = 0.0
        self.peer_lag_until = 0.0
        self.healthy_checkpoints = 0
        self.healthy_since: float | None = None
        self.recovery_window = 3 * self.CHECKPOINT_SECONDS
        self.recovery_penalty = 1.0
        self.last_recovery = 0.0
        self.last_peer_delay = 0.0

    def _in_probation(self, now: float) -> bool:
        return bool(self.fragment_checkpoints and self.last_recovery
                    and now - self.last_recovery
                    < max(3 * self.CHECKPOINT_SECONDS, 4 * self.last_peer_delay))

    def _checkpoint_due(self, size: int, now: float) -> bool:
        return (self._in_probation(now) or now >= self.next_checkpoint
                or self.checkpoint_messages + bool(size) >= self.CHECKPOINT_MESSAGES
                or self.checkpoint_bytes + size >= self.CHECKPOINT_BYTES)

    def _peer_lag(self, delay: float) -> None:
        now = time.monotonic()
        hold = delay
        cooldown = 4 * delay
        window = max(3 * self.CHECKPOINT_SECONDS, 2 * delay)
        if self.fragment_checkpoints and not self.provisional_hold:
            if self._in_probation(now):
                self.recovery_penalty = min(4.0, 4 * self.recovery_penalty)
            else:
                self.recovery_penalty = 1.0
        if self.fragment_checkpoints:
            hold = 2 * delay
            cooldown = self.recovery_penalty * delay if self.recovery_penalty > 1 else 0
            window = self.CHECKPOINT_SECONDS
        self.lagging = True
        self.provisional_hold = max(
            self.provisional_hold, min(self.MAX_PROVISIONAL_HOLD, hold)
        )
        # Repeated congestion during probation backs off; an isolated slowdown
        # can recover without repeating a full cooldown for every hold reduction.
        self.peer_lag_until = max(self.peer_lag_until, now + cooldown)
        self.next_provisional_at = now + self.provisional_hold
        self.healthy_checkpoints = 0
        self.healthy_since = None
        self.last_peer_delay = max(delay, self.LAG_SECONDS)
        self.recovery_window = window

    async def _checkpoint(self, size: int) -> None:
        now = time.monotonic()
        due = self._checkpoint_due(size, now)
        self.checkpoint_messages += bool(size)
        self.checkpoint_bytes += size
        if not due:
            return
        # Only this connection's sole sender waits. Keep one probe outstanding;
        # a protocol Pong is congestion feedback, not an application ACK.
        started = now
        pong = await self.ws.ping()
        threshold = max(self.LAG_SECONDS, 4 * (self.checkpoint_baseline or 0))
        if self.checkpoint_baseline is not None and not pong.done():
            done, _ = await asyncio.wait((pong,), timeout=threshold)
            if not done:
                self._peer_lag(threshold)
        await pong
        now = time.monotonic()
        delay = now - started
        if self.checkpoint_baseline is None:
            self.checkpoint_baseline = delay
        elif delay >= threshold:
            self._peer_lag(delay)
        else:
            self.checkpoint_baseline = min(self.checkpoint_baseline, delay)
            if (self.provisional_hold and now >= self.peer_lag_until
                    and not self.catch_up
                    and not any(entry[0].final for entry in self.pending)):
                self.healthy_checkpoints += 1
                if self.healthy_since is None:
                    self.healthy_since = now
                if (self.healthy_checkpoints >= 3
                        and now - self.healthy_since >= self.recovery_window):
                    if self.fragment_checkpoints:
                        self.provisional_hold = 0.0
                        self.last_recovery = now
                    else:
                        self.provisional_hold /= 2
                        if self.provisional_hold < self.LAG_SECONDS:
                            self.provisional_hold = 0.0
                    self.healthy_checkpoints = 0
                    self.healthy_since = None
            elif self.provisional_hold:
                self.healthy_checkpoints = 0
                self.healthy_since = None
        # Spread protocol work across clients instead of synchronizing probes
        # on the same source revision. Count/byte bounds still cap each window.
        interval = random.uniform(0.75, 1.25) if self.fragment_checkpoints else 1.0
        self.next_checkpoint = now + self.CHECKPOINT_SECONDS * interval
        self.checkpoint_messages = self.checkpoint_bytes = 0

    async def _wait_provisional(self, queued_at: float) -> bool:
        if not self.provisional_hold:
            return False
        delay = max(queued_at + self.provisional_hold,
                    self.next_provisional_at) - time.monotonic()
        if delay <= 0:
            return False
        self.ready.clear()
        try:
            await asyncio.wait_for(self.ready.wait(), delay)
        except asyncio.TimeoutError:
            pass
        return True

    def fail(self, reason: str) -> None:
        if self.failed.is_set():
            return
        self.failure_reason = reason
        self.failed.set()

    def check_recovery(self) -> None:
        if self.generation is not SESSION.generation:
            # Old queued frames and cursors belong to the previous lecture.
            # Reconnecting is explicit; silently rebasing cannot reset the
            # revision map of a client using the required protocol alone.
            self.fail("Source stream restarted; reconnect")
        elif (self.catch_up
              and max(self.final_cursor, self.in_flight_sequence)
              < SESSION.expired_sequence):
            self.fail("Final recovery window expired; reconnect")

    def _observe_lag(self, drop_segment: int | None = None) -> None:
        now = time.monotonic()
        if ((self.send_started is not None
             and now - self.send_started >= self.LAG_SECONDS)
                or (self.pending and now - self.pending[0][2] >= self.LAG_SECONDS)
                or len(self.pending) >= self.MAX_PENDING * 3 // 4
                or self.pending_bytes >= self.MAX_PENDING_BYTES * 3 // 4):
            self.lagging = True
        if self.lagging:
            # Keep the last pending update per segment. An accepted final
            # supersedes its provisionals; the revision gate forbids two finals.
            seen = set()
            kept = deque()
            for entry in reversed(self.pending):
                if entry[0].segment != drop_segment and entry[0].segment not in seen:
                    seen.add(entry[0].segment)
                    kept.appendleft(entry)
                else:
                    self.pending_bytes -= entry[1]
            self.coalesced += len(self.pending) - len(kept)
            if len(kept) != len(self.pending):
                self.pending = kept

    def offer(self, update: CaptionUpdate, size: int) -> None:
        self.check_recovery()
        if self.failed.is_set():
            return
        if self.catch_up:
            self.ready.set()
            return
        queued_at = time.monotonic()
        if self.provisional_hold:
            # Replacements share the first pending revision's deadline, so an
            # indefinitely revised active segment cannot postpone its own send.
            queued_at = next((entry[2] for entry in self.pending
                              if entry[0].segment == update.segment), queued_at)
        self._observe_lag(drop_segment=update.segment)
        sequence = SESSION.final_sequence if update.final else 0
        # Compact with the incoming frame before enforcing the hard bound.
        # The temporary entry is local and never becomes an unbounded backlog.
        if (len(self.pending) >= self.MAX_PENDING
                or self.pending_bytes + size > self.MAX_PENDING_BYTES):
            self.pending.clear()
            self.pending_bytes = 0
            self.catch_up = self.lagging = True
            self.catch_ups += 1
        else:
            self.pending.append((update, size, queued_at, sequence))
            self.pending_bytes += size
        self.ready.set()

    def _next_recovery(self) -> tuple[CaptionUpdate, int] | None:
        SESSION.prune_history()
        self.check_recovery()
        if self.failed.is_set():
            return None
        update = SESSION.final_after(self.final_cursor)
        if update is not None:
            # Recover one final at a time; no private replay copy or full-lecture
            # transcript. The in-flight final advances the cursor only on success.
            return update, self.final_cursor + 1
        for update in SESSION.active_updates():
            if self.sent_active.get(update.segment, 0) < update.revision:
                return update, 0
        self.catch_up = False
        if (not self.provisional_hold
                and self.last_send_seconds < self.RECOVER_SECONDS):
            self.lagging = False
        return None

    async def _send(self, payload: str, sequence: int = 0) -> int:
        size = (len(payload.encode('utf-8'))
                if isinstance(payload, str) else len(payload))
        self.send_started = time.monotonic()
        self.in_flight_sequence = sequence
        try:
            if (self.fragment_checkpoints and
                    (self.provisional_hold or self._checkpoint_due(size, self.send_started))):
                # Frame-based peer queues then hold fewer complete captions.
                # recv() still returns the identical single JSON message.
                step = max(1, (len(payload) + self.MAX_RECOVERY_FRAGMENTS - 1)
                           // self.MAX_RECOVERY_FRAGMENTS)
                await self.ws.send(payload[i:i + step]
                                   for i in range(0, len(payload), step))
            else:
                await self.ws.send(payload)
        finally:
            self.last_send_seconds = time.monotonic() - self.send_started
            self.send_started = None
            self.in_flight_sequence = 0
        return size

    async def run(self, history: list[dict[str, Any]]) -> None:
        # Establish path RTT before HISTORY can fill the peer's receive queue.
        await self._checkpoint(0)
        for message in history_messages(history):
            self.check_recovery()
            if self.failed.is_set():
                return
            size = await self._send(message)
            await self._checkpoint(size)
            await asyncio.sleep(0)
        del history, message
        while not self.failed.is_set():
            self.check_recovery()
            if self.failed.is_set():
                break
            self._observe_lag()
            recovery = self._next_recovery() if self.catch_up else None
            if recovery is not None:
                update, sequence = recovery
                if (not update.final
                        and await self._wait_provisional(0)):
                    continue
            elif self.pending:
                if self.provisional_hold:
                    # Finals bypass held provisionals without changing the
                    # relative receipt order of finals from different segments.
                    index = next((i for i, entry in enumerate(self.pending)
                                  if entry[0].final), 0)
                    if not self.pending[index][0].final:
                        if await self._wait_provisional(self.pending[index][2]):
                            continue
                    self.pending.rotate(-index)
                    update, size, _, sequence = self.pending.popleft()
                    self.pending.rotate(index)
                else:
                    update, size, _, sequence = self.pending.popleft()
                self.pending_bytes -= size
            else:
                self.ready.clear()
                await self.ready.wait()
                continue
            size = await self._send(update.payload, sequence)
            if update.final:
                self.final_cursor = sequence
                self.sent_active.pop(update.segment, None)
            else:
                self.sent_active[update.segment] = update.revision
                self.next_provisional_at = time.monotonic() + self.provisional_hold
                if len(self.sent_active) > SessionState.MAX_TRACKED_SEGMENTS:
                    active = {update.segment for update in SESSION.active_updates()}
                    self.sent_active = {
                        s: r for s, r in self.sent_active.items() if s in active
                    }
            # Hysteresis: resume full coverage only with little fresh queued work
            # and a fast completed send. Keep a single stalled send per client.
            await self._checkpoint(size)
            if (not self.provisional_hold and not self.catch_up
                    and len(self.pending) <= self.MAX_PENDING // 4
                    and self.pending_bytes <= self.MAX_PENDING_BYTES // 4
                    and self.last_send_seconds < self.RECOVER_SECONDS
                    and (not self.pending or time.monotonic() - self.pending[0][2]
                         < self.RECOVER_SECONDS)):
                self.lagging = False
            await asyncio.sleep(0)


async def ingest_caption_update(update: CaptionUpdate) -> None:
    """Commit accepted state and offer work without awaiting client I/O."""
    if not SESSION.apply(update):
        return
    size = (len(update.payload.encode("utf-8"))
            if isinstance(update.payload, str) else len(update.payload))
    for client in CLIENTS.values():
        client.offer(update, size)


async def handle_source(ws, hello: dict[str, Any]) -> None:
    # The session value is logged but not required: the server runs a single
    # session, SOURCE_HELLO's type already routes the connection, and refusing
    # the only source over a missing label would stop every caption.
    session = hello.get("session")
    print(f"source connected: session={session!r}", flush=True)
    SESSION.on_source_connect()

    try:
        async for raw in ws:
            try:
                msg, raw = _decode_source_frame(raw)
            except ValueError:
                # JSONDecodeError for malformed JSON, UnicodeDecodeError if the
                # frame arrived as binary with invalid UTF-8. Both subclass
                # ValueError; neither should drop the source connection.
                continue
            if not isinstance(msg, dict):
                continue

            msg_type = msg.get("type")
            if msg_type == "CAPTION_UPDATE":
                update = parse_caption_update(msg, raw)
                if update is None:
                    continue
                await ingest_caption_update(update)
            elif msg_type == "SOURCE_DONE":
                # Test-control message. The specification does not require it
                # to be forwarded to clients.
                print(f"source finished: segments={msg.get('segments')}", flush=True)
                SESSION.on_source_done()
            else:
                # Forward compatibility: ignore fields/messages this baseline
                # doesn't understand rather than crashing.
                continue
    finally:
        print(f"source disconnected: {SESSION.drop_summary()}", flush=True)


async def handle_client(ws, hello: dict[str, Any]) -> None:
    client_id = hello.get("client_id")
    last_segment = hello.get("last_segment", 0)
    last_revision = hello.get("last_revision", 0)
    last_segment = last_segment if _is_positive_int(last_segment) else 0
    last_revision = last_revision if _is_positive_int(last_revision) else 0
    seconds = _finite_float(hello.get("history_seconds"))
    seconds = 30 if seconds is None else min(60, max(0, seconds))
    # Snapshot and registration are synchronous. The sender owns HISTORY and
    # every subsequent live send; source ingestion never waits for either.
    items = SESSION.history_items(last_segment, last_revision, seconds)
    # Older Python websockets receivers can crash when recv() times out during
    # fragmentation (upstream #1773, fixed in 17.3). Unknown peers stay compatible.
    request = getattr(ws, 'request', None)
    headers = request.headers if request is not None else getattr(ws, 'request_headers', {})
    version = re.search(r'(?:^| )websockets/(\d{1,3})\.(\d{1,3})(?:\.\d{1,3})?(?: |$)',
                        headers.get('User-Agent', ''))
    fragment_checkpoints = bool(version and tuple(map(int, version.groups())) >= (17, 3))
    client = ClientState(ws, fragment_checkpoints=fragment_checkpoints)
    CLIENTS[ws] = client
    sender = asyncio.create_task(client.run(items))
    del items

    async def receive() -> None:
        async for _ in ws:
            pass

    reader = asyncio.create_task(receive())
    failure = asyncio.create_task(client.failed.wait())
    print(f"client connected: id={client_id}; clients={len(CLIENTS)}", flush=True)
    try:
        done, _ = await asyncio.wait(
            (sender, reader, failure), return_when=asyncio.FIRST_COMPLETED
        )
        for task in done:
            task.result()
    except ConnectionClosed:
        pass
    finally:
        CLIENTS.pop(ws, None)
        # Closing first releases a blocked send without cancelling a partially
        # transmitted WebSocket message. Cancellation is only final cleanup.
        try:
            await ws.close(code=1013 if client.failure_reason else 1000,
                           reason=client.failure_reason)
        finally:
            for task in (sender, reader, failure):
                task.cancel()
            await asyncio.gather(sender, reader, failure, return_exceptions=True)
        print(f"client disconnected: id={client_id}; clients={len(CLIENTS)}; "
              f"coalesced={client.coalesced}; catch_ups={client.catch_ups}; "
              f"reason={client.failure_reason!r}", flush=True)


async def handler(ws) -> None:
    try:
        raw = await asyncio.wait_for(ws.recv(), timeout=5)
        hello = json.loads(raw)
    except (asyncio.TimeoutError, ValueError):
        # ValueError covers JSONDecodeError and UnicodeDecodeError (a binary
        # hello frame with invalid UTF-8).
        await ws.close(code=1008, reason="Expected a valid hello message")
        return
    if not isinstance(hello, dict):
        await ws.close(code=1008, reason="Expected a valid hello message")
        return

    msg_type = hello.get("type")
    if msg_type == "SOURCE_HELLO":
        await handle_source(ws, hello)
    elif msg_type == "CLIENT_HELLO":
        await handle_client(ws, hello)
    else:
        await ws.close(code=1008, reason="Expected SOURCE_HELLO or CLIENT_HELLO")


async def prune_history_periodically() -> None:
    while True:
        await asyncio.sleep(5)
        SESSION.prune_history()
        for client in CLIENTS.values():
            client.check_recovery()


async def main_async(port: int) -> None:
    async with websockets.serve(
        handler,
        "0.0.0.0",
        port,
        max_size=2**20,
        write_limit=16 * 1024,
        close_timeout=5,
        ping_interval=20,
        ping_timeout=20,
    ):
        print(f"LiveRecall listening on ws://0.0.0.0:{port}", flush=True)
        cleanup = asyncio.create_task(prune_history_periodically())
        try:
            await asyncio.Future()
        finally:
            cleanup.cancel()
            await asyncio.gather(cleanup, return_exceptions=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    try:
        asyncio.run(main_async(args.port))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
