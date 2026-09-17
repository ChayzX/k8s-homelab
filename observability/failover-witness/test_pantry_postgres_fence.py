#!/usr/bin/env python3
"""Contract tests for the Minecraft-safe PantryBot writer fence."""

from pathlib import Path


ROOT = Path(__file__).parent
SCRIPT = ROOT / "fence-pantry-postgres.sh"
FAILBACK_DOC = ROOT.parent.parent / "docs" / "recovery" / "PANTRYBOT-POSTGRES-FAILBACK.md"


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


def test_failback_fence_scope_document_hazard_with_home_return() -> None:
    """Failback fence scope documents hazard with postgres-authority-home-return.

    During Oracle-to-home failback, the prepared home-return standby must survive
    the home fence operation so it can become the new Oracle after failback completes.

    The current home writer fence (fence-pantry-postgres.sh) includes home-return for
    Oracle promotion scenarios, but the failback procedure must preserve home-return
    as a standby. This means failback_controller.py's fence_home adapter must NOT use
    fence-pantry-postgres.sh directly without filtering.

    This test documents the known hazard as a regression guard:
    - If failback uses fence-pantry-postgres.sh directly, it will fence home-return
    - The failback fence scope must exclude postgres-authority-home-return
    - Either a separate fence adapter or script filtering is required

    Expected behavior:
    - Home writer fence (for Oracle promotion): includes home-return (correct)
    - Failback fence (for return-home): excludes home-return (required, not yet implemented)
    """
    text = SCRIPT.read_text()
    assert "postgres-authority-home-return" in text
    assert "STATEFULSETS" in text
    assert "SERVICES" in text
    doc = FAILBACK_DOC.read_text()
    assert "normal home fence must not be invoked during Oracle-to-home failback" in doc
    assert "separate Oracle fence adapter before acquiring the home lease" in doc
    assert "preserve `postgres-authority-home-return`" in doc


if __name__ == "__main__":
    test_fence_script_exists_and_targets_only_pantry_postgres()
    test_fence_script_never_stops_the_k3s_writer_domain()
    test_fence_script_fails_closed_and_verifies_zero_replicas()
    test_failback_fence_scope_document_hazard_with_home_return()
    print("test_pantry_postgres_fence: all assertions passed")
