#!/usr/bin/env python3
"""Guarded Oracle promotion controller for Authentik PostgreSQL.

This controller is intentionally opt-in.  It acquires the shared witness
authority, runs an explicit out-of-band fence command for the old home writer,
then changes the standby role, switches the Oracle-local Authentik secret to
the promoted service, and only then restarts the application tier. Losing the
lease fences the local Oracle writer domain.

The controller does not claim physical fencing of the home site.  Production
automatic promotion must remain disabled until the documented old-writer
fencing rehearsal has passed.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import time
from typing import Any
from urllib import request
from urllib.error import HTTPError

from oracle_promoter import OraclePromoter, PromotionAdapters, _kubectl, _postgres_promote_command
from command_policy import parse_operator_command


AUTHENTIK_DEPLOYMENTS = (
    "auth-authentik-server",
    "auth-authentik-worker",
)


def _parse_local_authentik_fence_command(raw: str | None) -> tuple[str, ...]:
    """Validate the local fence executable before any witness lease is used.

    Authentik must not accidentally run one of the PantryBot fence adapters.
    The executable is local to the Authentik writer domain, so unlike the
    remote old-writer command it must also exist and be executable at startup.
    """

    if not raw or not raw.strip():
        raise ValueError("--fence-command is required for Authentik promotion")
    try:
        command = parse_operator_command(raw, "--fence-command")
    except ValueError as error:
        raise ValueError(str(error)) from error
    lowered = tuple(token.lower() for token in command)
    if any("pantry" in token for token in lowered):
        raise ValueError("--fence-command must not use a Pantry-only fence command")
    executable = command[0]
    if not os.path.isfile(executable) or not os.access(executable, os.X_OK):
        raise ValueError(f"--fence-command executable is missing or not executable: {executable}")
    return command


def _authentik_target_ready(kubectl: Any, namespace: str, pod: str, service: str) -> bool:
    """Check the target pod and Service without mutating the cluster.

    This is deliberately a small, injectable probe so unit tests can exercise
    the fail-closed behavior without a production kubeconfig or API server.
    """

    try:
        kubectl("version", "--request-timeout=5s")
        pod_document = json.loads(
            kubectl("-n", namespace, "get", "pod", pod, "-o", "json")
        )
        if pod_document.get("status", {}).get("phase") != "Running":
            return False
        postgres_ready = any(
            status.get("name") == "postgres" and status.get("ready") is True
            for status in pod_document.get("status", {}).get("containerStatuses", [])
        )
        if not postgres_ready:
            return False
        kubectl("-n", namespace, "get", "service", service, "-o", "name")
        return True
    except (AttributeError, OSError, TypeError, ValueError, KeyError, subprocess.SubprocessError):
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


def _secret_data(namespace: str, secret_name: str, key: str) -> str:
    return _kubectl("-n", namespace, "get", "secret", secret_name, "-o", f"jsonpath={{.data.{key}}}")


def _patch_secret_key(namespace: str, secret_name: str, key: str, value: str) -> None:
    encoded = base64.b64encode(value.encode()).decode()
    patch = json.dumps({"data": {key: encoded}}, separators=(",", ":"))
    _kubectl("-n", namespace, "patch", "secret", secret_name, "--type=merge", "-p", patch)


def build_adapters(args: argparse.Namespace) -> PromotionAdapters:
    old_writer_fence_command = parse_operator_command(args.old_writer_fence_command, "--old-writer-fence-command")
    local_fence_command = _parse_local_authentik_fence_command(
        getattr(args, "fence_command", None)
    )

    def acquire() -> dict[str, Any] | None:
        try:
            result = _post(
                args.witness_url,
                args.secret,
                "/v1/authority/acquire",
                {"site": "oracle", "resource": "auth:postgres"},
            )
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
            {
                "site": "oracle",
                "resource": "auth:postgres",
                "epoch": token["epoch"],
                "token": token["token"],
            },
        )
        return result.get("ok") is True

    def recovery_state() -> str:
        return _kubectl(
            "-n",
            args.namespace,
            "exec",
            args.pod,
            "--",
            "sh",
            "-ec",
            'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "select pg_is_in_recovery();"',
        )

    def promote(_token: dict[str, Any]) -> None:
        recovery = recovery_state()
        if recovery == "t":
            _kubectl(
                "-n",
                args.namespace,
                "exec",
                args.pod,
                "--",
                *_postgres_promote_command("/var/lib/postgresql/data"),
            )
            deadline = time.monotonic() + args.timeout
            while time.monotonic() < deadline:
                if recovery_state() == "f":
                    break
                time.sleep(1)
            else:
                raise RuntimeError("Oracle Authentik PostgreSQL did not leave recovery mode")
        elif recovery != "f":
            raise RuntimeError(f"unexpected Oracle recovery state: {recovery!r}")
        _kubectl(
            "-n",
            args.namespace,
            "label",
            "pod",
            args.pod,
            "authentik.postgres/role=primary",
            "--overwrite",
        )

    def is_primary() -> bool:
        try:
            return recovery_state() == "f"
        except Exception:
            return False

    def ready() -> bool:
        return _authentik_target_ready(_kubectl, args.namespace, args.pod, args.service)

    def switch_endpoint() -> None:
        selector = {"app.kubernetes.io/name": "auth-postgresql-standby", "authentik.postgres/role": "primary"}
        _kubectl(
            "-n",
            args.namespace,
            "patch",
            "service",
            args.service,
            "--type=merge",
            "-p",
            json.dumps({"spec": {"selector": selector}}, separators=(",", ":")),
        )
        _patch_secret_key(args.namespace, args.secret_name, "AUTHENTIK_POSTGRESQL__HOST", f"{args.service}.{args.namespace}.svc.cluster.local")
        _patch_secret_key(args.namespace, args.secret_name, "AUTHENTIK_POSTGRESQL__PORT", "5432")
        for deployment in AUTHENTIK_DEPLOYMENTS:
            _kubectl("-n", args.namespace, "rollout", "restart", f"deployment/{deployment}")
            _kubectl("-n", args.namespace, "rollout", "status", f"deployment/{deployment}", "--timeout=180s")

    def enable_roles() -> None:
        for deployment in AUTHENTIK_DEPLOYMENTS:
            _kubectl("-n", args.namespace, "scale", f"deployment/{deployment}", "--replicas=1" if deployment.endswith("server") else "--replicas=2")
            _kubectl("-n", args.namespace, "rollout", "status", f"deployment/{deployment}", "--timeout=180s")

    def fence() -> None:
        # oculum-ignore-next-line [dangerous_function]: operator-supplied fence executable is argv-only and timeout-bounded
        subprocess.run(
            local_fence_command,
            check=True,
            timeout=30,
        )

    def fence_old_writer() -> None:
        # oculum-ignore-next-line [dangerous_function]: validated argv from explicit operator fence configuration; no shell
        subprocess.run(old_writer_fence_command, check=True, timeout=30)

    return PromotionAdapters(acquire, is_primary, promote, switch_endpoint, enable_roles, fence, renew, ready, fence_old_writer)


def run() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--witness-url", default=os.environ.get("WITNESS_URL"))
    parser.add_argument("--secret", default=os.environ.get("WITNESS_SHARED_SECRET"))
    parser.add_argument("--namespace", default="auth")
    parser.add_argument("--pod", default="auth-postgresql-standby-0")
    parser.add_argument("--service", default="auth-postgresql-standby")
    parser.add_argument("--secret-name", default="auth-authentik")
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--lease-seconds", type=int, default=30)
    parser.add_argument("--fence-command", default="/usr/local/lib/failover-witness/fence-writer-domain.sh")
    parser.add_argument(
        "--old-writer-fence-command",
        required=True,
        help="out-of-band command that fences the home writer and fails unless fencing succeeds",
    )
    args = parser.parse_args()
    if not args.witness_url or not args.secret:
        raise SystemExit("WITNESS_URL and WITNESS_SHARED_SECRET are required")
    try:
        adapters = build_adapters(args)
    except ValueError as error:
        raise SystemExit(str(error)) from error
    promoter = OraclePromoter(adapters)
    while not promoter.run_once():
        time.sleep(max(1, args.lease_seconds // 3))
    while promoter.renew_or_fence():
        time.sleep(max(1, args.lease_seconds // 3))
    raise SystemExit("Oracle Authentik authority lost; local writer domain fenced")


if __name__ == "__main__":
    run()
