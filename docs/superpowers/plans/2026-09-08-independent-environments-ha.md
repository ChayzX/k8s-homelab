# Independent Environments HA and Recovery Implementation Plan

> **Historical baseline:** This plan covers the recovery foundation that preceded the approved free active-active application design. Continue with [`2026-09-10-free-active-active-homelab.md`](2026-09-10-free-active-active-homelab.md) for the current goal. Minecraft remains excluded from active-active work.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the unsafe WAN-spanning embedded-etcd proposal with independent home/cloud environments and demonstrated recovery, while preserving a path to true automatic HA if suitable local hardware or managed infrastructure is added.

**Architecture:** Keep the home cluster focused on Minecraft and home-dependent services. Establish Oracle as an independent cloud service/recovery environment for internet-facing workloads, use GCP for external observation and coordination, and use chasebot as a modest local worker/recovery target. Do not make Kubernetes consensus depend on a WAN; use versioned encrypted backups, explicit promotion, and writer fencing for cross-site continuity.

**Tech Stack:** k3s, Kubernetes manifests, GitHub Actions, Cloudflare Tunnel, Tailscale, R2/rclone or Litestream, Authentik/Postgres, GitHub Issues.

**Spec:** `HA-EXPANSION.md` and GitHub Issue #191, updated by the independent Astra review recorded in issue comment 5595631092.

## Global Constraints

- GitHub Issues are the only project tracking system; do not use graphify or beads.
- Do not create a WAN-spanning embedded-etcd quorum.
- Do not demote `minecraftmachine`, rebuild the cluster, power off hosts, or alter privileged services without verified backups and an explicit maintenance/test gate.
- Preserve passcode files on chasebot.
- Keep emergency SSH/key access independent of Authentik/LDAP.
- Do not commit or push unless explicitly requested.

---

### Task 1: Rewrite the architecture decision record

**Files:**
- Modify: `HA-EXPANSION.md`
- Modify: `ARCHITECTURE.md` only where current facts directly contradict the new decision

**Interfaces:**
- Consumes: Astra’s independent findings and current live-state evidence.
- Produces: A dated architecture decision, explicit rejected alternatives, service placement, failure contracts, and migration gates.

- [x] **Step 1: Replace the current embedded-etcd target section** with the independent home/cloud design and the conditions for a future local quorum.
- [x] **Step 2: Add a service-placement matrix** covering Minecraft, PantryBot, JMusicBot, Opsbot, Authentik/Postgres, Operations, observability, CI tunnel, and GCP monitoring.
- [x] **Step 3: Add explicit HA versus DR definitions** and list the failure modes that must be tested: host loss, home WAN loss, whole-home power loss, R2 outage, partition with old writer alive, and damaged restore state.
- [x] **Step 4: Correct the NVMe fact** to state that `/mnt/nvme` is already mounted and requires a reversible migration plan rather than destructive reclamation.
- [x] **Step 5: Run `git diff --check` and search for contradictory Oracle+GCP+chasebot promotion language.**

### Task 2: Make recovery and deployment authority explicit

**Files:**
- Modify: `DEPLOYING.md`
- Modify: `jmusicbot/40-deployment-jmusicbot.yaml`
- Modify: `.github/workflows/jmusicbot-deploy.yml`
- Modify: `scripts/auto-update.sh` only after confirming the active cron owner

**Interfaces:**
- Consumes: Existing image workflows and live deployment facts.
- Produces: One documented deployment authority, recoverable image references, serialized rollouts, and rollback instructions.

- [x] **Step 1: Inventory all JMusicBot/Opsbot/Minecraft deployment authorities** without changing them.
- [x] **Step 2: Remove or disable only confirmed-retired duplicate deployment paths**, preserving passcode and recovery material. The JMusicBot updater and tower heartbeat cron entries were retired; Minecraft updater remains home-only.
- [x] **Step 3: Make checked-in manifests reference published, multi-arch images or document the generated image substitution as a required release artifact.**
- [x] **Step 4: Add workflow concurrency and an explicit rollback/health gate where the current workflow lacks them.**
- [x] **Step 5: Validate manifests and workflow syntax without deploying.**

### Task 3: Build a recovery inventory and backup verification gate

**Files:**
- Create: `docs/recovery/RECOVERY-INVENTORY.md`
- Create: `docs/recovery/RESTORE-REHEARSAL.md`
- Modify: `HA-EXPANSION.md`

**Interfaces:**
- Consumes: Live Kubernetes resources, repository manifests, backup object metadata, and GitHub secret inventory metadata.
- Produces: A non-secret inventory of datastore/token, application data, images, credentials, restore destinations, RPO/RTO, and verification commands.

- [x] **Step 1: Inventory namespaces, PVCs, images, Secrets by name, external endpoints, and host timers.**
- [x] **Step 2: Record which data is local-only, replicated, versioned, or unverified.**
- [x] **Step 3: Define isolated restore tests for k3s datastore, Authentik/Postgres, Operations, Minecraft, PantryBot, and JMusicBot.**
- [x] **Step 4: Add backup freshness, retention, integrity, and independent-credential acceptance criteria.**
- [x] **Step 5: Do not copy secret values into tracked files; validate only names, locations, and restoration ownership.**

### Task 4: Verify network and external monitoring prerequisites

**Files:**
- Create: `docs/recovery/NETWORK-VALIDATION.md`
- Create: `docs/recovery/EXTERNAL-MONITORING.md`
- Modify: `HA-EXPANSION.md`

**Interfaces:**
- Consumes: Node addresses, Tailscale state, Cloudflare routes, watcher behavior, and public health endpoints.
- Produces: A repeatable node-pair/API/DNS/MTU test matrix and an external-monitoring design that alerts when the API is unavailable.

- [x] **Step 1: Test every node pair for management, kubelet, pod, DNS, and required application ports.** The matrix was rerun from the tower, chasebot, and Oracle on 2026-09-10. Passing paths and intentional cross-site failures are recorded in `docs/recovery/NETWORK-VALIDATION.md`; ChaseBot's Kubernetes kubelet-proxy gate now passes through the stable LAN path.
- [x] **Step 2: Verify the chasebot-to-Oracle connectivity defect and document the exact route/interface correction required.** The LAN control-plane endpoint, tower API advertisement, and direct egress mode were applied with a root-owned rollback copy; all three kubelet proxy health checks return `ok`.
- [x] **Step 3: Define GCP/Oracle monitoring with independent credentials and notifications.** GCP now runs the public monitor, while UptimeRobot is the intended external public monitor. The GCP monitor also checks the protected external Kubernetes API route (`k8s-api.greeniespantry.uk`, expected unauthenticated HTTP 403); this proves Access/tunnel reachability, not authenticated API health. The retired Healthchecks.io callback is removed. Discord webhook configuration remains optional because no current Kubernetes webhook secret was present.
- [ ] **Step 4: Test human alert receipt with the tower unavailable without powering it off.** Detection was tested on 2026-09-10 by withdrawing both shared Cloudflare connectors; GCP reached the 3/3 degraded transition and recovered after both connectors were restored. Human notification receipt remains open because no Discord webhook is configured.

### Task 5: Implement only after recovery gates pass

**Files:**
- Modify: service-specific manifests and workflows identified by Tasks 2–4
- Modify: GitHub Issue #191 and linked issue comments

**Interfaces:**
- Consumes: Passing recovery, deployment, network, and monitoring gates.
- Produces: Independent cloud services, controlled promotion, measured recovery, and an updated decision record.

- [ ] **Step 1: Move one stateless/internet-facing service to the independent environment using a reversible deployment.**
- [ ] **Step 2: Validate service behavior, backup freshness, routing, and emergency access.**
- [ ] **Step 3: Move stateful services only after isolated restore succeeds.**
- [ ] **Step 4: Rehearse host loss, WAN loss, partition/fencing, R2 failure, and restore from retained generation.**
- [ ] **Step 5: Only then evaluate adding a suitable third LAN server or managed HA platform for automatic HA.**

## Self-review

- The plan covers the architecture rewrite, deployment authority, recovery evidence, network prerequisites, monitoring, and later implementation gates.
- No task authorizes WAN etcd, destructive storage changes, uncontrolled power tests, or secret-value storage.
- Tasks 1–3 and the read-only execution portion of Task 4 are complete. The
  remaining unchecked items are intentionally gated: human notification receipt,
  a dedicated read-only R2 credential, and the subsequent
  independent migration and failure rehearsals. Do not bypass those gates by detaching Oracle or
  starting a second JMusicBot writer.
