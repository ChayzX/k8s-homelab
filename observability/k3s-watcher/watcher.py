"""Host-side Loki watcher for k3s restart loops and actionable log errors.

The service deliberately fails open: an unavailable Cloudflared readiness
check never suppresses an alert.  Secrets and the systemd unit remain host
configuration; this module is the tracked source deployed by that unit.
"""

import fcntl
import json
import os
import re
import subprocess
import sys
import time

import requests

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
    """Normalize volatile connector fields so duplicate streams share a key."""
    if container == "cloudflared":
        lowered = line.lower()
        for phrase in (
            "failed to run the datagram handler",
            "failed to serve tunnel connection",
            "failed to accept incoming stream requests",
            "failed to refresh feature selector",
        ):
            if phrase in lowered:
                return f"log_error_{namespace}_{container}_{phrase}"
    normalized = re.sub(r"\b(connIndex|event|ip)=\S+", "", line, flags=re.IGNORECASE)
    normalized = re.sub(r"\s+", " ", normalized).strip().lower()[:180]
    return f"log_error_{namespace}_{container}_{match.group(0).lower()}_{normalized}"


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
    for namespace in WATCH_NAMESPACES:
        result = subprocess.run(
            ["kubectl", "get", "pods", "-n", namespace, "-o", "json"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            print(f"[k3s-watcher] kubectl get pods -n {namespace} failed: {result.stderr.strip()}")
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
                events = [t for t in _restart_events.get(key, []) if now - t <= RESTART_WINDOW_SECONDS]
                _restart_events[key] = events
                if len(events) >= RESTART_THRESHOLD and cooldown_ok(f"restart_loop_{key}"):
                    send_discord_alert(
                        "🔁 Restart loop detected",
                        f"{key} restarted {len(events)} times in {RESTART_WINDOW_SECONDS / 60:.0f} minutes.",
                        extra_user_ids=extra_recipients_for(namespace),
                    )


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
        return
    finally:
        _last_log_query_ns = end_ns

    rollout_state = {}
    ready_state = None
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
            if cooldown_ok(key):
                send_discord_alert(
                    f"⚠️ {container} log alert",
                    f"Matched pattern: `{match.group(0)}`\n\n{line}",
                    extra_user_ids=extra_recipients_for(namespace),
                )


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
        try:
            check_restart_loops()
        except Exception as exc:
            print(f"[k3s-watcher] Restart-loop check error: {exc}")
        try:
            check_log_errors()
        except Exception as exc:
            print(f"[k3s-watcher] Log-error check error: {exc}")
        time.sleep(POLL_INTERVAL_SECONDS)
