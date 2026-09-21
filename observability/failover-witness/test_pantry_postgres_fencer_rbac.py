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


def test_fencer_role_is_limited_to_named_pantry_postgres_objects() -> None:
    """Catch credential expansion beyond the three Home PostgreSQL targets."""
    resources = resources_by_kind()
    role = resources["Role"]
    names = {name for rule in role["rules"] for name in rule["resourceNames"]}

    assert names == {
        "postgres-authority",
        "postgres-authority-0",
        "postgres-authority-home",
        "postgres-authority-home-0",
        "postgres-authority-home-v2",
        "postgres-authority-home-v2-0",
        "postgres-authority-home-failback",
        "postgres-authority-home-failback-0",
        "postgres-authority-home-return",
        "postgres-authority-home-return-0",
    }
    assert {verb for rule in role["rules"] for verb in rule["verbs"]} <= {
        "get",
        "patch",
        "update",
        "delete",
    }


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
    resources = resources_by_kind()
    role = resources["Role"]
    names = {name for rule in role["rules"] for name in rule["resourceNames"]}

    script = Path(__file__).with_name("fence-home-direct-from-oracle.sh").read_text()
    match = re.search(r'STS="\$\{PANTRY_HOME_STATEFULSET:-([^}]+)\}"', script)
    assert match, "fence-home-direct-from-oracle.sh must define a PANTRY_HOME_STATEFULSET default"
    default_statefulset = match.group(1)

    assert default_statefulset in names, (
        f"{default_statefulset!r} is the live Home fence target but is not in the "
        "fencer Role's resourceNames — the scoped fence will fail Forbidden"
    )
    assert f"{default_statefulset}-0" in names, (
        f"{default_statefulset}-0 (the pod get/delete target) is missing from the fencer Role"
    )

