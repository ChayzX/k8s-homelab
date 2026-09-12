#!/usr/bin/env python3
"""Contract tests for the Oracle-side PantryBot PostgreSQL fence."""

from pathlib import Path


SCRIPT = Path(__file__).with_name("fence-pantry-postgres-oracle.sh")


def test_oracle_fence_is_narrow_and_explicit() -> None:
    text = SCRIPT.read_text()
    assert 'NAMESPACE="pantry-bot"' in text
    assert 'STATEFULSET="postgres-authority-standby"' in text
    assert 'SERVICE="postgres-authority-standby"' in text
    assert '"--confirm"' in text
    assert "explicit_confirmation_required" in text


def test_oracle_fence_does_not_target_home_or_minecraft() -> None:
    text = SCRIPT.read_text()
    assert "postgres-authority-home" not in text
    assert "k3s.service" not in text
    assert "systemctl stop" not in text


def test_oracle_fence_verifies_pod_and_service_are_gone() -> None:
    text = SCRIPT.read_text()
    assert "--replicas=0" in text
    assert "oracle_postgres_pod_still_present" in text
    assert "service_has_endpoints" in text
    assert "fence_status=passed" in text


if __name__ == "__main__":
    test_oracle_fence_is_narrow_and_explicit()
    test_oracle_fence_does_not_target_home_or_minecraft()
    test_oracle_fence_verifies_pod_and_service_are_gone()
    print("test_oracle_fence: all assertions passed")
