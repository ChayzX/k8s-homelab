import tempfile
from unittest.mock import Mock

import watcher


watcher._last_alert_time.clear()
watcher.COOLDOWN_SECONDS = 900
assert watcher.cooldown_ok("k") is True
assert watcher.cooldown_ok("k") is False
watcher._last_alert_time["k"] -= 901
assert watcher.cooldown_ok("k") is True

# A single transient line is not enough; recurrence for five minutes is
# required, and a quiet gap resets the pending incident.
watcher._pending_errors.clear()
watcher.ERROR_CONFIRMATION_SECONDS = 300
watcher.ERROR_PENDING_GAP_SECONDS = 90
assert watcher.persistent_error("error", now=1000) is False
assert watcher.persistent_error("error", now=1060) is False
assert watcher.persistent_error("error", now=1120) is False
assert watcher.persistent_error("error", now=1180) is False
assert watcher.persistent_error("error", now=1240) is False
assert watcher.persistent_error("error", now=1300) is True
assert watcher.persistent_error("error", now=1391) is False

for line in (
    "Connection error occurred",
    "EventSub disconnected, will reconnect",
    "Reconnecting to gateway",
    "Caused by: java.lang.Exception: stacktrace follows",
    "failed to fetch playlist",
    "FATAL: unrecoverable state",
):
    assert watcher.ERROR_RE.search(line)
assert not watcher.ERROR_RE.search("Overlay server listening on port 8080")

teardown = 'failed to run the datagram handler error="context canceled" connIndex=2'
assert watcher.CLOUDFLARED_BENIGN_RE.search(teardown)
assert watcher.cloudflared_teardown_suppressed(teardown, True)
assert not watcher.cloudflared_teardown_suppressed(teardown, False)
assert watcher.CLOUDFLARED_BENIGN_RE.search(
    "failed to accept incoming stream requests error=timeout: no recent network activity"
)
assert not watcher.CLOUDFLARED_BENIGN_RE.search(
    "Failed to refresh feature selector error=lookup cfd-features.argotunnel.com"
)

response = Mock(status_code=200)
session = Mock()
session.get.return_value = response
assert watcher.cloudflared_ready(session)
session.get.assert_called_once_with(
    watcher.CLOUDFLARED_READY_URL,
    timeout=watcher.CLOUDFLARED_READY_TIMEOUT_SECONDS,
)

base = {"metadata": {"generation": 2}, "spec": {"replicas": 1}}
assert watcher.deployment_rollout_active(
    [{**base, "status": {"observedGeneration": 2, "updatedReplicas": 0, "availableReplicas": 0}}]
)
assert not watcher.deployment_rollout_active(
    [{**base, "status": {"observedGeneration": 2, "updatedReplicas": 1, "availableReplicas": 1}}]
)

with tempfile.NamedTemporaryFile() as lock_file:
    watcher.WATCHER_LOCK_PATH = lock_file.name
    lock = watcher.acquire_singleton_lock()
    try:
        try:
            watcher.acquire_singleton_lock()
        except RuntimeError:
            pass
        else:
            raise AssertionError("expected duplicate watcher lock to fail")
    finally:
        lock.close()

print("test_watcher: all assertions passed")
