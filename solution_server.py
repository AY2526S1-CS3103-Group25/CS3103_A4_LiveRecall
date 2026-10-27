#!/usr/bin/env python3
"""
CS3103 Assignment 4 - LiveRecall naive working baseline.

This starter is intentionally useful but incomplete. It provides the required
CLI and a basic WebSocket broadcaster so that caption_source.py -> server ->
browser/load clients works on day one.

It DOES NOT implement the graded networking mechanisms:
  - revision-state management or deduplication
  - recent-history recovery or late-join recovery
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
from dataclasses import dataclass
from typing import Any

import websockets

# Naive global set of currently connected student clients.
# There is deliberately no per-client queue or recovery state here.
CLIENTS: set[Any] = set()


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
    """Latest accepted (revision, final) per segment for the single live session.

    Module-level lifetime, independent of any connection: state persists across
    source disconnect/reconnect so a source replaying already-accepted frames
    cannot cause duplicate or stale deliveries (a genuinely new lecture is a
    server restart).

    Memory is bounded: the source finalizes segments in order, so the
    contiguous finalized prefix is compacted to the `finalized_up_to`
    watermark and pruned from `segments`. The dict only holds segments above
    the watermark (normally just the active one); durability for everything
    at or below the watermark is enforced by the single int, so late replays
    of old finals are still rejected after pruning.
    """

    def __init__(self) -> None:
        self.finalized_up_to = 0  # every segment <= this is final and pruned
        self.segments: dict[int, tuple[int, bool]] = {}
        self.dropped_stale = 0
        self.dropped_duplicate = 0
        self.dropped_post_final = 0

    def apply(self, update: CaptionUpdate) -> bool:
        """Record the update; return True if it should be delivered to clients.

        Synchronous on purpose: the check-and-set must not be interrupted by an
        await, so concurrent ingests on the event loop cannot double-accept.

        Never delivers anything for an already-finalized segment, and never
        delivers a revision <= the stored one: the harness clients classify
        such frames as duplicate/stale and would not count even a final toward
        completeness, so relaying them can only hurt. The one concession to a
        source that declares final without bumping the revision: a final at
        exactly the stored provisional revision flips the stored flag (so the
        segment is durable and recorded as finalized) but is still not
        rebroadcast.
        """
        if update.segment <= self.finalized_up_to:
            self.dropped_post_final += 1
            return False
        stored = self.segments.get(update.segment)
        if stored is not None:
            revision, final = stored
            if final:
                self.dropped_post_final += 1
                return False
            if update.revision == revision:
                if update.final:
                    self.segments[update.segment] = (revision, True)
                    self._compact()
                self.dropped_duplicate += 1
                return False
            if update.revision < revision:
                self.dropped_stale += 1
                return False
        self.segments[update.segment] = (update.revision, update.final)
        if update.final:
            self._compact()
        return True

    def _compact(self) -> None:
        # Advance the watermark over the contiguous finalized prefix and drop
        # those entries; their durability is now carried by finalized_up_to.
        nxt = self.finalized_up_to + 1
        entry = self.segments.get(nxt)
        while entry is not None and entry[1]:
            del self.segments[nxt]
            self.finalized_up_to = nxt
            nxt += 1
            entry = self.segments.get(nxt)

    def drop_summary(self) -> str:
        return (
            f"dropped(stale={self.dropped_stale} "
            f"dup={self.dropped_duplicate} "
            f"post_final={self.dropped_post_final})"
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

    snapshot = list(CLIENTS)
    results = await asyncio.gather(
        *(client.send(raw_message) for client in snapshot),
        return_exceptions=True,
    )
    for client, result in zip(snapshot, results):
        if isinstance(result, Exception):
            CLIENTS.discard(client)


async def handle_source(ws, hello: dict[str, Any]) -> None:
    # The session value is logged but not required: the server runs a single
    # session, SOURCE_HELLO's type already routes the connection, and refusing
    # the only source over a missing label would stop every caption.
    session = hello.get("session")
    print(f"source connected: session={session!r}", flush=True)

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
            else:
                # Forward compatibility: ignore fields/messages this baseline
                # doesn't understand rather than crashing.
                continue
    finally:
        print(f"source disconnected: {SESSION.drop_summary()}", flush=True)


async def handle_client(ws, hello: dict[str, Any]) -> None:
    client_id = hello.get("client_id")

    # The baseline has no stored recovery state. Sending an empty HISTORY makes
    # the connection behavior visible and keeps the starter easy to inspect.
    # Students must implement correct HISTORY semantics for the assignment.
    await ws.send(json.dumps({"type": "HISTORY", "items": []}))

    CLIENTS.add(ws)
    print(f"client connected: id={client_id}; clients={len(CLIENTS)}", flush=True)
    try:
        # Student clients normally don't send further messages after hello.
        # Keep the WebSocket open until the client disconnects.
        async for _ in ws:
            pass
    finally:
        CLIENTS.discard(ws)
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
        await asyncio.Future()


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
