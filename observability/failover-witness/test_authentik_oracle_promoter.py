#!/usr/bin/env python3
"""Static contract tests for the guarded Authentik promotion controller."""

from argparse import Namespace
import json

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
        old_writer_fence_command="/usr/bin/ssh home sudo fence-writer-domain.sh k3s-agent.service",
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
        old_writer_fence_command="/usr/bin/ssh home sudo fence-writer-domain.sh k3s-agent.service",
    )

    adapters = promoter.build_adapters(args)
    assert adapters.fence_old_writer is not None
    adapters.fence_old_writer()

    assert calls == [
        (
            ("/usr/bin/ssh", "home", "sudo", "fence-writer-domain.sh", "k3s-agent.service"),
            {"check": True, "timeout": 30},
        )
    ]


def test_authentik_deployments_are_bounded_to_application_tier() -> None:
    assert promoter.AUTHENTIK_DEPLOYMENTS == (
        "auth-authentik-server",
        "auth-authentik-worker",
    )


def test_authentik_local_fence_rejects_missing_command() -> None:
    try:
        promoter._parse_local_authentik_fence_command(None)
    except ValueError as error:
        assert str(error) == "--fence-command is required for Authentik promotion"
    else:
        raise AssertionError("missing local fence command must fail closed")


def test_authentik_local_fence_rejects_pantry_only_command() -> None:
    try:
        promoter._parse_local_authentik_fence_command(
            "/usr/local/lib/failover-witness/fence-pantry-postgres.sh --confirm"
        )
    except ValueError as error:
        assert str(error) == "--fence-command must not use a Pantry-only fence command"
    else:
        raise AssertionError("Pantry-only fence command must be rejected")


def test_authentik_local_fence_rejects_missing_executable() -> None:
    try:
        promoter._parse_local_authentik_fence_command(
            "/definitely/missing/authentik-fence-command"
        )
    except ValueError as error:
        assert "missing or not executable" in str(error)
    else:
        raise AssertionError("missing fence executable must fail closed")


def test_authentik_target_ready_is_injectable_and_read_only() -> None:
    calls: list[tuple[str, ...]] = []

    def kubectl(*args: str) -> str:
        calls.append(args)
        if args[:1] == ("version",):
            return "client"
        if args[2:4] == ("get", "pod"):
            return json.dumps(
                {
                    "status": {
                        "phase": "Running",
                        "containerStatuses": [{"name": "postgres", "ready": True}],
                    }
                }
            )
        return "service/auth-postgresql-standby"

    assert promoter._authentik_target_ready(
        kubectl, "auth", "auth-postgresql-standby-0", "auth-postgresql-standby"
    )
    assert calls == [
        ("version", "--request-timeout=5s"),
        ("-n", "auth", "get", "pod", "auth-postgresql-standby-0", "-o", "json"),
        ("-n", "auth", "get", "service", "auth-postgresql-standby", "-o", "name"),
    ]


def test_authentik_target_ready_rejects_not_ready_postgres() -> None:
    def kubectl(*args: str) -> str:
        if args[:1] == ("version",):
            return "client"
        if args[2:4] == ("get", "pod"):
            return json.dumps(
                {
                    "status": {
                        "phase": "Running",
                        "containerStatuses": [{"name": "postgres", "ready": False}],
                    }
                }
            )
        raise AssertionError("service must not be queried for an unready pod")

    assert not promoter._authentik_target_ready(
        kubectl, "auth", "auth-postgresql-standby-0", "auth-postgresql-standby"
    )


if __name__ == "__main__":
    test_authentik_deployments_are_bounded_to_application_tier()
    print("test_authentik_oracle_promoter: all assertions passed")
