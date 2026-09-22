#!/usr/bin/env python3
"""Least-privilege and credential-lifetime contracts for the Home DB fencer."""

import re
from pathlib import Path

import yaml


MANIFEST = Path(__file__).with_name("pantry-postgres-fencer-rbac.yaml")


def resources_by_kind() -> dict[str, dict]:
    assert MANIFEST.exists(), "the production fencer RBAC manifest must be versioned"
    return {item["kind"]: item for item in yaml.safe_load_all(MANIFEST.read_text())}


def test_fencer_uses_a_revocable_service_account_token_secret() -> None:
    """Catch a short-lived `kubectl create token` credential that expires unattended."""
    resources = resources_by_kind()
    secret = resources["Secret"]

    assert secret["type"] == "kubernetes.io/service-account-token"
    assert secret["metadata"]["annotations"]["kubernetes.io/service-account.name"] == "pantry-postgres-fencer"
    assert "data" not in secret
    assert "stringData" not in secret


HOME_FENCE_DEPLOYMENTS = {
    "pantry-private-api",
    "pantry-overlay-delivery",
    "pantry-twitch-gateway",
    "pantry-twitch-dispatcher",
    "pantry-chat-worker",
    "pantry-private-site",
    "app-cloudflared",
    "pantry-bot",
}


def names_for(role: dict, resource: str) -> set[str]:
    return {name for rule in role["rules"] if resource in rule["resources"] for name in rule["resourceNames"]}


def test_fencer_role_is_limited_to_named_pantry_objects() -> None:
    """Catch credential expansion beyond the named Home PantryBot writer domain."""
    resources = resources_by_kind()
    role = resources["Role"]
    postgres = {
        "postgres-authority",
        "postgres-authority-home",
        "postgres-authority-home-v2",
        "postgres-authority-home-failback",
        "postgres-authority-home-return",
        "postgres-authority-standby-home-canada",
    }

    assert names_for(role, "statefulsets") == postgres
    assert names_for(role, "pods") == {f"{name}-0" for name in postgres}
    assert names_for(role, "deployments") == HOME_FENCE_DEPLOYMENTS
    assert all(rule["resourceNames"] for rule in role["rules"]), "every grant must be name-scoped"
    assert {verb for rule in role["rules"] for verb in rule["verbs"]} <= {
        "get",
        "patch",
        "update",
        "delete",
    }


def test_fencer_can_never_touch_shared_or_commands_connectors() -> None:
    """Catch a grant on the shared PantryBot tunnel (SSH/RDP/k8s-api/Authentik)."""
    role = resources_by_kind()["Role"]
    granted = {name for rule in role["rules"] for name in rule["resourceNames"]}
    assert not granted & {"cloudflared", "commands-cloudflared", "pantry-commands-site"}


def test_fencer_grant_covers_the_live_home_fence_script_default() -> None:
    """Catch renaming the live Home target without widening the RBAC grant.

    2026-09-21: fence-home-direct-from-oracle.sh's default was updated to
    postgres-authority-home-v2 (the live primary) without adding that name
    here. The scoped fence then failed Forbidden — misreported as "not
    found" by a stderr-swallowing bug — so every automatic promotion
    self-fenced Oracle's own standby instead of ever fencing Home. Before
    that rename, the same drift was a silent FALSE POSITIVE: the fence
    "succeeded" against a decommissioned StatefulSet while the real Home
    primary stayed live and unfenced, a genuine split-brain risk. This test
    exists so the next resource rename (e.g. a future -v3) fails loudly in
    CI instead of failing at 2am against production.
    """
    role = resources_by_kind()["Role"]
    script = Path(__file__).with_name("fence-home-direct-from-oracle.sh").read_text()

    sts = re.search(r'HOME_PANTRY_STATEFULSETS="([^"]+)"', script)
    deploy = re.search(r'PANTRY_WRITER_DEPLOYMENTS="([^"]+)"', script)
    assert sts and deploy, "fence-home-direct-from-oracle.sh must define its default fence lists"

    for name in sts.group(1).split():
        assert name in names_for(role, "statefulsets"), f"{name!r} is fenced but not granted — fence fails Forbidden"
        assert f"{name}-0" in names_for(role, "pods"), f"{name}-0 (pod delete target) is not granted"
    assert set(deploy.group(1).split()) == names_for(role, "deployments")
