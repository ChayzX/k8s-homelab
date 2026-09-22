#!/usr/bin/env python3
"""Behavior of the standby follower and the promotion acquire gate (#191)."""

import json
import os
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SYSID = "7687975437720940591"

# Fake psql: local answers come from scenario["local"], peers from
# scenario["peers"]["host:port"]; ALTER SYSTEM / CREATE_REPLICATION_SLOT calls
# are recorded. Unreachable peers are simply absent.
FAKE_PSQL = r'''#!/usr/bin/env python3
import json, os, re, sys
path = os.environ["FAKE_SCENARIO"]
sc = json.load(open(path))
args = sys.argv[1:]
conn = args[args.index("-d") + 1]
sql = args[args.index("-c") + 1]
sc.setdefault("calls", []).append([conn, sql])
def done(out=None, rc=0):
    json.dump(sc, open(path, "w"))
    if out is not None: print(out)
    sys.exit(rc)
m = re.search(r"host=(\S+) port=(\S+)", conn)
if m and "user=pantry_replicator" in conn:
    peer = sc["peers"].get(f"{m.group(1)}:{m.group(2)}")
    if peer is None: done(rc=2)
    if "CREATE_REPLICATION_SLOT" in sql: done("")
    done(f"{peer['recovery']}|{peer['sysid']}")
local = sc["local"]
if local is None: done(rc=2)
if "pg_control_checkpoint" in sql: done(f"{local['recovery']}|{local['sysid']}|4")
if "pg_stat_wal_receiver" in sql: done(f"{local['wal']}|0/1|0/1")
done("")
'''


def run_follower(local, peers, state_text=None, extra=None):
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / "psql").write_text(FAKE_PSQL); (tmp / "psql").chmod(0o755)
        scenario = tmp / "sc.json"; scenario.write_text(json.dumps({"local": local, "peers": peers}))
        state = tmp / "state"
        if state_text: state.write_text(state_text)
        env = {**os.environ, "PATH": f"{tmp}:{os.environ['PATH']}", "FAKE_SCENARIO": str(scenario),
               "SITE": "oracle", "PEERS": "home=100.84.89.87:5432 canada=127.0.0.1:25442",
               "PGDATA": str(tmp), "STATE": str(state), "ONESHOT": "1", "REPOINT_AFTER": "20"}
        env.update(extra or {})
        result = subprocess.run(["sh", str(ROOT / "pantry-standby-follower.sh")], capture_output=True, text=True, env=env)
        sc = json.loads(scenario.read_text())
        st = dict(l.split("=", 1) for l in state.read_text().splitlines()) if state.exists() else {}
        return result, sc.get("calls", []), st


def alters(calls):
    return [sql for _, sql in calls if sql.startswith("ALTER SYSTEM")]


STANDBY_DOWN = {"recovery": "t", "sysid": SYSID, "wal": "none|"}
OLD = "disconnected_since=1\nlast_streaming=1\n"


def test_streaming_standby_records_heartbeat_and_changes_nothing() -> None:
    result, calls, st = run_follower({"recovery": "t", "sysid": SYSID, "wal": "streaming|100.84.89.87:5432"}, {})
    assert result.returncode == 0, result.stderr
    assert st["role"] == "standby" and st["streaming"] == "1" and st["last_streaming"]
    assert st["upstream"] == "100.84.89.87:5432" and st["disconnected_since"] == ""
    assert alters(calls) == []


def test_cascading_from_a_standby_repoints_to_the_primary() -> None:
    """Catch a site left streaming through another standby after a hand-back."""
    peers = {"127.0.0.1:25442": {"recovery": "t", "sysid": SYSID},      # upstream: a standby
             "100.84.89.87:5432": {"recovery": "f", "sysid": SYSID}}    # the real primary
    _, calls, _ = run_follower({"recovery": "t", "sysid": SYSID, "wal": "streaming|127.0.0.1:25442"}, peers)
    assert any("host=100.84.89.87 port=5432" in s for s in alters(calls))


def test_streaming_from_the_primary_is_left_alone() -> None:
    peers = {"100.84.89.87:5432": {"recovery": "f", "sysid": SYSID}}
    _, calls, _ = run_follower({"recovery": "t", "sysid": SYSID, "wal": "streaming|100.84.89.87:5432"}, peers)
    assert alters(calls) == []


def test_primary_is_never_touched() -> None:
    _, calls, st = run_follower({"recovery": "f", "sysid": SYSID, "wal": "none|"}, {})
    assert st["role"] == "primary"
    assert alters(calls) == [] and not any("replicator" in c for c, _ in calls)


def test_follows_the_unique_same_sysid_primary_after_the_delay() -> None:
    peers = {"100.84.89.87:5432": {"recovery": "t", "sysid": SYSID},
             "127.0.0.1:25442": {"recovery": "f", "sysid": SYSID}}
    _, calls, _ = run_follower(STANDBY_DOWN, peers, OLD)
    a = alters(calls)
    assert any("host=127.0.0.1 port=25442" in s for s in a), a
    assert any("primary_slot_name = 'pantry_oracle_standby'" in s for s in a)
    assert any("CREATE_REPLICATION_SLOT pantry_oracle_standby" in s for _, s in calls)
    assert not any("password" in s for s in a)


def test_repoint_time_is_persisted_for_the_throttle() -> None:
    """Catch the 60s re-point throttle being lost across one-shot iterations."""
    peers = {"127.0.0.1:25442": {"recovery": "f", "sysid": SYSID}}
    _, _, st = run_follower(STANDBY_DOWN, peers, OLD)
    assert st["repointed_at"].isdigit()


def test_does_not_repoint_before_the_delay() -> None:
    peers = {"127.0.0.1:25442": {"recovery": "f", "sysid": SYSID}}
    _, calls, st = run_follower(STANDBY_DOWN, peers)  # first disconnected sample
    assert alters(calls) == [] and st["disconnected_since"]


def test_two_reachable_primaries_is_split_brain_and_nothing_changes() -> None:
    peers = {"100.84.89.87:5432": {"recovery": "f", "sysid": SYSID},
             "127.0.0.1:25442": {"recovery": "f", "sysid": SYSID}}
    result, calls, _ = run_follower(STANDBY_DOWN, peers, OLD)
    assert alters(calls) == []
    assert "SPLIT_BRAIN_SUSPECTED" in result.stderr


def test_foreign_cluster_primary_is_ignored() -> None:
    peers = {"100.84.89.87:5432": {"recovery": "f", "sysid": "1"}}
    _, calls, _ = run_follower(STANDBY_DOWN, peers, OLD)
    assert alters(calls) == []


def test_disconnection_clock_survives_restart() -> None:
    _, _, st = run_follower(STANDBY_DOWN, {}, "disconnected_since=123\nlast_streaming=100\n")
    assert st["disconnected_since"] == "123" and st["last_streaming"] == "100"


def gate(state: dict, now: int, delay: int = 45, max_stale: int = 600):
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "state"
        f.write_text("".join(f"{k}={v}\n" for k, v in state.items()))
        env = {**os.environ, "STATE_READ_COMMAND": f"cat {f}", "PRIORITY_DELAY_SECONDS": str(delay),
               "MAX_STALENESS_SECONDS": str(max_stale), "EXPECTED_SYSTEM_IDENTIFIER": SYSID, "GATE_NOW": str(now)}
        return subprocess.run(["bash", str(ROOT / "promotion-gate.sh")], capture_output=True, text=True, env=env)


def lost(since=1000, last=990, sampled=1100, **kw):
    return {"sampled_at": sampled, "role": "standby", "streaming": 0, "system_identifier": SYSID,
            "disconnected_since": since, "last_streaming": last, **kw}


def test_gate_waits_for_the_priority_delay_then_opens() -> None:
    """Catch a lower-priority site competing before the higher one had its turn."""
    assert gate(lost(sampled=1030), 1030).returncode == 1
    assert "priority_wait" in gate(lost(sampled=1030), 1030).stderr
    assert gate(lost(sampled=1050), 1050).returncode == 0


def test_gate_closed_while_upstream_is_alive() -> None:
    assert gate(lost(streaming=1), 1100).returncode == 1


def test_gate_refuses_a_stale_replica() -> None:
    """Catch promoting a standby that stopped streaming long before the primary died."""
    r = gate(lost(since=100, last=90, sampled=2000), 2000)
    assert r.returncode == 1 and "stale_replica" in r.stderr


def test_gate_fails_closed_on_old_or_foreign_or_missing_state() -> None:
    assert gate(lost(sampled=1000), 1100).returncode == 1          # follower dead
    assert gate(lost(system_identifier="1"), 1100).returncode == 1
    assert gate({"role": "standby"}, 1100).returncode == 1
    r = subprocess.run(["bash", str(ROOT / "promotion-gate.sh")], capture_output=True, text=True,
                       env={**os.environ, "STATE_READ_COMMAND": "false", "PRIORITY_DELAY_SECONDS": "0",
                            "EXPECTED_SYSTEM_IDENTIFIER": SYSID})
    assert r.returncode == 1


def test_gate_always_opens_for_a_primary() -> None:
    assert gate({"sampled_at": 1100, "role": "primary"}, 1100).returncode == 0
