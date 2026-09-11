import unittest

import health


class StandbyHealthTests(unittest.TestCase):
    def test_standby_health_port_is_reusable_after_stop(self):
        first_stop = health.start_background(19091)
        first_stop()

        second_stop = health.start_background(19091)
        second_stop()


if __name__ == "__main__":
    unittest.main()
