"""Site-scoped Opsbot ownership backed by the failover witness."""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import os
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class OwnershipError(RuntimeError):
    """Opsbot is not allowed to start or perform a protected operation."""


@dataclass(frozen=True)
class OwnershipConfig:
    site: str
    lease_seconds: int = 30
    witness_url: str = ""
    witness_secret: str = ""

    @classmethod
    def from_env(cls) -> "OwnershipConfig":
        site = os.environ.get("OPSBOT_SITE", "").strip()
        witness_url = os.environ.get("OPSBOT_WITNESS_URL", "").strip().rstrip("/")
        secret = os.environ.get("OPSBOT_WITNESS_SECRET", "")
        if site not in {"home", "oracle"}:
            raise OwnershipError("OPSBOT_SITE must be exactly home or oracle")
        if not witness_url:
            raise OwnershipError("OPSBOT_WITNESS_URL is required")
        if not secret:
            raise OwnershipError("OPSBOT_WITNESS_SECRET is required")
        try:
            lease_seconds = int(os.environ.get("OPSBOT_LEASE_SECONDS", "30"))
        except ValueError as error:
            raise OwnershipError("OPSBOT_LEASE_SECONDS must be an integer") from error
        if lease_seconds < 3:
            raise OwnershipError("OPSBOT_LEASE_SECONDS must be at least 3")
        return cls(site, lease_seconds, witness_url, secret)


@dataclass(frozen=True)
class Lease:
    site: str
    epoch: int
    expires_at: float
    token: str

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "Lease":
        return cls(
            site=str(payload["site"]),
            epoch=int(payload["epoch"]),
            expires_at=float(payload["expires_at"]),
            token=str(payload["token"]),
        )


class WitnessClient:
    def __init__(self, config: OwnershipConfig):
        self.config = config

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        request = Request(
            f"{self.config.witness_url}{path}",
            data=json.dumps(payload).encode(),
            headers={
                "Authorization": f"Bearer {self.config.witness_secret}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=5) as response:
                return json.loads(response.read())
        except HTTPError as error:
            if error.code == 409:
                return None
            raise OwnershipError(f"witness HTTP {error.code}") from error
        except (URLError, TimeoutError, OSError, json.JSONDecodeError) as error:
            raise OwnershipError(f"witness unavailable: {error}") from error

    def acquire(self, site: str) -> Lease | None:
        payload = self._post("/v1/authority/acquire", {"site": site})
        return Lease.from_payload(payload) if payload else None

    def renew(self, site: str, epoch: int, token: str) -> bool:
        payload = self._post(
            "/v1/authority/renew",
            {"site": site, "epoch": epoch, "token": token},
        )
        return bool(payload and payload.get("ok"))


class Ownership:
    def __init__(self, config: OwnershipConfig, witness: Any):
        self.config = config
        self.witness = witness
        self.lease: Lease | None = None
        self._valid = False
        self._fence_reason = "not acquired"
        self.renew_task: asyncio.Task[None] | None = None
        self._on_lost: Callable[[], Awaitable[None] | None] | None = None

    @classmethod
    def from_env(cls) -> "Ownership":
        config = OwnershipConfig.from_env()
        return cls(config, WitnessClient(config))

    def acquire(self) -> Lease:
        lease = self.witness.acquire(self.config.site)
        if (
            not lease
            or lease.site != self.config.site
            or lease.epoch < 1
            or not lease.token
            or lease.expires_at <= time.time()
        ):
            self._fence_reason = "site lease unavailable"
            raise OwnershipError(
                f"no valid Opsbot lease for site {self.config.site!r}"
            )
        self.lease = lease
        self._valid = True
        self._fence_reason = ""
        return lease

    def is_valid(self) -> bool:
        return self._valid and self.lease is not None and self.lease.expires_at > time.time()

    def require(self) -> None:
        if not self.is_valid():
            raise OwnershipError(f"Opsbot fenced: {self._fence_reason or 'lease expired'}")

    def renew_once(self) -> bool:
        if not self.is_valid():
            self._fence("lease expired before renewal")
            return False
        assert self.lease is not None
        try:
            renewed = self.witness.renew(
                self.config.site, self.lease.epoch, self.lease.token
            )
        except Exception as error:
            self._fence(f"lease renewal failed: {error}")
            return False
        if not renewed:
            self._fence("witness rejected lease renewal")
            return False
        self.lease = Lease(
            self.lease.site,
            self.lease.epoch,
            time.time() + self.config.lease_seconds,
            self.lease.token,
        )
        return True

    def _fence(self, reason: str) -> None:
        was_valid = self._valid
        self._valid = False
        self._fence_reason = reason
        if was_valid:
            print(f"[opsbot] ownership fenced: {reason}")

    def start(self, on_lost: Callable[[], Awaitable[None] | None]) -> None:
        self._on_lost = on_lost
        self.renew_task = asyncio.create_task(self._renew_loop())

    async def _renew_loop(self) -> None:
        while self.is_valid():
            if not self.renew_once():
                if self._on_lost:
                    result = self._on_lost()
                    if inspect.isawaitable(result):
                        await result
                return
            await asyncio.sleep(max(1, self.config.lease_seconds / 3))

    async def stop(self) -> None:
        if self.renew_task and self.renew_task is not asyncio.current_task():
            self.renew_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.renew_task
        self.renew_task = None
