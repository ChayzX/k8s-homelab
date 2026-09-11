#!/usr/bin/env python3
"""Guarded Oracle promotion controller for the PantryBot PostgreSQL standby."""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import time
from dataclasses import dataclass
from typing import Any, Callable
from urllib import request
from urllib.error import HTTPError


@dataclass
class PromotionAdapters:
    acquire: Callable[[], dict[str, Any] | None]
    is_primary: Callable[[], bool] | None
    promote: Callable[[dict[str, Any]], None]
    switch_endpoint: Callable[[], None]
    enable_roles: Callable[[], None]
    fence: Callable[[], None]
    renew: Callable[[dict[str, Any]], bool] | None = None


class OraclePromoter:
    def __init__(self, adapters: PromotionAdapters) -> None:
        self.adapters = adapters
        self.token: dict[str, Any] | None = None
        self.promoted = False
        self.fenced = False

    def _fence(self) -> None:
        if self.fenced:
            return
        self.adapters.fence()
        self.fenced = True
        self.token = None

    def run_once(self) -> bool:
        token = self.adapters.acquire()
        if not token:
            if self.adapters.is_primary and self.adapters.is_primary():
                self._fence()
            return False
        try:
            self.adapters.promote(token)
            self.adapters.switch_endpoint()
            self.adapters.enable_roles()
        except Exception:
            self._fence()
            raise
        self.token = token
        self.promoted = True
        return True

    def renew_or_fence(self) -> bool:
        if not self.promoted or not self.token or not self.adapters.renew:
            return False
        try:
            if self.adapters.renew(self.token):
                return True
        except Exception:
            pass
        self._fence()
        return False


def _post(base_url: str, secret: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
    payload = json.dumps(body).encode()
    req = request.Request(
        f"{base_url.rstrip('/')}{path}",
        data=payload,
        method="POST",
        headers={"Authorization": f"Bearer {secret}", "Content-Type": "application/json"},
    )
    with request.urlopen(req, timeout=5) as response:
        result = json.loads(response.read())
    if not isinstance(result, dict):
        raise ValueError("witness response must be an object")
    return result


def _kubectl(*args: str, input_text: str | None = None) -> str:
    result = subprocess.run(["kubectl", *args], check=True, capture_output=True, text=True, input=input_text)
    return result.stdout.strip()


def run() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--witness-url", default=os.environ.get("WITNESS_URL"))
    parser.add_argument("--secret", default=os.environ.get("WITNESS_SHARED_SECRET"))
    parser.add_argument("--lease-seconds", type=int, default=30)
    parser.add_argument("--namespace", default="pantry-bot")
    parser.add_argument("--pod", default="postgres-authority-standby-0")
    parser.add_argument("--service", default="postgres-authority-standby")
    args = parser.parse_args()
    if not args.witness_url or not args.secret:
        raise SystemExit("WITNESS_URL and WITNESS_SHARED_SECRET are required")

    def acquire() -> dict[str, Any] | None:
        try:
            result = _post(args.witness_url, args.secret, "/v1/authority/acquire", {"site": "oracle", "resource": "pantry:postgres"})
        except HTTPError as error:
            if error.code == 409:
                return None
            raise
        return result if result.get("token") else None

    def renew(token: dict[str, Any]) -> bool:
        result = _post(
            args.witness_url,
            args.secret,
            "/v1/authority/renew",
            {"site": "oracle", "resource": "pantry:postgres", "epoch": token["epoch"], "token": token["token"]},
        )
        return result.get("ok") is True

    def promote(_token: dict[str, Any]) -> None:
        recovery = _kubectl("-n", args.namespace, "exec", args.pod, "--", "sh", "-ec", "psql -U \"$POSTGRES_USER\" -d \"$POSTGRES_DB\" -Atc \"select pg_is_in_recovery();\"")
        if recovery == "t":
            _kubectl("-n", args.namespace, "exec", args.pod, "--", "pg_ctl", "-D", "/var/lib/postgresql/data", "promote")
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                if _kubectl("-n", args.namespace, "exec", args.pod, "--", "sh", "-ec", "psql -U \"$POSTGRES_USER\" -d \"$POSTGRES_DB\" -Atc \"select pg_is_in_recovery();\"") == "f":
                    break
                time.sleep(1)
            else:
                raise RuntimeError("Oracle PostgreSQL did not leave recovery mode")
        elif recovery != "f":
            raise RuntimeError(f"unexpected Oracle recovery state: {recovery!r}")
        _kubectl("-n", args.namespace, "label", "pod", args.pod, "pantrybot.postgres/role=primary", "--overwrite")

    def is_primary() -> bool:
        try:
            return _kubectl("-n", args.namespace, "exec", args.pod, "--", "sh", "-ec", "psql -U \"$POSTGRES_USER\" -d \"$POSTGRES_DB\" -Atc \"select pg_is_in_recovery();\"") == "f"
        except Exception:
            return False

    def switch_endpoint() -> None:
        selector = json.dumps({"app.kubernetes.io/name": "pantry-postgres-authority-standby", "pantrybot.postgres/role": "primary"}, separators=(",", ":"))
        _kubectl("-n", args.namespace, "patch", "service", args.service, "--type=merge", "-p", json.dumps({"spec": {"selector": json.loads(selector)}}))
        encoded = _kubectl("-n", args.namespace, "get", "secret", "pantry-bot-platform", "-o", "jsonpath={.data.PANTRY_DATABASE_URL}")
        current = base64.b64decode(encoded).decode()
        if "@" not in current:
            raise RuntimeError("PANTRY_DATABASE_URL has no authority component")
        userinfo = current.rsplit("@", 1)[0]
        database = current.rsplit("/", 1)[-1]
        local = f"{userinfo}@{args.service}.{args.namespace}.svc.cluster.local:5432/{database}"
        replacement = base64.b64encode(local.encode()).decode()
        _kubectl("-n", args.namespace, "patch", "secret", "pantry-bot-platform", "--type=merge", "-p", json.dumps({"data": {"PANTRY_DATABASE_URL": replacement}}))
        for deployment in ("pantry-private-api", "pantry-private-site", "pantry-overlay-delivery"):
            _kubectl("-n", args.namespace, "rollout", "restart", f"deployment/{deployment}")
            _kubectl("-n", args.namespace, "rollout", "status", f"deployment/{deployment}", "--timeout=180s")

    def enable_roles() -> None:
        for deployment, replicas in (("pantry-twitch-gateway", "1"), ("pantry-chat-worker", "2"), ("pantry-twitch-dispatcher", "1")):
            _kubectl("-n", args.namespace, "scale", f"deployment/{deployment}", f"--replicas={replicas}")
            _kubectl("-n", args.namespace, "rollout", "status", f"deployment/{deployment}", "--timeout=180s")

    def fence() -> None:
        subprocess.run(["systemctl", "stop", "k3s.service"], check=True, timeout=30)

    adapters = PromotionAdapters(acquire, is_primary, promote, switch_endpoint, enable_roles, fence, renew)
    promoter = OraclePromoter(adapters)
    while not promoter.run_once():
        time.sleep(max(1, args.lease_seconds // 3))
    while promoter.renew_or_fence():
        time.sleep(max(1, args.lease_seconds // 3))
    raise SystemExit("Oracle authority lost; local writer domain fenced")


if __name__ == "__main__":
    run()
