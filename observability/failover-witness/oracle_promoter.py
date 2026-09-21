#!/usr/bin/env python3
"""Guarded Oracle promotion controller for the PantryBot PostgreSQL standby."""

from __future__ import annotations

import argparse
import base64
import json
import os
import shlex
import subprocess
import tempfile
import time
from pathlib import Path
from threading import Event, Lock, Thread
from dataclasses import dataclass
from typing import Any, Callable
from urllib import request
from urllib.error import HTTPError

from command_policy import parse_operator_command


ORACLE_PROMOTION_DEPLOYMENTS = (
    "pantry-commands-site",
    "pantry-private-api",
    "pantry-private-site",
    "pantry-overlay-delivery",
    "pantry-twitch-gateway",
    "pantry-chat-worker",
    "pantry-twitch-dispatcher",
    # Without this, the overlay public route stays broken after an otherwise
    # complete automatic promotion: the new site's overlay backend comes up
    # but nothing serves its public tunnel. Caught live 2026-09-21 — the old
    # site's now-backend-less app-cloudflared kept round-robin-competing with
    # (nonexistent) traffic to the new site, producing intermittent/total 502s.
    "app-cloudflared",
)


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
    verify_promoted: Callable[[], bool] | None = None
    check_replication: Callable[[], None] | None = None
    publish_routes: Callable[[], None] | None = None
    verify_service: Callable[[], None] | None = None
    validate_generation: Callable[[dict[str, Any]], bool] | None = None
    record_progress: Callable[[dict[str, Any], str], None] | None = None


class ActivationJournal:
    """A primary may resume only the same durable activation and DB identity."""

    def __init__(self, path: Path, site: str = 'oracle') -> None:
        self.path = path
        self.site = site

    def load(self) -> dict[str, Any] | None:
        try:
            result = json.loads(self.path.read_text())
        except FileNotFoundError:
            return None
        if not isinstance(result, dict):
            raise RuntimeError("invalid activation journal")
        return result

    def may_resume(self, epoch: int, system_identifier: str) -> bool:
        prior = self.load()
        return bool(prior and prior.get('site') == self.site
                    and prior.get('resource') == 'pantry:postgres'
                    and prior.get('epoch') == epoch
                    and prior.get('system_identifier') == system_identifier
                    and prior.get('phase') in {'promoting', 'promoted', 'active'})

    def record(self, epoch: int, system_identifier: str, phase: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix='.activation.', dir=self.path.parent)
        try:
            with os.fdopen(fd, 'w') as stream:
                json.dump({'site': self.site, 'resource': 'pantry:postgres', 'epoch': epoch,
                           'system_identifier': system_identifier, 'phase': phase}, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)
            if os.name == 'posix':
                directory_fd = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


class AuthorityLost(RuntimeError):
    """No more activation operations may begin with this lease."""


class LeaseGuard:
    """Renew throughout slow activation; loss triggers the independent local fence.

    This guard supplements fencing. It cannot prove a paused host or an external
    subprocess stopped; the next owner still needs positive old-writer fencing.
    """

    def __init__(self, renew: Callable[[], bool], fence: Callable[[], None], interval: float) -> None:
        self.renew = renew
        self.fence = fence
        self.interval = interval
        self.stopped = Event()
        self.lost = Event()
        self.thread: Thread | None = None

    def start(self) -> None:
        # Verify possession immediately, before any infrastructure changes.
        if not self.renew():
            self.lost.set()
            self.fence()
            raise AuthorityLost("authority lost before activation")
        self.thread = Thread(target=self._run, name="postgres-authority-renewal", daemon=True)
        self.thread.start()

    def _run(self) -> None:
        while not self.stopped.wait(self.interval):
            try:
                valid = self.renew()
            except Exception:
                valid = False
            if not valid:
                self.lost.set()
                try:
                    self.fence()
                finally:
                    return

    def check(self) -> None:
        if self.lost.is_set():
            raise AuthorityLost("authority lost during activation")

    def close(self) -> None:
        self.stopped.set()
        if self.thread:
            self.thread.join(timeout=6)


class OraclePromoter:
    def __init__(self, adapters: PromotionAdapters, renewal_interval: float = 5) -> None:
        self.adapters = adapters
        self.token: dict[str, Any] | None = None
        self.promoted = False
        self.fenced = False
        self.renewal_interval = renewal_interval
        self.fence_lock = Lock()
        self.activation_guard: LeaseGuard | None = None

    def assert_activation_authority(self) -> None:
        if self.fenced or not self.activation_guard:
            raise AuthorityLost("activation authority is unavailable")
        self.activation_guard.check()

    def _fence(self) -> None:
        with self.fence_lock:
            if self.fenced:
                return
            self.adapters.fence()
            self.fenced = True
            self.token = None

    def run_once(self) -> bool:
        if self.fenced:
            raise AuthorityLost("fenced controller requires restart and recovery checks")
        if self.adapters.ready and not self.adapters.ready():
            return False
        try:
            token = self.adapters.acquire()
        except Exception:
            # A restarted writer cannot retain authority merely because the
            # witness request failed instead of returning a conflict.
            self._fence()
            raise
        if not token:
            if self.adapters.is_primary and self.adapters.is_primary():
                self._fence()
            return False
        guard = None
        if self.adapters.renew:
            guard = LeaseGuard(lambda: self.adapters.renew(token), self._fence, self.renewal_interval)
        self.activation_guard = guard
        def step(action: Callable[[], Any]) -> Any:
            if guard:
                guard.check()
            result = action()
            if guard:
                guard.check()
            return result
        resuming = False
        try:
            if guard:
                guard.start()
            if self.adapters.validate_generation:
                resuming = bool(step(lambda: self.adapters.validate_generation(token)))
            if not resuming:
                if self.adapters.fence_old_writer:
                    step(self.adapters.fence_old_writer)
                if self.adapters.check_replication:
                    step(self.adapters.check_replication)
                if self.adapters.record_progress:
                    step(lambda: self.adapters.record_progress(token, "promoting"))
                step(lambda: self.adapters.promote(token))
                if self.adapters.verify_promoted and not step(self.adapters.verify_promoted):
                    raise RuntimeError("target PostgreSQL promotion could not be verified")
                if self.adapters.record_progress:
                    step(lambda: self.adapters.record_progress(token, "promoted"))
        except Exception:
            # Everything up to and including a verified promotion is a
            # genuine authority/safety concern: fence, because the target
            # may be in an ambiguous or split-brain-risking state.
            self._fence()
            raise
        try:
            step(self.adapters.switch_endpoint)
            step(self.adapters.enable_roles)
            if self.adapters.verify_service:
                step(self.adapters.verify_service)
            if self.adapters.publish_routes:
                step(self.adapters.publish_routes)
            if self.adapters.record_progress:
                step(lambda: self.adapters.record_progress(token, "active"))
        except Exception:
            # The database is already correctly and verifiably promoted at
            # this point (verify_promoted passed, "promoted" was recorded).
            # A failure here is an operational/app-tier problem — self-fencing
            # would destroy a good primary over something like one slow or
            # crash-looping deployment rollout. Let it propagate so the
            # caller retries the remaining steps (the durable activation
            # journal lets the next attempt resume past validate_generation
            # instead of re-fencing anything) without touching the database.
            raise
        finally:
            if guard:
                guard.close()
            self.activation_guard = None
        # Closing can overlap one final in-flight renewal. Never declare a
        # controller active after its renewal thread already revoked authority.
        if guard:
            guard.check()
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
    # oculum-ignore-next-line [dangerous_function]: fixed kubectl executable with
    # caller-supplied argv only; no shell interpolation and a hard timeout.
    try:
        result = subprocess.run(["kubectl", "--request-timeout=15s", *args], check=True, capture_output=True, text=True, input=input_text, timeout=195)
    except subprocess.CalledProcessError as error:
        # Without this, the real failure reason (a missing kubeconfig, RBAC
        # denial, etc.) is invisible in journalctl — only "returned non-zero
        # exit status 1" ever surfaces, which cost a long live debugging
        # session to work around by reproducing the exact command by hand.
        stderr = (error.stderr or "").strip()
        raise RuntimeError(f"kubectl {' '.join(args)} failed rc={error.returncode}: {stderr}") from error
    return result.stdout.strip()


def _postgres_promote_command(data_directory: str) -> tuple[str, ...]:
    """Return the in-container promotion command for the official Postgres image.

    The standby pod currently runs its container as root, while PostgreSQL
    refuses to run ``pg_ctl`` as root.  The official image provides ``gosu``
    for the required least-privilege transition; unlike ``su-exec``, it is
    present in the live image.
    """
    return ("gosu", "postgres", "pg_ctl", "-D", data_directory, "promote")


def _postgres_query_command(port: int, user: str, database: str, query: str) -> tuple[str, ...]:
    """Build a deterministic in-pod psql command for the live Oracle topology."""
    return ("sh", "-ec", f"psql -h 127.0.0.1 -p {port} -U {shlex.quote(user)} -d {shlex.quote(database)} -Atc {shlex.quote(query)}")


def _primary_statefulset_patch() -> str:
    """Switch the promoted candidate from standby bootstrap to primary readiness.

    The reseed StatefulSet uses an init container and standby-only readiness
    while it is a replica. Once PostgreSQL is promoted, retaining either would
    make the pod fail readiness (or re-run basebackup on a restart), leaving the
    controller with a writable database but no endpoint. The patch is applied
    only after pg_is_in_recovery() becomes false and is idempotent at the caller.
    """
    return json.dumps([
        {"op": "remove", "path": "/spec/template/spec/initContainers"},
        {
            "op": "replace",
            "path": "/spec/template/spec/containers/0/readinessProbe/exec/command/2",
            "value": 'test "$(psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "select pg_is_in_recovery()")" = f',
        },
    ], separators=(",", ":"))


def run() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--witness-url", default=os.environ.get("WITNESS_URL"))
    parser.add_argument("--secret", default=os.environ.get("WITNESS_SHARED_SECRET"))
    parser.add_argument("--lease-seconds", type=int, default=30)
    parser.add_argument("--namespace", default="pantry-bot")
    parser.add_argument("--pod-namespace", default=None, help="namespace containing the PostgreSQL pod")
    parser.add_argument("--service-namespace", default=None, help="namespace containing the application Service and platform Secret")
    parser.add_argument("--pod", default="postgres-authority-standby-reseed-v2-0")
    parser.add_argument("--service", default="postgres-authority-standby-reseed-v2")
    parser.add_argument("--statefulset", default="postgres-authority-standby-reseed-v2")
    parser.add_argument("--data-directory", default="/var/lib/postgresql/data")
    parser.add_argument("--postgres-port", type=int, default=int(os.environ.get("PANTRY_ORACLE_POSTGRES_PORT", "5432")))
    parser.add_argument("--postgres-user", default=os.environ.get("PANTRY_ORACLE_POSTGRES_USER", "pantry"))
    parser.add_argument("--postgres-database", default=os.environ.get("PANTRY_ORACLE_POSTGRES_DATABASE", "pantry"))
    parser.add_argument("--manual-endpoint", action="store_true", help="retain a manually managed Endpoints object instead of changing Service selectors")
    parser.add_argument("--old-writer-fence-command", default=os.environ.get("OLD_WRITER_FENCE_COMMAND"))
    parser.add_argument("--local-writer-fence-command", default=os.environ.get("LOCAL_WRITER_FENCE_COMMAND"))
    parser.add_argument("--replication-check-command", default=os.environ.get("REPLICATION_CHECK_COMMAND"))
    parser.add_argument("--publish-routes-command", default=os.environ.get("PUBLISH_ROUTES_COMMAND"))
    parser.add_argument("--service-check-command", default=os.environ.get("SERVICE_CHECK_COMMAND"))
    parser.add_argument("--state-path", type=Path, default=Path("/var/lib/pantry-postgres-promoter/activation.json"))
    parser.add_argument("--site", choices=['home', 'oracle', 'canada'], default=os.environ.get('PANTRY_PROMOTION_SITE_NAME', 'oracle'))
    args = parser.parse_args()
    if not args.witness_url or not args.secret:
        raise SystemExit("WITNESS_URL and WITNESS_SHARED_SECRET are required")
    # Missing adapters must stop startup BEFORE acquiring a production lease.
    for name in ("old_writer_fence_command", "local_writer_fence_command", "replication_check_command", "publish_routes_command", "service_check_command"):
        if not getattr(args, name) or not shlex.split(getattr(args, name)):
            raise SystemExit(f"{name.upper()} is required before automatic promotion")
    if not 15 <= args.lease_seconds <= 30:
        raise SystemExit("--lease-seconds must match the witness TTL (15..30)")

    pod_namespace = args.pod_namespace or args.namespace
    service_namespace = args.service_namespace or args.namespace

    journal = ActivationJournal(args.state_path, site=args.site)
    generation: dict[str, Any] = {}

    def run_hook(command: str, timeout: int = 30) -> None:
        hook_env = os.environ.copy()
        hook_env.update(PANTRY_PROMOTION_SITE=args.site, PANTRY_PROMOTION_RESOURCE="pantry:postgres",
                        PANTRY_PROMOTION_EPOCH=str(generation.get("epoch", "")),
                        PANTRY_DATABASE_SYSTEM_IDENTIFIER=str(generation.get("system_identifier", "")),
                        PANTRY_RESUME_PRIMARY="true" if generation.get("resume") else "false")
        # oculum-ignore-next-line [dangerous_function]: explicit operator hook is parsed to argv, never passed to a shell, and timeout-bounded
        subprocess.run(shlex.split(command), check=True, timeout=timeout, env=hook_env)

    def activation_kubectl(*arguments: str) -> str:
        # Recheck between EACH rollout command, not only after the whole loop.
        promoter.assert_activation_authority()
        return _kubectl(*arguments)

    def acquire() -> dict[str, Any] | None:
        try:
            result = _post(args.witness_url, args.secret, "/v1/authority/acquire", {"site": args.site, "resource": "pantry:postgres"})
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
            {"site": args.site, "resource": "pantry:postgres", "epoch": token["epoch"], "token": token["token"]},
        )
        return result.get("ok") is True

    def promote(_token: dict[str, Any]) -> None:
        recovery = _kubectl("-n", pod_namespace, "exec", args.pod, "--", *_postgres_query_command(args.postgres_port, args.postgres_user, args.postgres_database, "select pg_is_in_recovery();"))
        if recovery == "t":
            activation_kubectl("-n", pod_namespace, "exec", args.pod, "--", *_postgres_promote_command(args.data_directory))
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                if _kubectl("-n", pod_namespace, "exec", args.pod, "--", *_postgres_query_command(args.postgres_port, args.postgres_user, args.postgres_database, "select pg_is_in_recovery();")) == "f":
                    break
                time.sleep(1)
            else:
                raise RuntimeError("Oracle PostgreSQL did not leave recovery mode")
        elif recovery != "f":
            raise RuntimeError(f"unexpected Oracle recovery state: {recovery!r}")
        # The candidate manifest is intentionally standby-shaped. Once the
        # database is writable, remove the basebackup init and switch readiness
        # to primary mode before routing or enabling application roles.
        init_containers = _kubectl(
            "-n", pod_namespace, "get", "statefulset", args.statefulset,
            "-o", "jsonpath={.spec.template.spec.initContainers}",
        )
        if init_containers:
            activation_kubectl(
                "-n", pod_namespace, "patch", "statefulset", args.statefulset,
                "--type=json", "-p", _primary_statefulset_patch(),
            )
            activation_kubectl(
                "-n", pod_namespace, "rollout", "status",
                f"statefulset/{args.statefulset}", "--timeout=180s",
            )
        activation_kubectl("-n", pod_namespace, "label", "pod", args.pod, "pantrybot.postgres/role=primary", "--overwrite")

    def is_primary() -> bool:
        try:
            return _kubectl("-n", pod_namespace, "exec", args.pod, "--", *_postgres_query_command(args.postgres_port, args.postgres_user, args.postgres_database, "select pg_is_in_recovery();")) == "f"
        except Exception:
            return False

    def ready() -> bool:
        try:
            _kubectl("version", "--request-timeout=5s")
            return True
        except Exception:
            return False

    def switch_endpoint() -> None:
        if not args.manual_endpoint:
            selector = json.dumps({"app.kubernetes.io/name": f"pantry-{args.statefulset}", "pantrybot.postgres/role": "primary"}, separators=(",", ":"))
            activation_kubectl("-n", service_namespace, "patch", "service", args.service, "--type=merge", "-p", json.dumps({"spec": {"selector": json.loads(selector)}}))
        encoded = _kubectl("-n", service_namespace, "get", "secret", "pantry-bot-platform", "-o", "jsonpath={.data.PANTRY_DATABASE_URL}")
        current = base64.b64decode(encoded).decode()
        if "@" not in current:
            raise RuntimeError("PANTRY_DATABASE_URL has no authority component")
        userinfo = current.rsplit("@", 1)[0]
        database = current.rsplit("/", 1)[-1]
        local = f"{userinfo}@{args.service}.{service_namespace}.svc.cluster.local:5432/{database}"
        replacement = base64.b64encode(local.encode()).decode()
        activation_kubectl("-n", service_namespace, "patch", "secret", "pantry-bot-platform", "--type=merge", "-p", json.dumps({"data": {"PANTRY_DATABASE_URL": replacement}}))

    def verify_promoted() -> bool:
        # Promotion must be observed from the database before routing or
        # mutating roles are enabled. A successful pg_ctl command alone is not
        # proof that PostgreSQL left recovery mode.
        return is_primary()

    def enable_roles() -> None:
        for deployment in ORACLE_PROMOTION_DEPLOYMENTS:
            activation_kubectl("-n", service_namespace, "scale", f"deployment/{deployment}", "--replicas=1")
            activation_kubectl("-n", service_namespace, "rollout", "restart", f"deployment/{deployment}")
            _kubectl("-n", service_namespace, "rollout", "status", f"deployment/{deployment}", "--timeout=180s")

    def fence() -> None:
        run_hook(args.local_writer_fence_command)
        if generation:
            journal.record(generation["epoch"], generation["system_identifier"], "fenced")

    def validate_generation(token: dict[str, Any]) -> bool:
        identity = _kubectl("-n", pod_namespace, "exec", args.pod, "--", *_postgres_query_command(args.postgres_port, args.postgres_user, args.postgres_database, "select system_identifier from pg_control_system();"))
        if not identity.isdigit():
            raise RuntimeError("target database identity is unknown")
        primary = is_primary()
        generation.update(epoch=token["epoch"], system_identifier=identity, resume=primary)
        if not primary:
            return False
        if not journal.may_resume(token["epoch"], identity):
            raise RuntimeError("already-primary target has no matching durable activation receipt")
        # A legitimate resume: the target is already promoted with a durable
        # receipt matching this exact epoch and database identity. Re-running
        # fence_old_writer/check_replication/promote here would be wrong, not
        # just redundant — check_replication in particular correctly rejects
        # an already-primary target for not being a standby, which previously
        # caused a resumed (successful) promotion to self-fence itself on the
        # very next controller restart.
        return True

    def record_progress(token: dict[str, Any], phase: str) -> None:
        with promoter.fence_lock:
            promoter.assert_activation_authority()
            journal.record(token["epoch"], generation["system_identifier"], phase)

    def fence_old_writer() -> None:
        if not args.old_writer_fence_command:
            raise RuntimeError("OLD_WRITER_FENCE_COMMAND is required before promotion")
        try:
            command = parse_operator_command(args.old_writer_fence_command, "OLD_WRITER_FENCE_COMMAND")
        except ValueError as error:
            raise RuntimeError(str(error)) from error
        # oculum-ignore-next-line [dangerous_function]: explicit operator fence argv parsed without shell interpolation and timeout-bounded
        subprocess.run(command, check=True, timeout=30)

    adapters = PromotionAdapters(
        acquire, is_primary, promote, switch_endpoint, enable_roles, fence,
        renew, ready, fence_old_writer, verify_promoted,
        check_replication=lambda: run_hook(args.replication_check_command),
        publish_routes=lambda: run_hook(args.publish_routes_command),
        verify_service=lambda: run_hook(args.service_check_command),
        validate_generation=validate_generation,
        record_progress=record_progress,
    )
    promoter = OraclePromoter(adapters, renewal_interval=min(5, args.lease_seconds / 3))
    while not promoter.run_once():
        time.sleep(max(1, args.lease_seconds // 3))
    while promoter.renew_or_fence():
        time.sleep(max(1, args.lease_seconds // 3))
    raise SystemExit(f"{args.site} authority lost; local writer domain fenced")


if __name__ == "__main__":
    run()
