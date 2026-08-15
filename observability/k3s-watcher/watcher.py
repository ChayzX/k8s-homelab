"""Host-side Loki watcher for k3s restart loops and actionable log errors.

The service deliberately fails open: an unavailable Cloudflared readiness
check never suppresses an alert.  Secrets and the systemd unit remain host
configuration; this module is the tracked source deployed by that unit.
"""

import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from collections import OrderedDict
from datetime import datetime, timezone

import requests


def _positive_number(name, default, cast):
    try:
        value = cast(os.environ.get(name, str(default)))
        if value <= 0:
            raise ValueError("must be positive")
        return value
    except (TypeError, ValueError) as exc:
        print(f"[k3s-watcher] Invalid optional {name}: {exc}; Operations disabled")
        return None

BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN")
USER_ID = os.environ.get("DISCORD_USER_ID")
DISCORD_API = "https://discord.com/api/v10"
LOKI_URL = os.environ.get("LOKI_URL", "http://10.43.54.179:3100")
WATCH_NAMESPACES = [
    ns.strip()
    for ns in os.environ.get("WATCH_NAMESPACES", "jmusicbot,pantry-bot").split(",")
    if ns.strip()
]
POLL_INTERVAL_SECONDS = int(os.environ.get("POLL_INTERVAL_SECONDS", "30"))
RESTART_THRESHOLD = int(os.environ.get("RESTART_THRESHOLD", "3"))
RESTART_WINDOW_SECONDS = int(os.environ.get("RESTART_WINDOW_SECONDS", "600"))
COOLDOWN_SECONDS = int(os.environ.get("ALERT_COOLDOWN_SECONDS", "900"))
WATCHER_LOCK_PATH = os.environ.get("WATCHER_LOCK_PATH", "/run/user/1000/k3s-watcher.lock")
ROLLOUT_SUPPRESSION_SECONDS = int(os.environ.get("ROLLOUT_SUPPRESSION_SECONDS", "180"))
ROLLOUT_POST_SUPPRESSION_SECONDS = int(
    os.environ.get("ROLLOUT_POST_SUPPRESSION_SECONDS", "180")
)
ERROR_CONFIRMATION_SECONDS = int(os.environ.get("ERROR_CONFIRMATION_SECONDS", "300"))
ERROR_PENDING_GAP_SECONDS = int(os.environ.get("ERROR_PENDING_GAP_SECONDS", "90"))
CLOUDFLARED_READY_URL = os.environ.get(
    "CLOUDFLARED_READY_URL", "http://cloudflared.pantry-bot.svc:2000/ready"
)
CLOUDFLARED_READY_TIMEOUT_SECONDS = float(
    os.environ.get("CLOUDFLARED_READY_TIMEOUT_SECONDS", "2")
)
OPERATIONS_ALERT_URL = os.environ.get("OPERATIONS_ALERT_URL", "").strip()
OPERATIONS_RECONCILE_URL = os.environ.get(
    "OPERATIONS_RECONCILE_URL",
    (
        OPERATIONS_ALERT_URL.rsplit("/", 1)[0] + "/reconcile"
        if OPERATIONS_ALERT_URL
        else ""
    ),
).strip()
OPERATIONS_ALERT_INGEST_KEY = os.environ.get("OPERATIONS_ALERT_INGEST_KEY", "")
OPERATIONS_CF_ACCESS_CLIENT_ID = os.environ.get("OPERATIONS_CF_ACCESS_CLIENT_ID", "")
OPERATIONS_CF_ACCESS_CLIENT_SECRET = os.environ.get(
    "OPERATIONS_CF_ACCESS_CLIENT_SECRET", ""
)
OPERATIONS_TIMEOUT_SECONDS = _positive_number("OPERATIONS_TIMEOUT_SECONDS", 3, float)
OPERATIONS_PENDING_LIMIT = _positive_number("OPERATIONS_PENDING_LIMIT", 200, int)
OPERATIONS_RETRY_BASE_SECONDS = _positive_number(
    "OPERATIONS_RETRY_BASE_SECONDS", 5, float
)

ERROR_PATTERNS = os.environ.get(
    "ERROR_PATTERNS",
    r"error|exception|fatal|disconnected|reconnect(ing)?|stacktrace|failed to",
)
ERROR_RE = re.compile(ERROR_PATTERNS, re.IGNORECASE)
BENIGN_PATTERNS = os.environ.get(
    "BENIGN_PATTERNS", r"canceled by remote with error code 0"
)
BENIGN_RE = re.compile(BENIGN_PATTERNS, re.IGNORECASE)
# Keep this intentionally narrow.  In particular, DNS, dial, origin and
# generic timeout messages are not included and remain alertable.
CLOUDFLARED_BENIGN_PATTERNS = os.environ.get(
    "CLOUDFLARED_BENIGN_PATTERNS",
    r"failed to run the datagram handler.*context canceled|"
    r"failed to serve tunnel connection.*accept stream listener encountered a failure|"
    r"failed to accept incoming stream requests.*no recent network activity",
)
CLOUDFLARED_BENIGN_RE = re.compile(CLOUDFLARED_BENIGN_PATTERNS, re.IGNORECASE)

EXTRA_RECIPIENTS = {}
for entry in os.environ.get("EXTRA_ALERT_RECIPIENTS", "").split(","):
    entry = entry.strip()
    if entry:
        namespace, user_id = entry.split(":", 1)
        EXTRA_RECIPIENTS.setdefault(namespace.strip(), []).append(user_id.strip())


def extra_recipients_for(namespace):
    return EXTRA_RECIPIENTS.get(namespace, ())


_last_alert_time = {}
_dm_channel_ids = {}
_last_restart_count = {}
_restart_events = {}
_last_log_query_ns = time.time_ns()
_rollout_suppression_started = {}
_pending_errors = {}
_active_log_events = {}
_operations_pending = OrderedDict()
_operations_active_events = set()
_reconciliation_started_at = time.time()
_reconciliation_warmup_seconds = max(
    RESTART_WINDOW_SECONDS, ERROR_CONFIRMATION_SECONDS + ERROR_PENDING_GAP_SECONDS
)


def acquire_singleton_lock():
    lock = open(WATCHER_LOCK_PATH, "w", encoding="utf-8")
    try:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        raise RuntimeError(f"another watcher already holds {WATCHER_LOCK_PATH}")
    return lock


def _get_dm_channel(user_id):
    if user_id in _dm_channel_ids:
        return _dm_channel_ids[user_id]
    response = requests.post(
        f"{DISCORD_API}/users/@me/channels",
        headers={"Authorization": f"Bot {BOT_TOKEN}"},
        json={"recipient_id": user_id},
        timeout=10,
    )
    response.raise_for_status()
    channel_id = response.json()["id"]
    _dm_channel_ids[user_id] = channel_id
    return channel_id


def send_discord_alert(title, description, color=0xE74C3C, extra_user_ids=()):
    if not BOT_TOKEN or not USER_ID:
        print(f"[k3s-watcher] Bot token/user id not set, would have alerted: {title}")
        return
    payload = {"embeds": [{"title": title, "description": description[:3900], "color": color}]}
    for user_id in (USER_ID, *extra_user_ids):
        try:
            channel_id = _get_dm_channel(user_id)
            response = requests.post(
                f"{DISCORD_API}/channels/{channel_id}/messages",
                headers={"Authorization": f"Bot {BOT_TOKEN}"},
                json=payload,
                timeout=10,
            )
            response.raise_for_status()
        except Exception as exc:
            print(f"[k3s-watcher] Failed to send Discord DM to {user_id}: {exc}")
            _dm_channel_ids.pop(user_id, None)


def _operations_enabled():
    values = (
        OPERATIONS_ALERT_URL,
        OPERATIONS_RECONCILE_URL,
        OPERATIONS_ALERT_INGEST_KEY,
        OPERATIONS_CF_ACCESS_CLIENT_ID,
        OPERATIONS_CF_ACCESS_CLIENT_SECRET,
        OPERATIONS_TIMEOUT_SECONDS,
        OPERATIONS_PENDING_LIMIT,
        OPERATIONS_RETRY_BASE_SECONDS,
    )
    if any(values) and not all(values):
        print("[k3s-watcher] Operations delivery is incomplete; check service environment")
    return all(values)


def _operations_headers():
    return {
        "X-Operations-Ingest-Key": OPERATIONS_ALERT_INGEST_KEY,
        "CF-Access-Client-Id": OPERATIONS_CF_ACCESS_CLIENT_ID,
        "CF-Access-Client-Secret": OPERATIONS_CF_ACCESS_CLIENT_SECRET,
    }


def queue_operations_alert(event):
    """Retain a bounded, de-duplicated event spool independent of DM cooldown."""
    if not _operations_enabled():
        return
    key = event["eventKey"]
    if key in _operations_active_events:
        # Keep the original occurrence timestamp stable until delivery succeeds.
        return
    _operations_active_events.add(key)
    while len(_operations_pending) >= OPERATIONS_PENDING_LIMIT:
        dropped_key, _ = _operations_pending.popitem(last=False)
        print(f"[k3s-watcher] Operations spool full; dropped oldest event {dropped_key}")
    _operations_pending[key] = {"event": event, "attempts": 0, "next_attempt": 0.0}


def finish_operations_lifecycle(active_event_keys):
    """Allow a future occurrence only after the current condition clears."""
    _operations_active_events.intersection_update(active_event_keys)


def flush_operations_alerts(session=requests, now=None):
    """Attempt due deliveries, retrying only transient network/server failures."""
    if not _operations_enabled():
        return
    now = time.time() if now is None else now
    for key, pending in list(_operations_pending.items()):
        if pending["next_attempt"] > now:
            continue
        retry = False
        try:
            response = session.post(
                OPERATIONS_ALERT_URL,
                headers=_operations_headers(),
                json=pending["event"],
                timeout=OPERATIONS_TIMEOUT_SECONDS,
            )
            if response.status_code in (429,) or response.status_code >= 500:
                retry = True
            elif 200 <= response.status_code < 300:
                _operations_pending.pop(key, None)
                continue
            else:
                print(
                    f"[k3s-watcher] Operations rejected event {key} "
                    f"with HTTP {response.status_code}; not retrying"
                )
                _operations_pending.pop(key, None)
                continue
        except (requests.ConnectionError, requests.Timeout) as exc:
            print(f"[k3s-watcher] Operations delivery failed for {key}: {exc}")
            retry = True
        except requests.RequestException as exc:
            print(f"[k3s-watcher] Operations request failed for {key}: {exc}; not retrying")
            _operations_pending.pop(key, None)
            continue
        if retry:
            pending["attempts"] += 1
            exponent = min(pending["attempts"] - 1, 8)
            delay = min(OPERATIONS_RETRY_BASE_SECONDS * 2 ** exponent, 300)
            pending["next_attempt"] = now + delay


def reconcile_operations(active_event_keys, session=requests):
    """Resolve stale events only after callers complete every collection source."""
    if not _operations_enabled():
        return True
    if len(active_event_keys) > 200:
        print("[k3s-watcher] More than 200 active alerts; reconciliation skipped safely")
        return False
    try:
        response = session.post(
            OPERATIONS_RECONCILE_URL,
            headers=_operations_headers(),
            json={"source": "k3s-watcher", "activeEventKeys": sorted(active_event_keys)},
            timeout=OPERATIONS_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        return True
    except requests.RequestException as exc:
        print(f"[k3s-watcher] Operations reconciliation failed: {exc}")
        return False


def _utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_workload(value):
    value = re.sub(r"[^a-z0-9-]", "-", (value or "unknown").lower()).strip("-")
    return (value or "unknown")[:63]


def workload_for_pod(pod):
    metadata = pod.get("metadata", {})
    labels = metadata.get("labels", {})
    for label in ("app.kubernetes.io/name", "app"):
        if labels.get(label):
            return _safe_workload(labels[label])
    owners = metadata.get("ownerReferences", [])
    if owners:
        name = owners[0].get("name", "")
        if owners[0].get("kind") == "ReplicaSet":
            name = re.sub(r"-[a-f0-9]{8,10}$", "", name)
        return _safe_workload(name)
    pod_name = re.sub(
        r"-[a-z0-9]{5,10}(?:-[a-z0-9]{5})?$", "", metadata.get("name", "")
    )
    return _safe_workload(pod_name)


def _event_key(kind, identity):
    return f"{kind}:{hashlib.sha256(identity.encode('utf-8')).hexdigest()}"


def cooldown_ok(key):
    now = time.time()
    if now - _last_alert_time.get(key, 0) >= COOLDOWN_SECONDS:
        _last_alert_time[key] = now
        return True
    return False


def persistent_error(key, now=None):
    """Require a fingerprint to recur continuously before it can alert."""
    now = time.time() if now is None else now
    previous = _pending_errors.get(key)
    if previous is None or now - previous["last_seen"] > ERROR_PENDING_GAP_SECONDS:
        _pending_errors[key] = {"first_seen": now, "last_seen": now}
        return False
    previous["last_seen"] = now
    return now - previous["first_seen"] >= ERROR_CONFIRMATION_SECONDS


def cloudflared_ready(session=requests):
    """Return True only for a successful readiness response; fail open."""
    try:
        response = session.get(CLOUDFLARED_READY_URL, timeout=CLOUDFLARED_READY_TIMEOUT_SECONDS)
        return response.status_code == 200
    except requests.RequestException as exc:
        print(f"[k3s-watcher] Cloudflared readiness check failed: {exc}")
        return False


def cloudflared_teardown_suppressed(line, ready):
    """Suppress only known QUIC teardown lines when the connector is healthy."""
    return bool(ready and CLOUDFLARED_BENIGN_RE.search(line))


def alert_fingerprint(namespace, container, line, match):
    """Normalize and hash volatile log content into an API-safe stable key."""
    if container == "cloudflared":
        lowered = line.lower()
        for phrase in (
            "failed to run the datagram handler",
            "failed to serve tunnel connection",
            "failed to accept incoming stream requests",
            "failed to refresh feature selector",
        ):
            if phrase in lowered:
                return _event_key("log", f"{namespace}/{container}/{phrase}")
    normalized = re.sub(r"\b(connIndex|event|ip)=\S+", "", line, flags=re.IGNORECASE)
    normalized = re.sub(r"\b[0-9a-f]{8,}\b", "<id>", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\b\d+\b", "<n>", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip().lower()[:500]
    identity = f"{namespace}/{container}/{match.group(0).lower()}/{normalized}"
    return _event_key("log", identity)


def deployment_rollout_active(deployments):
    for deployment in deployments:
        spec = deployment.get("spec", {})
        status = deployment.get("status", {})
        desired = spec.get("replicas", 1)
        if (
            status.get("observedGeneration") != deployment.get("metadata", {}).get("generation")
            or status.get("updatedReplicas", 0) < desired
            or status.get("availableReplicas", 0) < desired
        ):
            return True
    return False


def rollout_suppressed(namespace):
    result = subprocess.run(
        ["kubectl", "get", "deployments", "-n", namespace, "-o", "json"],
        capture_output=True, text=True, timeout=15,
    )
    if result.returncode != 0:
        return False
    active = deployment_rollout_active(json.loads(result.stdout).get("items", []))
    now = time.time()
    if not active:
        started = _rollout_suppression_started.pop(namespace, None)
        return bool(started and now - started <= ROLLOUT_POST_SUPPRESSION_SECONDS)
    started = _rollout_suppression_started.setdefault(namespace, now)
    return now - started <= ROLLOUT_SUPPRESSION_SECONDS


def check_restart_loops():
    active_event_keys = set()
    collection_complete = True
    for namespace in WATCH_NAMESPACES:
        result = subprocess.run(
            ["kubectl", "get", "pods", "-n", namespace, "-o", "json"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            print(f"[k3s-watcher] kubectl get pods -n {namespace} failed: {result.stderr.strip()}")
            collection_complete = False
            continue
        for pod in json.loads(result.stdout)["items"]:
            pod_name = pod["metadata"]["name"]
            for container_status in pod["status"].get("containerStatuses", []):
                key = f"{namespace}/{pod_name}/{container_status['name']}"
                count = container_status["restartCount"]
                previous = _last_restart_count.get(key)
                if previous is not None and count > previous:
                    _restart_events.setdefault(key, []).extend([time.time()] * (count - previous))
                _last_restart_count[key] = count
                now = time.time()
                events = [
                    t
                    for t in _restart_events.get(key, [])
                    if now - t <= RESTART_WINDOW_SECONDS
                ]
                _restart_events[key] = events
                if len(events) >= RESTART_THRESHOLD:
                    event_key = _event_key("restart", key)
                    active_event_keys.add(event_key)
                    message = (
                        f"{key} restarted {len(events)} times in "
                        f"{RESTART_WINDOW_SECONDS / 60:.0f} minutes."
                    )
                    queue_operations_alert({
                        "source": "k3s-watcher",
                        "eventKey": event_key,
                        "eventType": "restartLoop",
                        "severity": "critical",
                        "namespace": namespace,
                        "workload": workload_for_pod(pod),
                        "pod": pod_name,
                        "message": message,
                        "occurredAt": _utc_now(),
                    })
                if len(events) >= RESTART_THRESHOLD and cooldown_ok(f"restart_loop_{key}"):
                    send_discord_alert(
                        "🔁 Restart loop detected",
                        f"{key} restarted {len(events)} times in {RESTART_WINDOW_SECONDS / 60:.0f} minutes.",
                        extra_user_ids=extra_recipients_for(namespace),
                    )
    return collection_complete, active_event_keys


def check_log_errors():
    global _last_log_query_ns
    namespace_regex = "|".join(WATCH_NAMESPACES)
    end_ns = time.time_ns()
    query = '{namespace=~"%s"} |~ `(?i)%s`' % (namespace_regex, ERROR_PATTERNS)
    try:
        response = requests.get(
            f"{LOKI_URL}/loki/api/v1/query_range",
            params={"query": query, "start": _last_log_query_ns, "end": end_ns,
                    "limit": 200, "direction": "forward"},
            timeout=15,
        )
        response.raise_for_status()
        streams = response.json()["data"]["result"]
    except Exception as exc:
        print(f"[k3s-watcher] Loki query failed: {exc}")
        return False, set()
    _last_log_query_ns = end_ns

    rollout_state = {}
    ready_state = None
    active_event_keys = set()
    for stream in streams:
        labels = stream["stream"]
        container = labels.get("container", labels.get("app_kubernetes_io_name", "unknown"))
        namespace = labels.get("namespace", "unknown")
        for _, line in stream["values"]:
            if BENIGN_RE.search(line):
                continue
            if container == "cloudflared" and CLOUDFLARED_BENIGN_RE.search(line):
                if ready_state is None:
                    ready_state = cloudflared_ready()
                if cloudflared_teardown_suppressed(line, ready_state):
                    continue
            match = ERROR_RE.search(line)
            if not match:
                continue
            if namespace not in rollout_state:
                try:
                    rollout_state[namespace] = rollout_suppressed(namespace)
                except Exception as exc:
                    print(f"[k3s-watcher] rollout check failed for {namespace}: {exc}")
                    rollout_state[namespace] = False
            if rollout_state[namespace]:
                continue
            key = alert_fingerprint(namespace, container, line, match)
            if not persistent_error(key):
                continue
            _active_log_events[key] = time.time()
            active_event_keys.add(key)
            workload = _safe_workload(
                labels.get("app_kubernetes_io_name")
                or labels.get("app")
                or container
            )
            queue_operations_alert({
                "source": "k3s-watcher",
                "eventKey": key,
                "eventType": "logError",
                "severity": "warning",
                "namespace": namespace,
                "workload": workload,
                "pod": labels.get("pod"),
                "message": f"{container} repeatedly matched log pattern: {match.group(0)}",
                "occurredAt": _utc_now(),
            })
            if cooldown_ok(key):
                send_discord_alert(
                    f"⚠️ {container} log alert",
                    f"Matched pattern: `{match.group(0)}`\n\n{line}",
                    extra_user_ids=extra_recipients_for(namespace),
                )
    now = time.time()
    for key, last_seen in list(_active_log_events.items()):
        if now - last_seen <= ERROR_PENDING_GAP_SECONDS:
            active_event_keys.add(key)
        else:
            _active_log_events.pop(key, None)
    return True, active_event_keys


def run_checks_once():
    """Collect, deliver, and reconcile one pass without partial resolution."""
    restart_complete = False
    log_complete = False
    restart_keys = set()
    log_keys = set()
    try:
        restart_complete, restart_keys = check_restart_loops()
    except Exception as exc:
        print(f"[k3s-watcher] Restart-loop check error: {exc}")
    try:
        log_complete, log_keys = check_log_errors()
    except Exception as exc:
        print(f"[k3s-watcher] Log-error check error: {exc}")
    try:
        flush_operations_alerts()
    except Exception as exc:
        print(f"[k3s-watcher] Unexpected Operations delivery error: {exc}")
    if restart_complete and log_complete:
        active_keys = restart_keys | log_keys
        finish_operations_lifecycle(active_keys)
        if time.time() - _reconciliation_started_at < _reconciliation_warmup_seconds:
            return
        try:
            reconcile_operations(active_keys)
        except Exception as exc:
            print(f"[k3s-watcher] Unexpected Operations reconciliation error: {exc}")


if __name__ == "__main__":
    try:
        _singleton_lock = acquire_singleton_lock()
    except RuntimeError as exc:
        print(f"[k3s-watcher] {exc}; exiting duplicate process")
        sys.exit(0)
    send_discord_alert(
        "✅ Watcher online",
        f"Monitoring namespaces: {', '.join(WATCH_NAMESPACES)}\n"
        f"Restart-loop: >= {RESTART_THRESHOLD} restarts / {RESTART_WINDOW_SECONDS}s. "
        f"Log errors: pattern `{ERROR_PATTERNS}` (cooldown: {COOLDOWN_SECONDS}s).",
        color=0x2ECC71,
    )
    while True:
        run_checks_once()
        time.sleep(POLL_INTERVAL_SECONDS)
