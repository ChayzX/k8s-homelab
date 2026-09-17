import unittest

import health


class StandbyHealthTests(unittest.TestCase):
    def tearDown(self):
        health.mark_not_ready()

    def test_fencing_withdraws_readiness(self):
        import asyncio

        health.mark_ready()
        ready_response = asyncio.run(health._health(None))
        self.assertEqual(ready_response.status, 200)

        health.mark_not_ready()
        fenced_response = asyncio.run(health._health(None))
        self.assertEqual(fenced_response.status, 503)

    def test_standby_health_port_is_reusable_after_stop(self):
        first_stop = health.start_background(19091)
        first_stop()

        second_stop = health.start_background(19091)
        second_stop()


if __name__ == "__main__":
    unittest.main()
