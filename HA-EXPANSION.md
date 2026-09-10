# Homelab Availability and Recovery Architecture

**Status: architecture decision revised 2026-09-08.** The previous proposal to create one k3s embedded-etcd cluster spanning home and cloud is superseded. The current hardware and network do not justify WAN-spanning consensus. This plan separates home and cloud environments, improves recovery, and leaves a path to automatic HA if suitable local hardware or a properly redundant cloud platform is added.

## Decision summary

The recommended architecture is **independent environments with tested recovery**:

- **Home k3s:** keep Minecraft and home-dependent services on `minecraftmachine`; use `chasebot` as a modest local worker and prepared recovery target.
- **Oracle environment:** run independent cloud-side internet-facing services primarily on `pantry-bot-oracle`, after validating capacity and restoring each service in isolation.
- **GCP:** use `discordmusicbot` for external monitoring and recovery coordination. Its current 2 vCPU, 969 MiB RAM, no swap, and 10 GiB disk profile are not suitable for an etcd server.
- **Between environments:** use versioned, encrypted backups and controlled promotion. Standby writers must remain stopped until promotion proves the old writer cannot continue.

This provides site-level fault isolation and disaster recovery. It does **not** provide automatic failover for every host failure. Automatic HA requires either a suitable three-server LAN quorum or a genuinely redundant cloud platform.

## Why the former HA plan is rejected

Do not build either of these WAN-spanning embedded-etcd designs:

- `minecraftmachine + chasebot + pantry-bot-oracle`
- `chasebot + pantry-bot-oracle + upgraded GCP`

The independent review found approximately 127 ms home-to-Oracle RTT and an existing chasebot-to-Oracle reachability defect. All nodes being `Ready` only proves communication with the current control plane; it does not prove every node pair, pod network, DNS path, or application port works.

K3s documents distributed agents, but its distributed-cluster guidance does not establish WAN-spanning embedded-etcd servers as a supported dependable design. Etcd consensus across regions is technically possible but adds latency, bandwidth, partition, election, and operational risk. See [K3s distributed networking](https://docs.k3s.io/networking/distributed-multicloud) and [etcd FAQ](https://etcd.io/docs/v3.6/faq/).

A tiny GCP “witness” is also rejected: an etcd voting member is a full consensus participant, not a lightweight tie-breaker. Two embedded-etcd members are not an HA quorum. An external database can support a two-server k3s design only if the database itself is genuinely redundant; placing it on one existing host merely moves the single point of failure.

## Current verified state

| System | Current role | Relevant facts | Architectural use |
|---|---|---|---|
| `minecraftmachine` | k3s server and current home cluster | 16 logical CPUs, ~15.5 GiB RAM, HDD-backed k3s, WiFi reservation, `/mnt/nvme` already mounted with ~188 GiB available | Home cluster, Minecraft, local services; storage migration is a separate reversible project |
| `chasebot` | k3s agent | 2 CPUs, 3.3 GiB RAM, wired Ethernet, 14.9 GiB SSD, 2.7 GiB free after cleanup | Modest local workloads and prepared recovery target; not ready for etcd until storage/network gates pass |
| `pantry-bot-oracle` | independent single-server k3s standby environment | 2 CPUs, ~11.7 GiB RAM, ~40 GiB free observed, ARM64; Oracle egress is treated as constrained | Recovery/standby target only; home remains primary for high-egress writers |
| `discordmusicbot` | GCP VM, not in k3s | e2-micro, 969 MiB RAM, no swap, 10 GiB disk, ~3.4 GiB free | External observer and recovery coordinator only |

Current cluster health is not the same as architecture readiness. The three k3s nodes are Ready, but the full node-to-node and pod-to-pod network matrix is not proven.

## Service placement

| Service | Primary environment | Recovery posture | Remaining gate |
|---|---|---|---|
| Minecraft | Home | Offsite world plus sanitized configuration backup; isolated Paper/Geyser startup verified | Secret reconstruction and controlled promotion |
| PantryBot | Home or Oracle, one active writer | R2 restore-on-start exists | Writer fencing, generations, restore test |
| JMusicBot | Home primary; Oracle standby with zero active replicas | R2 file mirror exists and Oracle restore check passed | Explicit promotion only after home writer fencing; Oracle is not primary because of egress constraints; see [`ORACLE-JMUSICBOT-MIGRATION.md`](docs/recovery/ORACLE-JMUSICBOT-MIGRATION.md) |
| Opsbot | Oracle candidate | Stateless, multi-arch image | Scoped access to both environments and duplicate-work behavior test |
| Authentik + Postgres | Keep together initially | Database backup and isolated Authentik readiness restore verified | Non-production login and promotion procedure |
| Operations | Follows Authentik only after recovery proof | Currently tower-local | Emergency access independent of LDAP and promotion procedure |
| Observability | Home initially; external monitoring on GCP/Oracle | Grafana Cloud receives logs; GCP public and protected API-route checks are deployed; UptimeRobot owns external reachability | Authenticated API health, read-only R2 credential, and human delivery test |
| CI tunnel | Home initially | Treat as unavailable during home outage | Independent deployment/recovery path |

Do not create multiple bot replicas merely for appearance. Any service with external side effects needs duplicate-work prevention and a single active writer or explicit coordination.

## Availability contracts

Automatic HA means a specified failure is detected and recovered from without operator action, with a measured interruption and data-loss window. Restoring an R2 snapshot, rebuilding a server, or manually promoting a standby is disaster recovery. Automated restoration can become HA only after failure detection, fencing, data freshness, capacity, and routing are proven together.

Required failure tests:

1. Home host loss while home networking remains available.
2. Home WAN loss while the house remains powered.
3. Whole-home power loss.
4. Network partition where the old bot writer still has external connectivity.
5. R2/object-storage outage.
6. Damaged or incomplete restore generation.
7. Oracle host loss and Oracle replacement/recovery.

Do not use the 20-second Kubernetes toleration as a recovery-time guarantee. It excludes detection, scheduling, image pulls, restoration, startup, routing, and application readiness.

## Recovery requirements before migration

The following are mandatory before moving stateful services or changing server roles:

- Verify the actual k3s datastore and create a recoverable datastore snapshot plus matching server-token backup.
- Inventory all PVCs, images, manifests, external endpoints, host timers, and Secret names without putting secret values in Git.
- Prove isolated restore for k3s, Authentik/Postgres, Operations, Minecraft, PantryBot, and JMusicBot.
- Use retained, versioned backup generations and independent backup credentials so live deletion cannot erase every recovery copy.
- Add backup-age, upload-error, object-integrity, and restore-success monitoring.
- Ensure checked-in manifests use recoverable published images. JMusicBot now references its published GHCR image directly; future image substitutions must remain pinned and reproducible.
- Reconcile or retire legacy daily image-updater cron paths. There must be one authoritative deployment path per service.
- Add workflow concurrency and explicit health/rollback gates before depending on image portability.
- Keep emergency SSH/key access independent of Authentik/LDAP.

## Network and ingress requirements

Before any multi-environment service move, validate:

- Every node pair’s management, kubelet, pod, DNS, MTU, and required application paths.
- Chasebot-to-Oracle connectivity, including the missing Tailscale route/interface currently reported by the independent review.
- Cloudflare Tunnel connector health versus actual origin health; connector replicas alone do not guarantee application availability.
- CoreDNS, Authentik, Operations, CI tunnel, and watcher dependencies on `minecraftmachine`.
- Separate Pod/Service CIDRs if both environments are connected through the same tailnet.
- Public DNS/TLS behavior when home is unavailable.

GCP or Oracle monitoring must alert on public application failure, Kubernetes API unavailability, backup age, replication failures, and observer loss. The current GCP implementation proves public-path failure detection and recovery and checks the protected Kubernetes API route through Cloudflare Access; it does not authenticate to Kubernetes. Optional R2 freshness logic is implemented but disabled pending a dedicated read-only credential, and human notification receipt remains a gate. Authentik must not be a prerequisite for emergency access or monitoring.

## Storage note

The earlier assumption that the tower’s NVMe was only an unusable installer image is incorrect according to the independent review: `/mnt/nvme` is already mounted as ext4 with approximately 188 GiB available. It is currently used for logs and other host data. A future storage migration may use it, but service dependencies, mount ordering, rollback, and data integrity must be planned first. No repartitioning, erasure, or destructive reclamation is authorized by this document.

## Implementation phases

1. **Document and inventory:** this decision record, recovery inventory, network matrix, service/data ownership, and GitHub Issue #191 are the source of truth.
2. **Make deployment reproducible:** remove competing deployment authorities, publish recoverable multi-arch images, serialize rollouts, and document rollback.
3. **Make recovery real:** back up datastore/token and application state, retain generations, and complete isolated restore rehearsals.
4. **Make monitoring independent:** deploy external API/application/backup monitoring and test alert delivery without the tower.
5. **Validate networks:** correct chasebot-to-Oracle reachability and verify all required paths without assuming Kubernetes `Ready` is sufficient.
6. **Migrate one stateless service:** move one cloud-suitable service to the independent Oracle environment, validate routing, backups, emergency access, and duplicate-work behavior.
7. **Migrate stateful services selectively:** only after isolated restore passes and old-writer fencing is defined.
8. **Evaluate automatic HA:** add a suitable third LAN server for local quorum, or provision a properly redundant cloud platform. Do not stretch embedded etcd across WAN links.
9. **Rehearse distinct failures:** measure recovery time, data loss, routing behavior, and rollback for each failure contract.

## Tracking and continuation

- Primary issue: [#191 — independent home/cloud availability and recovery gates](https://github.com/ChayzX/k8s-homelab/issues/191), now updated with the independent review and superseding recommendation.
- Secret inventory/recovery work: [#192](https://github.com/ChayzX/k8s-homelab/issues/192).
- Related existing work to reconcile: [#40](https://github.com/ChayzX/k8s-homelab/issues/40), [#25](https://github.com/ChayzX/k8s-homelab/issues/25), and [#121](https://github.com/ChayzX/k8s-homelab/issues/121).
- Detailed implementation plan: `docs/superpowers/plans/2026-09-08-independent-environments-ha.md`.

No control-plane migration, host power test, destructive storage operation, or secret-value change is authorized by this document.
