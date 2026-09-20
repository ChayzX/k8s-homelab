# External Monitoring Design

Run monitoring outside `minecraftmachine`, preferably on Oracle or GCP. It must continue when the home cluster, CoreDNS, Authentik, local Grafana, and host watcher are unavailable. The first implementation is on the GCP VM.

## Signals

- Public HTTPS application health, including application-level response checks.
- Kubernetes API route availability, including an explicit API-unavailable alert branch.
- Node and workload health when the API is available.
- R2 backup age, upload failures, object checksum, and restore-test age.
- Heartbeat from the home watcher and backup jobs.
- Cloudflare Tunnel connector presence versus origin health.

## Current implementation evidence

GCP runs `homelab-external-monitor.service` as an unprivileged system user.
The monitor checks `status.greeniespantry.uk`, `grafana.greeniespantry.uk`,
`commands.greeniespantry.uk`, `mods.greeniespantry.uk`, `overlay.greeniespantry.uk`,
`oauth.greeniespantry.uk`, and `auth.greeniespantry.uk`. The service is active
and its persisted state is authoritative for the current public result. At
2026-09-11T06:07 CDT, GCP recorded `status=404` as an accepted status,
`grafana=200`, `authentik=200`, and `kubernetes-api=403` as healthy, while
`commands=502`, `mods=502`, and `oauth=502` were failing; the overall state was
`degraded` after 81 consecutive failure cycles. This is a Cloudflare
route/origin gate, not evidence that the home or Oracle PantryBot Deployments
are unready. Earlier
`HEARTBEAT_OK` entries came from the now-retired Healthchecks.io integration;
the public checks use no Kubernetes or Authentik credentials.

The CI tunnel exposes `https://k8s-api.greeniespantry.uk/version`; an
unauthenticated external probe returns HTTP 403 from Cloudflare Access. The
monitor can record this protected route as healthy with
`MONITOR_API_EXPECTED_STATUS=403`. This proves the external Access edge and
tunnel route are reachable, but not authenticated Kubernetes API health; the
GitHub Actions service token is intentionally not installed on the monitor VM.

The same GCP VM hosts the private failover witness on localhost. It is not
part of the monitor's health decision and is reached by the home site through
an outbound SSH tunnel, keeping coordination private without opening another
public application port.

The monitor is configured for Discord bot-DM delivery through
`MONITOR_DISCORD_BOT_TOKEN` and `MONITOR_DISCORD_USER_ID`. The webhook delivery
through `MONITOR_DISCORD_WEBHOOK` remains an optional fallback. The retired
Healthchecks.io heartbeat is not configured; UptimeRobot is the intended
external public monitoring service. The monitor persists an `active_alerts` map
with stable `external-monitor:<check>` identities, so a repeated failure or
process restart does not create a second notification for the same check; a
newly failed check or a recovered check gets its own transition.

On 2026-09-20, the repository's dependency-free
`probe-notification-receipt.py` was installed on GCP with SHA-256
`3382cceabcf6c630ccb0d4efdaabd4a26dc14e016954ca6c6dab2df9a6cca59`. The probe
records only non-sensitive delivery metadata. A controlled firing and recovery
rehearsal through the configured bot-DM transport returned `accepted=true` for
both transitions. This closes the direct provider-receipt gate; independent
human acknowledgement remains a separate operational check.

On 2026-09-10, the monitor independently observed a short public-edge
degradation: Grafana failed on three consecutive 60-second checks, with the
other public checks failing on the first two cycles. The monitor transitioned
`healthy` to `degraded` at the configured 3/3 threshold and then transitioned
back to `healthy` on the next cycle. The final persisted state had all public
checks passing, including Grafana HTTP 200, and the protected Kubernetes API
route returning its expected HTTP 403. This is evidence that failure
thresholding and recovery work; it is not evidence of human alert receipt or
of the underlying transient cause.

## Controlled outage evidence

On 2026-09-10, both Cloudflare connector Deployments were temporarily scaled
to zero without powering off a host. This was necessary because the CI and
PantryBot connector Deployments currently use the same underlying Cloudflare
Tunnel ID; stopping only PantryBot's two replicas left the CI connector serving
the public routes.

With both connectors absent, the GCP monitor observed HTTP 530 responses and
logged:

```text
CHECK_FAILURE count=1/3 failed=authentik,grafana,oauth,status
CHECK_FAILURE count=2/3 failed=authentik,grafana,oauth,status
CHECK_FAILURE count=3/3 failed=authentik,grafana,oauth,status
ALERT Alert `external-monitor:authentik` firing; failed checks: authentik,grafana,oauth,status
ALERT Alert `external-monitor:grafana` firing; failed checks: authentik,grafana,oauth,status
ALERT Alert `external-monitor:oauth` firing; failed checks: authentik,grafana,oauth,status
ALERT Alert `external-monitor:status` firing; failed checks: authentik,grafana,oauth,status
```

Both connector Deployments were restored to PantryBot=2 and CI=1, rollout
status passed, and Grafana returned HTTP 302. This proves external failure
detection and the monitor's degraded transition. It does **not** prove receipt
by a human notification channel: bot-DM delivery was configured and receipt was
tested later, on 2026-09-20. That later rehearsal does not retroactively prove
that the 2026-09-10 outage reached a human.

The monitor now contains an optional dependency-free R2 ListObjectsV2
freshness check. Its AWS Signature V4 implementation was verified read-only
against the existing Operations recovery prefix from the tower. It is deployed
on GCP but not enabled there yet: the current Kubernetes R2 credential is a
workload credential, not a proven read-only monitoring credential. Provisioning
a scoped read-only R2 credential and installing it in the root-owned GCP
environment file remains an explicit security gate.

## Constraints

- Use independent credentials and direct notification delivery.
- Do not require Authentik/LDAP to read alerts or SSH for recovery.
- Do not treat a connected tunnel as proof that its origin is healthy.
- Alert on missing data and monitor failure, not only negative health results.
- Keep direct notification receipt as an open gate until a webhook or provider
  notification is observed end to end.
