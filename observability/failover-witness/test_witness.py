#!/usr/bin/env python3
import tempfile
from pathlib import Path

from witness import WitnessState


with tempfile.TemporaryDirectory() as directory:
    state = WitnessState(Path(directory) / "state.json", "test-secret", lease_seconds=60)
    home = state.acquire("home")
    assert home and home["epoch"] == 1
    assert state.acquire("oracle") is None
    assert state.renew("home", home["epoch"], home["token"])
    assert not state.renew("oracle", home["epoch"], home["token"])
    state.data["expires_at"] = 0
    oracle = state.acquire("oracle")
    assert oracle and oracle["epoch"] == 2
    assert not state.renew("home", home["epoch"], home["token"])
print("test_witness: all assertions passed")
