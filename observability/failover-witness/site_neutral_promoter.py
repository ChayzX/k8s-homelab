#!/usr/bin/env python3
"""Site-neutral PantryBot PostgreSQL promotion controller.

This controller replaces the Oracle-hardcoded ``oracle_promoter.py``. A single
binary can run on any PantryBot site (home, oracle, or canada); the site
identity, PostgreSQL pod/service, deployment set, platform Secret, and the two
fence commands are supplied per host through CLI flags and a root-owned
EnvironmentFile (never committed).

It keeps the original guarded semantics:

* acquire a short-lived witness authority lease for ``pantry:postgres``;
* refuse to promote while another site holds the lease;
* run an explicit out-of-band fence for the old writer before promotion;
* promote the local standby, re-point the local Service endpoint Secret, and
  only then restart the application tier;
* on any failure or lease loss, fence the local writer domain.

Only PantryBot PostgreSQL is promoted here. Authentik is home-only by design
and is never auto-promoted by this controller.

Site preference: home and oracle are always preferred over canada. Canada is a
last-resort site only and is never automatically promoted: canada promotion
requires the explicit ``--allow-canada-last-resort`` flag (caller-supplied
twist), is refused in serve mode (``--once`` only), and must be the result of a
manual recovery runbook, never an enabled systemd unit.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import shlex
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence
from urllib import request
from urllib.error import HTTPError

VALID_SITES = ("home", "oracle", "canada")
PROMOTION_RESOURCE = "pantry:postgres"


@dataclass
class SiteConfig:
    site: str
    witness_url: str
    secret: str
    namespace: str = "pantry-bot"
    pod: str = "postgres-authority-standby-0"
    service: str = "postgres-authority-standby"
    service_label_name: str = "pantry-postgres-authority-standby"
    role_label: str = "pantrybot.postgres/role"
    platform_secret: str = "pantry-bot-platform"
    database_url_key: str = "PANTRY_DATABASE_URL"
    deployments: tuple[str, ...] = (
        "pantry-commands-site",
        "pantry-private-api",
        "pantry-private-site",
        "pantry-overlay-delivery",
        "pantry-twitch-gateway",
        "pantry-chat-worker",
        "pantry-twitch-dispatcher",
    )
    role_deployments: tuple[tuple[str, str], ...] = (
        ("pantry-twitch-gateway", "1"),
        ("pantry-chat-worker", "2"),
        ("pantry-twitch-dispatcher", "1"),
    )
    lease_seconds: int = 30
    promote_timeout: int = 60
    rollout_timeout: int = 180
    fence_command: tuple[str, ...] = ("/usr/local/lib/failover-witness/fence-writer-domain.sh", "k3s.service")
    allow_canada_last_resort: bool = False
    old_writer_fence_command: tuple[str, ...] = ()
    kubectl: str = "kubectl"


@dataclass
class PromotionAdapters:
    acquire: Callable[[], dict[str, Any] | None]
    is_primary: Callable[[], bool] | None
    promote: Callable[[dict[str, Any]], None]
    switch_endpoint: Callable[[], None]
    enable_roles: Callable[[], None]
    fence: Callable[[], None]
    renew: Callable[[dict[str, Any]], bool] | None = None
    ready: Callable[[], bool] | None = None
    fence_old_writer: Callable[[], None] | None = None


class SitePromoter:
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
        if self.adapters.ready and not self.adapters.ready():
            return False
        token = self.adapters.acquire()
        if not token:
            if self.adapters.is_primary and self.adapters.is_primary():
                self._fence()
            return False
        try:
            if self.adapters.fence_old_writer:
                self.adapters.fence_old_writer()
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


def _postgres_promote_command(data_directory: str) -> tuple[str, ...]:
    """Return a pg_ctl command that is valid in the official Postgres image."""
    return ("su-exec", "postgres", "pg_ctl", "-D", data_directory, "promote")


def _make_kubectl(binary: str) -> Callable[..., str]:
    def _kubectl(*args: str, input_text: str | None = None) -> str:
        result = subprocess.run(
            [binary, *args],
            check=True,
            capture_output=True,
            text=True,
            input=input_text,
        )
        return result.stdout.strip()

    return _kubectl


def _shlex_tuple(value: str) -> tuple[str, ...]:
    return tuple(shlex.split(value))


def _split_deployments(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in value.split(",") if part.strip())


def _split_role_deployments(value: str) -> tuple[tuple[str, str], ...]:
    roles = []
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        name, sep, replicas = part.rpartition("=")
        if not sep or not replicas:
            raise argparse.ArgumentTypeError(f"role deployment must be name=replicas, got {part!r}")
        roles.append((name.strip(), replicas.strip()))
    return tuple(roles)


def build_adapters(config: SiteConfig) -> PromotionAdapters:
    if config.site not in VALID_SITES:
        raise ValueError(f"site must be one of {', '.join(VALID_SITES)}")
    if config.site == "canada" and not config.allow_canada_last_resort:
        raise ValueError("canada is a last-resort site; promotion requires --allow-canada-last-resort")
    if not config.old_writer_fence_command:
        raise ValueError("old-writer fence command must not be empty (required before promotion)")

    kubectl = _make_kubectl(config.kubectl)

    def acquire() -> dict[str, Any] | None:
        try:
            result = _post(
                config.witness_url,
                config.secret,
                "/v1/authority/acquire",
                {"site": config.site, "resource": PROMOTION_RESOURCE},
            )
        except HTTPError as error:
            if error.code == 409:
                return None
            raise
        return result if result.get("token") else None

    def renew(token: dict[str, Any]) -> bool:
        result = _post(
            config.witness_url,
            config.secret,
            "/v1/authority/renew",
            {
                "site": config.site,
                "resource": PROMOTION_RESOURCE,
                "epoch": token["epoch"],
                "token": token["token"],
            },
        )
        return result.get("ok") is True

    def promote(_token: dict[str, Any]) -> None:
        recovery = _kubectl(
            "-n", config.namespace, "exec", config.pod, "--", "sh", "-ec",
            'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "select pg_is_in_recovery();"',
        )
        if recovery == "t":
            _kubectl("-n", config.namespace, "exec", config.pod, "--", *_postgres_promote_command("/var/lib/postgresql/data"))
            deadline = time.monotonic() + config.promote_timeout
            while time.monotonic() < deadline:
                if _kubectl(
                    "-n", config.namespace, "exec", config.pod, "--", "sh", "-ec",
                    'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "select pg_is_in_recovery();"',
                ) == "f":
                    break
                time.sleep(1)
            else:
                raise RuntimeError(f"{config.site} PostgreSQL did not leave recovery mode")
        elif recovery != "f":
            raise RuntimeError(f"unexpected {config.site} recovery state: {recovery!r}")
        _kubectl("-n", config.namespace, "label", "pod", config.pod, f"{config.role_label}=primary", "--overwrite")

    def is_primary() -> bool:
        try:
            return _kubectl(
                "-n", config.namespace, "exec", config.pod, "--", "sh", "-ec",
                'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "select pg_is_in_recovery();"',
            ) == "f"
        except Exception:
            return False

    def ready() -> bool:
        try:
            _kubectl("version", "--request-timeout=5s")
            return True
        except Exception:
            return False

    def switch_endpoint() -> None:
        selector = json.dumps(
            {"app.kubernetes.io/name": config.service_label_name, config.role_label: "primary"},
            separators=(",", ":"),
        )
        _kubectl(
            "-n", config.namespace, "patch", "service", config.service,
            "--type=merge", "-p", json.dumps({"spec": {"selector": json.loads(selector)}}),
        )
        encoded = _kubectl("-n", config.namespace, "get", "secret", config.platform_secret, "-o", f"jsonpath={{.data.{config.database_url_key}}}")
        current = base64.b64decode(encoded).decode()
        if "@" not in current:
            raise RuntimeError(f"{config.database_url_key} has no authority component")
        userinfo = current.rsplit("@", 1)[0]
        database = current.rsplit("/", 1)[-1]
        local = f"{userinfo}@{config.service}.{config.namespace}.svc.cluster.local:5432/{database}"
        replacement = base64.b64encode(local.encode()).decode()
        _kubectl(
            "-n", config.namespace, "patch", "secret", config.platform_secret,
            "--type=merge", "-p", json.dumps({"data": {config.database_url_key: replacement}}),
        )
        for deployment in config.deployments:
            _kubectl("-n", config.namespace, "rollout", "restart", f"deployment/{deployment}")
            _kubectl("-n", config.namespace, "rollout", "status", f"deployment/{deployment}", f"--timeout={config.rollout_timeout}s")

    def enable_roles() -> None:
        for deployment, replicas in config.role_deployments:
            _kubectl("-n", config.namespace, "scale", f"deployment/{deployment}", f"--replicas={replicas}")
            _kubectl("-n", config.namespace, "rollout", "status", f"deployment/{deployment}", f"--timeout={config.rollout_timeout}s")

    def fence() -> None:
        subprocess.run(config.fence_command, check=True, timeout=30)

    def fence_old_writer() -> None:
        subprocess.run(config.old_writer_fence_command, check=True, timeout=30)

    return PromotionAdapters(acquire, is_primary, promote, switch_endpoint, enable_roles, fence, renew, ready, fence_old_writer)


def make_parser(env: dict[str, str] | None = None) -> argparse.ArgumentParser:
    environment = dict(os.environ if env is None else env)
    parser = argparse.ArgumentParser(description="Site-neutral PantryBot PostgreSQL promotion controller")
    parser.add_argument("--site", default=environment.get("PROMOTION_SITE"))
    parser.add_argument("--witness-url", default=environment.get("WITNESS_URL"))
    parser.add_argument("--secret", default=environment.get("WITNESS_SHARED_SECRET"))
    parser.add_argument("--lease-seconds", type=int, default=30, dest="lease_seconds")
    parser.add_argument("--once", action="store_true", help="single acquire+promote pass, then exit (0 on success, 1 otherwise)")
    parser.add_argument("--namespace", default="pantry-bot")
    parser.add_argument("--pod", default="postgres-authority-standby-0")
    parser.add_argument("--service", default="postgres-authority-standby")
    parser.add_argument("--service-label-name", default="pantry-postgres-authority-standby")
    parser.add_argument("--role-label", default="pantrybot.postgres/role")
    parser.add_argument("--platform-secret", default="pantry-bot-platform")
    parser.add_argument("--database-url-key", default="PANTRY_DATABASE_URL")
    parser.add_argument("--old-writer-fence-command", default=environment.get("OLD_WRITER_FENCE_COMMAND"))
    parser.add_argument(
        "--allow-canada-last-resort",
        action="store_true",
        default=environment.get("CANADA_LAST_RESORT") == "1",
        help="permit a one-shot canada promotion (last-resort site; never in serve mode)",
    )
    parser.add_argument("--fence-command", default="/usr/local/lib/failover-witness/fence-writer-domain.sh k3s.service")
    parser.add_argument("--promote-timeout", type=int, default=60, dest="promote_timeout")
    parser.add_argument("--rollout-timeout", type=int, default=180, dest="rollout_timeout")
    parser.add_argument("--kubectl", default=environment.get("KUBECTL_BIN", "kubectl"))
    parser.add_argument(
        "--deployments",
        type=_split_deployments,
        default=(
            "pantry-commands-site",
            "pantry-private-api",
            "pantry-private-site",
            "pantry-overlay-delivery",
            "pantry-twitch-gateway",
            "pantry-chat-worker",
            "pantry-twitch-dispatcher",
        ),
    )
    parser.add_argument(
        "--role-deployments",
        type=_split_role_deployments,
        default=(("pantry-twitch-gateway", "1"), ("pantry-chat-worker", "2"), ("pantry-twitch-dispatcher", "1")),
    )
    return parser


def config_from_args(args: argparse.Namespace) -> SiteConfig:
    if not args.site:
        raise SystemExit("PROMOTION_SITE (or --site) is required")
    if not args.witness_url or not args.secret:
        raise SystemExit("WITNESS_URL and WITNESS_SHARED_SECRET are required")
    return SiteConfig(
        site=args.site,
        witness_url=args.witness_url,
        secret=args.secret,
        namespace=args.namespace,
        pod=args.pod,
        service=args.service,
        service_label_name=args.service_label_name,
        role_label=args.role_label,
        platform_secret=args.platform_secret,
        database_url_key=args.database_url_key,
        deployments=args.deployments,
        role_deployments=args.role_deployments,
        lease_seconds=args.lease_seconds,
        promote_timeout=args.promote_timeout,
        rollout_timeout=args.rollout_timeout,
        fence_command=_shlex_tuple(args.fence_command),
        old_writer_fence_command=_shlex_tuple(args.old_writer_fence_command or ""),
        kubectl=args.kubectl,
        allow_canada_last_resort=args.allow_canada_last_resort,
    )


def run() -> None:
    args = make_parser().parse_args()
    if args.site == "canada" and not args.once:
        raise SystemExit("canada is a last-resort site; serve mode refused (--once required)")
    config = config_from_args(args)
    promoter = SitePromoter(build_adapters(config))
    if not args.once:
        while not promoter.run_once():
            time.sleep(max(1, config.lease_seconds // 3))
        while promoter.renew_or_fence():
            time.sleep(max(1, config.lease_seconds // 3))
        raise SystemExit(f"{config.site} authority lost; local writer domain fenced")
    if not promoter.run_once():
        raise SystemExit(f"{config.site} authority not acquired; promotion refused (fail-closed)")
    print(f"{config.site}_promotion_complete")


if __name__ == "__main__":
    run()