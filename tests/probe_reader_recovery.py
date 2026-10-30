"""Probe slow-reader recovery without changing the instructor harness.

Run from the repository root:
    ./venv/bin/python tests/probe_reader_recovery.py 4
    ./venv/bin/python tests/probe_reader_recovery.py 10

The argument is the slow period in seconds. JSON results go under the ignored
results/reader-recovery directory. This experiment takes about 37 seconds.
"""
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import websockets
import solution_server as server


async def main():
    removal = float(sys.argv[1])
    profiles = {'normal': 16, 'slow_q4': 4, 'slow_q16': 16, 'slow_q64': 64}
    ready = {name: asyncio.Event() for name in profiles}
    seen = {name: set() for name in profiles}
    revisions = {name: set() for name in profiles}
    states = {}
    samples = {name: [] for name in profiles if name != 'normal'}
    generated, emitted_at, errors = set(), {}, []
    started = time.monotonic()
    segments = 240

    async with websockets.serve(server.handler, '127.0.0.1', 0,
                                write_limit=16 * 1024, close_timeout=5,
                                ping_interval=20, ping_timeout=20) as listener:
        uri = f'ws://127.0.0.1:{listener.sockets[0].getsockname()[1]}'

        async def receive(name, queue):
            async with websockets.connect(uri, max_queue=queue) as ws:
                await ws.send(json.dumps(dict(type='CLIENT_HELLO', client_id=name,
                                             history_seconds=0)))
                async for raw in ws:
                    msg = json.loads(raw)
                    if msg['type'] == 'HISTORY':
                        states[name] = next(c for c in server.CLIENTS.values()
                                            if c.ws.remote_address[1] == ws.local_address[1])
                        ready[name].set()
                        continue
                    pair = (msg['segment'], msg['revision'])
                    if pair in revisions[name]:
                        errors.append((name, 'duplicate', pair))
                    if any(s == pair[0] and r > pair[1] for s, r in revisions[name]):
                        errors.append((name, 'stale', pair))
                    revisions[name].add(pair)
                    if msg['final']:
                        seen[name].add(msg['segment'])
                    if name != 'normal' and time.monotonic() - started < removal:
                        await asyncio.sleep(0.1)

        readers = [asyncio.create_task(receive(name, queue))
                   for name, queue in profiles.items()]
        try:
            await asyncio.gather(*(event.wait() for event in ready.values()))
            started = time.monotonic()

            async def observe():
                while True:
                    now = time.monotonic() - started
                    for name, values in samples.items():
                        client = states[name]
                        values.append(dict(elapsed=now,
                                           final_gap=len(generated - seen[name]),
                                           hold=client.provisional_hold,
                                           pending=len(client.pending),
                                           pending_bytes=client.pending_bytes))
                        assert len(client.pending) <= client.MAX_PENDING
                        assert client.pending_bytes <= client.MAX_PENDING_BYTES
                    await asyncio.sleep(0.02)

            observer = asyncio.create_task(observe())
            try:
                for segment in range(1, segments + 1):
                    emitted_at[segment] = time.monotonic() - started
                    for revision in range(1, 4):
                        msg = dict(type='CAPTION_UPDATE', session='L1',
                                   segment=segment, revision=revision, final=revision == 3,
                                   source_ts=time.time(), text=f'caption {segment}')
                        if msg['final']:
                            generated.add(segment)
                        await server.ingest_caption_update(
                            server.parse_caption_update(msg, json.dumps(msg)))
                        await asyncio.sleep(0.04 if revision < 3 else 0.07)
                deadline = time.monotonic() + 5
                while any(len(items) != segments for items in seen.values()):
                    assert time.monotonic() < deadline, 'final recovery timed out'
                    await asyncio.sleep(0.02)
            finally:
                observer.cancel()
                await asyncio.gather(observer, return_exceptions=True)
                if not observer.cancelled():
                    observer.result()

            tail = {s for s, at in emitted_at.items()
                    if at >= max(emitted_at.values()) - 5}
            results = {}
            for name, values in samples.items():
                post = [s for s in values if s['elapsed'] >= removal]
                held = [i for i, s in enumerate(post) if s['hold'] > 0]
                stable = next((s['elapsed'] - removal for s in post if s['hold'] == 0), None)
                if held:
                    stable = (post[held[-1] + 1]['elapsed'] - removal
                              if held[-1] + 1 < len(post) else None)
                results[name] = dict(
                    receiver_queue=profiles[name],
                    fragmented_checkpoints=states[name].fragment_checkpoints,
                    finals=len(seen[name]),
                    gap_at_removal=min(values, key=lambda s: abs(s['elapsed'] - removal))['final_gap'],
                    catch_up_seconds=next((s['elapsed'] - removal for s in post
                                           if s['final_gap'] == 0), None),
                    sustained_full_updates_seconds=stable,
                    tail_update_coverage_pct=100 * sum(s in tail for s, r in revisions[name]) / (3 * len(tail)),
                    max_pending=max(s['pending'] for s in values),
                    max_pending_bytes=max(s['pending_bytes'] for s in values))
                after = [s for s, at in emitted_at.items() if at >= removal]
                missing = [s for s in after if any((s, r) not in revisions[name]
                                                  for r in range(1, 4))]
                suffix = [s for s in after if not missing or s > max(missing)]
                results[name]['full_revision_coverage_after_removal_seconds'] = (
                    emitted_at[min(suffix)] - removal if suffix else None)
            result = dict(removal_seconds=removal, source_segment_period_seconds=0.15,
                          slow_read_seconds=0.1, websockets=websockets.__version__,
                          expected_finals=segments, normal_finals=len(seen['normal']),
                          normal_updates=len(revisions['normal']), expected_updates=3 * segments,
                          errors=errors, profiles=results, samples=samples)
            output = Path(__file__).resolve().parents[1] / 'results' / 'reader-recovery'
            output.mkdir(parents=True, exist_ok=True)
            (output / f'recovery_matrix_{removal:g}.json').write_text(
                json.dumps(result, indent=2))
            print(json.dumps({k: v for k, v in result.items() if k != 'samples'}, indent=2))
        finally:
            for task in readers:
                task.cancel()
            await asyncio.gather(*readers, return_exceptions=True)


if __name__ == '__main__':
    asyncio.run(main())
