#!/usr/bin/env python3
"""Tests for the fail-closed local PostgreSQL self-fence."""

from fence_agent import FenceAgent


def test_initial_authority_failure_fences_before_writer_can_start() -> None:
    calls: list[str] = []
    agent = FenceAgent(
        acquire=lambda: None,
        renew=lambda _token: True,
        fence=lambda: calls.append("fence"),
        lease_seconds=30,
    )

    assert agent.acquire_initial() is False
    assert calls == ["fence"]


def test_renewal_failure_fences_immediately_and_only_once() -> None:
    calls: list[str] = []
    agent = FenceAgent(
        acquire=lambda: {"epoch": 4, "token": "token"},
        renew=lambda _token: False,
        fence=lambda: calls.append("fence"),
        lease_seconds=30,
    )

    assert agent.acquire_initial() is True
    assert agent.renew_or_fence() is False
    assert agent.renew_or_fence() is False
    assert calls == ["fence"]


def test_successful_renewal_keeps_writer_authorized() -> None:
    calls: list[str] = []
    agent = FenceAgent(
        acquire=lambda: {"epoch": 2, "token": "token"},
        renew=lambda token: token.token == "token",
        fence=lambda: calls.append("fence"),
        lease_seconds=30,
    )

    assert agent.acquire_initial() is True
    assert agent.renew_or_fence() is True
    assert calls == []


if __name__ == "__main__":
    test_initial_authority_failure_fences_before_writer_can_start()
    test_renewal_failure_fences_immediately_and_only_once()
    test_successful_renewal_keeps_writer_authorized()
    print("test_fence_agent: all assertions passed")
