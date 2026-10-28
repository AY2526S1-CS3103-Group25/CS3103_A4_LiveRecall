"""Controlled slow-reader regressions independent of OS socket buffer sizes."""

import asyncio
import json
import unittest
from unittest.mock import patch

import solution_server as server
from test_server_history import PausedClient, caption


async def until(predicate):
    async def wait():
        while not predicate():
            await asyncio.sleep(0)
    await asyncio.wait_for(wait(), 2)


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

    async def test_queue_limits_include_utf8_payload_bytes(self):
        ws = PausedClient()
        client = await self.connect(ws)
        client.MAX_PENDING = 2
        for segment in range(1, 10):
            await server.ingest_caption_update(caption(segment))
        self.assertLessEqual(len(client.pending), 2)
        self.assertLessEqual(client.pending_bytes, client.MAX_PENDING_BYTES)


if __name__ == '__main__':
    unittest.main()
