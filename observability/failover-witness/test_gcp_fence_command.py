#!/usr/bin/env python3
"""Contract test for the GCP-to-home fence relay."""

from pathlib import Path


SCRIPT = Path(__file__).with_name("gcp-fence-home-writer.sh")


def test_relay_uses_strict_local_forward_and_explicit_confirmation() -> None:
    text = SCRIPT.read_text()
    assert "StrictHostKeyChecking=yes" in text
    assert "-p 2223" in text
    assert "root@127.0.0.1" in text
    assert "fence-pantry-postgres --confirm\n" in text


def test_relay_does_not_stop_k3s() -> None:
    text = SCRIPT.read_text()
    assert "k3s.service" not in text
    assert "systemctl" not in text


if __name__ == "__main__":
    test_relay_uses_strict_local_forward_and_explicit_confirmation()
    test_relay_does_not_stop_k3s()
    print("test_gcp_fence_command: all assertions passed")
