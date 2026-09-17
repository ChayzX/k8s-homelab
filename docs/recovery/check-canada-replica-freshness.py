#!/usr/bin/env python3
"""Read-only live replication check; never grants promotion or changes a lease."""
import argparse
import json
import math
import re
import subprocess
import sys
import time
from pathlib import Path

SQL = """
BEGIN READ ONLY;
SET LOCAL statement_timeout = '5s';
SELECT json_build_object(
  'system_identifier', (pg_control_system()).system_identifier::text,
  'timeline', (pg_control_checkpoint()).timeline_id,
  'in_recovery', pg_is_in_recovery(),
  'sampled_at', extract(epoch FROM clock_timestamp()),
  'version', current_setting('server_version_num')::int,
  'flush_lsn', CASE WHEN NOT pg_is_in_recovery() THEN pg_current_wal_flush_lsn()::text END,
  'receive_lsn', pg_last_wal_receive_lsn()::text,
  'replay_lsn', pg_last_wal_replay_lsn()::text,
  'receiver_status', (SELECT status FROM pg_stat_wal_receiver),
  'receiver_timeline', (SELECT received_tli FROM pg_stat_wal_receiver),
  'slot_name', (SELECT slot_name FROM pg_stat_wal_receiver)
);
ROLLBACK;
"""

ALLOWED_PROBE_BINARIES = {"psql", "kubectl"}


def lsn(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9A-Fa-f]{1,8}/[0-9A-Fa-f]{1,8}", value):
        raise ValueError("missing or invalid WAL position")
    high, low = value.split("/")
    return (int(high, 16) << 32) + int(low, 16)


def probe(command, label):
    if not isinstance(command, list) or not command or not all(isinstance(item, str) for item in command):
        raise ValueError(f"{label} command must be an argument array")
    if Path(command[0]).name not in ALLOWED_PROBE_BINARIES:
        raise ValueError(f"{label} command must invoke psql or kubectl")
    # Configured argv is a trusted psql transport. SQL arrives on stdin; no shell
    # interpolation, connection strings or passwords are printed.
    try:
        result = subprocess.run(command, input=SQL, text=True, capture_output=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        raise RuntimeError(f"{label} probe could not finish") from None
    if result.returncode:
        raise RuntimeError(f"{label} probe failed; inspect transport and database privileges privately")
    rows = [line for line in result.stdout.splitlines() if line.lstrip().startswith("{")]
    if len(rows) != 1:
        raise RuntimeError(f"{label} probe did not produce exactly one sample")
    return json.loads(rows[0])


def validate(source, standby, identity, slot, max_lag_bytes, max_age_seconds, now):
    if not str(identity).isdigit() or not slot:
        raise ValueError("verified database identity and replication slot are required")
    if max_lag_bytes < 0 or not math.isfinite(max_age_seconds) or max_age_seconds <= 0:
        raise ValueError("explicit nonnegative lag and positive sample-age limits are required")
    for label, sample in (("source", source), ("standby", standby)):
        if sample.get("system_identifier") != str(identity):
            raise ValueError(f"{label} database identity mismatch")
        age = now - float(sample["sampled_at"])
        if not math.isfinite(age) or age < -2 or age > max_age_seconds:
            raise ValueError(f"{label} sample is stale or its clock is inconsistent")
    if source.get("in_recovery") is not False or standby.get("in_recovery") is not True:
        raise ValueError("expected a Canada primary and an Oracle standby")
    if source["version"] // 10000 != standby["version"] // 10000:
        raise ValueError("PostgreSQL major versions differ")
    if standby.get("receiver_status") != "streaming" or standby.get("slot_name") != slot:
        raise ValueError("standby is not streaming through the expected replication slot")
    if source.get("timeline") != standby.get("receiver_timeline") or source.get("timeline") != standby.get("timeline"):
        raise ValueError("current source, receiver and replay timeline do not match")
    receive = lsn(standby.get("receive_lsn"))
    replay = lsn(standby.get("replay_lsn"))
    source_flush = lsn(source.get("flush_lsn"))
    if receive < replay:
        raise ValueError("streaming replay position is ahead of received WAL; inspect recovery source")
    lag = max(0, source_flush - replay)
    backlog = receive - replay
    if lag > max_lag_bytes or backlog > max_lag_bytes:
        raise ValueError("replication lag exceeds the explicit byte limit")
    return {"ok": True, "mode": "read-only-live-observation", "system_identifier": str(identity),
            "timeline": source["timeline"], "source_flush_lsn": source["flush_lsn"],
            "standby_receive_lsn": standby["receive_lsn"], "standby_replay_lsn": standby["replay_lsn"],
            "sampled_lag_bytes": lag, "receive_replay_backlog_bytes": backlog,
            "promotion_authorized": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--expected-system-identifier", required=True)
    parser.add_argument("--expected-slot", default="oracle_from_canada")
    parser.add_argument("--max-lag-bytes", type=int, required=True)
    parser.add_argument("--max-sample-age-seconds", type=float, required=True)
    args = parser.parse_args()
    try:
        config = json.loads(args.config.read_text())
        source = probe(config["source_psql"], "source")
        standby = probe(config["standby_psql"], "standby")
        result = validate(source, standby, args.expected_system_identifier, args.expected_slot,
                          args.max_lag_bytes, args.max_sample_age_seconds, time.time())
    except (KeyError, ValueError, RuntimeError, OSError) as error:
        print(json.dumps({"ok": False, "error": str(error), "promotion_authorized": False}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
