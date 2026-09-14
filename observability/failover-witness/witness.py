#!/usr/bin/env python3
"""Small, authenticated fencing-epoch witness for the two-site failover plan."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
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
        self.data = self._load()

    def _load(self) -> dict[str, Any]:
        try:
            loaded = json.loads(self.path.read_text())
            if not isinstance(loaded, dict):
                raise ValueError("state must be an object")
            if "leases" in loaded and isinstance(loaded["leases"], dict):
                return loaded
            # Preserve the pre-resource single lease as the legacy default.
            return {"leases": {"default": loaded}}
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
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def authorized(self, supplied: str | None) -> bool:
        return bool(supplied) and hmac.compare_digest(supplied.encode(), self.secret)

    def acquire(self, site: str, resource: str = "default") -> dict[str, Any] | None:
        now = time.time()
        with self.lock:
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
            self._save()
            return {"site": site, "epoch": lease["epoch"], "expires_at": lease["expires_at"], "token": token}

    def renew(self, site: str, epoch: int, token: str, resource: str = "default") -> bool:
        with self.lock:
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
            self._save()
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

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/healthz":
            self._json(HTTPStatus.OK, {"ok": True})
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

    def log_message(self, *_args: object) -> None:
        return


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--listen", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--state", type=Path, default=Path("/var/lib/failover-witness/state.json"))
    args = parser.parse_args()
    secret = os.environ.get("WITNESS_SHARED_SECRET")
    if not secret:
        raise SystemExit("WITNESS_SHARED_SECRET is required")
    Handler.state = WitnessState(args.state, secret)
    ThreadingHTTPServer((args.listen, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
