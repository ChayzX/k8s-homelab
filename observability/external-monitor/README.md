# Independent external monitor

This service runs outside the home k3s cluster and checks public application
behavior without Kubernetes, Authentik, or the home watcher. It is suitable
for the small GCP VM. It is installed and enabled on `discordmusicbot` as
`homelab-external-monitor.service` as of 2026-09-09.

Optional `/etc/homelab-monitor/monitor.env` values:

```text
MONITOR_DISCORD_WEBHOOK=https://discord.com/api/webhooks/...
MONITOR_INTERVAL_SECONDS=60
MONITOR_TIMEOUT_SECONDS=12
# Optional protected Kubernetes API route check. A 403 means Cloudflare Access
# is reachable; it does not prove authenticated Kubernetes API health.
MONITOR_API_URL=https://k8s-api.greeniespantry.uk/version
MONITOR_API_EXPECTED_STATUS=403
# Optional R2 freshness checks. Format: name=prefix:max_age_seconds,...
MONITOR_R2_BUCKET=pantry-bot-backups
MONITOR_R2_ENDPOINT=https://<account>.r2.cloudflarestorage.com
MONITOR_R2_ACCESS_KEY_ID=...
MONITOR_R2_SECRET_ACCESS_KEY=...
MONITOR_R2_PREFIXES=auth=recovery/auth-postgresql/:172800,operations=recovery/operations/:172800,minecraft=recovery/minecraft/:172800,minecraft-config=recovery/minecraft-config/:172800,jmusicbot=jmusicbot/:86400
```

The R2 credentials must be scoped read-only to the recovery bucket and stored
only in the root-owned mode-600 environment file on the external VM. When
`MONITOR_R2_PREFIXES` is set, incomplete credentials or a stale/missing prefix
fails the monitor; when it is unset, R2 checks are explicitly skipped.

The monitor code is deployed on GCP, but R2 checks remain disabled until a
read-only monitoring credential is provisioned. Do not reuse a workload
credential with write/delete access for this purpose.

The default public checks include the split PantryBot surfaces:
`commands.greeniespantry.uk` and `mods.greeniespantry.uk`. These validate that
the OAuth-free viewer command guide and private moderator UI routes are
reachable independently of the operator OAuth hostname. HTTP success and
redirect responses are accepted because the monitor tests origin availability,
not authenticated browser state.

The monitor is a singleton evaluator for its state ledger. Each cycle takes a
non-blocking advisory lock beside `state.json`; an overlapping process exits
with `MONITOR_LOCKED` without probing or notifying. State is replaced
atomically after a cycle, so a process interruption cannot leave truncated
JSON that would cause a replayed transition on restart.

Do not commit this file or put its values in Kubernetes manifests. The monitor
logs state transitions and writes the latest result to
`/var/lib/homelab-monitor/state.json`. UptimeRobot is the external monitor for
public endpoint availability; its account configuration is outside this repo.

## Notification receipt probe

When a webhook is configured, each firing/recovery transition records a
bounded receipt in `notification_receipts`. A receipt means the provider
accepted the HTTP request (2xx); it does not prove that a human read the
message. The record contains only the check identity, event, transport,
provider status, acceptance, timestamp, and a non-secret failure reason. Alert
text, webhook URLs, and credentials are never persisted.

During an outage rehearsal, probe the provider-acceptance contract from the
external VM:

```bash
python3 /usr/local/lib/homelab-monitor/probe-notification-receipt.py \
  --identity external-monitor:status \
  --event firing \
  --max-age-seconds 900
```

Exit status 0 means a recent accepted receipt exists. A missing, stale, or
non-accepted receipt exits 1 and prints only a small JSON reason. Human receipt
still requires an independent operator confirmation.

When changing the environment file, write a complete merged file and restart
the unit rather than replacing it with only the newly added variable.
