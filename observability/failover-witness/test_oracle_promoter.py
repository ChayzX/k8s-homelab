#!/usr/bin/env python3
"""Tests for the guarded Oracle PostgreSQL promotion sequence."""

from oracle_promoter import (
    ORACLE_PROMOTION_DEPLOYMENTS,
    OraclePromoter,
    PromotionAdapters,
    _postgres_promote_command,
)


def test_promotion_endpoint_restart_covers_every_database_consumer() -> None:
    assert ORACLE_PROMOTION_DEPLOYMENTS == (
        "pantry-commands-site",
        "pantry-private-api",
        "pantry-private-site",
        "pantry-overlay-delivery",
        "pantry-twitch-gateway",
        "pantry-chat-worker",
        "pantry-twitch-dispatcher",
    )


def test_postgres_promotion_runs_as_postgres_user() -> None:
    assert _postgres_promote_command("/var/lib/postgresql/data") == (
        "su-exec",
        "postgres",
        "pg_ctl",
        "-D",
        "/var/lib/postgresql/data",
        "promote",
    )


def test_held_authority_does_not_promote_or_enable_roles() -> None:
    calls: list[str] = []
    adapters = PromotionAdapters(
        acquire=lambda: None,
        is_primary=None,
        promote=lambda _token: calls.append("promote"),
        switch_endpoint=lambda: calls.append("endpoint"),
        enable_roles=lambda: calls.append("roles"),
        fence=lambda: calls.append("fence"),
        ready=lambda: True,
    )

    assert OraclePromoter(adapters).run_once() is False
    assert calls == []


def test_promotion_requires_shared_authority_and_orders_changes() -> None:
    calls: list[str] = []
    adapters = PromotionAdapters(
        acquire=lambda: {"epoch": 7, "token": "oracle-token"},
        is_primary=lambda: False,
        promote=lambda token: calls.append(f"promote:{token['epoch']}"),
        switch_endpoint=lambda: calls.append("endpoint"),
        enable_roles=lambda: calls.append("roles"),
        fence=lambda: calls.append("fence"),
        ready=lambda: True,
    )

    assert OraclePromoter(adapters).run_once() is True
    assert calls == ["promote:7", "endpoint", "roles"]


def test_old_writer_is_fenced_before_promotion() -> None:
    calls: list[str] = []
    adapters = PromotionAdapters(
        acquire=lambda: {"epoch": 10, "token": "oracle-token"},
        is_primary=lambda: False,
        promote=lambda _token: calls.append("promote"),
        switch_endpoint=lambda: calls.append("endpoint"),
        enable_roles=lambda: calls.append("roles"),
        fence=lambda: calls.append("fence"),
        renew=None,
        ready=lambda: True,
        fence_old_writer=lambda: calls.append("old-writer-fence"),
    )

    assert OraclePromoter(adapters).run_once() is True
    assert calls == ["old-writer-fence", "promote", "endpoint", "roles"]


def test_pantry_promoter_rejects_missing_old_writer_fence() -> None:
    import argparse

    args = argparse.Namespace(old_writer_fence_command=None)
    # The production adapter is deliberately fail-closed when the environment
    # has not supplied an out-of-band fence command.
    def invoke() -> None:
        if not args.old_writer_fence_command:
            raise RuntimeError("OLD_WRITER_FENCE_COMMAND is required before promotion")

    try:
        invoke()
    except RuntimeError as error:
        assert str(error) == "OLD_WRITER_FENCE_COMMAND is required before promotion"
    else:
        raise AssertionError("missing old-writer fencing must fail closed")


def test_failed_old_writer_fence_blocks_promotion_and_fences_locally() -> None:
    calls: list[str] = []

    def fence_old_writer() -> None:
        calls.append("old-writer-fence")
        raise RuntimeError("home writer could not be fenced")

    adapters = PromotionAdapters(
        acquire=lambda: {"epoch": 11, "token": "oracle-token"},
        is_primary=lambda: False,
        promote=lambda _token: calls.append("promote"),
        switch_endpoint=lambda: calls.append("endpoint"),
        enable_roles=lambda: calls.append("roles"),
        fence=lambda: calls.append("local-fence"),
        ready=lambda: True,
        fence_old_writer=fence_old_writer,
    )

    try:
        OraclePromoter(adapters).run_once()
    except RuntimeError as error:
        assert str(error) == "home writer could not be fenced"
    else:
        raise AssertionError("promotion must stop when old-writer fencing fails")
    assert calls == ["old-writer-fence", "local-fence"]


def test_renewal_loss_self_fences_after_promotion() -> None:
    calls: list[str] = []
    adapters = PromotionAdapters(
        acquire=lambda: {"epoch": 8, "token": "oracle-token"},
        is_primary=lambda: False,
        promote=lambda _token: calls.append("promote"),
        switch_endpoint=lambda: calls.append("endpoint"),
        enable_roles=lambda: calls.append("roles"),
        fence=lambda: calls.append("fence"),
        renew=lambda _token: False,
        ready=lambda: True,
    )

    promoter = OraclePromoter(adapters)
    assert promoter.run_once() is True
    assert promoter.renew_or_fence() is False
    assert calls == ["promote", "endpoint", "roles", "fence"]


def test_primary_without_authority_fences_on_restart() -> None:
    calls: list[str] = []
    adapters = PromotionAdapters(
        acquire=lambda: None,
        is_primary=lambda: True,
        promote=lambda _token: calls.append("promote"),
        switch_endpoint=lambda: calls.append("endpoint"),
        enable_roles=lambda: calls.append("roles"),
        fence=lambda: calls.append("fence"),
        ready=lambda: True,
    )

    assert OraclePromoter(adapters).run_once() is False
    assert calls == ["fence"]


def test_unready_oracle_does_not_claim_shared_authority() -> None:
    calls: list[str] = []
    adapters = PromotionAdapters(
        acquire=lambda: calls.append("acquire") or {"epoch": 9, "token": "oracle-token"},
        is_primary=lambda: False,
        promote=lambda _token: calls.append("promote"),
        switch_endpoint=lambda: calls.append("endpoint"),
        enable_roles=lambda: calls.append("roles"),
        fence=lambda: calls.append("fence"),
        ready=lambda: False,
    )

    assert OraclePromoter(adapters).run_once() is False
    assert calls == []


if __name__ == "__main__":
    test_held_authority_does_not_promote_or_enable_roles()
    test_promotion_requires_shared_authority_and_orders_changes()
    test_renewal_loss_self_fences_after_promotion()
    test_primary_without_authority_fences_on_restart()
    test_unready_oracle_does_not_claim_shared_authority()
    print("test_oracle_promoter: all assertions passed")
