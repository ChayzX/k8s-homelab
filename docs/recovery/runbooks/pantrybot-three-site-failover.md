# PantryBot three-site automated failover (Home › Oracle › Canada)

Tracking: k8s-homelab#191, pantry-bot#147. Code: `observability/failover-witness/`.

## Model

- **One writer, fenced.** The GCP witness lease `pantry:postgres` (30 s TTL, epoch bumps on every grant) is the only authority. A site serves writes only while its promoter or agent holds the lease.
- **Priority without preemption.** A standby may compete for the lease only through `promotion-gate.sh`, which requires all of:
  - its upstream has been gone for its **priority delay** (Home 0 s, Oracle 45 s, Canada 120 s);
  - it streamed within 600 s (**freshness**);
  - it has the expected system identifier.

  If a higher-priority site promotes first, the follower re-points to it, streaming resumes, and the gate closes again.
- **Failback is voluntary.** Only the active holder yields (`handback.sh` on k3s sites, `Hand-Back` in the Canada agent). It yields only after a higher-priority standby has streamed with ≤1 MB lag for 10 minutes. It drains, waits for the target to replay its final LSN, then stops renewing.
- **Unreachable old writer.** When the old writer is silent at the first network probe (exit 75), it counts as fenced by lease expiry plus a 20 s grace. Connection refused, auth errors and mid-fence errors all block.

## Components per site

| | Home (k3s) | Oracle (k3s) | Canada (Windows/Docker) |
|---|---|---|---|
| Promoter | `pantry-postgres-home-promoter` (`oracle_promoter.py --site home`) | `pantry-postgres-oracle-promoter` | NSSM `PantryBot Canada site agent` (`pantry-site-agent.ps1`) |
| Local fence | `fence-home-postgres-local.sh` (`PANTRY_FENCE_APPS_ONLY=1` for a proven standby) | `fence-oracle-postgres-local.sh` | `fence-canada-writer.ps1` (standby-aware) |
| Fences others | `fence-old-writers-from-home.sh` → `fence-oracle-direct.sh` + `fence-canada-from-oracle.sh` (forced-command gate key) | `fence-old-writers-from-oracle.sh` | `pantry-writer-fence.ps1 -Site home/oracle` |
| Follower | `pantry-standby-follower` (systemd loop) | same | NSSM `PantryBot Canada standby follower` |
| Rejoin | `pantry-standby-rejoin.timer` | same | inside the agent |
| Hand-back | never (top priority) | `HANDBACK_COMMAND=handback.sh` → Home | agent → Home, else Oracle |
| Routes | `publish-cloudflare-routes.sh` | same | `publish-routes.ps1` |
| Witness path | local tunnel `:18765` | local tunnel `:18765` | direct tunnel (`pantry-witness-canada` on the witness VM, forward-only), then the Home and Oracle relay NodePort `:31421` |

**Replication.** Every standby connects straight to the primary:
- Home: `100.84.89.87:5432` (tailscale)
- Oracle: `100.78.181.15:5432`
- Canada: reached through the reverse tunnels on each k3s host at `127.0.0.1:25442`

Every site's `pg_hba` allows `pantry_replicator` from all three sites plus `172.17.0.1`. `PGDATA/.pgpass` has a wildcard entry for the replicator. Slots are named `pantry_<site>_standby`; the follower or rejoin creates them on the new primary.

**Routes.** Home and Oracle share the `PantryBot-App` tunnel (30dde1eb) with identical k8s origins. Only the active site runs `app-cloudflared`, which the fences enforce. DNS moves only to or from Canada's tunnel (8392cd48). Tunnel 59569621 is never touched.

## Rehearsed (2026-09-22)

| Scenario | Result | Mods/overlay impact |
|---|---|---|
| Canada → Home (Home promoter, first run) | promoted, RPO 0 | 3m06s |
| Home host loss → Oracle (automatic) | promoted at epoch 88; Canada re-pointed itself | ~2m |
| Home returns | backed up, reseeded from Oracle, streaming (automatic) | 0 |
| Oracle → Home voluntary hand-back (automatic after 10 min) | Oracle yielded; Home epoch 89; Oracle rejoined once Home was primary | 2m07s |
| Canada fence/restore | sandbox-verified end to end | 0 |
| Home + Oracle loss → Canada last resort (automatic) | promoted at epoch 90 over the direct witness path; parallel fences; DNS moved to Canada | ~10m (exit-code bug looped the attempt; fixed ac257d5 — expected ~3.5m) |
| Home + Oracle return | Home reseeded from Canada, Oracle restarted as standby (automatic) | 0 |
| Canada → Home voluntary hand-back (automatic after 10 min) | Canada yielded; Home epoch 91; Canada rejoined as standby | 3m54s |
| Home loss (2nd run) → **Canada** won instead of Oracle | priority bug: stale hand-back journal let Canada reclaim; fixed 97fd871 (safe: single fenced writer) | 1m54s |
| Canada → Oracle voluntary hand-back (Home down) | Oracle epoch 93; Canada rejoined as standby | 2m57s |
| **Oracle primary fails → Home** (R5) | Home epoch 94 on priority 0; Canada correctly stayed out | 1m50s |
| Home + Oracle loss → Canada, clean timed run (R6), Home-aligned images | Canada epoch 95; all 4 roles visible in Grafana | 3m35s |
| Canada → Home hand-back (Oracle auto re-pointed off a standby cascade) | Home epoch 96 | 2m58s |

All directions rehearsed. Canada also self-heals Docker Desktop (it quit on its own after a background self-update on 2026-09-22; auto-updates are now disabled).

## App versions (no drift, #379)

CI deploys a component to its target, then propagates the **same immutable tag**
to the other two sites (`propagate-oracle` / `propagate-home` / `propagate-canada`
in pantry-bot's `deploy.yml`). Standby sites are never started by a deploy:
Oracle keeps its replica count (`kubectl set image`, never `apply`), and Canada
only restarts a role that is already running.

- Canada's tags live in `C:\ProgramData\PantryBotCanadaPrep\canada-images.json`,
  written by CI and read by `start-canada-production.ps1` (the in-script map is
  the fallback).
- `Sync site images` (weekly, report-only; `apply=true` to fix) reconciles Oracle
  and Canada to whatever Home runs, for anything changed out of band.
- Canada runner prerequisites: its service account (`NETWORK SERVICE`) is in
  `docker-users` (restart the runner service after adding it) and has Modify on
  `canada-images.json`. Service accounts cannot use the Windows credential
  helper, so the job logs in with `docker/login-action`.

## Operator notes

- **Current authority:** `/var/lib/pantry-postgres-promoter/activation.json` on Home or Oracle, `C:\ProgramData\PantryBotCanadaPrep\agent\activation.json` on Canada. The phase is `active` on the holder.
- **Freshness / role:** `PGDATA/pantry-follower.state` on each database.
- **Never `source` a `/etc/failover-witness/*.env` in a shell.** An unquoted multi-word value gets executed (this ran a fence on the live primary on 2026-09-22). A test enforces quoting.
- **Never re-apply** `home/postgres-authority-standby-home-canada.yaml` to a live standby: it pins `replicas: 0`. Patch it instead.
- **Manual Canada rollback** after an aborted cutover, only when no other site promoted: `restore-canada-after-fence.ps1 -ConfirmNoOtherPrimary`.
- **Backups:** rejoin writes a backup before any reseed, to `/var/backups/pantry/` on k3s sites and `C:\ProgramData\PantryBotCanadaPrep\backups\` on Canada.

## Owner actions outstanding

1. ~~Canada direct witness path~~ done 2026-09-22: local user `pantry-witness-canada` on the witness VM (`gcp/witness-canada-user.sh`), NSSM `PantryBot Canada witness tunnel`.
2. ~~Canada Cloudflare token~~ done: installed from Home's token (owner's call) at `C:\ProgramData\PantryBotCanadaPrep\fence\cloudflare-token`.
3. **Cleanup that deletes data** (left for the owner):
   - `canada-production.env.bak-rotate` on Canada (holds old secrets);
   - the `canada-reseed*` temp files;
   - the stale 0/0 StatefulSets and PVCs on both clusters;
   - the `pantrybot-canada-replica-prep` namespace (the old-lineage standby, now scaled to 0);
   - the old `authority-gate.ps1` scheduled tasks (disabled).
4. **k8s-homelab#378:** hostNetwork standbys inherit `127.0.0.1 trust` in `pg_hba`.
