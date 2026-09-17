#!/usr/bin/env python3
"""Fail-closed witness lease agent for a site-local PostgreSQL writer.

This is a software self-fence, not a claim of physical fencing.  The local
writer must be started only after ``acquire_initial`` succeeds.  Any witness
acquire/renew error calls the supplied fence operation immediately.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import time
from dataclasses import dataclass
from typing import Callable, Any
from urllib import request


@dataclass(frozen=True)
class AuthorityToken:
    epoch: int
    token: str


class FenceAgent:
    def __init__(
        self,
        acquire: Callable[[], dict[str, Any] | None],
        renew: Callable[[AuthorityToken], bool],
        fence: Callable[[], None],
        lease_seconds: int,
    ) -> None:
        if lease_seconds < 5:
            raise ValueError("lease_seconds must be at least 5")
        self._acquire = acquire
        self._renew = renew
        self._fence = fence
        self._fenced = False
        self.token: AuthorityToken | None = None
        self.lease_seconds = lease_seconds

    def _fail_closed(self) -> None:
        if self._fenced:
            return
        self._fence()
        self._fenced = True
        self.token = None

    def acquire_initial(self) -> bool:
        try:
            result = self._acquire()
            if not result:
                self._fail_closed()
                return False
            self.token = AuthorityToken(int(result["epoch"]), str(result["token"]))
            return True
        except Exception:
            self._fail_closed()
            return False

    def renew_or_fence(self) -> bool:
        if self._fenced or self.token is None:
            self._fail_closed()
            return False
        try:
            if self._renew(self.token):
                return True
        except Exception:
            pass
        self._fail_closed()
        return False


def _post(base_url: str, secret: str, path: str, body: dict[str, Any], timeout: float) -> dict[str, Any]:
    payload = json.dumps(body).encode()
    req = request.Request(
        f"{base_url.rstrip('/')}{path}",
        data=payload,
        method="POST",
        headers={"Authorization": f"Bearer {secret}", "Content-Type": "application/json"},
    )
    with request.urlopen(req, timeout=timeout) as response:
        decoded = json.loads(response.read())
    if not isinstance(decoded, dict):
        raise ValueError("witness response must be an object")
    return decoded


def run() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--site", choices=("home", "oracle"), required=True)
    parser.add_argument("--resource", required=True)
    parser.add_argument("--witness-url", default=os.environ.get("WITNESS_URL"))
    parser.add_argument("--secret", default=os.environ.get("WITNESS_SHARED_SECRET"))
    parser.add_argument("--lease-seconds", type=int, default=30)
    parser.add_argument("--fence-command", required=True)
    args = parser.parse_args()
    if not args.witness_url or not args.secret:
        raise SystemExit("WITNESS_URL and WITNESS_SHARED_SECRET are required")

    def acquire() -> dict[str, Any] | None:
        result = _post(args.witness_url, args.secret, "/v1/authority/acquire", {"site": args.site, "resource": args.resource}, 5)
        return result if result.get("token") else None

    def renew(token: AuthorityToken) -> bool:
        result = _post(
            args.witness_url,
            args.secret,
            "/v1/authority/renew",
            {"site": args.site, "resource": args.resource, "epoch": token.epoch, "token": token.token},
            5,
        )
        return result.get("ok") is True

    command = shlex.split(args.fence_command)
    if not command:
        raise SystemExit("--fence-command must not be empty")

    def fence() -> None:
        # oculum-ignore-next-line [dangerous_function]: explicit operator fence argv; shell execution is disabled and timeout is bounded
        subprocess.run(command, check=True, timeout=30)

    agent = FenceAgent(acquire, renew, fence, args.lease_seconds)
    if not agent.acquire_initial():
        raise SystemExit("witness authority unavailable; local writer fenced")
    while True:
        time.sleep(max(1, args.lease_seconds // 3))
        if not agent.renew_or_fence():
            raise SystemExit("witness renewal failed; local writer fenced")


if __name__ == "__main__":
    run()
