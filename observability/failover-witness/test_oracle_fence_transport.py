#!/usr/bin/env python3
"""Contract tests for the loopback-only GCP-to-Oracle fence transport."""

from pathlib import Path


ROOT = Path(__file__).parent
UNIT = ROOT / "failover-witness-oracle-fence-tunnel.service"
WRAPPER = ROOT / "gcp-fence-oracle-writer.sh"


def test_oracle_tunnel_is_reverse_and_loopback_bound() -> None:
    text = UNIT.read_text()
    assert "-R 127.0.0.1:18767:127.0.0.1:22" in text
    assert "StrictHostKeyChecking=yes" in text
    assert "ExitOnForwardFailure=yes" in text


def test_gcp_wrapper_uses_forced_oracle_fence() -> None:
    text = WRAPPER.read_text()
    assert "127.0.0.1" in text
    assert "-p 18767" in text
    assert "fence-pantry-postgres-oracle --confirm" in text
    assert "oracle-fence" in text


if __name__ == "__main__":
    test_oracle_tunnel_is_reverse_and_loopback_bound()
    test_gcp_wrapper_uses_forced_oracle_fence()
    print("test_oracle_fence_transport: all assertions passed")
