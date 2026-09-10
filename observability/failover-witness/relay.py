#!/usr/bin/env python3
"""Private node-local TCP relay for the host's SSH witness tunnel."""

from __future__ import annotations

import argparse
import socket
import socketserver
import threading
from dataclasses import dataclass


@dataclass(frozen=True)
class RelayTarget:
    host: str
    port: int


class _RelayHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        server = self.server
        assert isinstance(server, RelayServer)
        try:
            upstream = socket.create_connection(server.target, timeout=server.connect_timeout)
        except OSError:
            return

        with upstream:
            downstream_to_upstream = threading.Thread(
                target=_copy, args=(self.request, upstream), daemon=True
            )
            upstream_to_downstream = threading.Thread(
                target=_copy, args=(upstream, self.request), daemon=True
            )
            downstream_to_upstream.start()
            upstream_to_downstream.start()
            downstream_to_upstream.join()
            upstream_to_downstream.join()


def _copy(source: socket.socket, destination: socket.socket) -> None:
    try:
        while True:
            payload = source.recv(64 * 1024)
            if not payload:
                return
            destination.sendall(payload)
    except OSError:
        return


class RelayServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        target: RelayTarget,
        *,
        connect_timeout: float = 2,
    ) -> None:
        self.target = (target.host, target.port)
        self.connect_timeout = connect_timeout
        super().__init__(address, _RelayHandler)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--listen-host", default="0.0.0.0")
    parser.add_argument("--listen-port", type=int, default=18766)
    parser.add_argument("--upstream-host", default="127.0.0.1")
    parser.add_argument("--upstream-port", type=int, default=18765)
    parser.add_argument("--connect-timeout", type=float, default=2)
    args = parser.parse_args()
    server = RelayServer(
        (args.listen_host, args.listen_port),
        RelayTarget(args.upstream_host, args.upstream_port),
        connect_timeout=args.connect_timeout,
    )
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
