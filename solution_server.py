#!/usr/bin/env python3
"""
CS3103 Assignment 4 - LiveRecall naive working baseline.

This starter is intentionally useful but incomplete. It provides the required
CLI and a basic WebSocket broadcaster so that caption_source.py -> server ->
browser/load clients works on day one.

It DOES NOT implement the graded networking mechanisms:
  - bounded per-client application queues
  - slow-client isolation
  - semantic coalescing / backpressure
  - constrained-bandwidth overload handling

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
import time
from collections import deque
from dataclasses import dataclass
from typing import Any

import websockets

# Per-client locks order HISTORY before live sends. Bounded sender queues and
# slow-client isolation remain separate work (issues #9/#10).
CLIENTS: dict[Any, asyncio.Lock] = {}


@dataclass(frozen=True, slots=True)
class CaptionUpdate:
    """A validated CAPTION_UPDATE.

    `payload` is the JSON string forwarded to clients: the original frame
    as received (byte-exact, extra fields preserved) unless a lenient field
    (`source_ts`, `text`) had to be defaulted, in which case it is a
    re-serialization of the frame with the defaults filled in.
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
        self._history: deque[tuple[float, CaptionUpdate]] = deque()
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
        if update.final:
            self._remember_final(update)
            self._compact()
        if len(self._segments) > self.MAX_TRACKED_SEGMENTS:
            self._force_close_oldest()
        return True

    def _remember_final(self, update: CaptionUpdate) -> None:
        self.prune_history()
        self._history.append((time.monotonic(), update))

    def prune_history(self) -> None:
        cutoff = time.monotonic() - self.HISTORY_RETENTION_SECONDS
        while self._history and self._history[0][0] <= cutoff:
            self._history.popleft()

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
            update for received_at, update in self._history
            if received_at >= cutoff
            and (update.segment, update.revision) > (last_segment, last_revision)
        ]
        return [json.loads(update.payload) for update in sorted(
            updates, key=lambda update: update.segment
        )]

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


def _decode_source_frame(raw: str | bytes) -> tuple[Any, str | bytes]:
    """Decode a source frame into (message, payload to forward).

    `json.loads` accepts the JavaScript-only `NaN`/`Infinity`/`-Infinity`
    literals and `json.dumps` re-emits them, but they are not valid JSON:
    a browser's `JSON.parse` rejects the entire frame, so a caption carrying
    one in any field — including an unknown extra one we never look at —
    would be lost. When such a literal is seen it is replaced with null and
    the payload re-serialized. `parse_constant` fires only in that case, so
    ordinary frames keep their byte-exact payload and pay nothing.
    """
    seen: list[str] = []
    msg = json.loads(raw, parse_constant=lambda name: seen.append(name) or None)
    if not seen:
        return msg, raw
    print(f"rewrote non-JSON literal(s) to null: {', '.join(seen)}", flush=True)
    return msg, json.dumps(msg)


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


async def ingest_caption_update(update: CaptionUpdate) -> None:
    """Gate validated caption updates through the revision state, then relay.

    Pipeline: parse_caption_update (shape) -> SESSION.apply (revision gate)
    -> broadcast. State is committed before the broadcast await, so an update
    arriving during a slow broadcast still sees it. Per-client queueing and
    overload coalescing replace the broadcast side, not this gate.
    """
    if not SESSION.apply(update):
        return
    await broadcast_naively(update.payload)


async def broadcast_naively(raw_message: str) -> None:
    """Send one message directly to every connected client.

    This is intentionally a naive baseline. The source handler waits for all
    sends to complete. Under pressure, this design may accumulate delay and is
    not the target architecture for the assignment.
    """
    if not CLIENTS:
        return

    snapshot = list(CLIENTS.items())
    results = await asyncio.gather(
        *(send_live(client, lock, raw_message) for client, lock in snapshot),
        return_exceptions=True,
    )
    for (client, _), result in zip(snapshot, results):
        if isinstance(result, Exception):
            CLIENTS.pop(client, None)


async def send_live(ws, lock: asyncio.Lock, raw_message: str) -> None:
    async with lock:
        await ws.send(raw_message)


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
    lock = asyncio.Lock()
    try:
        async with lock:
            # No await between snapshot and registration: each accepted final
            # belongs either to HISTORY or to subsequent live delivery. Holding
            # this lock across the send prevents live frames overtaking HISTORY.
            items = SESSION.history_items(last_segment, last_revision, seconds)
            CLIENTS[ws] = lock
            await ws.send(json.dumps({"type": "HISTORY", "items": items}))
        print(f"client connected: id={client_id}; clients={len(CLIENTS)}", flush=True)
        # Student clients normally don't send further messages after hello.
        # Keep the WebSocket open until the client disconnects.
        async for _ in ws:
            pass
    finally:
        CLIENTS.pop(ws, None)
        print(f"client disconnected: id={client_id}; clients={len(CLIENTS)}", flush=True)


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


async def main_async(port: int) -> None:
    async with websockets.serve(
        handler,
        "0.0.0.0",
        port,
        max_size=2**20,
        ping_interval=20,
        ping_timeout=20,
    ):
        print(f"LiveRecall baseline listening on ws://0.0.0.0:{port}", flush=True)
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
