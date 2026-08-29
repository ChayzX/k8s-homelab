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

watcher._pending_workload_conditions.clear()
watcher.WORKLOAD_CONFIRMATION_SECONDS = 300
assert watcher.persistent_workload_condition("image-pull", True, now=1000) is False
assert watcher.persistent_workload_condition("image-pull", True, now=1060) is False
assert watcher.persistent_workload_condition("image-pull", True, now=1120) is False
assert watcher.persistent_workload_condition("image-pull", True, now=1180) is False
assert watcher.persistent_workload_condition("image-pull", True, now=1240) is False
assert watcher.persistent_workload_condition("image-pull", True, now=1300) is True
assert watcher.persistent_workload_condition("image-pull", False, now=1310) is False
assert watcher.persistent_workload_condition("image-pull", True, now=1311) is False
assert watcher.persistent_workload_condition("image-pull", True, now=1402) is False

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

health_session = Mock()
health_session.get.return_value = Mock(status_code=200)
assert watcher.functional_health_check(
    "pantry-bot", "http://10.43.170.195/health", health_session
) == (True, "")
health_session.get.return_value = Mock(status_code=503)
healthy, message = watcher.functional_health_check(
    "pantry-bot", "http://10.43.170.195/health", health_session
)
assert healthy is False
assert "HTTP 503" in message
health_session.get.side_effect = watcher.requests.Timeout("timed out")
healthy, message = watcher.functional_health_check(
    "pantry-bot", "http://10.43.170.195/health", health_session
)
assert healthy is False
assert "unreachable" in message

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

# Volatile IDs and counters normalize to the same hashed, API-safe key.
match = watcher.ERROR_RE.search("error id deadbeef123 counter 42")
fingerprint_one = watcher.alert_fingerprint(
    "pantry-bot", "pantry-bot", "error id deadbeef123 counter 42", match
)
match = watcher.ERROR_RE.search("error id cafe123456 counter 99")
fingerprint_two = watcher.alert_fingerprint(
    "pantry-bot", "pantry-bot", "error id cafe123456 counter 99", match
)
assert fingerprint_one == fingerprint_two
assert fingerprint_one.startswith("log:")
assert len(fingerprint_one) == 68

pod = {
    "metadata": {
        "name": "pantry-bot-7d9cbbf889-ab123",
        "labels": {"app.kubernetes.io/name": "pantry-bot"},
    }
}
assert watcher.workload_for_pod(pod) == "pantry-bot"

# Operations is a separate, bounded delivery target. A retry preserves the
# exact payload (including occurredAt) and ordinary 4xx responses are dropped.
watcher.OPERATIONS_ALERT_URL = "https://operations.example/api/v1/alerts/events"
watcher.OPERATIONS_RECONCILE_URL = "https://operations.example/api/v1/alerts/reconcile"
watcher.OPERATIONS_ALERT_INGEST_KEY = "ingest"
watcher.OPERATIONS_CF_ACCESS_CLIENT_ID = "client"
watcher.OPERATIONS_CF_ACCESS_CLIENT_SECRET = "secret"
watcher.OPERATIONS_RETRY_BASE_SECONDS = 5
watcher.OPERATIONS_PENDING_LIMIT = 2
watcher._operations_pending.clear()

watcher.os.environ["TEST_OPERATIONS_NUMBER"] = "invalid"
try:
    assert watcher._positive_number("TEST_OPERATIONS_NUMBER", 3, float) is None
finally:
    watcher.os.environ.pop("TEST_OPERATIONS_NUMBER")

event = {
    "source": "k3s-watcher",
    "eventKey": "restart:" + "a" * 64,
    "eventType": "restartLoop",
    "severity": "critical",
    "namespace": "pantry-bot",
    "workload": "pantry-bot",
    "pod": "pantry-bot-abc",
    "message": "restart loop",
    "occurredAt": "2026-08-15T00:00:00Z",
}
watcher.queue_operations_alert(event)
retry_session = Mock()
retry_session.post.side_effect = watcher.requests.Timeout("timed out")
watcher.flush_operations_alerts(retry_session, now=100)
pending = watcher._operations_pending[event["eventKey"]]
assert pending["event"] == event
assert pending["next_attempt"] == 105

success_session = Mock()
success_session.post.return_value.status_code = 200
watcher.flush_operations_alerts(success_session, now=105)
assert not watcher._operations_pending
sent = success_session.post.call_args
assert sent.kwargs["json"]["occurredAt"] == "2026-08-15T00:00:00Z"
assert sent.kwargs["headers"]["CF-Access-Client-Id"] == "client"

watcher.finish_operations_lifecycle(set())
watcher.queue_operations_alert(event)
bad_request_session = Mock()
bad_request_session.post.return_value.status_code = 422
watcher.flush_operations_alerts(bad_request_session, now=200)
assert not watcher._operations_pending
watcher.finish_operations_lifecycle(set())

for transient_status in (429, 500, 503):
    watcher.queue_operations_alert(event)
    transient_session = Mock()
    transient_session.post.return_value.status_code = transient_status
    watcher.flush_operations_alerts(transient_session, now=300)
    assert event["eventKey"] in watcher._operations_pending
    watcher._operations_pending.clear()
    watcher.finish_operations_lifecycle(set())

# The oldest pending occurrence is evicted when the configured bound is hit.
watcher.OPERATIONS_PENDING_LIMIT = 2
for suffix in ("1", "2", "3"):
    queued = dict(event, eventKey="restart:" + suffix * 64)
    watcher.queue_operations_alert(queued)
assert list(watcher._operations_pending) == [
    "restart:" + "2" * 64,
    "restart:" + "3" * 64,
]
watcher._operations_pending.clear()

# CF Access client id/secret are optional: blank on both sides still enables
# Operations delivery (current setup — Authentik's skip_path_regex already
# exempts this path), and the headers omit CF-Access-* entirely rather than
# sending empty values.
watcher.OPERATIONS_CF_ACCESS_CLIENT_ID = ""
watcher.OPERATIONS_CF_ACCESS_CLIENT_SECRET = ""
assert watcher._operations_enabled() is True
headers = watcher._operations_headers()
assert "CF-Access-Client-Id" not in headers
assert "CF-Access-Client-Secret" not in headers
assert headers["X-Operations-Ingest-Key"] == "ingest"

# One set and one blank is a real misconfiguration, not "unused" — delivery
# stays disabled.
watcher.OPERATIONS_CF_ACCESS_CLIENT_ID = "client"
watcher.OPERATIONS_CF_ACCESS_CLIENT_SECRET = ""
assert watcher._operations_enabled() is False
watcher.OPERATIONS_CF_ACCESS_CLIENT_ID = "client"
watcher.OPERATIONS_CF_ACCESS_CLIENT_SECRET = "secret"
assert watcher._operations_enabled() is True

# Reconciliation uses the same layered credentials and sorted active keys.
reconcile_session = Mock()
reconcile_session.post.return_value.raise_for_status.return_value = None
assert watcher.reconcile_operations({"log:" + "b" * 64}, reconcile_session)
reconcile_call = reconcile_session.post.call_args
assert reconcile_call.kwargs["json"] == {
    "source": "k3s-watcher",
    "activeEventKeys": ["log:" + "b" * 64],
}
reconcile_session.reset_mock()
assert not watcher.reconcile_operations(
    {f"log:{index:064x}" for index in range(201)}, reconcile_session
)
reconcile_session.post.assert_not_called()

# A partial Kubernetes/Loki collection must never reconcile active alerts.
original_restart_check = watcher.check_restart_loops
original_log_check = watcher.check_log_errors
original_flush = watcher.flush_operations_alerts
original_reconcile = watcher.reconcile_operations
original_started_at = watcher._reconciliation_started_at
try:
    watcher.check_restart_loops = Mock(return_value=(False, {"restart:" + "c" * 64}))
    watcher.check_log_errors = Mock(return_value=(True, {"log:" + "d" * 64}))
    watcher.flush_operations_alerts = Mock()
    watcher.reconcile_operations = Mock()
    watcher.run_checks_once()
    watcher.reconcile_operations.assert_not_called()

    watcher.check_restart_loops.return_value = (True, {"restart:" + "c" * 64})
    watcher.run_checks_once()
    watcher.reconcile_operations.assert_not_called()
    watcher._reconciliation_started_at = (
        watcher.time.time() - watcher._reconciliation_warmup_seconds - 1
    )
    watcher.run_checks_once()
    watcher.reconcile_operations.assert_called_once_with(
        {"restart:" + "c" * 64, "log:" + "d" * 64}
    )
finally:
    watcher.check_restart_loops = original_restart_check
    watcher.check_log_errors = original_log_check
    watcher.flush_operations_alerts = original_flush
    watcher.reconcile_operations = original_reconcile
    watcher._reconciliation_started_at = original_started_at

# A restart alert attempts Discord before queueing the independently retried
# Operations occurrence, and the occurrence has a stable hashed identity.
original_namespaces = watcher.WATCH_NAMESPACES
original_subprocess_run = watcher.subprocess.run
original_discord = watcher.send_discord_alert
original_queue = watcher.queue_operations_alert
original_flush = watcher.flush_operations_alerts
try:
    watcher.WATCH_NAMESPACES = ["pantry-bot"]
    watcher._last_restart_count.clear()
    watcher._restart_events.clear()
    watcher._last_alert_time.clear()
    restart_key = "pantry-bot/pantry-bot-abc/app"
    watcher._last_restart_count[restart_key] = 0
    watcher._last_alert_time[f"restart_loop_{restart_key}"] = watcher.time.time()
    pod_response = Mock(returncode=0, stderr="")
    pod_response.stdout = watcher.json.dumps({
        "items": [{
            "metadata": {
                "name": "pantry-bot-abc",
                "labels": {"app": "pantry-bot"},
            },
            "status": {
                "containerStatuses": [{"name": "app", "restartCount": 3}]
            },
        }]
    })
    watcher.subprocess.run = Mock(return_value=pod_response)
    delivery_order = []
    watcher.send_discord_alert = Mock(side_effect=lambda *a, **k: delivery_order.append("dm"))
    queued_events = []

    def capture_event(event):
        delivery_order.append("operations")
        queued_events.append(event)

    watcher.queue_operations_alert = capture_event
    watcher.flush_operations_alerts = Mock()
    complete, active = watcher.check_restart_loops()
    assert complete is True
    assert delivery_order == ["operations"]
    assert queued_events[0]["source"] == "k3s-watcher"
    assert queued_events[0]["workload"] == "pantry-bot"
    assert queued_events[0]["eventKey"] in active
finally:
    watcher.WATCH_NAMESPACES = original_namespaces
    watcher.subprocess.run = original_subprocess_run
    watcher.send_discord_alert = original_discord
    watcher.queue_operations_alert = original_queue
    watcher.flush_operations_alerts = original_flush

# Raw Loki content remains Discord-only even when it contains credential-like
# text; the persisted Operations message contains only target and pattern.
original_get = watcher.requests.get
original_rollout = watcher.rollout_suppressed
original_persistent = watcher.persistent_error
original_cooldown_ok = watcher.cooldown_ok
original_queue = watcher.queue_operations_alert
try:
    raw_line = "fatal token=super-secret-value"
    loki_response = Mock()
    loki_response.raise_for_status.return_value = None
    loki_response.json.return_value = {
        "data": {"result": [{
            "stream": {
                "namespace": "pantry-bot",
                "container": "pantry-bot",
                "pod": "pantry-bot-abc",
            },
            "values": [["1", raw_line]],
        }]}
    }
    watcher.requests.get = Mock(return_value=loki_response)
    watcher.rollout_suppressed = Mock(return_value=False)
    watcher.persistent_error = Mock(return_value=True)
    watcher.cooldown_ok = Mock(return_value=False)
    captured = []
    watcher.queue_operations_alert = captured.append
    complete, _ = watcher.check_log_errors()
    assert complete is True
    assert len(captured) == 1
    assert "super-secret-value" not in captured[0]["message"]
    assert captured[0]["message"] == "pantry-bot repeatedly matched log pattern: fatal"
finally:
    watcher.requests.get = original_get
    watcher.rollout_suppressed = original_rollout
    watcher.persistent_error = original_persistent
    watcher.cooldown_ok = original_cooldown_ok
    watcher.queue_operations_alert = original_queue

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

# A previously-alerted workload condition that clears produces a recovery
# DM (green, cooldown-gated) and a distinct workloadRecovered event; a
# condition that is still active stays tracked and sends nothing.
original_queue = watcher.queue_operations_alert
original_discord = watcher.send_discord_alert
original_cooldown_ok = watcher.cooldown_ok
watcher._active_workload_alerts.clear()
watcher._last_alert_time.clear()
try:
    watcher._active_workload_alerts["workload:" + "e" * 64] = {
        "namespace": "pantry-bot",
        "workload": "pantry-bot",
        "pod": "pantry-bot-abc",
        "recovery_message": "Pod pantry-bot-abc container app is back up (was CreateContainerConfigError).",
    }
    watcher._active_workload_alerts["workload:" + "f" * 64] = {
        "namespace": "jmusicbot",
        "workload": "jmusicbot",
        "pod": None,
        "recovery_message": "Deployment jmusicbot/jmusicbot is back up (was unavailable).",
    }
    captured = []
    watcher.queue_operations_alert = captured.append
    watcher.send_discord_alert = Mock()
    watcher.cooldown_ok = Mock(return_value=True)
    watcher.sweep_workload_recoveries(set())
    assert watcher._active_workload_alerts == {}
    assert watcher.send_discord_alert.call_count == 2
    dm = watcher.send_discord_alert.call_args_list[0]
    assert dm.args[0] == "✅ pantry-bot workload back up"
    assert dm.kwargs["color"] == 0x2ECC71
    assert len(captured) == 2
    assert all(event["eventType"] == "workloadRecovered" for event in captured)
    assert all(event["eventKey"].endswith(":recovered") for event in captured)

    # An active key remains tracked and produces no recovery message.
    watcher._active_workload_alerts.clear()
    watcher.send_discord_alert.reset_mock()
    watcher._active_workload_alerts["workload:" + "e" * 64] = {
        "namespace": "pantry-bot",
        "workload": "pantry-bot",
        "pod": "pantry-bot-abc",
        "recovery_message": "still down",
    }
    watcher.sweep_workload_recoveries({"workload:" + "e" * 64})
    assert len(watcher._active_workload_alerts) == 1
    watcher.send_discord_alert.assert_not_called()
finally:
    watcher.queue_operations_alert = original_queue
    watcher.send_discord_alert = original_discord
    watcher.cooldown_ok = original_cooldown_ok
    watcher._active_workload_alerts.clear()

print("test_watcher: all assertions passed")
