#!/usr/bin/env python3
import tempfile
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Event, Thread
from unittest.mock import patch
from urllib.request import urlopen

from witness import Handler, WitnessState


with tempfile.TemporaryDirectory() as directory:
    state = WitnessState(Path(directory) / "state.json", "test-secret", lease_seconds=60)
    home = state.acquire("home")
    assert home and home["epoch"] == 1
    same_site = state.acquire("home")
    assert same_site and same_site["epoch"] == home["epoch"] and same_site["token"] == home["token"]
    assert state.acquire("oracle") is None
    assert state.renew("home", home["epoch"], home["token"])
    assert not state.renew("oracle", home["epoch"], home["token"])
    state.data["leases"]["default"]["expires_at"] = 0
    oracle = state.acquire("oracle")
    assert oracle and oracle["epoch"] == 2
    assert not state.renew("home", home["epoch"], home["token"])

    pantry = state.acquire("home", resource="pantry")
    assert pantry and pantry["epoch"] == 1
    assert state.acquire("oracle", resource="pantry") is None
    assert state.acquire("oracle", resource="opsbot")

    canada = state.acquire("canada", resource="pantry:postgres")
    assert canada
    restarted = WitnessState(state.path, "test-secret", lease_seconds=60)
    assert restarted.acquire("oracle", resource="pantry:postgres") is None
    resumed = restarted.acquire("canada", resource="pantry:postgres")
    assert resumed["epoch"] == canada["epoch"] and resumed["token"] == canada["token"]
    restarted.data["leases"]["pantry:postgres"]["expires_at"] = 0
    successor = restarted.acquire("oracle", resource="pantry:postgres")
    assert successor["epoch"] > canada["epoch"]
    assert not restarted.renew("canada", canada["epoch"], canada["token"], resource="pantry:postgres")

    lease = restarted.data["leases"]["pantry:postgres"]
    lease.update(epoch=42, holder="home", expires_at=120.5, token="must-not-leak", token_hash="must-not-leak-either")
    metrics = restarted.prometheus_metrics(now=100.0)
    assert 'failover_witness_healthy 1' in metrics
    assert 'failover_witness_lease_epoch{resource="pantry:postgres"} 42' in metrics
    assert 'failover_witness_lease_expires_at_seconds{resource="pantry:postgres"} 120.5' in metrics
    assert 'failover_witness_lease_active{resource="pantry:postgres",holder="home"} 1' in metrics
    assert "must-not-leak" not in metrics

    expired_metrics = restarted.prometheus_metrics(now=121.0)
    assert 'failover_witness_lease_active{resource="pantry:postgres",holder="home"} 0' in expired_metrics

    # The current time must be sampled after waiting for the state lock. A
    # scrape blocked behind durable persistence must not report an authority
    # lease that expired while it was waiting.
    attempted_lock = Event()
    allow_lock = Event()

    class DelayedLock:
        def __enter__(self):
            attempted_lock.set()
            assert allow_lock.wait(timeout=2)
            return self

        def __exit__(self, *_args):
            return False

    original_lock = restarted.lock
    restarted.lock = DelayedLock()
    delayed_metrics = []
    lease["expires_at"] = 150.0
    try:
        with patch("witness.time.time", side_effect=lambda: 200.0 if allow_lock.is_set() else 100.0):
            metrics_thread = Thread(target=lambda: delayed_metrics.append(restarted.prometheus_metrics()))
            metrics_thread.start()
            assert attempted_lock.wait(timeout=2)
            allow_lock.set()
            metrics_thread.join(timeout=2)
            assert not metrics_thread.is_alive()
    finally:
        restarted.lock = original_lock
    assert 'failover_witness_lease_active{resource="pantry:postgres",holder="home"} 0' in delayed_metrics[0]

    Handler.state = restarted
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server_thread = Thread(target=server.serve_forever)
    server_thread.start()
    try:
        with urlopen(f"http://127.0.0.1:{server.server_port}/metrics", timeout=2) as response:
            assert response.status == 200
            assert response.headers["Content-Type"] == "text/plain; version=0.0.4; charset=utf-8"
            assert "failover_witness_lease_epoch" in response.read().decode()
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join()

    # A disk failure cannot grant an in-memory lease on the next request.
    with patch.object(restarted, "_save", side_effect=OSError("disk full")):
        try:
            restarted.acquire("home", resource="disk-failure-test")
        except OSError:
            pass
        else:
            raise AssertionError("unpersisted epoch must not be acknowledged")
    assert restarted.persistence_failed
    assert 'failover_witness_healthy 0' in restarted.prometheus_metrics(now=121.0)
    for action in (
        lambda: restarted.acquire("home", resource="disk-failure-test"),
        lambda: restarted.renew("oracle", successor["epoch"], successor["token"], resource="pantry:postgres"),
    ):
        try:
            action()
        except RuntimeError:
            pass
        else:
            raise AssertionError("storage failure must block all authority grants and renewals")
    recovered = WitnessState(state.path, "test-secret")
    assert recovered.data["leases"]["pantry:postgres"]["epoch"] == successor["epoch"]
    assert "disk-failure-test" not in recovered.data["leases"]
    malformed = Path(directory) / "malformed.json"
    for contents in ('{"leases": []}', '{}', '{"leases": {"pantry:postgres": {"epoch": "20"}}}'):
        malformed.write_text(contents)
        try:
            WitnessState(malformed, "test-secret")
        except ValueError:
            pass
        else:
            raise AssertionError("corrupt durable history must never silently reset epochs")
print("test_witness: all assertions passed")
