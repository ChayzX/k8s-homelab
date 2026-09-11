#!/usr/bin/env python3
import socket
import threading
import unittest
from pathlib import Path

from relay import RelayServer, RelayTarget


class RelayTests(unittest.TestCase):
    def test_forwards_a_tcp_request_to_the_node_local_upstream(self):
        upstream = socket.socket()
        upstream.bind(("127.0.0.1", 0))
        upstream.listen(1)
        upstream_port = upstream.getsockname()[1]

        def serve_upstream():
            connection, _ = upstream.accept()
            with connection:
                connection.sendall(connection.recv(1024).upper())

        thread = threading.Thread(target=serve_upstream, daemon=True)
        thread.start()
        relay = RelayServer(
            ("127.0.0.1", 0),
            RelayTarget("127.0.0.1", upstream_port),
            connect_timeout=1,
        )
        relay_thread = threading.Thread(target=relay.serve_forever, daemon=True)
        relay_thread.start()
        relay_port = relay.server_address[1]
        try:
            with socket.create_connection(("127.0.0.1", relay_port), timeout=1) as client:
                client.sendall(b"healthz")
                self.assertEqual(client.recv(1024), b"HEALTHZ")
        finally:
            relay.shutdown()
            relay.server_close()
            upstream.close()

    def test_home_units_use_loopback_upstreams_and_service_is_node_local(self):
        root = Path(__file__).parent
        for unit in (
            "failover-witness-home-tunnel.service",
            "failover-witness-chasebot-tunnel.service",
            "failover-witness-oracle-tunnel.service",
        ):
            text = (root / unit).read_text()
            self.assertIn("-L 127.0.0.1:18765:127.0.0.1:8765", text)
            self.assertNotIn("-L 192.168.40.", text)

        manifest = (root / "failover-witness-relay.yaml").read_text()
        self.assertIn("kind: DaemonSet", manifest)
        self.assertIn("hostNetwork: true", manifest)
        self.assertIn("--listen-host 0.0.0.0", manifest)
        self.assertNotIn("$(LISTEN_HOST)", manifest)
        self.assertIn("internalTrafficPolicy: Local", manifest)
        self.assertIn("kubernetes.io/hostname", manifest)
        self.assertIn("minecraftmachine", manifest)
        self.assertIn("chasebot", manifest)
        self.assertIn("pantry-bot-oracle", manifest)


if __name__ == "__main__":
    unittest.main()
