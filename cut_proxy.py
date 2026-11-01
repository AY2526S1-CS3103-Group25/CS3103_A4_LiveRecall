#!/usr/bin/env python3
"""Cuttable TCP proxy in front of the LiveRecall server, for disconnect demos.

Ctrl-C on the server is a poor disconnect: it also kills caption_source.py
(the starter source has no reconnect) and discards the server's recent
history, so there is nothing left for a reconnecting client to recover. This
proxy breaks only the browser clients' path. The server process, its history,
and the source connection all survive, which is what makes recovery visible.

The proxy takes over the port the browser already dials, so client/app.js
needs no change:

  python solution_server.py --port 8799                  # real server
  python cut_proxy.py --listen 8765 --target 8799        # the cuttable path
  python -m http.server 8000 --directory client
  python caption_source.py --uri ws://127.0.0.1:8799     # source bypasses it
  browser -> http://127.0.0.1:8000  (dials ws://127.0.0.1:8765)

Commands typed at the proxy's terminal:

  cut     close live connections; clients reconnect immediately
  down    close them and stop listening, so redials are refused as well
  up      listen again; clients reconnect and recover what they missed
  freeze  keep sockets open but relay nothing, which is a silently dead path:
          the tab still reports "connected" because no close ever arrives
  thaw    resume relaying
  status  print mode and live connection count
  quit    exit

Use `down`, wait, then `up` for an outage long enough that finalized captions
accumulate; `cut` alone only exercises the reconnect, not the recovery.

Once stdin reaches end of input (nohup, a closed pipe, CI) the proxy just
relays until it is killed.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

BUFFER_BYTES = 65536


class CutProxy:
    def __init__(self, listen_port: int, target_port: int) -> None:
        self.listen_port = listen_port
        self.target_port = target_port
        self.pairs: list[tuple[asyncio.StreamWriter, asyncio.StreamWriter]] = []
        self.relaying = asyncio.Event()
        self.relaying.set()
        self.server: asyncio.Server | None = None

    # --- relaying ---------------------------------------------------------

    async def _pump(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                # Gate before reading, not after: while frozen, bytes stay in
                # the kernel receive buffer, which is how a dead path looks.
                await self.relaying.wait()
                data = await reader.read(BUFFER_BYTES)
                if not data:
                    break
                writer.write(data)
                await writer.drain()
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            if not writer.is_closing():
                writer.close()

    async def _handle(self, client_r: asyncio.StreamReader, client_w: asyncio.StreamWriter) -> None:
        try:
            server_r, server_w = await asyncio.open_connection("127.0.0.1", self.target_port)
        except OSError as exc:
            print(f"[proxy] upstream connect failed: {exc}", flush=True)
            client_w.close()
            return
        pair = (client_w, server_w)
        self.pairs.append(pair)
        print(f"[proxy] connection open; live={len(self.pairs)}", flush=True)
        try:
            await asyncio.gather(
                self._pump(client_r, server_w),
                self._pump(server_r, client_w),
            )
        finally:
            if pair in self.pairs:
                self.pairs.remove(pair)
            for writer in pair:
                if not writer.is_closing():
                    writer.close()
            print(f"[proxy] connection closed; live={len(self.pairs)}", flush=True)

    # --- commands ---------------------------------------------------------

    def cut(self) -> int:
        count = len(self.pairs)
        for pair in list(self.pairs):
            for writer in pair:
                if not writer.is_closing():
                    writer.close()
        return count

    async def up(self) -> None:
        if self.server is not None:
            return
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", self.listen_port)
        print(
            f"[proxy] listening on 127.0.0.1:{self.listen_port}"
            f" -> 127.0.0.1:{self.target_port}",
            flush=True,
        )

    async def down(self) -> None:
        # Cut before awaiting wait_closed(): since 3.12 it waits for every
        # live connection handler to finish, so closing the listener first
        # would block here until the relayed connections ended on their own.
        count = self.cut()
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()
            self.server = None
        print(f"[proxy] not listening; cut {count} connection(s)", flush=True)

    def status(self) -> None:
        mode = "relaying" if self.relaying.is_set() else "frozen"
        listening = "listening" if self.server is not None else "not listening"
        print(f"[proxy] {mode}; {listening}; live={len(self.pairs)}", flush=True)

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
                print(f"[proxy] cut {self.cut()} connection(s)", flush=True)
            elif command == "down":
                await self.down()
            elif command == "up":
                await self.up()
            elif command == "freeze":
                self.relaying.clear()
                print("[proxy] frozen: sockets open, nothing relayed", flush=True)
            elif command == "thaw":
                self.relaying.set()
                print("[proxy] relaying again", flush=True)
            elif command == "status":
                self.status()
            elif command in {"quit", "exit"}:
                return True
            elif command:
                print(f"[proxy] unknown command: {command}", flush=True)


async def main_async(listen_port: int, target_port: int) -> None:
    proxy = CutProxy(listen_port, target_port)
    await proxy.up()
    if not await proxy.run_commands():
        # Nothing more will be typed (nohup, a closed pipe, CI): keep relaying
        # until the process is killed, which is itself the cut in that mode.
        print("[proxy] no more input; relaying until killed", flush=True)
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
