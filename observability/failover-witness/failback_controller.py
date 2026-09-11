#!/usr/bin/env python3
"""Guarded Oracle-to-home PostgreSQL failback state machine.

This module deliberately contains only ordering and safety policy.  The
provider-specific adapters must prove each operation against the live system;
no adapter is supplied by default and no production action is implicit.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class FailbackAdapters:
    oracle_is_primary: Callable[[], bool]
    home_is_fenced: Callable[[], bool]
    stop_oracle_roles: Callable[[], None]
    seed_home_standby: Callable[[], None]
    home_standby_caught_up: Callable[[], bool]
    fence_oracle: Callable[[], None]
    acquire_home: Callable[[], dict[str, Any] | None]
    promote_home: Callable[[dict[str, Any]], None]
    switch_home_endpoint: Callable[[], None]
    enable_home_roles: Callable[[], None]
    scale_oracle_roles_down: Callable[[], None]
    renew_home: Callable[[dict[str, Any]], bool] | None = None
    fence_home: Callable[[], None] | None = None


class FailbackController:
    """Perform one guarded return-home transition.

    The Oracle application roles are stopped before copying the final standby
    state.  Oracle is fenced before the home witness lease is acquired.  Home
    application roles are enabled only after local promotion and endpoint
    readiness callbacks succeed.  A renewal failure always invokes the home
    fence callback.
    """

    def __init__(self, adapters: FailbackAdapters) -> None:
        if adapters.fence_home is None:
            raise ValueError("a fence_home adapter is required")
        self.adapters = adapters
        self.token: dict[str, Any] | None = None
        self.promoted = False
        self.fenced = False

    def _fence_home(self) -> None:
        if self.fenced:
            return
        self.adapters.fence_home()
        self.fenced = True
        self.token = None

    def run_once(self) -> bool:
        if not self.adapters.oracle_is_primary():
            return False
        if not self.adapters.home_is_fenced():
            return False

        self.adapters.stop_oracle_roles()
        self.adapters.seed_home_standby()
        if not self.adapters.home_standby_caught_up():
            return False

        self.adapters.fence_oracle()
        token = self.adapters.acquire_home()
        if not token:
            self._fence_home()
            return False
        try:
            self.adapters.promote_home(token)
            self.adapters.switch_home_endpoint()
            self.adapters.enable_home_roles()
            self.adapters.scale_oracle_roles_down()
        except Exception:
            self._fence_home()
            raise
        self.token = token
        self.promoted = True
        return True

    def renew_or_fence(self) -> bool:
        if not self.promoted or not self.token or not self.adapters.renew_home:
            return False
        try:
            if self.adapters.renew_home(self.token):
                return True
        except Exception:
            pass
        self._fence_home()
        return False
