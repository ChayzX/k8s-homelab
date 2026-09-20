#!/usr/bin/env python3
"""Small, authenticated fencing-epoch witness for the two-site failover plan."""

from __future__ import annotations

import argparse
import copy
import hashlib
import hmac
import json
import math
import os
import secrets
import tempfile
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock
from typing import Any


class WitnessState:
    def __init__(self, path: Path, secret: str, lease_seconds: int = 30) -> None:
        self.path = path
        self.secret = secret.encode()
        self.lease_seconds = lease_seconds
        self.lock = Lock()
        self.persistence_failed = False
        self.data = self._load()

    def _load(self) -> dict[str, Any]:
        try:
            loaded = json.loads(self.path.read_text())
            if not isinstance(loaded, dict):
                raise ValueError("state must be an object")
            if "leases" in loaded:
                if not isinstance(loaded["leases"], dict):
                    raise ValueError("leases must be an object; refusing to reset epochs")
            else:
                # Preserve a valid pre-resource lease, never reinterpret a
                # corrupt resource map as a new, empty authority history.
                loaded = {"leases": {"default": loaded}}
            for resource, lease in loaded["leases"].items():
                if not isinstance(resource, str) or not resource or not isinstance(lease, dict):
                    raise ValueError("invalid durable lease")
                if type(lease.get("epoch")) is not int or lease["epoch"] < 0:
                    raise ValueError("invalid durable epoch")
                if lease.get("holder") not in {None, "home", "oracle", "canada"}:
                    raise ValueError("invalid durable holder")
                expires = lease.get("expires_at")
                if not isinstance(expires, (int, float)) or not math.isfinite(expires):
                    raise ValueError("invalid durable expiry")
            return loaded
        except FileNotFoundError:
            return {"leases": {}}

    def _lease(self, resource: str) -> dict[str, Any]:
        leases = self.data.setdefault("leases", {})
        return leases.setdefault(
            resource,
            {"epoch": 0, "holder": None, "expires_at": 0, "token": None, "token_hash": None},
        )

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w") as output:
                json.dump(self.data, output, sort_keys=True)
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)
            # Persist the rename as well as the new file's contents. Without
            # this, a host crash can roll back an acknowledged fencing epoch.
            if os.name == "posix":
                directory_fd = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _persist_or_restore(self, before: dict[str, Any]) -> None:
        try:
            self._save()
        except Exception:
            self.persistence_failed = True
            # Never let a later same-site acquire return a token whose write
            # failed. If replace succeeded but directory fsync failed, reload
            # the on-disk generation so its epoch is never reused in memory.
            try:
                self.data = self._load()
            except Exception:
                self.data = before
            raise

    def authorized(self, supplied: str | None) -> bool:
        return bool(supplied) and hmac.compare_digest(supplied.encode(), self.secret)

    def prometheus_metrics(self, now: float | None = None) -> str:
        """Return token-free health and authority metrics for PostgreSQL."""
        resource = "pantry:postgres"
        with self.lock:
            observed_at = time.time() if now is None else now
            healthy = 0 if self.persistence_failed else 1
            lease = self.data.get("leases", {}).get(resource, {})
            epoch = int(lease.get("epoch", 0))
            holder = lease.get("holder") or "none"
            expires_at = float(lease.get("expires_at", 0))
            active = int(holder != "none" and expires_at > observed_at)
        return "\n".join(
            (
                "# HELP failover_witness_healthy Whether the witness can safely grant or renew authority.",
                "# TYPE failover_witness_healthy gauge",
                f"failover_witness_healthy {healthy}",
                "# HELP failover_witness_lease_epoch Latest durable fencing epoch for the resource.",
                "# TYPE failover_witness_lease_epoch gauge",
                f'failover_witness_lease_epoch{{resource="{resource}"}} {epoch}',
                "# HELP failover_witness_lease_expires_at_seconds Lease expiry as a Unix timestamp.",
                "# TYPE failover_witness_lease_expires_at_seconds gauge",
                f'failover_witness_lease_expires_at_seconds{{resource="{resource}"}} {expires_at}',
                "# HELP failover_witness_lease_active Whether the recorded holder has an unexpired lease.",
                "# TYPE failover_witness_lease_active gauge",
                f'failover_witness_lease_active{{resource="{resource}",holder="{holder}"}} {active}',
                "",
            )
        )

    def acquire(self, site: str, resource: str = "default") -> dict[str, Any] | None:
        now = time.time()
        with self.lock:
            if self.persistence_failed:
                raise RuntimeError("witness storage fault requires recovery before granting authority")
            before = copy.deepcopy(self.data)
            lease = self._lease(resource)
            if lease["holder"] and lease["expires_at"] > now:
                if lease["holder"] != site or not lease.get("token"):
                    return None
                return {"site": site, "epoch": lease["epoch"], "expires_at": lease["expires_at"], "token": lease["token"]}
            lease["epoch"] = int(lease["epoch"]) + 1
            token = secrets.token_urlsafe(32)
            lease.update(
                holder=site,
                expires_at=now + self.lease_seconds,
                token=token,
                token_hash=hashlib.sha256(token.encode()).hexdigest(),
            )
            self._persist_or_restore(before)
            return {"site": site, "epoch": lease["epoch"], "expires_at": lease["expires_at"], "token": token}

    def renew(self, site: str, epoch: int, token: str, resource: str = "default") -> bool:
        with self.lock:
            if self.persistence_failed:
                raise RuntimeError("witness storage fault requires recovery before renewing authority")
            before = copy.deepcopy(self.data)
            lease = self._lease(resource)
            valid = (
                lease["holder"] == site
                and int(lease["epoch"]) == epoch
                and lease["expires_at"] > time.time()
                and hmac.compare_digest(lease["token_hash"] or "", hashlib.sha256(token.encode()).hexdigest())
            )
            if not valid:
                return False
            lease["expires_at"] = time.time() + self.lease_seconds
            self._persist_or_restore(before)
            return True


class Handler(BaseHTTPRequestHandler):
    state: WitnessState

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(length) or b"{}")

    def _text(self, status: int, body: str) -> None:
        encoded = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/healthz":
            ok = not self.state.persistence_failed
            self._json(HTTPStatus.OK if ok else HTTPStatus.SERVICE_UNAVAILABLE, {"ok": ok})
        elif self.path == "/metrics":
            self._text(HTTPStatus.OK, self.state.prometheus_metrics())
        else:
            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        supplied = self.headers.get("Authorization", "").removeprefix("Bearer ")
        if not self.state.authorized(supplied):
            self._json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
            return
        try:
            body = self._body()
            site = str(body["site"])
            resource = str(body.get("resource", "default"))
            if not resource or len(resource) > 128:
                raise ValueError("resource must be between 1 and 128 characters")
            if not site or site not in {"home", "oracle", "canada"}:
                raise ValueError("site must be home, oracle, or canada")
            if self.path == "/v1/authority/acquire":
                result = self.state.acquire(site, resource)
                self._json(HTTPStatus.OK if result else HTTPStatus.CONFLICT, result or {"error": "lease held"})
                return
            if self.path == "/v1/authority/renew":
                ok = self.state.renew(site, int(body["epoch"]), str(body["token"]), resource)
                self._json(HTTPStatus.OK if ok else HTTPStatus.CONFLICT, {"ok": ok})
                return
            raise ValueError("not found")
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            self._json(HTTPStatus.BAD_REQUEST if str(error) != "not found" else HTTPStatus.NOT_FOUND, {"error": str(error)})
        except (OSError, RuntimeError):
            self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "witness storage unavailable"})

    def log_message(self, *_args: object) -> None:
        return


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--listen", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--state", type=Path, default=Path("/var/lib/failover-witness/state.json"))
    parser.add_argument("--initialize-state", action="store_true", help="Explicit first installation only; never use to recover lost epochs")
    args = parser.parse_args()
    secret = os.environ.get("WITNESS_SHARED_SECRET")
    if not secret:
        raise SystemExit("WITNESS_SHARED_SECRET is required")
    if not args.state.exists() and not args.initialize_state:
        raise SystemExit("witness state is missing; restore its durable epoch history before restarting (first install: --initialize-state)")
    Handler.state = WitnessState(args.state, secret)
    ThreadingHTTPServer((args.listen, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
