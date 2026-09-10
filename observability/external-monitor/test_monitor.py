import json
import importlib.util
import sys
import tempfile
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("monitor.py")
spec = importlib.util.spec_from_file_location("homelab_monitor", MODULE_PATH)
monitor = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = monitor
spec.loader.exec_module(monitor)


XML = b"""<?xml version='1.0' encoding='UTF-8'?>
<ListBucketResult xmlns='http://s3.amazonaws.com/doc/2006-03-01/'>
  <Contents><Key>recovery/operations/operations.db.gz</Key><LastModified>2026-09-10T01:00:00.000Z</LastModified><Size>42</Size></Contents>
  <Contents><Key>other/old.bin</Key><LastModified>2026-09-01T01:00:00.000Z</LastModified><Size>7</Size></Contents>
</ListBucketResult>
"""


fresh = monitor.r2_freshness_from_xml(
    XML,
    prefix="recovery/operations/",
    max_age_seconds=3600,
    now=monitor.parse_timestamp("2026-09-10T01:30:00.000Z"),
)
assert fresh["ok"] is True
assert fresh["key"] == "recovery/operations/operations.db.gz"
assert fresh["size"] == 42

stale = monitor.r2_freshness_from_xml(
    XML,
    prefix="recovery/operations/",
    max_age_seconds=1200,
    now=monitor.parse_timestamp("2026-09-10T01:30:00.000Z"),
)
assert stale["ok"] is False
assert stale["reason"] == "stale"

missing = monitor.r2_freshness_from_xml(
    XML,
    prefix="recovery/minecraft/",
    max_age_seconds=3600,
    now=monitor.parse_timestamp("2026-09-10T01:30:00.000Z"),
)
assert missing == {"ok": False, "reason": "missing"}

assert monitor.parse_expected_statuses("200, 401,403") == frozenset({200, 401, 403})
assert monitor.parse_expected_statuses("") == frozenset({200})

check_names = {item.name for item in monitor.CHECKS}
assert {"commands", "mods"}.issubset(check_names)
assert next(item for item in monitor.CHECKS if item.name == "commands").url == "https://commands.greeniespantry.uk/"
assert next(item for item in monitor.CHECKS if item.name == "mods").url == "https://mods.greeniespantry.uk/"
assert next(item for item in monitor.CHECKS if item.name == "overlay").url == "https://overlay.greeniespantry.uk/"

# External continuity alerts have durable, per-check identities.  A changed
# failed-check set while the monitor remains degraded must not collapse into a
# single aggregate transition or lose the identity across process restarts.
assert monitor.alert_identity("commands") == "external-monitor:commands"
assert monitor.alert_identity("commands") == monitor.alert_identity("commands")

original_checks = monitor.CHECKS
original_check = monitor.check
original_state_file = monitor.STATE_FILE
original_notify = monitor.notify
original_failure_threshold = monitor.FAILURE_THRESHOLD
try:
    monitor.CHECKS = (
        monitor.Check("commands", "https://commands.example/", frozenset({200})),
        monitor.Check("mods", "https://mods.example/", frozenset({200})),
    )
    outcomes = {"commands": False, "mods": True}
    monitor.check = lambda item: {"ok": outcomes[item.name]}
    notifications = []
    monitor.notify = notifications.append
    monitor.FAILURE_THRESHOLD = 1

    with tempfile.TemporaryDirectory() as directory:
        monitor.STATE_FILE = Path(directory) / "state.json"
        monitor.run_once()
        first = json.loads(monitor.STATE_FILE.read_text())
        assert first["active_alerts"] == {
            "commands": "external-monitor:commands",
        }
        assert notifications == [
            "Alert `external-monitor:commands` firing; failed checks: commands",
        ]

        # A restart reads the persisted identity.  Repeating the same failure
        # does not send a duplicate notification.
        monitor.run_once()
        assert len(notifications) == 1

        # A newly failed check gets its own identity while the original stays
        # active; clearing one emits only that check's recovery.
        outcomes["mods"] = False
        monitor.run_once()
        assert notifications[-1] == (
            "Alert `external-monitor:mods` firing; failed checks: commands,mods"
        )
        outcomes["commands"] = True
        monitor.run_once()
        assert notifications[-1] == "Alert `external-monitor:commands` recovered"
finally:
    monitor.CHECKS = original_checks
    monitor.check = original_check
    monitor.STATE_FILE = original_state_file
    monitor.notify = original_notify
    monitor.FAILURE_THRESHOLD = original_failure_threshold

print("test_monitor: all assertions passed")
