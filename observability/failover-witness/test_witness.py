#!/usr/bin/env python3
import tempfile
from pathlib import Path

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
print("test_witness: all assertions passed")
