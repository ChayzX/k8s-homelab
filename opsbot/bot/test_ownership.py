#!/usr/bin/env python3
"""Focused ownership/fencing contract tests for Opsbot."""

import asyncio
import os
import unittest
from unittest.mock import Mock

from ownership import Lease, Ownership, OwnershipConfig, OwnershipError


class FakeWitness:
    def __init__(self, lease=None, renew_result=True):
        self.lease = lease
        self.renew_result = renew_result
        self.acquires = []
        self.renewals = []

    def acquire(self, site):
        self.acquires.append(site)
        return self.lease

    def renew(self, site, epoch, token):
        self.renewals.append((site, epoch, token))
        return self.renew_result


class OwnershipTests(unittest.TestCase):
    def test_missing_site_or_witness_credentials_is_a_startup_gate(self):
        original = {key: os.environ.pop(key, None) for key in (
            "OPSBOT_SITE", "OPSBOT_WITNESS_URL", "OPSBOT_WITNESS_SECRET"
        )}
        try:
            with self.assertRaises(OwnershipError):
                Ownership.from_env()
        finally:
            for key, value in original.items():
                if value is not None:
                    os.environ[key] = value

    def test_acquire_rejects_a_lease_for_another_site(self):
        witness = FakeWitness(Lease(site="oracle", epoch=4, expires_at=999, token="t"))
        ownership = Ownership(OwnershipConfig("home", 30), witness)

        with self.assertRaises(OwnershipError):
            ownership.acquire()
        self.assertFalse(ownership.is_valid())

    def test_acquire_rejects_a_lease_without_a_fencing_token(self):
        witness = FakeWitness(Lease(site="home", epoch=4, expires_at=9999999999, token=""))
        ownership = Ownership(OwnershipConfig("home", 30), witness)

        with self.assertRaises(OwnershipError):
            ownership.acquire()

    def test_valid_lease_is_required_and_renewal_loss_fences(self):
        witness = FakeWitness(Lease(site="home", epoch=7, expires_at=9999999999, token="t"), False)
        ownership = Ownership(OwnershipConfig("home", 30), witness)

        ownership.acquire()
        ownership.require()
        self.assertFalse(ownership.renew_once())
        with self.assertRaises(OwnershipError):
            ownership.require()

    def test_fence_callback_stops_runtime_after_renewal_loss(self):
        witness = FakeWitness(Lease(site="home", epoch=7, expires_at=9999999999, token="t"), False)
        ownership = Ownership(OwnershipConfig("home", 30), witness)
        ownership.acquire()
        stopped = asyncio.Event()

        async def stop():
            stopped.set()

        async def run():
            ownership.start(stop)
            await asyncio.sleep(0)
            await ownership.renew_task
            self.assertTrue(stopped.is_set())
            await ownership.stop()

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
