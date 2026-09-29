#!/usr/bin/env python3
"""POST /alerts/grafana: auth, validation, delivery, fencing, rate limiting."""

import asyncio
import json
import unittest

from aiohttp.test_utils import AioHTTPTestCase

import alerts
import health

TOKEN = "t" * 64


def grafana_payload(status="firing", n=1, alertname="PantryBot metrics absent"):
    return {
        "receiver": "opsbot-dm",
        "status": status,
        "orgId": 1,
        "alerts": [
            {
                "status": status,
                "labels": {
                    "alertname": alertname,
                    "grafana_folder": "pantry-bot",
                    "severity": "critical",
                    "job": f"pantry-bot-api-oracle-{i}",
                },
                "annotations": {"summary": f"target {i} is down"},
                "generatorURL": "http://grafana.local/alerting/grafana/x/view",
                "fingerprint": f"fp{i}",
            }
            for i in range(n)
        ],
        "groupLabels": {"alertname": alertname, "grafana_folder": "pantry-bot"},
        "commonLabels": {
            "alertname": alertname,
            "grafana_folder": "pantry-bot",
            "severity": "critical",
        },
        "commonAnnotations": {},
        "externalURL": "http://grafana.local/",
        "version": "1",
        "groupKey": "{}:{alertname=\"x\"}",
        "truncatedAlerts": 0,
    }


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class RelayTestCase(AioHTTPTestCase):
    max_per_window = 5

    async def get_application(self):
        self.sent = []
        self.deliverable = True
        self.fail_send = False
        self.clock = FakeClock()

        async def send(text):
            if self.fail_send:
                raise RuntimeError("discord down")
            self.sent.append(text)

        self.relay = alerts.AlertRelay(
            TOKEN,
            send,
            lambda: self.deliverable,
            max_per_window=self.max_per_window,
            window_seconds=60,
            send_timeout=1,
            clock=self.clock,
            flush_poll_seconds=0.01,
        )
        health.set_alert_relay(self.relay)
        return health._application()

    async def asyncTearDown(self):
        task = self.relay._flush_task
        if task is not None and not task.done():
            task.cancel()
        health.set_alert_relay(None)
        await super().asyncTearDown()

    async def post(self, body, token=TOKEN, raw=False):
        headers = {"Content-Type": "application/json"}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        data = body if raw else json.dumps(body)
        return await self.client.post("/alerts/grafana", data=data, headers=headers)


class AuthAndValidationTests(RelayTestCase):
    async def test_missing_token_is_401(self):
        response = await self.post(grafana_payload(), token=None)
        self.assertEqual(response.status, 401)
        self.assertEqual(self.sent, [])

    async def test_wrong_token_is_401(self):
        response = await self.post(grafana_payload(), token="nope")
        self.assertEqual(response.status, 401)

    async def test_non_bearer_scheme_is_401(self):
        response = await self.client.post(
            "/alerts/grafana",
            data=json.dumps(grafana_payload()),
            headers={"Authorization": f"Basic {TOKEN}"},
        )
        self.assertEqual(response.status, 401)

    async def test_non_json_is_400(self):
        response = await self.post(b"not json{", raw=True)
        self.assertEqual(response.status, 400)

    async def test_json_without_alerts_is_400(self):
        response = await self.post({"hello": "world"})
        self.assertEqual(response.status, 400)

    async def test_oversize_body_is_413(self):
        big = grafana_payload()
        big["message"] = "x" * (alerts.MAX_BODY_BYTES + 10)
        response = await self.post(big)
        self.assertEqual(response.status, 413)
        self.assertEqual(self.sent, [])

    async def test_oversize_chunked_body_is_413(self):
        async def gen():
            for _ in range(10):
                yield b"x" * 10_000

        response = await self.client.post(
            "/alerts/grafana", data=gen(), headers={"Authorization": f"Bearer {TOKEN}"}
        )
        self.assertEqual(response.status, 413)

    async def test_get_is_not_allowed(self):
        response = await self.client.get("/alerts/grafana")
        self.assertEqual(response.status, 405)


class DeliveryTests(RelayTestCase):
    async def test_valid_notification_sends_one_dm(self):
        response = await self.post(grafana_payload(n=2))
        self.assertEqual(response.status, 200)
        self.assertEqual(len(self.sent), 1)
        dm = self.sent[0]
        self.assertIn("[FIRING] PantryBot metrics absent", dm)
        self.assertIn("2 alerts", dm)
        self.assertIn("severity=critical", dm)
        self.assertIn("job=pantry-bot-api-oracle-1", dm)
        self.assertIn("target 0 is down", dm)
        self.assertIn("http://grafana.local/alerting/grafana/x/view", dm)
        self.assertNotIn(TOKEN, dm)

    async def test_resolved_status(self):
        await self.post(grafana_payload(status="resolved"))
        self.assertIn("[RESOLVED]", self.sent[0])

    async def test_not_owner_is_503_and_not_sent(self):
        self.deliverable = False
        response = await self.post(grafana_payload())
        self.assertEqual(response.status, 503)
        self.assertEqual(self.sent, [])

    async def test_discord_failure_is_503(self):
        self.fail_send = True
        response = await self.post(grafana_payload())
        self.assertEqual(response.status, 503)

    async def test_failed_send_does_not_consume_budget(self):
        self.fail_send = True
        for _ in range(10):
            await self.post(grafana_payload())
        self.fail_send = False
        response = await self.post(grafana_payload())
        self.assertEqual(response.status, 200)


class RateLimitTests(RelayTestCase):
    max_per_window = 2

    async def test_excess_is_coalesced_and_summarised_later(self):
        for _ in range(2):
            self.assertEqual((await self.post(grafana_payload())).status, 200)
        for _ in range(3):
            self.assertEqual((await self.post(grafana_payload())).status, 202)
        self.assertEqual(len(self.sent), 2)

        # Window rolls over: the flusher sends exactly one summary DM.
        self.clock.now += 61
        await asyncio.wait_for(self.relay._flush_task, timeout=2)
        self.assertEqual(len(self.sent), 3)
        self.assertIn("[COALESCED]", self.sent[2])
        self.assertIn("3 more Grafana notifications", self.sent[2])
        self.assertIn("FIRING PantryBot metrics absent x3", self.sent[2])

    async def test_coalesced_summary_is_retried_not_dropped(self):
        for _ in range(3):
            await self.post(grafana_payload())
        self.fail_send = True
        self.clock.now += 61
        await asyncio.sleep(0.2)  # first flush attempt fails
        self.assertEqual(len(self.sent), 2)
        self.assertTrue(self.relay._suppressed)
        self.fail_send = False
        self.clock.now += 6  # past the 5s retry backoff
        await asyncio.wait_for(self.relay._flush_task, timeout=2)
        self.assertIn("[COALESCED]", self.sent[-1])
        self.assertFalse(self.relay._suppressed)


class UnconfiguredAndStandbyTests(unittest.IsolatedAsyncioTestCase):
    async def test_unset_token_is_503(self):
        async def send(_text):
            raise AssertionError("must not send")

        relay = alerts.AlertRelay("", send, lambda: True)
        app = health._application()
        from aiohttp.test_utils import TestClient, TestServer

        health.set_alert_relay(relay)
        try:
            async with TestClient(TestServer(app)) as client:
                response = await client.post(
                    "/alerts/grafana", json=grafana_payload(), headers={"Authorization": "Bearer "}
                )
                self.assertEqual(response.status, 503)
        finally:
            health.set_alert_relay(None)

    async def test_no_relay_registered_is_503(self):
        from aiohttp.test_utils import TestClient, TestServer

        health.set_alert_relay(None)
        async with TestClient(TestServer(health._application())) as client:
            response = await client.post(
                "/alerts/grafana", json=grafana_payload(), headers={"Authorization": f"Bearer {TOKEN}"}
            )
            self.assertEqual(response.status, 503)


class FormattingTests(unittest.TestCase):
    def test_long_payload_fits_discord_limit(self):
        payload = grafana_payload(n=50)
        for alert in payload["alerts"]:
            alert["annotations"]["summary"] = "y" * 5000
        text = alerts.format_notification(payload)
        self.assertLessEqual(len(text), 2000)
        self.assertIn("…and 45 more", text)

    def test_mixed_alertnames(self):
        payload = grafana_payload(n=2)
        payload["groupLabels"] = {}
        payload["commonLabels"] = {}
        payload["alerts"][1]["labels"]["alertname"] = "Other"
        self.assertIn("2 alert rules", alerts.format_notification(payload))


if __name__ == "__main__":
    unittest.main()
