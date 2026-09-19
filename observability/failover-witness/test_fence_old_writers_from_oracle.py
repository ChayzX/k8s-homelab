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
    assert 'HOME_HOST="${PANTRY_HOME_FENCE_HOST:-100.84.89.87}"' in text
    assert 'HOME_USER="${PANTRY_HOME_FENCE_USER:-chase}"' in text
    assert 'StrictHostKeyChecking=yes' in text
    assert 'sudo -n /usr/local/lib/failover-witness/fence-pantry-postgres.sh' in text
    assert 'invalid_home_identity' in text
