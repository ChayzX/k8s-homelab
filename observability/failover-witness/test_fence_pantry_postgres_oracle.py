#!/usr/bin/env python3
"""Contract tests for the home-to-Oracle PantryBot PostgreSQL fence transport."""

from pathlib import Path


SCRIPT = Path(__file__).with_name("fence-pantry-postgres-oracle.sh")


def test_scope_and_confirmation() -> None:
    text = SCRIPT.read_text()
    assert 'NAMESPACE="pantry-bot"' in text
    assert 'STATEFULSET="postgres-authority-standby-reseed"' in text
    assert '[[ "$mode" == "--confirm" || "$mode" == "--dry-run" ]]' in text
    assert "explicit_confirmation_required" in text


def test_transport_is_fixed_and_fails_closed() -> None:
    text = SCRIPT.read_text()
    assert "BatchMode=yes" in text
    assert "StrictHostKeyChecking=yes" in text
    assert "sudo -n kubectl" in text
    assert "invalid_oracle_host" in text
    assert "ssh_key_unreadable" in text
    assert "known_hosts_unreadable" in text
    assert "k3s" in text.lower()  # the help contract forbids stopping k3s
    assert "systemctl" not in text


def test_only_named_resource_and_no_data_deletion() -> None:
    text = SCRIPT.read_text().lower()
    assert "scale statefulset" in text
    assert "delete pod" in text
    assert "delete pvc" not in text
    assert "persistentvolumeclaim" not in text
    assert "kubectl.*secret" not in text
    assert "routes" in text
    assert "oracle_postgres_pod_still_present" in text
    assert "service_has_endpoints" in text


if __name__ == "__main__":
    test_scope_and_confirmation()
    test_transport_is_fixed_and_fails_closed()
    test_only_named_resource_and_no_data_deletion()
    print("test_fence_pantry_postgres_oracle: all assertions passed")
