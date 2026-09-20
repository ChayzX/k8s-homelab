#!/usr/bin/env python3
"""Contract tests for the ChaseBot forced Home-writer fence."""

from pathlib import Path


ROOT = Path(__file__).parent
SCRIPT = ROOT / "remote-fence-writer.sh"


def test_forced_fence_stops_lease_renewer_before_database_fence() -> None:
    """Catch a fence that removes PostgreSQL while authority keeps renewing."""
    text = SCRIPT.read_text()

    stop = "systemctl stop pantry-postgres-self-fence.service"
    verify = "systemctl is-active --quiet pantry-postgres-self-fence.service"
    scoped_fence = "fence-pantry-postgres.sh --confirm"

    assert stop in text
    assert verify in text
    assert scoped_fence in text
    assert text.index(stop) < text.index(verify) < text.index(scoped_fence)


def test_scoped_fence_failure_uses_writer_domain_fallback() -> None:
    """Catch a partial fence that could leave a PostgreSQL process writable."""
    text = SCRIPT.read_text()

    assert "fence-writer-domain.sh k3s-agent.service" in text
    assert "scoped_database_fence_failed" in text

