#!/usr/bin/env python3
"""Cuttable TCP proxy in front of the LiveRecall server, for disconnect demos.

Ctrl-C on the server is a poor disconnect: it also kills caption_source.py and
discards the history a reconnecting client would recover. The proxy breaks only
the browser clients' path (it takes over the port the browser already dials),
so the server, its history, and the source connection all survive.

Port layout and the stdin command reference (cut / down / up / freeze / thaw /
status / quit) live in DEMO_GUIDE.md under "Disconnect / recovery demo".
"""

from __future__ import annotations

import argparse
import asyncio
import sys

BUFFER_BYTES = 65536


def log(*args) -> None:
    print(*args, flush=True)


class Link:
    """A client connection and the upstream connection paired with it."""

    def __init__(self, client_w: asyncio.StreamWriter, server_w: asyncio.StreamWriter,
                 pumps: list[asyncio.Task]) -> None:
        self.writers = (client_w, server_w)
        self.pumps = pumps

    def close(self) -> None:
        # Cancel before closing: a pump held at the freeze gate is not
        # reading, so closing its socket alone would never wake it and the
        # link would leak out of the live set.
        for pump in self.pumps:
            pump.cancel()
        for writer in self.writers:
            if not writer.is_closing():
                writer.close()


class CutProxy:
    def __init__(self, listen_port: int, target_port: int) -> None:
        self.listen_port = listen_port
        self.target_port = target_port
        self.links: list[Link] = []
        self.relaying = asyncio.Event()
        self.relaying.set()
        self.server: asyncio.Server | None = None

    async def _pump(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                # Gate before reading, not after: while frozen, bytes stay in
                # the kernel receive buffer, which is how a dead path looks.
                await self.relaying.wait()
                data = await reader.read(BUFFER_BYTES)
                if not data:
                    break
                # Gate again: a read that was already in flight when
                # `freeze` was typed must not deliver its chunk either.
                await self.relaying.wait()
                writer.write(data)
                await writer.drain()
        except ConnectionError:
            pass
        finally:
            if not writer.is_closing():
                writer.close()

    async def _handle(self, client_r: asyncio.StreamReader, client_w: asyncio.StreamWriter) -> None:
        try:
            server_r, server_w = await asyncio.open_connection("127.0.0.1", self.target_port)
        except OSError as exc:
            log(f"[proxy] upstream connect failed: {exc}")
            client_w.close()
            return
        link = Link(client_w, server_w, [
            asyncio.create_task(self._pump(client_r, server_w)),
            asyncio.create_task(self._pump(server_r, client_w)),
        ])
        self.links.append(link)
        log(f"[proxy] connection open; live={len(self.links)}")
        try:
            await asyncio.gather(*link.pumps, return_exceptions=True)
        finally:
            self.links.remove(link)
            link.close()
            log(f"[proxy] connection closed; live={len(self.links)}")

    def cut(self) -> int:
        count = len(self.links)
        for link in list(self.links):
            link.close()
        return count

    async def up(self) -> None:
        if self.server is not None:
            return
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", self.listen_port)
        log(f"[proxy] listening on 127.0.0.1:{self.listen_port}"
            f" -> 127.0.0.1:{self.target_port}")

    async def down(self) -> None:
        # Cut before awaiting wait_closed(): since 3.12 it waits for every
        # live connection handler to finish, so closing the listener first
        # would block here until the relayed connections ended on their own.
        count = self.cut()
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()
            self.server = None
        log(f"[proxy] not listening; cut {count} connection(s)")

    def status(self) -> None:
        mode = "relaying" if self.relaying.is_set() else "frozen"
        listening = "listening" if self.server is not None else "not listening"
        log(f"[proxy] {mode}; {listening}; live={len(self.links)}")

    async def run_commands(self) -> bool:
        """Read commands until `quit` (True) or end of input (False).

        The read runs in a worker thread rather than through
        loop.connect_read_pipe, which does not reliably deliver lines from a
        terminal on macOS.
        """
        while True:
            line = await asyncio.to_thread(sys.stdin.readline)
            if not line:
                return False
            command = line.strip().lower()
            if command == "cut":
                log(f"[proxy] cut {self.cut()} connection(s)")
            elif command == "down":
                await self.down()
            elif command == "up":
                await self.up()
            elif command == "freeze":
                self.relaying.clear()
                log("[proxy] frozen: sockets open, nothing relayed")
            elif command == "thaw":
                self.relaying.set()
                log("[proxy] relaying again")
            elif command == "status":
                self.status()
            elif command in {"quit", "exit"}:
                return True
            elif command:
                log(f"[proxy] unknown command: {command}")


async def main_async(listen_port: int, target_port: int) -> None:
    proxy = CutProxy(listen_port, target_port)
    await proxy.up()
    if not await proxy.run_commands():
        # Nothing more will be typed (nohup, a closed pipe, CI): keep relaying
        # until the process is killed, which is itself the cut in that mode.
        log("[proxy] no more input; relaying until killed")
        await asyncio.Future()
    await proxy.down()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--listen", type=int, default=8765,
                        help="Port the browser clients dial")
    parser.add_argument("--target", type=int, default=8799,
                        help="Port solution_server.py is listening on")
    args = parser.parse_args()
    if args.listen == args.target:
        parser.error("--listen and --target must differ")
    try:
        asyncio.run(main_async(args.listen, args.target))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
