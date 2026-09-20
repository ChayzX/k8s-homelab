#!/usr/bin/env python3
"""Static contract tests for the composite Oracle old-writer fence."""

from pathlib import Path


ROOT = Path(__file__).parent


def test_composite_requires_explicit_mode_and_calls_both_fences() -> None:
    text = (ROOT / "fence-old-writers-from-oracle.sh").read_text()
    assert 'explicit_confirmation_required' in text
    assert 'fence-home-from-oracle.sh' in text
    assert 'fence-canada-from-oracle.sh' in text
    assert 'old_writer_fence=verified home=verified canada=verified' in text


def test_home_transport_is_identity_pinned_and_no_arbitrary_command() -> None:
    text = (ROOT / "fence-home-from-oracle.sh").read_text()
    assert 'GCP_HOST="136.113.178.106"' in text
    assert 'GCP_USER="sa_105559435168833655240"' in text
    assert 'StrictHostKeyChecking=yes' in text
    assert 'REMOTE_FENCE="/usr/local/lib/failover-witness/gcp-fence-home-writer.sh"' in text
    assert 'sudo -n "$REMOTE_FENCE"' in text


def test_home_transport_uses_independent_gcp_forced_fence() -> None:
    """Catch a transport that bypasses the ChaseBot lease-renewer fence."""
    text = (ROOT / "fence-home-from-oracle.sh").read_text()

    assert "136.113.178.106" in text
    assert "gcp-witness-oracle" in text
    assert "gcp-fence-home-writer.sh" in text
    assert "100.84.89.87" not in text
    assert "fence-pantry-postgres.sh" not in text
