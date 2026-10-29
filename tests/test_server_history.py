"""Issue #6 regressions; run with python -m unittest discover -s tests -p 'test_*.py'."""

import asyncio
import json
import unittest
from unittest.mock import patch

import solution_server as server


def caption(segment, revision=1, final=True, source_ts=1000):
    msg = {
        "type": "CAPTION_UPDATE", "session": "L1", "segment": segment,
        "revision": revision, "source_ts": source_ts, "final": final,
        "text": f"caption {segment}", "extra": "preserved",
    }
    return server.parse_caption_update(msg, json.dumps(msg))


class HistoryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        clock = patch.object(server, "time").start()
        self.wall = clock.time
        self.wall.return_value = 1000
        self.clock = clock.monotonic
        self.clock.return_value = 100
        self.addCleanup(patch.stopall)
        self.state = server.SessionState()

    def items(self, segment=0, revision=0, seconds=60):
        return self.state.history_items(segment, revision, seconds)

    def test_only_accepted_finals_survive_revision_compaction(self):
        for update in [caption(1, final=False), caption(1), caption(1),
                       caption(1, 2, False), caption(1, 3), caption(2, final=False)]:
            self.state.apply(update)
        self.assertEqual(self.items(), [json.loads(caption(1).payload)])
        self.assertNotIn(1, self.state._segments)

    def test_order_and_cursor_revision_boundary(self):
        for segment in [3, 1, 2]:
            self.state.apply(caption(segment, revision=3))
        self.assertEqual([m["segment"] for m in self.items()], [1, 2, 3])
        self.assertEqual([m["segment"] for m in self.items(2, 2)], [2, 3])
        self.assertEqual([m["segment"] for m in self.items(2, 3)], [3])
        self.assertEqual([m["segment"] for m in self.items(2, 4)], [3])
        self.assertEqual(self.items(3, 3), [])

    def test_requested_windows_include_exact_cutoff(self):
        for segment, age in enumerate([61, 60, 30, 15, 0], start=1):
            self.clock.return_value = 100 - age
            self.state.apply(caption(segment, source_ts=1000 - age))
        self.clock.return_value = 100
        for seconds, expected in [(0, []), (15, [4, 5]), (30, [3, 4, 5]),
                                  (60, [2, 3, 4, 5])]:
            with self.subTest(seconds=seconds):
                self.assertEqual([m["segment"] for m in self.items(seconds=seconds)],
                                 expected)

    def test_source_clock_skew_does_not_hide_recent_finals(self):
        for segment, timestamp in enumerate([939, 1061, 10**10], start=1):
            self.state.apply(caption(segment, source_ts=timestamp))
        self.wall.return_value = 10**11
        self.assertEqual([m["segment"] for m in self.items()], [1, 2, 3])
        self.assertEqual([m["source_ts"] for m in self.items()],
                         [939, 1061, 10**10])

    def test_storage_expiry_does_not_remove_duplicate_protection(self):
        self.state.apply(caption(1))
        self.clock.return_value = 169.9
        self.assertEqual(self.items(), [])  # retained beyond the request window
        self.assertEqual(len(self.state._history), 1)
        self.clock.return_value = 170
        self.state.prune_history()
        self.assertEqual(self.items(), [])
        self.assertFalse(self.state.apply(caption(1)))
        self.assertEqual(self.items(), [])

    def test_future_source_timestamp_cannot_prevent_storage_expiry(self):
        self.state.apply(caption(1, source_ts=10**10))
        self.wall.return_value = 900  # wall-clock changes cannot extend storage
        self.clock.return_value = 171
        self.state.prune_history()
        self.assertEqual(self.items(), [])

    def test_rolling_storage_does_not_accumulate_the_transcript(self):
        for segment in range(1, 201):
            self.clock.return_value = segment
            self.wall.return_value = 1000 + segment
            self.state.apply(caption(segment, source_ts=1000 + segment))
        self.assertEqual(len(self.state._history), 70)
        self.assertEqual(len(self.items()), 61)

    def test_history_follows_both_existing_stream_reset_paths(self):
        self.state.apply(caption(1))
        self.state.on_source_connect()  # ordinary reconnect retains history
        self.assertEqual(len(self.items()), 1)
        self.state.apply(caption(1, final=False))  # PR #22's fresh-stream fallback
        self.assertEqual(self.items(), [])
        self.state.apply(caption(1, revision=2))
        self.state.on_source_done()
        self.assertEqual(len(self.items()), 1)  # DONE alone must not erase context
        self.state.on_source_connect()
        self.assertEqual(self.items(), [])

    def test_force_closed_segment_late_final_is_recoverable_once(self):
        self.state.MAX_TRACKED_SEGMENTS = 1
        self.state.apply(caption(1, final=False))
        self.state.apply(caption(2))
        self.assertTrue(self.state.apply(caption(1, revision=2)))
        self.assertFalse(self.state.apply(caption(1, revision=2)))
        self.assertEqual([m["segment"] for m in self.items()], [1, 2])

    async def test_idle_cleanup_expires_history_without_new_messages(self):
        self.state.apply(caption(1))
        self.clock.return_value = 171
        with patch.object(server, "SESSION", self.state), patch.object(
            server.asyncio, "sleep", side_effect=[None, asyncio.CancelledError]
        ):
            with self.assertRaises(asyncio.CancelledError):
                await server.prune_history_periodically()
        self.assertEqual(len(self.state._history), 0)


class HistoryMessageTests(unittest.TestCase):
    def test_large_snapshot_is_lossless_and_each_message_fits_receiver(self):
        items = [json.loads(caption(segment).payload)
                 for segment in range(1, 20001)]
        messages = list(server.history_messages(items))
        self.assertGreater(len(messages), 1)
        recovered = []
        for raw in messages:
            self.assertLessEqual(len(raw.encode('utf-8')), 2**20)
            message = json.loads(raw)
            self.assertEqual(message['type'], 'HISTORY')
            recovered.extend(message['items'])
        self.assertEqual(recovered, items)

    def test_empty_snapshot_still_replies_with_history(self):
        messages = list(server.history_messages([]))
        self.assertEqual([json.loads(raw) for raw in messages],
                         [{'type': 'HISTORY', 'items': []}])

    def test_unicode_budget_counts_encoded_bytes_and_keeps_extra_fields(self):
        items = [json.loads(caption(segment).payload) for segment in range(1, 8)]
        for item in items:
            item['text'] = '\u754c' * 100
        with patch.object(server, 'MAX_HISTORY_MESSAGE_BYTES', 1024):
            messages = list(server.history_messages(items))
        self.assertGreater(len(messages), 1)
        self.assertTrue(all(len(raw.encode('utf-8')) <= 1024 for raw in messages))
        self.assertEqual([item for raw in messages
                          for item in json.loads(raw)['items']], items)

    def test_caption_that_fits_alone_is_not_lost_to_history_wrapper(self):
        item = json.loads(caption(1).payload)
        size = len(json.dumps(item, separators=(',', ':')).encode('utf-8'))
        item['text'] += 'x' * (1024 - size)
        with patch.object(server, 'MAX_HISTORY_MESSAGE_BYTES', 1024):
            messages = list(server.history_messages([item]))
        self.assertEqual(json.loads(messages[0]), {'type': 'HISTORY', 'items': []})
        self.assertEqual(json.loads(messages[1]), item)
        self.assertTrue(all(len(raw.encode('utf-8')) <= 1024 for raw in messages))


class PausedClient:
    def __init__(self, fail=False):
        self.history_started = asyncio.Event()
        self.release_history = asyncio.Event()
        self.finished = asyncio.Event()
        self.messages = []
        self.fail = fail

    async def send(self, raw):
        msg = json.loads(raw)
        if msg["type"] == "HISTORY":
            self.history_started.set()
            await self.release_history.wait()
            if self.fail:
                raise ConnectionError("client disconnected during history")
        self.messages.append(msg)

    async def close(self, **kwargs):
        self.release_history.set()
        self.finished.set()

    def __aiter__(self):
        return self

    async def __anext__(self):
        await self.finished.wait()
        raise StopAsyncIteration


class HandoffTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        patch.object(server, "SESSION", server.SessionState()).start()
        patch.object(server, "CLIENTS", {}).start()
        patch.object(server.time, "time", return_value=1000).start()
        self.addCleanup(patch.stopall)

    async def test_history_then_live_without_gap_or_duplicate(self):
        await server.ingest_caption_update(caption(1))
        client = PausedClient()
        handler = asyncio.create_task(server.handle_client(client, {}))
        try:
            await asyncio.wait_for(client.history_started.wait(), 1)
            live = asyncio.create_task(server.ingest_caption_update(caption(2)))
            await asyncio.sleep(0)
            self.assertEqual(client.messages, [])
            client.release_history.set()
            await asyncio.wait_for(live, 1)
            async def delivered():
                while len(client.messages) < 2:
                    await asyncio.sleep(0)
            await asyncio.wait_for(delivered(), 1)
            self.assertEqual([m["type"] for m in client.messages],
                             ["HISTORY", "CAPTION_UPDATE"])
            self.assertEqual([m["segment"] for m in client.messages[0]["items"]], [1])
            self.assertEqual(client.messages[1]["segment"], 2)
        finally:
            client.finished.set()
            handler.cancel()
            await asyncio.gather(handler, return_exceptions=True)
        self.assertNotIn(client, server.CLIENTS)

    async def test_failed_history_send_removes_client(self):
        client = PausedClient(fail=True)
        client.release_history.set()
        with self.assertRaises(ConnectionError):
            await server.handle_client(client, {})
        self.assertNotIn(client, server.CLIENTS)

    async def test_failed_live_send_closes_connection_and_finishes_handler(self):
        class FailedLiveClient(PausedClient):
            async def send(self, raw):
                if json.loads(raw)['type'] == 'CAPTION_UPDATE':
                    raise ConnectionError('live send failed')
                await super().send(raw)

        client = FailedLiveClient()
        client.release_history.set()
        handler = asyncio.create_task(server.handle_client(client, {}))
        try:
            await asyncio.wait_for(client.history_started.wait(), 1)
            await server.ingest_caption_update(caption(1))
            await asyncio.wait_for(asyncio.gather(handler, return_exceptions=True), 1)
            self.assertTrue(client.finished.is_set())
            self.assertNotIn(client, server.CLIENTS)
        finally:
            handler.cancel()
            await asyncio.gather(handler, return_exceptions=True)

    async def test_all_history_batches_precede_live_without_loss_or_duplicate(self):
        for segment in range(1, 9):
            await server.ingest_caption_update(caption(segment))
        client = PausedClient()
        with patch.object(server, 'MAX_HISTORY_MESSAGE_BYTES', 512):
            handler = asyncio.create_task(server.handle_client(client, {}))
            live = None
            try:
                await asyncio.wait_for(client.history_started.wait(), 1)
                live = asyncio.create_task(server.ingest_caption_update(caption(9)))
                await asyncio.sleep(0)
                client.release_history.set()
                await asyncio.wait_for(live, 1)
                async def delivered():
                    while not any(m.get('segment') == 9 for m in client.messages):
                        await asyncio.sleep(0)
                await asyncio.wait_for(delivered(), 1)
                histories = client.messages[:-1]
                self.assertGreater(len(histories), 1)
                self.assertTrue(all(m['type'] == 'HISTORY' for m in histories))
                self.assertEqual([item['segment'] for m in histories for item in m['items']],
                                 list(range(1, 9)))
                self.assertEqual(client.messages[-1]['segment'], 9)
            finally:
                handler.cancel()
                if live is not None:
                    live.cancel()
                await asyncio.gather(handler, *([live] if live else []),
                                     return_exceptions=True)

    async def test_hello_clamps_windows_and_defaults_invalid_fields(self):
        with patch.object(server.time, "monotonic",
                          return_value=server.time.monotonic() - 45):
            await server.ingest_caption_update(caption(1, source_ts=955))
        for hello, expected in [({}, []), ({"history_seconds": 100}, [1]),
                                ({"history_seconds": -1}, []),
                                ({"history_seconds": None}, []),
                                ({"history_seconds": 60, "last_segment": [],
                                  "last_revision": True}, [1])]:
            with self.subTest(hello=hello):
                client = PausedClient()
                client.release_history.set()
                client.finished.set()
                await server.handle_client(client, hello)
                self.assertEqual([m["segment"] for m in client.messages[0]["items"]],
                                 expected)


class WebSocketRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_large_history_fits_real_receiver_then_continues_live(self):
        state = server.SessionState()
        for segment in range(1, 20001):
            state.apply(caption(segment, source_ts=server.time.time()))
        with patch.object(server, 'SESSION', state), patch.object(server, 'CLIENTS', {}):
            async with server.websockets.serve(server.handler, '127.0.0.1', 0) as listener:
                port = listener.sockets[0].getsockname()[1]
                async with server.websockets.connect(
                    f'ws://127.0.0.1:{port}', max_size=2**20
                ) as ws:
                    await ws.send(json.dumps({'type': 'CLIENT_HELLO',
                                              'history_seconds': 60}))
                    recovered = []
                    while len(recovered) < 20000:
                        raw = await asyncio.wait_for(ws.recv(), 5)
                        self.assertLessEqual(len(raw.encode('utf-8')), 2**20)
                        message = json.loads(raw)
                        self.assertEqual(message['type'], 'HISTORY')
                        recovered.extend(item['segment'] for item in message['items'])
                    self.assertEqual(recovered, list(range(1, 20001)))
                    await server.ingest_caption_update(caption(20001))
                    live = json.loads(await asyncio.wait_for(ws.recv(), 2))
                    self.assertEqual((live['type'], live['segment']),
                                     ('CAPTION_UPDATE', 20001))

    async def test_late_join_and_incremental_reconnect(self):
        async def receive(ws):
            return json.loads(await asyncio.wait_for(ws.recv(), 2))

        async def hello(ws, segment=0):
            await ws.send(json.dumps({
                "type": "CLIENT_HELLO", "client_id": 1, "session": "L1",
                "last_segment": segment, "last_revision": 1 if segment else 0,
                "history_seconds": 60,
            }))
            return await receive(ws)

        with patch.object(server, "SESSION", server.SessionState()), patch.object(
            server, "CLIENTS", {}
        ):
            async with server.websockets.serve(server.handler, "127.0.0.1", 0) as listener:
                port = listener.sockets[0].getsockname()[1]
                uri = f"ws://127.0.0.1:{port}"
                async with server.websockets.connect(uri) as source, \
                        server.websockets.connect(uri) as observer:
                    await source.send(json.dumps({"type": "SOURCE_HELLO", "session": "L1"}))
                    self.assertEqual(await hello(observer), {"type": "HISTORY", "items": []})
                    await source.send(caption(1, source_ts=server.time.time()).payload)
                    self.assertEqual((await receive(observer))["segment"], 1)
                    async with server.websockets.connect(uri) as late:
                        history = await hello(late)
                        self.assertEqual([m["segment"] for m in history["items"]], [1])
                        await source.send(caption(2, source_ts=server.time.time()).payload)
                        self.assertEqual((await receive(observer))["segment"], 2)
                        self.assertEqual((await receive(late))["segment"], 2)
                    await source.send(caption(3, source_ts=server.time.time()).payload)
                    self.assertEqual((await receive(observer))["segment"], 3)
                    async with server.websockets.connect(uri) as resumed:
                        history = await hello(resumed, segment=2)
                        self.assertEqual([m["segment"] for m in history["items"]], [3])
                    async with server.websockets.connect(uri) as current:
                        self.assertEqual(await hello(current, segment=3),
                                         {"type": "HISTORY", "items": []})


if __name__ == "__main__":
    unittest.main()
