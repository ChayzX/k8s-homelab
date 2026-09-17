import json
import fcntl
import importlib.util
import subprocess
import sys
import tempfile
from urllib.error import HTTPError
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

# A duplicate evaluator must not race the durable ledger and emit a second
# notification for the same transition.  This simulates another evaluator
# holding the advisory lock while this evaluator attempts a cycle.
original_state_file = monitor.STATE_FILE
original_notify = monitor.notify
try:
    with tempfile.TemporaryDirectory() as directory:
        monitor.STATE_FILE = Path(directory) / "state.json"
        lock_path = monitor.monitor_lock_path()
        with lock_path.open("a+") as held_lock:
            fcntl.flock(held_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            notifications = []
            monitor.notify = notifications.append
            monitor.run_once()
            assert notifications == []
            assert not monitor.STATE_FILE.exists()
finally:
    monitor.STATE_FILE = original_state_file
    monitor.notify = original_notify

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


# Notification delivery must produce a provider-acceptance receipt that can be
# inspected after a failover rehearsal without exposing alert text or secrets.
original_webhook = monitor.WEBHOOK
original_urlopen = monitor.urllib.request.urlopen
try:
    monitor.WEBHOOK = "https://notify.example.test/hook-secret"

    class AcceptedResponse:
        status = 204

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monitor.urllib.request.urlopen = lambda request, timeout: AcceptedResponse()
    accepted = monitor.notify("private alert text")
    assert accepted["accepted"] is True
    assert accepted["transport"] == "webhook"
    assert accepted["status"] == 204
    assert "message" not in accepted
    assert "hook-secret" not in json.dumps(accepted)

    def reject(request, timeout):
        raise HTTPError(request.full_url, 503, "unavailable", {}, None)

    monitor.urllib.request.urlopen = reject
    rejected = monitor.notify("private alert text")
    assert rejected["accepted"] is False
    assert rejected["status"] == 503
    assert rejected["reason"] == "http_error"
finally:
    monitor.WEBHOOK = original_webhook
    monitor.urllib.request.urlopen = original_urlopen


original_checks = monitor.CHECKS
original_check = monitor.check
original_state_file = monitor.STATE_FILE
original_notify = monitor.notify
original_failure_threshold = monitor.FAILURE_THRESHOLD
try:
    monitor.CHECKS = (monitor.Check("commands", "https://commands.example/", frozenset({200})),)
    monitor.check = lambda item: {"ok": False}
    monitor.FAILURE_THRESHOLD = 1
    monitor.notify = lambda message: {
        "accepted": True,
        "transport": "test",
        "status": 204,
        "observed_at": 123,
    }
    with tempfile.TemporaryDirectory() as directory:
        monitor.STATE_FILE = Path(directory) / "state.json"
        monitor.run_once()
        state = json.loads(monitor.STATE_FILE.read_text())
        assert state["notification_receipts"] == [{
            "identity": "external-monitor:commands",
            "event": "firing",
            "accepted": True,
            "transport": "test",
            "status": 204,
            "observed_at": 123,
        }]
finally:
    monitor.CHECKS = original_checks
    monitor.check = original_check
    monitor.STATE_FILE = original_state_file
    monitor.notify = original_notify
    monitor.FAILURE_THRESHOLD = original_failure_threshold


receipt_state = {
    "notification_receipts": [{
        "identity": "external-monitor:commands",
        "event": "firing",
        "accepted": True,
        "transport": "webhook",
        "status": 204,
        "observed_at": 100,
    }]
}
found, receipt = monitor.notification_receipt_probe(
    receipt_state,
    identity="external-monitor:commands",
    event="firing",
    max_age_seconds=101,
    now=200,
)
assert found is True
assert receipt["status"] == 204

stale, reason = monitor.notification_receipt_probe(
    receipt_state,
    identity="external-monitor:commands",
    event="firing",
    max_age_seconds=99,
    now=200,
)
assert stale is False
assert reason == "stale"

with tempfile.TemporaryDirectory() as directory:
    state_file = Path(directory) / "state.json"
    state_file.write_text(json.dumps(receipt_state))
    # oculum-ignore-next-line [dangerous_function]: test invokes a fixed Python fixture with controlled temp-file argv
    probe = subprocess.run(
        [
            sys.executable,
            str(MODULE_PATH.with_name("probe-notification-receipt.py")),
            "--state-file",
            str(state_file),
            "--identity",
            "external-monitor:commands",
            "--event",
            "firing",
            "--max-age-seconds",
            "9999999999",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert probe.returncode == 0
    assert json.loads(probe.stdout)["ok"] is True

print("test_monitor: all assertions passed")
