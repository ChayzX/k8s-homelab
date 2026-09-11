#!/usr/bin/env python3
"""Static contract tests for the guarded Authentik promotion controller."""

from argparse import Namespace

import authentik_oracle_promoter as promoter


def test_authentik_promotion_targets_the_auth_resource_and_local_secret(monkeypatch) -> None:
    calls: list[tuple[str, ...]] = []

    monkeypatch.setattr(promoter, "_post", lambda *_args, **_kwargs: {"epoch": 3, "token": "t"})
    monkeypatch.setattr(promoter, "_kubectl", lambda *args, **_kwargs: calls.append(args) or "t")
    args = Namespace(
        witness_url="http://witness",
        secret="secret",
        namespace="auth",
        pod="auth-postgresql-standby-0",
        service="auth-postgresql-standby",
        secret_name="auth-authentik",
        timeout=1,
        fence_command="/bin/true",
        old_writer_fence_command="ssh home sudo fence-writer-domain.sh k3s-agent.service",
    )

    adapters = promoter.build_adapters(args)
    token = adapters.acquire()
    assert token == {"epoch": 3, "token": "t"}
    adapters.switch_endpoint()

    assert any("auth-postgresql-standby" in call for call in calls)
    assert any("AUTHENTIK_POSTGRESQL__HOST" in item for call in calls for item in call)
    assert not any("pantry" in item for call in calls for item in call)


def test_authentik_promotion_wires_an_explicit_old_writer_fence(monkeypatch) -> None:
    calls: list[tuple[tuple[str, ...], dict[str, object]]] = []
    monkeypatch.setattr(
        promoter.subprocess,
        "run",
        lambda command, **kwargs: calls.append((tuple(command), kwargs)),
    )
    args = Namespace(
        witness_url="http://witness",
        secret="secret",
        namespace="auth",
        pod="auth-postgresql-standby-0",
        service="auth-postgresql-standby",
        secret_name="auth-authentik",
        timeout=1,
        fence_command="/bin/true",
        old_writer_fence_command="ssh home sudo fence-writer-domain.sh k3s-agent.service",
    )

    adapters = promoter.build_adapters(args)
    assert adapters.fence_old_writer is not None
    adapters.fence_old_writer()

    assert calls == [
        (
            ("ssh", "home", "sudo", "fence-writer-domain.sh", "k3s-agent.service"),
            {"check": True, "timeout": 30},
        )
    ]


def test_authentik_deployments_are_bounded_to_application_tier() -> None:
    assert promoter.AUTHENTIK_DEPLOYMENTS == (
        "auth-authentik-server",
        "auth-authentik-worker",
    )


if __name__ == "__main__":
    test_authentik_deployments_are_bounded_to_application_tier()
    print("test_authentik_oracle_promoter: all assertions passed")
