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


def _is_positive_int(value: Any) -> bool:
    # bool is a subclass of int, so it must be rejected explicitly.
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


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

    source_ts = msg.get("source_ts")
    patched: dict[str, Any] = {}
    if (
        isinstance(source_ts, bool)
        or not isinstance(source_ts, (int, float))
        or not math.isfinite(source_ts)
    ):
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
        source_ts=float(source_ts),
        final=final,
        text=text,
        payload=payload,
    )


async def ingest_caption_update(update: CaptionUpdate) -> None:
    """Entry point for validated caption updates.

    The session-state manager (revision dedup + broadcast policy) replaces
    this body; ingestion/parsing above stays unchanged.
    """
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
                msg = json.loads(raw)
            except json.JSONDecodeError:
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
    except (asyncio.TimeoutError, json.JSONDecodeError):
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
