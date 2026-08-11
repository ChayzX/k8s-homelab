"""Minecraft server metrics exporter -- k3s port.

Port of /home/chase/docker/observability/monitoring/minecraft-exporter/minecraft_exporter.py.
The RCON-derived metrics (TPS, players, up) and the alerting behaviour are
unchanged. Two things had to change because Minecraft moved into a pod, and one
of them removes metrics -- read the notes below before wiring dashboards.

1. RCON host
------------
Was `RCON_HOST = "localhost"`. Now resolved, in order:
  * $MINECRAFT_RCON_HOST if set (use this if you ever move the exporter
    in-cluster: set it to minecraft-rcon.minecraft.svc.cluster.local);
  * otherwise the RCON Service's *ClusterIP*, looked up with kubectl and
    re-resolved whenever a connection fails (ClusterIPs change when a Service
    is recreated).

Why ClusterIP rather than the documented "node IP + NodePort" option: a
NodePort would put a plaintext-authenticating RCON port on the LAN, and RCON's
handshake sends the password in the clear. ClusterIP addresses are reachable
from the node itself -- kube-proxy programs the same nftables rules for
host-originated traffic -- so we get the in-cluster path without adding any new
externally-reachable surface. The only cost is a kubectl dependency, which this
script needs anyway to find the world directory.

2. Process metrics: REMOVED
---------------------------
`minecraft_process_cpu_percent` and `minecraft_process_memory_bytes` came from
psutil scanning the host process table for `paper.jar`. From outside the pod's
PID namespace that lookup finds nothing, so those gauges could only ever have
gone stale or zero.

They are deliberately NOT re-implemented by querying Prometheus/cAdvisor and
re-exporting: that would mean Prometheus scraping this exporter to read back a
value Prometheus already scraped from the kubelet one hop earlier -- redundant,
one extra failure domain, one extra scrape interval of staleness, and a
guaranteed source of "why do these two panels disagree" confusion.

The tradeoff, stated honestly: metric *names* change, so any dashboard panel or
alert rule referencing the old gauges must be rewritten against cAdvisor. The
kubelet exposes cAdvisor natively at /metrics/cadvisor (which is why the
standalone cadvisor container was dropped from the stack), so Prometheus needs a
kubelet scrape job with a ServiceAccount token for these to exist at all.

Replacements for dashboards -- the "Minecraft memory vs its 8Gi limit" panel
should use the first two directly:

  memory used   container_memory_working_set_bytes{namespace="minecraft",pod=~"minecraft-.*",container="minecraft"}
  memory limit  container_spec_memory_limit_bytes{namespace="minecraft",pod=~"minecraft-.*",container="minecraft"}
  cpu cores     rate(container_cpu_usage_seconds_total{namespace="minecraft",pod=~"minecraft-.*",container="minecraft"}[5m])
  OOM kills     increase(container_oom_events_total{namespace="minecraft",pod=~"minecraft-.*"}[24h])

Note working_set (not RSS) is the number the kubelet actually compares against
the limit when deciding to OOM-kill, so the panel is more correct than the gauge
it replaces, not merely equivalent.

3. World size
-------------
`minecraft_world_size_bytes` is kept. The world now lives in a local-path PVC,
so the host directory is resolved from the PV's claimRef instead of being a
constant -- the PV UUID in the path differs per install.

Deployment decision: this stays a HOST systemd unit
---------------------------------------------------
It could run in-cluster as a Deployment (cleaner DNS, self-healing, and it could
mount the world PVC instead of reading a host path). Recommendation is to keep
it on the host, for three reasons:
  * Monitoring independence. This process is what DMs you when Minecraft stops
    answering. In-cluster on a single-node cluster it shares every failure mode
    with the thing it watches, and a node/k3s problem silences the alert exactly
    when you need it.
  * No image to build. There is no python image for this; in-cluster means
    either a new local build + `k3s ctr images import` cycle on every script
    edit, or a ConfigMap plus pip-install-at-startup. Both are more moving parts
    than the venv that already exists at
    /home/chase/docker/observability/node_exporter/.venv.
  * It already works this way, and the migration plan explicitly favours fewer
    simultaneous changes.
The cost is that Prometheus scrapes it as a static node-IP target rather than by
Service DNS -- exactly like the native node_exporter on :9100, which the plan
already keeps as a static target for the same "it is genuinely a host thing"
reason. If it is ever moved in-cluster, set MINECRAFT_RCON_HOST and
MINECRAFT_WORLD_DIR and nothing else in this file needs to change.

Assumptions (the Minecraft manifests did not exist when this was written):
Deployment `minecraft` and PVC `minecraft-world` in namespace `minecraft`, RCON
on 25575 behind a ClusterIP Service. All overridable by env var.
"""

import json
import os
import re
import socket
import struct
import subprocess
import time

import requests
from prometheus_client import start_http_server, Gauge

# systemd user units inherit a minimal PATH; kubectl lives in /usr/local/bin.
os.environ["PATH"] = "/usr/local/bin:" + os.environ.get("PATH", "/usr/bin:/bin")
os.environ.setdefault("KUBECONFIG", "/etc/rancher/k3s/k3s.yaml")

POLL_INTERVAL_SECONDS = 15
WORLD_SIZE_CHECK_INTERVAL_SECONDS = int(os.environ.get("WORLD_SIZE_CHECK_INTERVAL_SECONDS", "600"))

KUBECTL = os.environ.get("KUBECTL_BIN", "kubectl")
NAMESPACE = os.environ.get("MINECRAFT_NAMESPACE", "minecraft")
DEPLOYMENT = os.environ.get("MINECRAFT_DEPLOYMENT", "minecraft")
PVC_NAME = os.environ.get("MINECRAFT_PVC", "minecraft-world")
RCON_SERVICE = os.environ.get("MINECRAFT_RCON_SERVICE", "minecraft-rcon")

# Empty = resolve the Service ClusterIP dynamically (see module docstring).
RCON_HOST = os.environ.get("MINECRAFT_RCON_HOST", "")
RCON_PORT = int(os.environ.get("MINECRAFT_RCON_PORT", "25575"))
RCON_PASSWORD = os.environ["MINECRAFT_RCON_PASSWORD"]
# Empty = resolve the PVC's host path dynamically.
WORLD_DIR = os.environ.get("MINECRAFT_WORLD_DIR", "")
WORLD_FOLDERS = ["world", "world_nether", "world_the_end"]

# scripts/minecraft-auto-update.sh's own state/log files (see its header).
# Plain text, not JSON: STATE_FILE holds a single line "$MC_VERSION
# $build_id" (e.g. "1.21.11 127"), rewritten only on a SUCCESSFUL deploy --
# a no-op run ("nothing new upstream") or a failed run leaves it untouched.
# LOG_FILE gets at least one timestamped log() line appended every run,
# success or failure, so its mtime is a reasonable "last attempt" proxy.
# Neither file exists until the updater has run at least once.
UPDATE_STATE_FILE = os.environ.get("MINECRAFT_UPDATE_STATE_FILE", "/home/chase/minecraft/.last-built-mc-version")
UPDATE_LOG_FILE = os.environ.get("MINECRAFT_UPDATE_LOG_FILE", "/home/chase/minecraft/logs/mc-updater.log")
UPDATE_CHECK_INTERVAL_SECONDS = int(os.environ.get("UPDATE_CHECK_INTERVAL_SECONDS", "300"))

# Same alerting bot/DM pattern as host-health/observability-watcher.
BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN")
USER_ID = os.environ.get("DISCORD_USER_ID")
DISCORD_API = "https://discord.com/api/v10"
COOLDOWN_SECONDS = int(os.environ.get("ALERT_COOLDOWN_SECONDS", "300"))
LOW_TPS_THRESHOLD = float(os.environ.get("LOW_TPS_THRESHOLD", "15"))
LOW_TPS_SUSTAINED_CHECKS = int(os.environ.get("LOW_TPS_SUSTAINED_CHECKS", "4"))  # 4 * 15s = 1 minute

tps_1m = Gauge("minecraft_tps_1m", "Server TPS averaged over the last 1 minute")
tps_5m = Gauge("minecraft_tps_5m", "Server TPS averaged over the last 5 minutes")
tps_15m = Gauge("minecraft_tps_15m", "Server TPS averaged over the last 15 minutes")
players_online = Gauge("minecraft_players_online", "Current online player count")
players_max = Gauge("minecraft_players_max", "Configured max player count")
server_up = Gauge("minecraft_server_up", "1 if RCON connected successfully, 0 otherwise")
world_size_bytes = Gauge("minecraft_world_size_bytes", "Disk usage of a world folder", ["world"])
# minecraft_process_cpu_percent / minecraft_process_memory_bytes intentionally
# gone -- see "Process metrics: REMOVED" in the module docstring.

# scripts/minecraft-auto-update.sh tracking -- see UPDATE_STATE_FILE/
# UPDATE_LOG_FILE above for what these are read from.
update_last_attempt = Gauge("minecraft_update_last_attempt_timestamp_seconds", "Unix time of the last auto-update run (attempt, not necessarily success)")
update_last_success_ts = Gauge("minecraft_update_last_success_timestamp_seconds", "Unix time of the last auto-update run that actually shipped a new build")
update_last_success = Gauge("minecraft_update_last_success", "1 if the most recent auto-update run completed without error (including a no-op 'nothing new'), 0 if it failed")
update_current_build = Gauge("minecraft_update_current_build", "Info-style gauge, always 1, labeled with the build currently on disk per STATE_FILE", ["version", "build"])

MC_COLOR_CODE = re.compile(r"§.")
TPS_PATTERN = re.compile(r"([\d.]+),\s*([\d.]+),\s*([\d.]+)")
LIST_PATTERN = re.compile(r"There are (\d+) of a max of (\d+) players online")
UPDATE_LOG_TS_RE = re.compile(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]")
UPDATE_LOG_SUCCESS_RE = re.compile(r"\] (Success: |No change since last build)")
UPDATE_LOG_FAILURE_RE = re.compile(r"\] (ERROR: |Rolling back: )")

_last_alert_time = {}
_dm_channel_id = None
_low_tps_streak = 0
_was_up = True
_rcon_endpoint = None      # cached (host, port), invalidated on any failure
_world_dir = None          # cached resolved PVC host path
_world_dir_warned = False


def _get_dm_channel():
    global _dm_channel_id
    if _dm_channel_id:
        return _dm_channel_id
    resp = requests.post(
        f"{DISCORD_API}/users/@me/channels",
        headers={"Authorization": f"Bot {BOT_TOKEN}"},
        json={"recipient_id": USER_ID},
        timeout=10,
    )
    resp.raise_for_status()
    _dm_channel_id = resp.json()["id"]
    return _dm_channel_id


def send_discord_alert(title, description, color=0xE74C3C):
    if not BOT_TOKEN or not USER_ID:
        print(f"[minecraft-exporter] Bot token/user id not set, would have alerted: {title}")
        return
    try:
        channel_id = _get_dm_channel()
        payload = {
            "embeds": [{"title": title, "description": description[:3900], "color": color}]
        }
        resp = requests.post(
            f"{DISCORD_API}/channels/{channel_id}/messages",
            headers={"Authorization": f"Bot {BOT_TOKEN}"},
            json=payload,
            timeout=10,
        )
        resp.raise_for_status()
    except Exception as e:
        print(f"[minecraft-exporter] Failed to send Discord DM: {e}")
        global _dm_channel_id
        _dm_channel_id = None


def cooldown_ok(key):
    now = time.time()
    if now - _last_alert_time.get(key, 0) >= COOLDOWN_SECONDS:
        _last_alert_time[key] = now
        return True
    return False


# --- kubectl lookups ---------------------------------------------------------


def kubectl(*args, timeout=15):
    proc = subprocess.run([KUBECTL, *args], capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"kubectl {' '.join(args)}: {proc.stderr.strip()}")
    return proc.stdout.strip()


def resolve_rcon_endpoint():
    """(host, port) for RCON, cached until a connection fails.

    Explicit MINECRAFT_RCON_HOST wins. Otherwise look up the Service ClusterIP;
    if the configured Service name is absent, fall back to any Service in the
    namespace exposing RCON_PORT (the Minecraft manifests are authored
    separately and the Service name is the likeliest thing to differ).
    """
    global _rcon_endpoint
    if _rcon_endpoint:
        return _rcon_endpoint
    if RCON_HOST:
        _rcon_endpoint = (RCON_HOST, RCON_PORT)
        return _rcon_endpoint

    svc = None
    try:
        svc = json.loads(kubectl("get", "svc", RCON_SERVICE, "-n", NAMESPACE, "-o", "json"))
    except Exception:
        items = json.loads(kubectl("get", "svc", "-n", NAMESPACE, "-o", "json")).get("items", [])
        for candidate in items:
            ports = [p.get("port") for p in candidate.get("spec", {}).get("ports", [])]
            if RCON_PORT in ports and candidate["spec"].get("clusterIP") not in (None, "None"):
                svc = candidate
                break
        if svc is None:
            raise RuntimeError(
                f"no Service in {NAMESPACE} exposes port {RCON_PORT}; set MINECRAFT_RCON_SERVICE "
                "or MINECRAFT_RCON_HOST"
            )

    cluster_ip = svc["spec"].get("clusterIP")
    if not cluster_ip or cluster_ip == "None":
        raise RuntimeError(f"Service {svc['metadata']['name']} is headless; need a ClusterIP")
    _rcon_endpoint = (cluster_ip, RCON_PORT)
    print(f"[minecraft-exporter] RCON endpoint resolved to {cluster_ip}:{RCON_PORT}")
    return _rcon_endpoint


def pod_status_hint():
    """Short human string about the pod, used to make down-alerts actionable.

    Purely cosmetic -- a rollout and a crash-loop produce identical socket
    errors, and 'ContainerCreating' vs 'CrashLoopBackOff' is the difference
    between ignoring the DM and getting out of bed.
    """
    try:
        raw = kubectl(
            "get", "pods", "-n", NAMESPACE,
            "-l", f"app={DEPLOYMENT}", "-o", "json",
        )
        items = json.loads(raw).get("items", [])
        if not items:
            return f"no pods matching app={DEPLOYMENT} in namespace {NAMESPACE}"
        bits = []
        for pod in items:
            phase = pod.get("status", {}).get("phase", "?")
            states = []
            for cs in pod.get("status", {}).get("containerStatuses", []) or []:
                state = next(iter(cs.get("state", {})), "?")
                reason = (cs.get("state", {}).get(state) or {}).get("reason")
                states.append(f"{cs['name']}={reason or state} (restarts {cs.get('restartCount', 0)})")
            bits.append(f"{pod['metadata']['name']}: {phase}" + (f", {', '.join(states)}" if states else ""))
        return "; ".join(bits)
    except Exception as e:
        return f"pod status unavailable ({e})"


# --- RCON polling (unchanged apart from where it connects) -------------------


def rcon_command(sock, request_id, command):
    payload = command.encode("utf-8") + b"\x00\x00"
    packet = struct.pack("<ii", request_id, 2) + payload
    sock.send(struct.pack("<i", len(packet)) + packet)
    length = struct.unpack("<i", sock.recv(4))[0]
    data = sock.recv(length)
    return data[8:-2].decode("utf-8", errors="replace")


def poll_rcon():
    global _low_tps_streak, _was_up, _rcon_endpoint
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(5)
    try:
        host, port = resolve_rcon_endpoint()
        sock.connect((host, port))
        auth_payload = RCON_PASSWORD.encode("utf-8") + b"\x00\x00"
        auth_packet = struct.pack("<ii", 1, 3) + auth_payload
        sock.send(struct.pack("<i", len(auth_packet)) + auth_packet)
        length = struct.unpack("<i", sock.recv(4))[0]
        auth_reply = sock.recv(length)
        request_id = struct.unpack("<i", auth_reply[:4])[0]
        if request_id == -1:
            print("[minecraft-exporter] RCON auth failed")
            server_up.set(0)
            return

        tps_raw = MC_COLOR_CODE.sub("", rcon_command(sock, 2, "tps"))
        match = TPS_PATTERN.search(tps_raw)
        if match:
            tps_1m_val = float(match.group(1))
            tps_1m.set(tps_1m_val)
            tps_5m.set(float(match.group(2)))
            tps_15m.set(float(match.group(3)))

            if tps_1m_val < LOW_TPS_THRESHOLD:
                _low_tps_streak += 1
            else:
                _low_tps_streak = 0

            if _low_tps_streak >= LOW_TPS_SUSTAINED_CHECKS and cooldown_ok("low_tps"):
                send_discord_alert(
                    "🐢 Minecraft server lagging",
                    f"TPS has been below {LOW_TPS_THRESHOLD} for "
                    f"{_low_tps_streak * POLL_INTERVAL_SECONDS}s (currently {tps_1m_val:.1f}).",
                )

        list_raw = MC_COLOR_CODE.sub("", rcon_command(sock, 3, "list"))
        match = LIST_PATTERN.search(list_raw)
        if match:
            players_online.set(int(match.group(1)))
            players_max.set(int(match.group(2)))

        server_up.set(1)
        if not _was_up and cooldown_ok("server_recovered"):
            send_discord_alert("✅ Minecraft server back up", "RCON reconnected successfully.", color=0x2ECC71)
        _was_up = True
    except Exception as e:
        print(f"[minecraft-exporter] RCON poll failed: {e}")
        server_up.set(0)
        # Drop the cached ClusterIP: a Service recreate looks exactly like this
        # and would otherwise leave us hammering a dead address forever.
        _rcon_endpoint = None
        if _was_up and cooldown_ok("server_down"):
            send_discord_alert(
                "🔴 Minecraft server down",
                f"RCON connection failed: {e}\n\nPod state: {pod_status_hint()}",
            )
        _was_up = False
    finally:
        sock.close()


# --- world size (host path now comes from the PVC) ---------------------------


def resolve_world_dir():
    """Host directory backing the world PVC; cached once resolved.

    Same lookup as minecraft_backup.py -- duplicated rather than shared so each
    script stays a single self-contained file, matching how the Discord helper
    was already duplicated between these two.
    """
    global _world_dir
    if _world_dir:
        return _world_dir
    if WORLD_DIR:
        _world_dir = WORLD_DIR
        return _world_dir
    items = json.loads(kubectl("get", "pv", "-o", "json")).get("items", [])
    for pv in items:
        claim = pv.get("spec", {}).get("claimRef") or {}
        if claim.get("namespace") != NAMESPACE or claim.get("name") != PVC_NAME:
            continue
        spec = pv["spec"]
        path = (spec.get("hostPath") or {}).get("path") or (spec.get("local") or {}).get("path")
        if path:
            _world_dir = path
            print(f"[minecraft-exporter] World PVC {NAMESPACE}/{PVC_NAME} -> {path}")
            return _world_dir
    raise RuntimeError(f"no PV bound to PVC {NAMESPACE}/{PVC_NAME}")


_last_world_size_check = 0


def poll_world_size():
    global _last_world_size_check, _world_dir_warned
    now = time.time()
    if now - _last_world_size_check < WORLD_SIZE_CHECK_INTERVAL_SECONDS:
        return
    _last_world_size_check = now

    # World size is a nice-to-have; never let it take the exporter (and with it
    # the down-alerting) off the air.
    try:
        world_dir = resolve_world_dir()
    except Exception as e:
        if not _world_dir_warned:
            print(f"[minecraft-exporter] World size unavailable: {e}")
            _world_dir_warned = True
        return

    for world in WORLD_FOLDERS:
        path = os.path.join(world_dir, world)
        if not os.path.isdir(path):
            continue
        total = 0
        for dirpath, _, filenames in os.walk(path):
            for f in filenames:
                try:
                    total += os.path.getsize(os.path.join(dirpath, f))
                except OSError:
                    pass
        world_size_bytes.labels(world=world).set(total)

    # Walking the world tree as a non-root user needs traversal rights on
    # /var/lib/rancher/k3s/storage; see scripts/README.md. A permission problem
    # shows up as every gauge sitting at 0 rather than as an error, so say so.
    if not _world_dir_warned and not os.access(world_dir, os.R_OK | os.X_OK):
        print(
            f"[minecraft-exporter] WARNING: {world_dir} is not readable by uid {os.getuid()}; "
            "world size metrics will stay at 0."
        )
        _world_dir_warned = True


# --- auto-update tracking (scripts/minecraft-auto-update.sh) -----------------


def _tail_lines(path, max_bytes=8192):
    """Last `max_bytes` of a file, split into lines. [] if it doesn't exist."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - max_bytes))
            data = f.read()
    except OSError:
        return []
    return data.decode("utf-8", errors="replace").splitlines()


_last_update_check = 0


def poll_update_status():
    """Read minecraft-auto-update.sh's state/log files, if they exist yet.

    Neither file exists until the updater has run at least once (it isn't
    installed in crontab until the `minecraft` namespace does), so every
    lookup here is best-effort: gauges simply stay at their zero default
    rather than the process crashing or logging noise every poll.
    """
    global _last_update_check
    now = time.time()
    if now - _last_update_check < UPDATE_CHECK_INTERVAL_SECONDS:
        return
    _last_update_check = now

    # Current build + last successful *update* (as opposed to last successful
    # no-op check): STATE_FILE is "$MC_VERSION $build_id", rewritten only by
    # the script's success path.
    try:
        with open(UPDATE_STATE_FILE) as f:
            version, build = f.read().split()
        update_current_build.clear()  # drop the previous build's label combo
        update_current_build.labels(version=version, build=build).set(1)
        update_last_success_ts.set(os.path.getmtime(UPDATE_STATE_FILE))
    except (OSError, ValueError):
        pass

    # Last run's outcome (attempt, whether or not it shipped a build): scan
    # the log tail from the end for the latest terminal marker line. "No
    # change since last build" counts as success -- the run completed
    # cleanly, it just found nothing new upstream.
    for line in reversed(_tail_lines(UPDATE_LOG_FILE)):
        if UPDATE_LOG_SUCCESS_RE.search(line):
            update_last_success.set(1)
        elif UPDATE_LOG_FAILURE_RE.search(line):
            update_last_success.set(0)
        else:
            continue
        ts_match = UPDATE_LOG_TS_RE.match(line)
        if ts_match:
            update_last_attempt.set(time.mktime(time.strptime(ts_match.group(1), "%Y-%m-%d %H:%M:%S")))
        break
    else:
        # No terminal marker in the tail (short/rotated log) -- mtime is a
        # coarse but reasonable "something happened" fallback.
        try:
            update_last_attempt.set(os.path.getmtime(UPDATE_LOG_FILE))
        except OSError:
            pass


if __name__ == "__main__":
    start_http_server(9202)
    send_discord_alert(
        "✅ Minecraft exporter online",
        f"Alerting on: server down, TPS < {LOW_TPS_THRESHOLD} sustained "
        f"{LOW_TPS_SUSTAINED_CHECKS * POLL_INTERVAL_SECONDS}s+.",
        color=0x2ECC71,
    )
    while True:
        poll_rcon()
        poll_world_size()
        poll_update_status()
        time.sleep(POLL_INTERVAL_SECONDS)
