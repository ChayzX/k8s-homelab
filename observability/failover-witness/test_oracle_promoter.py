#!/usr/bin/env python3
"""Tests for the guarded Oracle PostgreSQL promotion sequence."""

from oracle_promoter import OraclePromoter, PromotionAdapters


def test_held_authority_does_not_promote_or_enable_roles() -> None:
    calls: list[str] = []
    adapters = PromotionAdapters(
        acquire=lambda: None,
        is_primary=None,
        promote=lambda _token: calls.append("promote"),
        switch_endpoint=lambda: calls.append("endpoint"),
        enable_roles=lambda: calls.append("roles"),
        fence=lambda: calls.append("fence"),
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
    )

    assert OraclePromoter(adapters).run_once() is True
    assert calls == ["promote:7", "endpoint", "roles"]


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
    )

    assert OraclePromoter(adapters).run_once() is False
    assert calls == ["fence"]


if __name__ == "__main__":
    test_held_authority_does_not_promote_or_enable_roles()
    test_promotion_requires_shared_authority_and_orders_changes()
    test_renewal_loss_self_fences_after_promotion()
    test_primary_without_authority_fences_on_restart()
    print("test_oracle_promoter: all assertions passed")
