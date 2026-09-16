#!/usr/bin/env python3
"""Contract and behavior tests for the site-neutral PantryBot promoter."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest import mock

from site_neutral_promoter import (
    PROMOTION_RESOURCE,
    VALID_SITES,
    SiteConfig,
    SitePromoter,
    build_adapters,
    config_from_args,
    make_parser,
)


ROOT = Path(__file__).parent
SCRIPT = ROOT / "site_neutral_promoter.py"


def make_config(**overrides: object) -> SiteConfig:
    values: dict[str, object] = {
        "site": "oracle",
        "witness_url": "http://127.0.0.1:8765",
        "secret": "test-secret",
        "old_writer_fence_command": ("fence-old", "--confirm"),
        "fence_command": ("fence-local", "k3s.service"),
    }
    values.update(overrides)
    return SiteConfig(**values)


class PromoterContractTests(unittest.TestCase):
    def test_script_declares_site_neutral_pantry_only_controller(self):
        text = SCRIPT.read_text()
        self.assertIn("home", text)
        self.assertIn("oracle", text)
        self.assertIn("canada", text)
        self.assertIn('PROMOTION_RESOURCE = "pantry:postgres"', text)
        self.assertTrue("Authentik is home-only by design" in text or "never auto-promoted" in text)

    def test_valid_sites_match_the_witness(self):
        witness_text = (ROOT / "witness.py").read_text()
        for site in VALID_SITES:
            self.assertIn(f'"{site}"', witness_text)

    def test_config_requires_old_writer_fence_before_promotion(self):
        config = make_config(old_writer_fence_command=())
        with self.assertRaises(ValueError):
            build_adapters(config)

    def test_config_rejects_unknown_site(self):
        config = make_config(site="mars")
        with self.assertRaises(ValueError):
            build_adapters(config)

    def test_promotion_resource_matches_witness_lease(self):
        self.assertEqual(PROMOTION_RESOURCE, "pantry:postgres")

    def test_witness_json_acceptance_contract(self):
        body = json.dumps({"site": "canada", "resource": PROMOTION_RESOURCE})
        self.assertIn('"resource"', body)
        self.assertTrue(body.split('"resource": "')[1].startswith("pantry:postgres"))


class PromoterBehaviorTests(unittest.TestCase):
    def test_refuses_promotion_when_lease_held_and_not_primary(self):
        adapters = build_adapters(make_config())
        adapters.acquire = lambda: None
        adapters.is_primary = lambda: False
        adapters.ready = lambda: True
        adapters.fence = mock.Mock()
        promoter = SitePromoter(adapters)
        self.assertFalse(promoter.run_once())
        adapters.fence.assert_not_called()
        self.assertFalse(promoter.promoted)

    def test_fences_local_writer_when_another_site_is_primary(self):
        adapters = build_adapters(make_config())
        adapters.acquire = lambda: None
        adapters.is_primary = lambda: True
        adapters.ready = lambda: True
        adapters.fence = mock.Mock()
        promoter = SitePromoter(adapters)
        self.assertFalse(promoter.run_once())
        adapters.fence.assert_called_once()

    def test_promotes_and_fences_old_writer_in_order(self):
        order: list[str] = []
        adapters = build_adapters(make_config())
        token = {"epoch": 7, "token": "tok"}
        adapters.ready = lambda: True
        adapters.acquire = lambda: token
        adapters.fence_old_writer = lambda: order.append("fence_old_writer")
        adapters.promote = lambda _token: order.append("promote")
        adapters.switch_endpoint = lambda: order.append("switch_endpoint")
        adapters.enable_roles = lambda: order.append("enable_roles")
        adapters.fence = mock.Mock()
        promoter = SitePromoter(adapters)
        self.assertTrue(promoter.run_once())
        self.assertEqual(order, ["fence_old_writer", "promote", "switch_endpoint", "enable_roles"])
        self.assertTrue(promoter.promoted)
        adapters.fence.assert_not_called()

    def test_failure_during_promotion_fences_and_reraises(self):
        adapters = build_adapters(make_config())
        adapters.ready = lambda: True
        adapters.acquire = lambda: {"epoch": 8, "token": "tok"}
        adapters.promote = mock.Mock(side_effect=RuntimeError("boom"))
        adapters.fence_old_writer = mock.Mock()
        adapters.fence = mock.Mock()
        promoter = SitePromoter(adapters)
        with self.assertRaises(RuntimeError):
            promoter.run_once()
        adapters.fence.assert_called_once()
        self.assertFalse(promoter.promoted)

    def test_renew_failure_fences(self):
        adapters = build_adapters(make_config())
        promoter = SitePromoter(adapters)
        promoter.promoted = True
        promoter.token = {"epoch": 9, "token": "tok"}
        adapters.renew = lambda _token: False
        adapters.fence = mock.Mock()
        self.assertFalse(promoter.renew_or_fence())
        adapters.fence.assert_called_once()

    def test_renew_success_keeps_lease(self):
        adapters = build_adapters(make_config())
        promoter = SitePromoter(adapters)
        promoter.promoted = True
        promoter.token = {"epoch": 10, "token": "tok"}
        adapters.renew = lambda _token: True
        adapters.fence = mock.Mock()
        self.assertTrue(promoter.renew_or_fence())
        adapters.fence.assert_not_called()


class PromoterCliTests(unittest.TestCase):
    def test_cli_environment_and_flags(self):
        env = {
            "PROMOTION_SITE": "canada",
            "WITNESS_URL": "http://witness:8765",
            "WITNESS_SHARED_SECRET": "s",
        }
        parser = make_parser(env)
        args = parser.parse_args(["--old-writer-fence-command", "fence-old --confirm"])
        config = config_from_args(args)
        self.assertEqual(config.site, "canada")
        self.assertEqual(config.witness_url, "http://witness:8765")
        self.assertEqual(config.old_writer_fence_command, ("fence-old", "--confirm"))
        self.assertEqual(config.deployments[0], "pantry-commands-site")
        self.assertEqual(config.role_deployments[0], ("pantry-twitch-gateway", "1"))

    def test_cli_custom_deployments_parsed(self):
        parser = make_parser()
        args = parser.parse_args(
            [
                "--site", "home",
                "--witness-url", "http://w:8765",
                "--secret", "s",
                "--old-writer-fence-command", "f old",
                "--deployments", "a-service,b-worker",
                "--role-deployments", "x=1,y=2",
            ]
        )
        config = config_from_args(args)
        self.assertEqual(config.deployments, ("a-service", "b-worker"))
        self.assertEqual(config.role_deployments, (("x", "1"), ("y", "2")))

    def test_once_flag_is_accepted(self):
        parser = make_parser()
        args = parser.parse_args(["--once"])
        self.assertTrue(args.once)
        plain = make_parser().parse_args([])
        self.assertFalse(plain.once)


if __name__ == "__main__":
    unittest.main()