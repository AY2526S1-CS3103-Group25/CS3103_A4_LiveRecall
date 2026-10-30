"""Controlled slow-reader regressions independent of OS socket buffer sizes."""

import asyncio
import json
import random
import unittest
from unittest.mock import patch

import solution_server as server
from test_server_history import PausedClient, caption, reassemble


async def until(predicate):
    async def wait():
        while not predicate():
            await asyncio.sleep(0)
    await asyncio.wait_for(wait(), 2)


class PausedLiveClient(PausedClient):
    def __init__(self):
        super().__init__()
        self.release_history.set()
        self.live_started = asyncio.Event()
        self.release_live = asyncio.Event()

    async def send(self, raw):
        raw = reassemble(raw)
        if json.loads(raw)['type'] == 'CAPTION_UPDATE' and not self.live_started.is_set():
            self.live_started.set()
            await self.release_live.wait()
        await super().send(raw)

    async def close(self, **kwargs):
        self.release_live.set()
        await super().close(**kwargs)


class PausedPongClient(PausedClient):
    def __init__(self):
        super().__init__()
        self.release_history.set()
        self.pause_pongs = False
        self.pong_started = asyncio.Event()
        self.pong = None

    async def ping(self):
        if not self.pause_pongs:
            return await super().ping()
        self.pong = asyncio.get_running_loop().create_future()
        self.pong_started.set()
        return self.pong

    async def close(self, **kwargs):
        if self.pong is not None and not self.pong.done():
            self.pong.cancel()
        await super().close(**kwargs)


class DeliveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        patch.object(server, "SESSION", server.SessionState()).start()
        patch.object(server, "CLIENTS", {}).start()
        self.addCleanup(patch.stopall)
        self.handlers = []

    async def asyncTearDown(self):
        for task in self.handlers:
            task.cancel()
        await asyncio.gather(*self.handlers, return_exceptions=True)

    async def connect(self, ws, **hello):
        task = asyncio.create_task(server.handle_client(ws, hello))
        self.handlers.append(task)
        await until(lambda: ws in server.CLIENTS)
        return server.CLIENTS[ws]

    async def test_blocked_history_does_not_stall_source_or_normal_client(self):
        slow, normal = PausedClient(), PausedClient()
        normal.release_history.set()
        await self.connect(slow)
        await self.connect(normal)
        await slow.history_started.wait()
        for segment in range(1, 6):
            await server.ingest_caption_update(caption(segment))
            await asyncio.sleep(0)
        await until(lambda: len(normal.messages) == 6)
        self.assertEqual([m['segment'] for m in normal.messages[1:]], list(range(1, 6)))
        self.assertEqual(slow.messages, [])
        slow.release_history.set()
        await until(lambda: len(slow.messages) == 6)
        self.assertEqual(slow.messages[0]['type'], 'HISTORY')

    async def test_normal_mode_preserves_all_revisions(self):
        ws = PausedClient()
        ws.release_history.set()
        await self.connect(ws)
        for segment in range(1, 5):
            for revision in range(1, 4):
                await server.ingest_caption_update(caption(segment, revision, revision == 3))
                await asyncio.sleep(0)
        await until(lambda: len(ws.messages) == 13)
        self.assertEqual([(m['segment'], m['revision']) for m in ws.messages[1:]],
                         [(s, r) for s in range(1, 5) for r in range(1, 4)])

    async def test_delayed_pong_isolated_and_sent_final_cursor_already_advanced(self):
        slow, normal = PausedPongClient(), PausedClient()
        slow.request_headers = {'User-Agent': 'Python/3.11 websockets/17.3'}
        normal.release_history.set()
        client = await self.connect(slow)
        await self.connect(normal)
        await until(lambda: len(slow.messages) == 1)
        client.next_checkpoint = 0
        slow.pause_pongs = True
        await server.ingest_caption_update(caption(1))
        await slow.pong_started.wait()
        self.assertEqual(client.final_cursor, 1)
        for segment in range(2, 7):
            await server.ingest_caption_update(caption(segment))
        await until(lambda: len(normal.messages) == 7)
        await until(lambda: client.provisional_hold > 0)
        slow.pause_pongs = False
        slow.pong.set_result(0)
        await until(lambda: len(slow.messages) == 7)
        self.assertEqual([m['segment'] for m in slow.messages[1:]], list(range(1, 7)))
        self.assertTrue(client.lagging)
        self.assertGreater(client.provisional_hold, 0)

    async def test_final_supersedes_held_provisionals_without_waiting_for_deadline(self):
        ws = PausedPongClient()
        ws.request_headers = {'User-Agent': 'Python/3.11 websockets/17.3'}
        client = await self.connect(ws)
        await until(lambda: len(ws.messages) == 1)
        client._peer_lag(1)
        await server.ingest_caption_update(caption(1, 1, False))
        await asyncio.sleep(0)
        await server.ingest_caption_update(caption(1, 2, False))
        self.assertEqual(len(ws.messages), 1)
        await server.ingest_caption_update(caption(1, 3, True))
        await until(lambda: len(ws.messages) == 2)
        self.assertEqual((ws.messages[-1]['revision'], ws.messages[-1]['final']), (3, True))

    async def test_held_active_revision_delivered_without_further_source_input(self):
        ws = PausedPongClient()
        ws.request_headers = {'User-Agent': 'Python/3.11 websockets/17.3'}
        client = await self.connect(ws)
        await until(lambda: len(ws.messages) == 1)
        client._peer_lag(0.03)
        await server.ingest_caption_update(caption(1, 1, False))
        first_deadline = client.pending[0][2]
        await server.ingest_caption_update(caption(1, 2, False))
        self.assertEqual(client.pending[0][2], first_deadline)
        await until(lambda: len(ws.messages) == 2)
        self.assertEqual(ws.messages[-1]['revision'], 2)

    async def test_finals_bypass_a_different_held_active_segment_in_receipt_order(self):
        ws = PausedPongClient()
        ws.request_headers = {'User-Agent': 'Python/3.11 websockets/17.3'}
        client = await self.connect(ws)
        await until(lambda: len(ws.messages) == 1)
        client._peer_lag(1)
        await server.ingest_caption_update(caption(1, final=False))
        await asyncio.sleep(0)
        await server.ingest_caption_update(caption(3))
        await server.ingest_caption_update(caption(2))
        await until(lambda: len(ws.messages) == 3)
        self.assertEqual([m['segment'] for m in ws.messages[1:]], [3, 2])
        self.assertEqual([entry[0].segment for entry in client.pending], [1])
        client.provisional_hold = 0
        client.ready.set()
        await until(lambda: len(ws.messages) == 4)
        self.assertEqual(ws.messages[-1]['segment'], 1)

    async def test_sent_final_can_expire_while_waiting_for_pong_without_replay(self):
        ws = PausedPongClient()
        client = await self.connect(ws)
        await until(lambda: len(ws.messages) == 1)
        client.MAX_PENDING = 1
        client.next_checkpoint = 0
        ws.pause_pongs = True
        now = server.time.monotonic()
        with patch.object(server.time, 'monotonic', return_value=now - 71):
            await server.ingest_caption_update(caption(1))
        await ws.pong_started.wait()
        server.SESSION.prune_history()
        for segment in range(2, 5):
            await server.ingest_caption_update(caption(segment))
        self.assertTrue(client.catch_up)
        self.assertEqual(server.SESSION.expired_sequence, 1)
        self.assertEqual(client.final_cursor, 1)
        self.assertFalse(client.failed.is_set())
        ws.pause_pongs = False
        ws.pong.set_result(0)
        await until(lambda: len(ws.messages) == 5)
        self.assertEqual([m['segment'] for m in ws.messages[1:]], [1, 2, 3, 4])

    async def test_initial_path_rtt_is_measured_before_classifying_congestion(self):
        ws = PausedPongClient()
        ws.pause_pongs = True
        client = server.ClientState(ws)
        client.LAG_SECONDS = 0.001
        checkpoint = asyncio.create_task(client._checkpoint(0))
        try:
            await ws.pong_started.wait()
            await asyncio.sleep(0.01)
            ws.pong.set_result(0)
            await checkpoint
            self.assertGreater(client.checkpoint_baseline, client.LAG_SECONDS)
            self.assertEqual(client.provisional_hold, 0)
        finally:
            await ws.close()
            checkpoint.cancel()
            await asyncio.gather(checkpoint, return_exceptions=True)

    async def test_peer_lag_hysteresis_requires_sustained_healthy_checkpoints(self):
        client = server.ClientState(PausedClient(), fragment_checkpoints=True)
        await client._checkpoint(0)
        client._peer_lag(0.4)
        hold = client.provisional_hold
        async def checkpoint():
            client.next_checkpoint = 0
            await client._checkpoint(1)
        # A few immediate checkpoints don't demonstrate sustained recovery.
        for _ in range(4):
            await checkpoint()
        self.assertEqual(client.provisional_hold, hold)
        client.peer_lag_until = 0
        for _ in range(2):
            await checkpoint()
        self.assertEqual(client.provisional_hold, hold)
        for _ in range(3):
            await checkpoint()
        self.assertEqual(client.provisional_hold, hold)
        client.healthy_since -= client.recovery_window
        await checkpoint()
        self.assertEqual(client.provisional_hold, 0)
        self.assertGreater(client.last_recovery, 0)
        client._peer_lag(0.4)
        self.assertEqual(client.recovery_penalty, 4)
        self.assertGreater(client.provisional_hold, 0)

    async def test_closing_during_checkpoint_releases_pending_pong(self):
        ws = PausedPongClient()
        client = await self.connect(ws)
        await until(lambda: len(ws.messages) == 1)
        client.next_checkpoint = 0
        ws.pause_pongs = True
        await server.ingest_caption_update(caption(1))
        await ws.pong_started.wait()
        await ws.close()
        await until(lambda: ws not in server.CLIENTS)
        self.assertTrue(ws.pong.done())

    async def test_probation_checks_each_message_before_resuming_periodic_probes(self):
        ws = PausedClient()
        client = server.ClientState(ws, fragment_checkpoints=True)
        await client._checkpoint(0)
        client.last_recovery = server.time.monotonic()
        client.last_peer_delay = 1
        client.next_checkpoint = server.time.monotonic() + 100
        with patch.object(ws, 'ping', wraps=ws.ping) as ping:
            await client._checkpoint(1)
            self.assertEqual(ping.await_count, 1)
            client.last_recovery -= 10
            client.next_checkpoint = server.time.monotonic() + 100
            await client._checkpoint(1)
            self.assertEqual(ping.await_count, 1)

    async def test_relapse_backoff_is_bounded_and_resets_after_stable_full_delivery(self):
        client = server.ClientState(PausedClient(), fragment_checkpoints=True)
        for _ in range(6):
            client.provisional_hold = 0
            client.last_recovery = server.time.monotonic()
            client.last_peer_delay = 1
            client._peer_lag(1)
        self.assertEqual(client.recovery_penalty, 4)
        client.provisional_hold = 0
        client.last_recovery -= 10
        client._peer_lag(1)
        self.assertEqual(client.recovery_penalty, 1)

    async def test_recovery_fragments_reassemble_identical_unicode_json(self):
        async with server.websockets.serve(server.handler, '127.0.0.1', 0) as listener:
            uri = f'ws://127.0.0.1:{listener.sockets[0].getsockname()[1]}'
            async with server.websockets.connect(uri, max_size=2**20) as ws:
                await ws.send(json.dumps(dict(type='CLIENT_HELLO', history_seconds=0)))
                self.assertEqual(json.loads(await ws.recv())['type'], 'HISTORY')
                client = next(iter(server.CLIENTS.values()))
                fixed_receiver = tuple(map(int, server.websockets.__version__.split('.')[:2])) >= (17, 3)
                self.assertEqual(client.fragment_checkpoints, fixed_receiver)
                client.fragment_checkpoints = True
                client._peer_lag(0.2)
                msg = json.loads(caption(1).payload)
                msg['text'] = '\u754c\U0001f642' * 1000
                raw = json.dumps(msg, ensure_ascii=False)
                await server.ingest_caption_update(server.parse_caption_update(msg, raw))
                fragments = [part async for part in ws.recv_streaming()]
                self.assertGreater(len(fragments), 1)
                self.assertEqual(''.join(fragments), raw)
                client.provisional_hold = 0
                client.last_recovery = 0
                client.lagging = False
                await server.ingest_caption_update(caption(2))
                fragments = [part async for part in ws.recv_streaming()]
                self.assertEqual(len(fragments), 1)
                self.assertEqual(json.loads(fragments[0])['segment'], 2)
                client.next_checkpoint = 0
                await server.ingest_caption_update(caption(3))
                fragments = [part async for part in ws.recv_streaming()]
                self.assertGreater(len(fragments), 1)
                self.assertEqual(json.loads(''.join(fragments))['segment'], 3)

    async def test_checkpoint_count_and_byte_caps_override_periodic_deadline(self):
        ws = PausedClient()
        client = server.ClientState(ws)
        with patch.object(ws, 'ping', wraps=ws.ping) as ping:
            await client._checkpoint(0)
            client.next_checkpoint = server.time.monotonic() + 100
            for _ in range(client.CHECKPOINT_MESSAGES - 1):
                await client._checkpoint(1)
            self.assertEqual(ping.await_count, 1)
            await client._checkpoint(1)
            self.assertEqual(ping.await_count, 2)
            client.next_checkpoint = server.time.monotonic() + 100
            await client._checkpoint(client.CHECKPOINT_BYTES)
            self.assertEqual(ping.await_count, 3)

    async def test_fragmentation_is_gated_by_fixed_receiver_version(self):
        for agent, enabled in [
            ('Python/3.11 websockets/16.1.1', False),
            ('Python/3.11 websockets/17.2', False),
            ('Python/3.11 websockets/17.3', True),
            ('Python/3.11 websockets/17.3.1', True),
            ('Python/3.11 websockets/17.3rc1', False),
            ('Mozilla/5.0', False),
        ]:
            with self.subTest(agent=agent):
                ws = PausedClient()
                ws.request_headers = {'User-Agent': agent}
                ws.release_history.set()
                client = await self.connect(ws)
                self.assertEqual(client.fragment_checkpoints, enabled)

    async def test_older_receivers_keep_unfragmented_gradual_recovery(self):
        ws = PausedClient()
        client = server.ClientState(ws)
        await client._checkpoint(0)
        client._peer_lag(0.4)
        self.assertEqual(client.provisional_hold, 0.4)
        client.next_checkpoint = 0
        with patch.object(ws, 'send', wraps=ws.send) as send:
            await client._send(caption(1).payload)
            self.assertIsInstance(send.call_args.args[0], str)
        client.peer_lag_until = 0
        client.healthy_since = server.time.monotonic() - client.recovery_window
        client.healthy_checkpoints = 2
        await client._checkpoint(1)
        self.assertEqual(client.provisional_hold, 0.2)

    async def test_blocked_later_history_batch_isolated_with_bounded_live_catch_up(self):
        class PausedSecondBatch(PausedClient):
            def __init__(self):
                super().__init__()
                self.release_history.set()
                self.batches = 0
                self.blocked = asyncio.Event()
                self.release_second = asyncio.Event()

            async def send(self, raw):
                raw = reassemble(raw)
                if json.loads(raw)['type'] == 'HISTORY':
                    self.batches += 1
                    if self.batches == 2:
                        self.blocked.set()
                        await self.release_second.wait()
                await super().send(raw)

            async def close(self, **kwargs):
                self.release_second.set()
                await super().close(**kwargs)

        for segment in range(1, 6):
            await server.ingest_caption_update(caption(segment))
        slow, normal = PausedSecondBatch(), PausedClient()
        normal.release_history.set()
        with patch.object(server, 'MAX_HISTORY_MESSAGE_BYTES', 512):
            client = await self.connect(slow, history_seconds=60)
            client.MAX_PENDING = 4
            await self.connect(normal, history_seconds=0)
            await asyncio.wait_for(slow.blocked.wait(), 1)
            for segment in range(6, 41):
                await server.ingest_caption_update(caption(segment))
                self.assertLessEqual(len(client.pending), client.MAX_PENDING)
                self.assertLessEqual(client.pending_bytes, client.MAX_PENDING_BYTES)
            await until(lambda: len(normal.messages) == 36)
            self.assertEqual([m['segment'] for m in normal.messages[1:]],
                             list(range(6, 41)))
            self.assertTrue(client.catch_up)
            slow.release_second.set()
            await until(lambda: any(m.get('segment') == 40 for m in slow.messages))
            finals = [item['segment'] for m in slow.messages
                      for item in (m['items'] if m['type'] == 'HISTORY' else [m])]
            self.assertEqual(finals, list(range(1, 41)))

    async def test_queue_limits_include_utf8_payload_bytes(self):
        ws = PausedClient()
        client = await self.connect(ws)
        client.MAX_PENDING = 2
        for segment in range(1, 10):
            await server.ingest_caption_update(caption(segment))
        self.assertLessEqual(len(client.pending), 2)
        self.assertLessEqual(client.pending_bytes, client.MAX_PENDING_BYTES)

    async def test_final_overflow_recovers_all_finals_and_newest_active(self):
        ws = PausedClient()
        client = await self.connect(ws)
        client.MAX_PENDING = 2
        await ws.history_started.wait()
        for segment in [3, 1, 2, 4, 5]:
            await server.ingest_caption_update(caption(segment))
        for revision in range(1, 8):
            await server.ingest_caption_update(caption(6, revision, False))
        self.assertTrue(client.catch_up)
        self.assertEqual(client.pending_bytes, 0)
        ws.release_history.set()
        await until(lambda: len(ws.messages) == 7)
        self.assertEqual([m['segment'] for m in ws.messages[1:6]], [3, 1, 2, 4, 5])
        self.assertEqual((ws.messages[-1]['segment'], ws.messages[-1]['revision']), (6, 7))
        await until(lambda: not client.catch_up)
        self.assertFalse(client.lagging)
        for revision in range(8, 11):
            await server.ingest_caption_update(caption(6, revision, revision == 10))
            await asyncio.sleep(0)
        await until(lambda: len(ws.messages) == 10)
        self.assertEqual([m['revision'] for m in ws.messages[-3:]], [8, 9, 10])

    async def test_lag_coalesces_pending_provisionals_without_losing_final(self):
        ws = PausedClient()
        client = await self.connect(ws)
        client.MAX_PENDING = 4
        for revision in range(1, 21):
            await server.ingest_caption_update(caption(1, revision, False))
        self.assertTrue(client.lagging)
        self.assertEqual([entry[0].revision for entry in client.pending], [20])
        await server.ingest_caption_update(caption(1, 21))
        self.assertEqual([(entry[0].revision, entry[0].final) for entry in client.pending], [(21, True)])
        self.assertGreater(client.coalesced, 0)
        ws.release_history.set()
        await until(lambda: len(ws.messages) == 2)
        self.assertTrue(ws.messages[-1]['final'])

    async def test_catch_up_does_not_replay_context_excluded_by_hello(self):
        for segment in range(1, 4):
            await server.ingest_caption_update(caption(segment))
        ws = PausedClient()
        client = await self.connect(ws, history_seconds=0)
        client.MAX_PENDING = 1
        for segment in range(4, 9):
            await server.ingest_caption_update(caption(segment))
        ws.release_history.set()
        await until(lambda: len(ws.messages) == 6)
        self.assertEqual(ws.messages[0]['items'], [])
        self.assertEqual([m['segment'] for m in ws.messages[1:]], list(range(4, 9)))

    async def test_valid_oversized_utf8_caption_uses_single_frame_catch_up(self):
        ws = PausedClient()
        client = await self.connect(ws)
        client.MAX_PENDING_BYTES = 100
        msg = json.loads(caption(1, final=False).payload)
        msg['text'] = '\u754c' * 1000
        raw = json.dumps(msg, ensure_ascii=False)
        self.assertGreater(len(raw.encode('utf-8')), client.MAX_PENDING_BYTES)
        await server.ingest_caption_update(server.parse_caption_update(msg, raw))
        self.assertEqual(client.pending_bytes, 0)
        self.assertTrue(client.catch_up)
        ws.release_history.set()
        await until(lambda: len(ws.messages) == 2)
        self.assertEqual(ws.messages[-1]['text'], msg['text'])

    async def test_expired_final_window_closes_instead_of_silently_skipping(self):
        ws = PausedClient()
        client = await self.connect(ws)
        client.MAX_PENDING = 1
        with patch.object(server.time, 'monotonic', return_value=100) as clock:
            await server.ingest_caption_update(caption(1))
            clock.return_value = 180
            await server.ingest_caption_update(caption(2))
            client.check_recovery()
        self.assertTrue(client.failed.is_set())
        self.assertIn('expired', client.failure_reason)
        await until(lambda: ws not in server.CLIENTS)

    async def test_failure_reason_preserves_the_first_cause(self):
        client = server.ClientState(PausedClient())
        client.fail('Final recovery window expired; reconnect')
        server.SESSION.on_source_done()
        server.SESSION.on_source_connect()
        client.check_recovery()
        self.assertEqual(client.failure_reason,
                         'Final recovery window expired; reconnect')

    async def test_stream_reset_closes_old_delivery_state(self):
        ws = PausedClient()
        client = await self.connect(ws)
        await server.ingest_caption_update(caption(1))
        server.SESSION.on_source_done()
        server.SESSION.on_source_connect()
        await server.ingest_caption_update(caption(1, final=False))
        self.assertTrue(client.failed.is_set())
        await until(lambda: ws not in server.CLIENTS)

    async def test_blocked_live_final_is_not_replayed_during_catch_up(self):
        slow, normal = PausedLiveClient(), PausedClient()
        normal.release_history.set()
        client = await self.connect(slow)
        client.MAX_PENDING = 2
        await self.connect(normal)
        await server.ingest_caption_update(caption(1))
        await slow.live_started.wait()
        for segment in range(2, 11):
            await server.ingest_caption_update(caption(segment))
        await until(lambda: len(normal.messages) == 11)
        self.assertTrue(client.catch_up)
        self.assertEqual(len(slow.messages), 1)
        slow.release_live.set()
        await until(lambda: len(slow.messages) == 11)
        self.assertEqual([m['segment'] for m in slow.messages[1:]], list(range(1, 11)))

    async def test_blocked_send_enters_lag_mode_by_elapsed_time(self):
        ws = PausedLiveClient()
        client = await self.connect(ws)
        await server.ingest_caption_update(caption(1, final=False))
        await ws.live_started.wait()
        # No timing sleeps: emulate 200 ms spent waiting in ws.send().
        client.send_started -= 0.200
        for revision in [2, 3, 4]:
            await server.ingest_caption_update(caption(1, revision, False))
        self.assertTrue(client.lagging)
        self.assertEqual([entry[0].revision for entry in client.pending], [4])
        ws.release_live.set()
        await until(lambda: len(ws.messages) == 3)
        self.assertEqual([m['revision'] for m in ws.messages[1:]], [1, 4])

    async def test_byte_limit_recovers_finals_without_count_overflow(self):
        ws = PausedClient()
        client = await self.connect(ws)
        size = len(caption(1).payload.encode('utf-8'))
        client.MAX_PENDING_BYTES = size * 2
        for segment in range(1, 20):
            await server.ingest_caption_update(caption(segment))
            self.assertLessEqual(client.pending_bytes, client.MAX_PENDING_BYTES)
            self.assertLessEqual(len(client.pending), client.MAX_PENDING)
        self.assertTrue(client.catch_up)
        ws.release_history.set()
        await until(lambda: len(ws.messages) == 20)
        self.assertEqual([m['segment'] for m in ws.messages[1:]], list(range(1, 20)))

    async def test_pruned_in_flight_final_is_still_safe_to_complete(self):
        ws = PausedLiveClient()
        client = await self.connect(ws)
        client.MAX_PENDING = 1
        now = server.time.monotonic()
        with patch.object(server.time, 'monotonic', return_value=now - 71):
            await server.ingest_caption_update(caption(1))
        await ws.live_started.wait()
        with patch.object(server.time, 'monotonic', return_value=now - 61):
            await server.ingest_caption_update(caption(2))
            await server.ingest_caption_update(caption(3))
        server.SESSION.prune_history()
        client.check_recovery()
        self.assertFalse(client.failed.is_set())
        ws.release_live.set()
        await until(lambda: len(ws.messages) == 4)
        self.assertEqual([m['segment'] for m in ws.messages[1:]], [1, 2, 3])

    async def test_bursty_stream_preserves_finals_and_never_regresses(self):
        rng = random.Random(3103)
        ws = PausedClient()
        ws.release_history.set()
        client = await self.connect(ws)
        client.MAX_PENDING = 4
        client.MAX_PENDING_BYTES = 1000
        expected = {}
        for segment in range(1, 101):
            final_revision = rng.randint(3, 30)
            expected[segment] = final_revision
            for revision in range(1, final_revision + 1):
                await server.ingest_caption_update(caption(segment, revision, revision == final_revision))
                self.assertLessEqual(len(client.pending), client.MAX_PENDING)
                self.assertLessEqual(client.pending_bytes, client.MAX_PENDING_BYTES)
                if rng.random() < 0.1:
                    await asyncio.sleep(0)
        await until(lambda: sum(m.get('final', False) for m in ws.messages) == 100)
        finals = [m for m in ws.messages if m.get('final')]
        self.assertEqual({m['segment']: m['revision'] for m in finals}, expected)
        self.assertEqual(len(finals), 100)
        revisions = {}
        for msg in ws.messages[1:]:
            previous = revisions.get(msg['segment'], 0)
            self.assertGreaterEqual(msg['revision'], previous)
            revisions[msg['segment']] = msg['revision']


if __name__ == '__main__':
    unittest.main()
