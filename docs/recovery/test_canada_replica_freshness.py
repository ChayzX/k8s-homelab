import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("freshness", Path(__file__).with_name("check-canada-replica-freshness.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def sample_pair():
    source = {"system_identifier": "123", "timeline": 9, "sampled_at": 100, "version": 160004, "in_recovery": False, "flush_lsn": "1/100"}
    standby = {"system_identifier": "123", "timeline": 9, "sampled_at": 101, "version": 160004, "in_recovery": True, "receive_lsn": "1/100", "replay_lsn": "1/100", "receiver_status": "streaming", "receiver_timeline": 9, "slot_name": "oracle_from_canada"}
    return source, standby


source, standby = sample_pair()
result = module.validate(source, standby, "123", "oracle_from_canada", 0, 10, 102)
assert result["ok"] and result["promotion_authorized"] is False
for unsafe_command in (["bash", "-c", "echo unsafe"], ["/tmp/psql-wrapper"]):
    try:
        module.probe(unsafe_command, "test")
    except ValueError:
        pass
    else:
        raise AssertionError(f"untrusted probe executable was accepted: {unsafe_command[0]}")
for change in (
    {"in_recovery": False}, {"system_identifier": "wrong"}, {"receiver_timeline": 8},
    {"receiver_status": "stopped"}, {"replay_lsn": "1/FF"}, {"slot_name": "home_slot"},
    {"sampled_at": 1}, {"sampled_at": 200}, {"replay_lsn": None}, {"version": 170000},
):
    source, standby = sample_pair()
    standby.update(change)
    try:
        module.validate(source, standby, "123", "oracle_from_canada", 0, 10, 102)
    except (ValueError, TypeError):
        pass
    else:
        raise AssertionError(f"unsafe sample passed: {change}")
print("test_canada_replica_freshness: 11 cases passed")
