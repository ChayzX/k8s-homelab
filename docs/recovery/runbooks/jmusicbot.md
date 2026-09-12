# JMusicBot disaster recovery and controlled promotion SOP

## Current topology and authority

JMusicBot is in namespace `jmusicbot`, with the main workload
`deployment/jmusicbot` and health Service `jmusicbot-health`. The checked-in
Deployment uses a pinned image, `Recreate`, `emptyDir`, an R2 restore init
container, and an R2 sync sidecar. Mutable files are under the documented
`jmusicbot/` R2 prefix. The release notifier has separate state and is not part
of the first failover path.

Home is the normal Discord voice/music owner. Oracle is a warm standby/recovery
target with constrained egress and must not connect to Discord while home is
running. Follow `docs/recovery/ORACLE-JMUSICBOT-MIGRATION.md` for site setup and
`jmusicbot/README.md`/`SECRETS.md` for deployment/Secret contracts.

## Normal health check

```bash
kubectl -n jmusicbot get deploy,pods,svc,pvc -o wide
kubectl -n jmusicbot describe deployment/jmusicbot
kubectl -n jmusicbot logs deployment/jmusicbot --since=30m --tail=200
kubectl -n jmusicbot get secret jmusicbot-config-txt jmusicbot-r2 -o name
kubectl -n jmusicbot run --rm -i --restart=Never curl-health --image=curlimages/curl -- \
  curl -fsS http://jmusicbot-health.jmusicbot.svc.cluster.local:9091/health
```

The last command is an in-cluster probe and does not prove Discord voice or
R2 sync. Check Discord behavior and R2 generation separately without printing
tokens.

## Scenario SOPs

| Scenario | Detection | Safe diagnosis | Restore / promotion and fencing | Verification and gate |
|---|---|---|---|---|
| Process or pod crash | Pod restart/probe failure, Discord disconnect, health failure | Events, previous logs, image digest, init restore and sidecar logs; do not start Oracle as a second writer | Home: `kubectl -n jmusicbot rollout restart deployment/jmusicbot`; `Recreate` must finish old pod before new start. | Health endpoint, Discord reconnect once, restored files present, and R2 sync succeeds. |
| Home node loss | Home pod/node unavailable, Discord voice lost | Confirm whether the old bot can still reconnect and whether R2 has a fresh generation; do not start Oracle yet | Fence/stop home JMusicBot and verify it cannot reconnect before scaling Oracle. Use the migration sequence, not a parallel deployment. | Exactly one Discord session/voice owner, Oracle health, startup logs, and representative audio/command check. |
| Home outage/WAN loss | Discord bot absent and home management unavailable | Check Oracle management, R2 access, Discord API/egress independently; avoid assuming R2 freshness | Controlled promotion only after home writer/session fencing and current R2 generation are recorded. Oracle egress must be measured. | RTO/RPO, no duplicate voice session, health route, representative audio behavior, and free-tier egress evidence. |
| Oracle outage | Standby pod/node unavailable while home is healthy | Home remains authoritative; verify no Oracle Discord connection | Repair Oracle in zero-replica/standby state and re-run restore validation. | Home uninterrupted; Oracle only returns after it is disconnected from Discord. |
| Split brain/duplicate voice owner | Two Discord sessions, duplicated messages/audio, reconnect loop | Stop both external owners if ownership is uncertain; inspect pod logs and deployment replicas | Fence the old instance, revoke/rotate credentials if necessary, and start exactly one owner. `Recreate` and scaling are not sufficient host fences by themselves. | Discord audit/session check shows one owner; old pod cannot reconnect. |
| R2/state/PVC corruption | Restore init fails, missing settings/token file, checksum mismatch, sidecar sync error | Preserve current local files and R2 object metadata; do not delete the only local copy or overwrite R2 blindly | Restore a selected R2 generation into an isolated directory; verify checksums/ownership, then roll back the image or generation. | `serversettings.json`/token presence by filename only, health, startup, and selected state checksum. |
| Secret/certificate failure | `CreateContainerConfigError`, Discord auth failure, R2 401/403 | Check Secret names/key names and R2 response code without values | Reconstruct `jmusicbot-config-txt` and `jmusicbot-r2` using the documented secret-restore RBAC path; restart only after keys exist. | Health, Discord login, R2 restore/sync, and no secret in logs/issues. |
| Routing/tunnel/health failure | Health Service fails or external observer says down while bot plays | Compare Service endpoints, probe, pod logs, and observer route; health Service is in-cluster | Restore `45-service-health.yaml`/observer configuration; do not expose Discord bot internals publicly. | In-cluster `/health` and external observer both pass, with Discord session still single-owner. |
| Resource/egress exhaustion | OOM/eviction, disk full, R2 backlog, Oracle bandwidth growth, audio buffering | `kubectl top`, PVC/emptyDir usage, sidecar logs, provider usage; do not assume Oracle free egress is unlimited | Pause/limit media, keep home owner if healthy, or stop standby. Do not add simultaneous voice replicas. | Resource headroom, R2 sync current, audio/reconnect behavior, and free-tier measurements attached to #191. |
| Rollback/failback | Promoted Oracle bot fails startup/audio or home returns | Preserve R2 generation, image digest, owner/fence evidence, and Discord state before reversing | Stop/fence Oracle, verify it cannot reconnect, confirm home can restore/catch up, then start home and check its health before any route change. Roll back image with `rollout undo` only after state compatibility review. | Home sole owner, Oracle zero/disconnected, representative audio/command pass, R2 sync current, and issue evidence complete. |

## Controlled promotion

1. Capture home image digest, pod identity, Secret names, newest R2 generation,
   and current Discord session state.
2. Stop/fence home and verify it cannot reconnect or send. If this cannot be
   proven, do not start Oracle.
3. Apply the independent Oracle namespace/service-account/Secret contract and
   `40-deployment-jmusicbot.yaml`; do not apply the release notifier/PVC path
   for the first recovery.
4. Verify R2 restore init, health, logs, Discord login, and representative
   media behavior.
5. Observe Oracle egress and record RTO/RPO before declaring promotion.

## Known gates

- Oracle is constrained by egress; sustained media usage is not assumed free.
- The main bot's R2 recovery proves file retrieval, not complete promotion or
  writer fencing.
- Do not run two Discord voice writers for “active-active.”
