import importlib.util
import sys
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

print("test_monitor: all assertions passed")
