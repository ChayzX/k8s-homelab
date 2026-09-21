#!/usr/bin/env python3
"""Least-privilege and credential-lifetime contracts for the Home DB fencer."""

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

