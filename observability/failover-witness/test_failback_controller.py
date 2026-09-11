#!/usr/bin/env python3
"""Tests for the guarded Oracle-to-home PostgreSQL failback sequence."""

from failback_controller import FailbackAdapters, FailbackController


def test_failback_requires_a_home_fence_adapter() -> None:
    try:
        FailbackController(
            FailbackAdapters(
                oracle_is_primary=lambda: True,
                home_is_fenced=lambda: True,
                stop_oracle_roles=lambda: None,
                seed_home_standby=lambda: None,
                home_standby_caught_up=lambda: True,
                fence_oracle=lambda: None,
                acquire_home=lambda: None,
                promote_home=lambda _token: None,
                switch_home_endpoint=lambda: None,
                enable_home_roles=lambda: None,
                scale_oracle_roles_down=lambda: None,
                fence_home=None,
            )
        )
    except ValueError as error:
        assert "fence_home" in str(error)
    else:
        raise AssertionError("missing home fence adapter was accepted")


def test_failback_refuses_without_oracle_primary_or_home_fence() -> None:
    calls: list[str] = []
    adapters = FailbackAdapters(
        oracle_is_primary=lambda: False,
        home_is_fenced=lambda: True,
        stop_oracle_roles=lambda: calls.append("stop-oracle-roles"),
        seed_home_standby=lambda: calls.append("seed-home"),
        home_standby_caught_up=lambda: True,
        fence_oracle=lambda: calls.append("fence-oracle"),
        acquire_home=lambda: {"epoch": 9, "token": "home-token"},
        promote_home=lambda _token: calls.append("promote-home"),
        switch_home_endpoint=lambda: calls.append("home-endpoint"),
        enable_home_roles=lambda: calls.append("home-roles"),
        scale_oracle_roles_down=lambda: calls.append("scale-oracle"),
        renew_home=lambda _token: True,
        fence_home=lambda: calls.append("fence-home"),
    )

    controller = FailbackController(adapters)
    assert controller.run_once() is False
    assert calls == []

    adapters.oracle_is_primary = lambda: True
    adapters.home_is_fenced = lambda: False
    assert controller.run_once() is False
    assert calls == []


def test_failback_orders_seed_fence_lease_promotion_and_role_switch() -> None:
    calls: list[str] = []
    adapters = FailbackAdapters(
        oracle_is_primary=lambda: True,
        home_is_fenced=lambda: True,
        stop_oracle_roles=lambda: calls.append("stop-oracle-roles"),
        seed_home_standby=lambda: calls.append("seed-home"),
        home_standby_caught_up=lambda: True,
        fence_oracle=lambda: calls.append("fence-oracle"),
        acquire_home=lambda: {"epoch": 9, "token": "home-token"},
        promote_home=lambda token: calls.append(f"promote-home:{token['epoch']}"),
        switch_home_endpoint=lambda: calls.append("home-endpoint"),
        enable_home_roles=lambda: calls.append("home-roles"),
        scale_oracle_roles_down=lambda: calls.append("scale-oracle"),
        renew_home=lambda _token: True,
        fence_home=lambda: calls.append("fence-home"),
    )

    controller = FailbackController(adapters)
    assert controller.run_once() is True
    assert calls == [
        "stop-oracle-roles",
        "seed-home",
        "fence-oracle",
        "promote-home:9",
        "home-endpoint",
        "home-roles",
        "scale-oracle",
    ]


def test_failback_does_not_promote_when_home_standby_is_not_caught_up() -> None:
    calls: list[str] = []
    adapters = FailbackAdapters(
        oracle_is_primary=lambda: True,
        home_is_fenced=lambda: True,
        stop_oracle_roles=lambda: calls.append("stop-oracle-roles"),
        seed_home_standby=lambda: calls.append("seed-home"),
        home_standby_caught_up=lambda: False,
        fence_oracle=lambda: calls.append("fence-oracle"),
        acquire_home=lambda: {"epoch": 9, "token": "home-token"},
        promote_home=lambda _token: calls.append("promote-home"),
        switch_home_endpoint=lambda: calls.append("home-endpoint"),
        enable_home_roles=lambda: calls.append("home-roles"),
        scale_oracle_roles_down=lambda: calls.append("scale-oracle"),
        renew_home=lambda _token: True,
        fence_home=lambda: calls.append("fence-home"),
    )

    assert FailbackController(adapters).run_once() is False
    assert calls == ["stop-oracle-roles", "seed-home"]


def test_failback_fences_home_if_authority_renewal_is_lost() -> None:
    calls: list[str] = []
    adapters = FailbackAdapters(
        oracle_is_primary=lambda: True,
        home_is_fenced=lambda: True,
        stop_oracle_roles=lambda: calls.append("stop-oracle-roles"),
        seed_home_standby=lambda: calls.append("seed-home"),
        home_standby_caught_up=lambda: True,
        fence_oracle=lambda: calls.append("fence-oracle"),
        acquire_home=lambda: {"epoch": 9, "token": "home-token"},
        promote_home=lambda _token: calls.append("promote-home"),
        switch_home_endpoint=lambda: calls.append("home-endpoint"),
        enable_home_roles=lambda: calls.append("home-roles"),
        scale_oracle_roles_down=lambda: calls.append("scale-oracle"),
        renew_home=lambda _token: False,
        fence_home=lambda: calls.append("fence-home"),
    )

    controller = FailbackController(adapters)
    assert controller.run_once() is True
    assert controller.renew_or_fence() is False
    assert calls[-1] == "fence-home"


if __name__ == "__main__":
    test_failback_requires_a_home_fence_adapter()
    test_failback_refuses_without_oracle_primary_or_home_fence()
    test_failback_orders_seed_fence_lease_promotion_and_role_switch()
    test_failback_does_not_promote_when_home_standby_is_not_caught_up()
    test_failback_fences_home_if_authority_renewal_is_lost()
    print("test_failback_controller: all assertions passed")
