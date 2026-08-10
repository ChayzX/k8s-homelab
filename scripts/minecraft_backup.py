"""Daily Minecraft world backup -- k3s port.

Port of /home/chase/docker/observability/monitoring/minecraft-exporter/minecraft_backup.py.
Same shape as the original (RCON save-off -> save-all flush -> tar.gz -> save-on,
7-day retention, Discord DM on failure); everything that changed is a
consequence of Minecraft moving from a host process into a pod.

What changed and why
--------------------
1. RCON no longer goes to localhost:25575. Two paths, tried in order:
     a. `kubectl exec deploy/minecraft -n minecraft -- rcon-cli <cmd>` when the
        server image ships an RCON client (itzg-style `rcon-cli`, or `mcrcon`).
        Preferred: no password handling here, no network path to get wrong.
     b. Otherwise the original raw-socket RCON client, pointed at the RCON
        Service's ClusterIP. ClusterIP addresses ARE reachable from the node
        itself (kube-proxy programs the same nftables rules for host-originated
        traffic), which is why this works from a host systemd timer without a
        NodePort. We resolve the ClusterIP with kubectl on every run because it
        changes whenever the Service is recreated.
2. The world data lives in a local-path PVC, whose on-disk directory is named
   after a PV UUID that differs per install. We resolve it dynamically from the
   PV's claimRef rather than hardcoding it. A hardcoded UUID would survive
   exactly until the first PVC recreate, then back up nothing.
3. Hibernation is gone (msh retired; the server is always-on at Xmx6G), so the
   "is the server asleep" branch is gone with it. What replaces it is a
   *readiness* check: if the pod isn't Ready (mid-rollout, crash-looping,
   restarting), we skip the RCON save with a loud log line and still archive
   what is on disk. A slightly-stale backup beats no backup, and a pod that is
   down is not writing to the world anyway.

Assumptions (verify once the Minecraft manifests land in
/home/chase/k8s-homelab/minecraft/ -- they did not exist when this was written):
  * Deployment `minecraft` in namespace `minecraft`.
  * PVC `minecraft-world` in that namespace, holding the whole server dir, so
    the world folders sit at <pvc>/world, <pvc>/world_nether, <pvc>/world_the_end.
  * A Service exposing RCON on 25575. Any of the names/ports below can be
    overridden with env vars without editing this file.

Run as the same user/timer as before; see scripts/README.md for the unit and the
one-time permission fix for reading under /var/lib/rancher/k3s/storage.
"""

import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import tarfile
import time
from datetime import datetime, timezone

import requests

# cron/systemd-user give us a minimal PATH; kubectl lives in /usr/local/bin.
os.environ["PATH"] = "/usr/local/bin:" + os.environ.get("PATH", "/usr/bin:/bin")
# k3s is installed with --write-kubeconfig-mode 644, so the default is readable.
os.environ.setdefault("KUBECONFIG", "/etc/rancher/k3s/k3s.yaml")

KUBECTL = os.environ.get("KUBECTL_BIN", "kubectl")
NAMESPACE = os.environ.get("MINECRAFT_NAMESPACE", "minecraft")
DEPLOYMENT = os.environ.get("MINECRAFT_DEPLOYMENT", "minecraft")
PVC_NAME = os.environ.get("MINECRAFT_PVC", "minecraft-world")
RCON_SERVICE = os.environ.get("MINECRAFT_RCON_SERVICE", "minecraft-rcon")

RCON_PORT = int(os.environ.get("MINECRAFT_RCON_PORT", "25575"))
RCON_PASSWORD = os.environ["MINECRAFT_RCON_PASSWORD"]
# Leave MINECRAFT_WORLD_DIR unset to resolve the PVC's host path dynamically;
# set it to pin an explicit path (e.g. when restoring from a copy).
WORLD_DIR = os.environ.get("MINECRAFT_WORLD_DIR") or None
BACKUP_DIR = os.environ.get("MINECRAFT_BACKUP_DIR", "/home/chase/minecraft-backups")
RETENTION_COUNT = int(os.environ.get("MINECRAFT_BACKUP_RETENTION", "7"))
WORLD_FOLDERS = ["world", "world_nether", "world_the_end"]

BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN")
USER_ID = os.environ.get("DISCORD_USER_ID")
DISCORD_API = "https://discord.com/api/v10"


def send_discord_alert(title, description, color=0xE74C3C):
    if not BOT_TOKEN or not USER_ID:
        print(f"[minecraft-backup] Bot token/user id not set, would have alerted: {title}")
        return
    try:
        resp = requests.post(
            f"{DISCORD_API}/users/@me/channels",
            headers={"Authorization": f"Bot {BOT_TOKEN}"},
            json={"recipient_id": USER_ID},
            timeout=10,
        )
        resp.raise_for_status()
        channel_id = resp.json()["id"]
        resp = requests.post(
            f"{DISCORD_API}/channels/{channel_id}/messages",
            headers={"Authorization": f"Bot {BOT_TOKEN}"},
            json={"embeds": [{"title": title, "description": description[:3900], "color": color}]},
            timeout=10,
        )
        resp.raise_for_status()
    except Exception as e:
        print(f"[minecraft-backup] Failed to send Discord DM: {e}")


# --- kubectl plumbing -------------------------------------------------------


def kubectl(*args, timeout=30, check=True):
    """Run kubectl and return stdout. Raises on non-zero when check=True."""
    proc = subprocess.run(
        [KUBECTL, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if check and proc.returncode != 0:
        raise RuntimeError(
            f"kubectl {' '.join(args)} failed ({proc.returncode}): {proc.stderr.strip()}"
        )
    return proc.stdout.strip()


def resolve_world_dir():
    """Find the host directory backing the world PVC.

    local-path-provisioner creates one directory per PV under
    /var/lib/rancher/k3s/storage/pvc-<uuid>_<namespace>_<claim>. The UUID is
    generated at provision time, so it differs per install and changes if the
    PVC is ever recreated -- hence the lookup instead of a constant.

    Older/newer provisioner versions emit either a hostPath PV or a `local` PV;
    we accept both.
    """
    if WORLD_DIR:
        print(f"[minecraft-backup] Using pinned MINECRAFT_WORLD_DIR={WORLD_DIR}")
        return WORLD_DIR

    raw = kubectl("get", "pv", "-o", "json")
    for pv in json.loads(raw).get("items", []):
        claim = pv.get("spec", {}).get("claimRef") or {}
        if claim.get("namespace") != NAMESPACE or claim.get("name") != PVC_NAME:
            continue
        spec = pv["spec"]
        path = (spec.get("hostPath") or {}).get("path") or (spec.get("local") or {}).get("path")
        if path:
            print(f"[minecraft-backup] Resolved PVC {NAMESPACE}/{PVC_NAME} -> {path}")
            return path
        raise RuntimeError(
            f"PV {pv['metadata']['name']} is bound to {NAMESPACE}/{PVC_NAME} but is neither a "
            "hostPath nor a local volume; set MINECRAFT_WORLD_DIR explicitly."
        )

    raise RuntimeError(
        f"no PersistentVolume is bound to PVC {NAMESPACE}/{PVC_NAME}. "
        "Check the claim name (MINECRAFT_PVC) against `kubectl get pvc -n "
        f"{NAMESPACE}`."
    )


def deployment_is_ready():
    """True when at least one Minecraft pod is Ready.

    Replaces the old hibernation check. Not-Ready means mid-restart, crash-loop
    or image pull -- all cases where RCON will refuse the connection and where
    the JVM is not writing to the world, so archiving the on-disk state is both
    safe and still worth doing.
    """
    try:
        out = kubectl(
            "get", "deployment", DEPLOYMENT, "-n", NAMESPACE,
            "-o", "jsonpath={.status.readyReplicas}", check=False,
        )
        return out.isdigit() and int(out) > 0
    except Exception as e:
        print(f"[minecraft-backup] Could not query deployment readiness: {e}")
        return False


# --- RCON: exec-in-pod first, ClusterIP socket second ------------------------


def detect_in_pod_rcon_client():
    """Return the name of an RCON CLI available inside the server container.

    Preferred over the raw socket because it needs no network path and no
    password on this side -- images that ship rcon-cli also configure it.
    Returns None if the image has neither (e.g. a plain temurin + paper.jar
    image), in which case we fall back to the ClusterIP socket.
    """
    try:
        out = kubectl(
            "exec", f"deployment/{DEPLOYMENT}", "-n", NAMESPACE, "--",
            "sh", "-c", "command -v rcon-cli || command -v mcrcon || true",
            timeout=20, check=False,
        )
    except Exception as e:
        print(f"[minecraft-backup] RCON client probe failed: {e}")
        return None
    binary = out.splitlines()[0].strip() if out else ""
    if binary.endswith("rcon-cli"):
        return "rcon-cli"
    if binary.endswith("mcrcon"):
        return "mcrcon"
    return None


class ExecRcon:
    """RCON over `kubectl exec`. One exec per command -- fine at 3 commands/day."""

    def __init__(self, client):
        self.client = client

    def command(self, cmd):
        if self.client == "rcon-cli":
            argv = ["rcon-cli", cmd]
        else:
            # mcrcon needs to be told where to go; localhost is inside the pod.
            argv = ["mcrcon", "-H", "127.0.0.1", "-P", str(RCON_PORT),
                    "-p", RCON_PASSWORD, "-w", "0", cmd]
        return kubectl(
            "exec", f"deployment/{DEPLOYMENT}", "-n", NAMESPACE, "--", *argv,
            timeout=30,
        )

    def close(self):
        pass


class SocketRcon:
    """The original raw RCON client, pointed at the Service's ClusterIP."""

    def __init__(self, host, port):
        self.request_id = 1
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.settimeout(10)
        self.sock.connect((host, port))
        payload = RCON_PASSWORD.encode("utf-8") + b"\x00\x00"
        packet = struct.pack("<ii", 1, 3) + payload
        self.sock.send(struct.pack("<i", len(packet)) + packet)
        length = struct.unpack("<i", self.sock.recv(4))[0]
        reply = self.sock.recv(length)
        if struct.unpack("<i", reply[:4])[0] == -1:
            raise RuntimeError("RCON auth failed")

    def command(self, cmd):
        self.request_id += 1
        payload = cmd.encode("utf-8") + b"\x00\x00"
        packet = struct.pack("<ii", self.request_id, 2) + payload
        self.sock.send(struct.pack("<i", len(packet)) + packet)
        length = struct.unpack("<i", self.sock.recv(4))[0]
        data = self.sock.recv(length)
        return data[8:-2].decode("utf-8", errors="replace")

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


def resolve_rcon_cluster_ip():
    """ClusterIP + port of the RCON Service.

    Tries the configured Service name first, then falls back to scanning the
    namespace for any Service exposing RCON_PORT -- the Minecraft manifests are
    written by a different pass and the Service name is the detail most likely
    to differ from the assumption at the top of this file.
    """
    try:
        raw = kubectl("get", "svc", RCON_SERVICE, "-n", NAMESPACE, "-o", "json")
        svc = json.loads(raw)
    except Exception:
        raw = kubectl("get", "svc", "-n", NAMESPACE, "-o", "json")
        svc = None
        for candidate in json.loads(raw).get("items", []):
            ports = [p.get("port") for p in candidate.get("spec", {}).get("ports", [])]
            if RCON_PORT in ports and candidate["spec"].get("clusterIP") not in (None, "None"):
                svc = candidate
                print(
                    f"[minecraft-backup] Service '{RCON_SERVICE}' not found; using "
                    f"'{candidate['metadata']['name']}' which exposes {RCON_PORT}."
                )
                break
        if svc is None:
            raise RuntimeError(
                f"no Service in namespace {NAMESPACE} exposes port {RCON_PORT}; "
                "set MINECRAFT_RCON_SERVICE."
            )

    cluster_ip = svc["spec"].get("clusterIP")
    if not cluster_ip or cluster_ip == "None":
        raise RuntimeError(
            f"Service {svc['metadata']['name']} is headless; this script needs a ClusterIP."
        )
    port = next(
        (p["port"] for p in svc["spec"]["ports"] if p.get("port") == RCON_PORT),
        svc["spec"]["ports"][0]["port"],
    )
    return cluster_ip, port


def connect_rcon():
    """Best available RCON channel, or None if the server can't be talked to.

    Returning None is not fatal: main() logs it and backs up the on-disk state
    anyway (see the readiness note in the module docstring).
    """
    if not deployment_is_ready():
        print(
            f"[minecraft-backup] deployment/{DEPLOYMENT} has no Ready replica -- skipping the "
            "RCON save. Backing up the on-disk world as-is; the server is not writing to it."
        )
        return None

    client = detect_in_pod_rcon_client()
    if client:
        print(f"[minecraft-backup] Using in-pod {client} via kubectl exec")
        return ExecRcon(client)

    try:
        host, port = resolve_rcon_cluster_ip()
        print(f"[minecraft-backup] No in-pod RCON client; using ClusterIP {host}:{port}")
        return SocketRcon(host, port)
    except Exception as e:
        print(
            f"[minecraft-backup] WARNING: could not reach RCON ({e}). Continuing without a "
            "flush -- the archive may miss chunks modified since the last autosave."
        )
        return None


# --- backup ------------------------------------------------------------------


def check_readable(path):
    """Fail early and legibly if the PVC directory isn't readable by this user.

    /var/lib/rancher/k3s and its storage/ subtree are root-owned. The world
    files themselves are written by the container as UID 1000 (= chase), but the
    intermediate directories may not be traversable. See scripts/README.md for
    the one-time `chmod o+rx` fix or the run-as-root alternative.
    """
    if not os.path.isdir(path):
        raise RuntimeError(f"world directory {path} does not exist")
    if not os.access(path, os.R_OK | os.X_OK):
        raise RuntimeError(
            f"{path} is not readable by uid {os.getuid()}. Either run this timer as root, or "
            "grant traversal once: sudo chmod o+rx /var/lib/rancher /var/lib/rancher/k3s "
            "/var/lib/rancher/k3s/storage (see scripts/README.md)."
        )


def prune_old_backups():
    if not os.path.isdir(BACKUP_DIR):
        return
    backups = sorted(
        (f for f in os.listdir(BACKUP_DIR) if f.startswith("world-backup-") and f.endswith(".tar.gz")),
    )
    excess = len(backups) - RETENTION_COUNT
    for old in backups[:max(excess, 0)]:
        path = os.path.join(BACKUP_DIR, old)
        os.remove(path)
        print(f"[minecraft-backup] Pruned old backup: {old}")


def main():
    os.makedirs(BACKUP_DIR, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    archive_path = os.path.join(BACKUP_DIR, f"world-backup-{timestamp}.tar.gz")

    world_dir = resolve_world_dir()
    check_readable(world_dir)

    present = [w for w in WORLD_FOLDERS if os.path.isdir(os.path.join(world_dir, w))]
    if not present:
        # A silently-empty tarball is the exact failure the original's alert
        # text warns about, so refuse to produce one.
        raise RuntimeError(
            f"none of {WORLD_FOLDERS} found under {world_dir}. The PVC may mount the server dir "
            "at a different level than assumed -- check `ls` on that path."
        )

    rcon = connect_rcon()
    try:
        if rcon:
            print("[minecraft-backup] Disabling autosave")
            rcon.command("save-off")
            print("[minecraft-backup] Forcing save-all")
            rcon.command("save-all flush")
            time.sleep(5)  # let the async save actually finish writing to disk

        print(f"[minecraft-backup] Archiving to {archive_path}")
        with tarfile.open(archive_path, "w:gz") as tar:
            for world in present:
                tar.add(os.path.join(world_dir, world), arcname=world)
    finally:
        if rcon:
            print("[minecraft-backup] Re-enabling autosave")
            try:
                rcon.command("save-on")
            except Exception as e:
                # Leaving autosave off would be far worse than a failed backup,
                # so this gets its own alert rather than riding on the outer one.
                print(f"[minecraft-backup] FAILED to re-enable autosave: {e}", file=sys.stderr)
                send_discord_alert(
                    "🔴 Minecraft autosave may still be OFF",
                    f"`save-on` failed after the backup: {e}\n\nRun `save-on` via RCON "
                    f"(`kubectl exec -n {NAMESPACE} deploy/{DEPLOYMENT} -- rcon-cli save-on`) "
                    "or restart the pod, or the world stops persisting.",
                )
            rcon.close()

    size_mb = os.path.getsize(archive_path) / (1024 * 1024)
    print(f"[minecraft-backup] Done: {archive_path} ({size_mb:.1f} MB)")

    # Backups land on the same 5900rpm /dev/sda2 as everything else; a full
    # disk would take the cluster down with it, so say something while there
    # is still room to act.
    free_gb = shutil.disk_usage(BACKUP_DIR).free / (1024 ** 3)
    if free_gb < 5:
        send_discord_alert(
            "🟠 Backup disk running low",
            f"Only {free_gb:.1f} GiB free on the filesystem holding {BACKUP_DIR}.",
            color=0xE67E22,
        )

    prune_old_backups()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[minecraft-backup] FAILED: {e}", file=sys.stderr)
        send_discord_alert(
            "🔴 Minecraft backup failed",
            f"{e}\n\nA silently failing backup is worse than no backup -- check "
            "`journalctl --user -u minecraft-backup` for the full error.",
        )
        sys.exit(1)
