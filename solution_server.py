#!/usr/bin/env python3
"""
CS3103 Assignment 4 - LiveRecall naive working baseline.

This starter is intentionally useful but incomplete. It provides the required
CLI and a basic WebSocket broadcaster so that caption_source.py -> server ->
browser/load clients works on day one.

It DOES NOT implement the graded networking mechanisms:
  - revision-state management or deduplication
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

# Finalized captions keyed by segment, used to answer HISTORY for
# reconnecting and late-joining clients (issue #8).
#
# Bounded by time, not by count: clients may request at most 60 s of
# history, so retain a little more (70 s) and evict on every ingested
# update. The second, defensive bound drops entries whose source_ts is
# absurdly in the future, so a broken source clock cannot keep an entry
# alive past eviction forever.
HISTORY_RETENTION_S = 70.0
FINAL_HISTORY: dict[int, CaptionUpdate] = {}


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


def evict_expired_finals() -> None:
    """Drop finalized captions that have left the recovery window.

    Called on every ingested update: the source streams continuously, so
    the window stays current without a separate timer, and the map holds
    only a retention window of segments, so a linear scan is cheap.
    """
    now = time.time()
    for segment in list(FINAL_HISTORY):
        source_ts = FINAL_HISTORY[segment].source_ts
        if (source_ts <= now - HISTORY_RETENTION_S
                or source_ts > now + HISTORY_RETENTION_S):
            FINAL_HISTORY.pop(segment, None)


def record_final_for_history(update: CaptionUpdate) -> None:
    """Maintain FINAL_HISTORY for reconnect/late-join recovery (issue #8).

    Only finalized captions are stored: HISTORY must contain finals only,
    and the test harness flags any non-final item as invalid. A final is
    durable state, so an older revision of a segment never replaces the
    stored one (protocol invariant 1); only a strictly newer revision may
    supersede it.
    """
    evict_expired_finals()
    if not update.final:
        return
    stored = FINAL_HISTORY.get(update.segment)
    if stored is not None and update.revision < stored.revision:
        print(
            f"ignoring older final: segment={update.segment} "
            f"revision={update.revision} < stored {stored.revision}",
            flush=True,
        )
        return
    FINAL_HISTORY[update.segment] = update


def _nonnegative_int(value: Any) -> int:
    """Sanitize a CLIENT_HELLO resume field; anything junk becomes 0.

    0 means "holds nothing", so a malformed resume point degrades to the
    full recovery window instead of hiding captions from the client.
    """
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value


def build_history_items(hello: dict[str, Any]) -> list[dict[str, Any]]:
    """Build the HISTORY reply for a CLIENT_HELLO (issue #8).

    Items are the finalized captions the client's reported point does not
    already cover, oldest to newest: every segment above last_segment, plus
    last_segment itself only when a strictly newer revision was finalized
    after the client's last acceptance. Because delivery on one socket is
    ordered and the client reports its newest accepted final, nothing at or
    below the reported point is re-sent, so a final is never delivered to
    the same client twice.

    The reply is bounded by HISTORY_RETENTION_S rather than the client's
    history_seconds: a client that missed finals older than its display
    window keeps a low resume point and re-requests them on its next
    reconnect, so capping the reply could cost completeness without
    saving bandwidth.
    """
    last_segment = _nonnegative_int(hello.get("last_segment"))
    last_revision = _nonnegative_int(hello.get("last_revision"))
    items: list[dict[str, Any]] = []
    for segment in sorted(FINAL_HISTORY):
        update = FINAL_HISTORY[segment]
        if segment < last_segment:
            continue
        if segment == last_segment and update.revision <= last_revision:
            continue
        # The payload is always valid JSON: the original frame parsed on
        # ingest, or a re-serialization with defaulted fields. Items must
        # be objects, so re-parse rather than ship the raw string.
        items.append(json.loads(update.payload))
    return items


async def ingest_caption_update(update: CaptionUpdate) -> None:
    """Entry point for validated caption updates.

    Finalized captions are recorded before the broadcast, so a client that
    reconnects while this update is in flight can still recover it from
    HISTORY. The session-state manager (revision dedup + per-client
    queues) replaces this body later; ingestion/parsing stays unchanged.
    """
    record_final_for_history(update)
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
        print("source disconnected", flush=True)


async def handle_client(ws, hello: dict[str, Any]) -> None:
    client_id = hello.get("client_id")

    # HISTORY goes out before the client joins the broadcast set, so a
    # caption can never reach it in both the recovery burst and the live
    # stream: the burst ends first, then live updates begin.
    await ws.send(json.dumps({
        "type": "HISTORY",
        "items": build_history_items(hello),
    }))

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
