#!/usr/bin/env python3
"""Contract tests for the Minecraft-safe PantryBot writer fence."""

from pathlib import Path


ROOT = Path(__file__).parent
SCRIPT = ROOT / "fence-pantry-postgres.sh"


def test_fence_script_exists_and_targets_only_pantry_postgres() -> None:
    text = SCRIPT.read_text()
    assert 'NAMESPACE="pantry-bot"' in text
    assert 'postgres-authority-home-return' in text
    assert 'postgres-authority-home-failback' in text
    assert 'postgres-authority"' in text
    assert "kubectl" in text


def test_fence_script_never_stops_the_k3s_writer_domain() -> None:
    text = SCRIPT.read_text()
    assert "k3s.service" not in text
    assert "k3s-agent.service" not in text
    assert "systemctl stop" not in text
    assert "systemctl kill" not in text


def test_fence_script_fails_closed_and_verifies_zero_replicas() -> None:
    text = SCRIPT.read_text()
    assert "set -Eeuo pipefail" in text
    assert "--replicas=0" in text
    assert "fence_status=passed" in text
    assert "fence_status=failed" in text


def test_fence_script_has_a_non_mutating_help_path() -> None:
    text = SCRIPT.read_text()
    assert '"--help"' in text
    assert "do not use it as a health check" in text


def test_fence_script_requires_explicit_confirmation() -> None:
    text = SCRIPT.read_text()
    assert '"--confirm"' in text
    assert "explicit_confirmation_required" in text


if __name__ == "__main__":
    test_fence_script_exists_and_targets_only_pantry_postgres()
    test_fence_script_never_stops_the_k3s_writer_domain()
    test_fence_script_fails_closed_and_verifies_zero_replicas()
    print("test_pantry_postgres_fence: all assertions passed")
