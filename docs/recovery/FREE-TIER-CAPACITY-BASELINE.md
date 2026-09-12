# Free-Tier Capacity Baseline

**Measured:** 2026-09-11 CDT; refreshed after the PantryBot HA overlays, live PostgreSQL standby, and removal of the disposable Oracle rehearsal

This is the initial capacity gate for the free active-active design. It is an observation record, not an authorization to deploy production failover.

## Observed hosts

| Host | CPU | Memory | Disk | Current observation | Gate |
|---|---:|---:|---:|---|---|
| `minecraftmachine` | 16 logical CPUs | 15 GiB total, 8 GiB available | Root 3.4 TiB free; `/mnt/nvme` 188 GiB free | Home control plane and Minecraft host; Minecraft excluded from this project | Do not alter Minecraft placement |
| `pantry-bot-oracle` | 2 vCPU; 2 allocatable k3s CPU | 11,932 MiB total, 10,164 MiB available; k3s currently 4% CPU / 15% memory | 40 GiB free | Independent arm64 Oracle k3s Ready; PantryBot stateless/API/overlay capacity plus the physical PostgreSQL standby Ready | Keep resource limits explicit; recheck with side-effect roles and egress |
| `discordmusicbot` | 2 vCPU | 969 MiB total, 578 MiB available at check | 3.3 GiB free | x86_64 observer host; no swap, k3s, or Docker active; OS Login SSH and passwordless sudo verified | Observer-only; any coordination witness must be lightweight and pass a measured memory/network test |
| `chasebot` | 2 allocatable CPU | 1,807 MiB currently used (54% of node memory); 433m CPU (21%) | Local-path storage only; current PVCs are RWO and node-local | Ready second home k3s node; hosts PantryBot stateless/API/overlay capacity and the live PostgreSQL primary | Use for stateless replicas and the home primary only; do not treat it as an independent site |

## Current home-to-Oracle network observation

From `minecraftmachine` over the existing Tailscale path to
`pantry-bot-oracle.tailc0f3c6.ts.net` (`100.78.181.15`), four ICMP probes
returned 4/4 packets with 0% loss and 126.7–131.3 ms latency (129.1 ms
average, 1.9 ms deviation). This is suitable for asynchronous application
replication and queue processing, but is not evidence for synchronous
cross-site PostgreSQL commits or a stretched k3s control plane.

The same host currently reports 16 logical CPUs, 15,898 MiB RAM with 8,107 MiB
available, 3.4 TiB free on `/`, and 188 GiB free on `/mnt/nvme`.

Current cluster evidence also shows `chasebot` Ready with 2 CPU and 3,342,604
KiB allocatable memory. Kubernetes reports 433m CPU and 1,807 MiB memory in
use on that node. Its local-path RWO storage confirms that it adds compute
capacity inside the home failure domain, not independent state redundancy.

Oracle k3s is also currently a single Ready arm64 control-plane node with 2
allocatable CPU and 11,932 MiB total memory. After deploying the stateless
PantryBot public/private UI, API, and overlay capacity plus the persistent
physical standby, the live host check reports 91m CPU (4%) and 1,835Mi memory
(15%), with 40GiB free on the root filesystem. The standby is streaming with
equal receive/replay LSNs. The old disposable `pantry-bot-db-rehearsal`
namespace was removed after this measurement. The side-effecting gateway,
worker, and dispatcher deployments remain at zero replicas pending
source-fencing proof. This confirms available free-tier application/database-
standby capacity, not automatic cross-site failover.

The home cluster currently reports `chasebot` at 433m CPU and 1,807Mi memory
(21% and 54%) and `minecraftmachine` at 573m CPU and 10,180Mi memory (3% and
82%). The home PantryBot PostgreSQL authority is Ready on `chasebot`; its
non-secret state copy contains 275 users and 1,700 inventory rows, while the
legacy SQLite deployment remains the active writer. These readings are a
point-in-time observation and do not authorize adding workloads to the
Minecraft node or declaring the database redundant.

## GCP evidence boundary

OS Login SSH access to `discordmusicbot` was verified on 2026-09-10 as
`chasepdrsn_gmail_com` using the existing operator key. The host reports 2
vCPUs, 969 MiB RAM, 3.3 GiB free root disk, x86_64, no swap, and no active
k3s or Docker service. Passwordless sudo is available for the monitor service.
This confirms a lightweight observer host, not a database or general-purpose
application site.

This proves host access and observer operation only. It does not verify the
active GCP project, instance region, billing account, free-tier eligibility,
disk allowance, or monthly egress. Those account-level facts still require
Cloud Shell or another authenticated GCP operator environment before relying
on the VM for anything beyond the observer and a measured lightweight
coordination candidate.

## Published free-tier constraints (account eligibility still unverified)

- Google Compute Engine Free Tier currently covers one non-preemptible
  `e2-micro` month in `us-west1`, `us-central1`, or `us-east1`, 30 GB-months
  of standard persistent disk, and 1 GB/month outbound transfer from North
  America to eligible destinations. This is an observer-sized allowance, not
  a safe assumption for PostgreSQL, k3s, or active application capacity.
  Source: <https://docs.cloud.google.com/free/docs/free-cloud-features>.
- Oracle Always Free currently documents the first 3,000 OCPU-hours and 18,000
  GB-hours monthly for Ampere A1, equivalent to 4 total OCPUs and 24 GB of
  memory, plus 200 GB combined block volume. Idle compute may be reclaimed
  when CPU/network (and A1 memory) remain below 20% at the 95th percentile for
  seven days. The current Oracle host measurement fits the practical second
  application-site role, but tenancy limits and reclamation status still need
  account-level verification. Source:
  <https://docs.public.content.oci.oraclecloud.com/iaas/Content/FreeTier/resourceref.htm>.
- Cloudflare R2 Standard currently includes 10 GB-month storage, 1 million
  Class A operations, 10 million Class B operations, and free egress monthly;
  Infrequent Access is not covered by that free tier. Source:
  <https://developers.cloudflare.com/r2/pricing/>.

These published limits do not replace account-level billing, region, quota,
egress, or current-usage checks. No production placement is approved from
published limits alone.

## Initial conclusions

- Oracle has the most spare memory and is the practical second application site, but its 2 vCPU limit requires worker and database resource limits.
- GCP has enough observed headroom for monitoring, but not enough to assume a full database, k3s, or general-purpose coordination workload. Keep it observer-first; a lightweight witness candidate still needs a measured memory/network test and account-level billing/egress verification.
- The home tower has ample storage but is not a second failure domain. Its large disk does not make Minecraft or home-local state highly available.
- The initial design should use PostgreSQL queue/state on the two application sites and only add a third coordination participant after validating GCP memory, disk, and network impact.

## Free-cost gates

Before production reliance, record:

1. Google Cloud project, region, billing, disk, and monthly egress eligibility; host SSH access is now verified, but account-level eligibility is not.
2. Oracle Always Free tenancy/region eligibility, current A1 usage, and idle reclamation status.
3. R2 object bytes, request volume, retention growth, and backup egress.
4. Cloudflare plan and route behavior; paid Load Balancing remains excluded from the baseline.
5. Peak PantryBot CPU/memory/egress on both sites after the first non-production deployment.

The design must stop before a paid service, instance resize, quota increase, or unexpected egress cost. Free-tier eligibility is not inferred from host size alone.

## Access gap

The local key `/home/chase/.ssh/mini-pc` did not authenticate as `chase@192.168.40.200` during the original measurement. Kubernetes now confirms the node is reachable and Ready, but direct SSH identity/key validation remains a management-access follow-up. This is not a reason to change the active architecture; the existing GitHub issue for ChaseBot access/recovery should record the correct username/key path before any direct host migration.
