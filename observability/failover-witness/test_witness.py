#!/usr/bin/env python3
import tempfile
from pathlib import Path
from unittest.mock import patch

from witness import WitnessState


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

    # A disk failure cannot grant an in-memory lease on the next request.
    with patch.object(restarted, "_save", side_effect=OSError("disk full")):
        try:
            restarted.acquire("home", resource="disk-failure-test")
        except OSError:
            pass
        else:
            raise AssertionError("unpersisted epoch must not be acknowledged")
    assert restarted.persistence_failed
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
