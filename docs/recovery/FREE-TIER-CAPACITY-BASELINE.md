# Free-Tier Capacity Baseline

**Measured:** 2026-09-13 CDT; refreshed after the PantryBot HA overlays, live PostgreSQL standby, and removal of the disposable Oracle rehearsal

This is the initial capacity gate for the free active-active design. It is an observation record, not an authorization to deploy production failover.

## Observed hosts

| Host | CPU | Memory | Disk | Current observation | Gate |
|---|---:|---:|---:|---|---|
| `minecraftmachine` | 16 logical CPUs | 15 GiB total, 8 GiB available | Root 3.4 TiB free; `/mnt/nvme` 188 GiB free | Home control plane and Minecraft host; Minecraft excluded from this project | Do not alter Minecraft placement |
| `pantry-bot-oracle` | 2 vCPU; 2 allocatable k3s CPU | 12,219,244 KiB allocatable; live usage 470m CPU / 5,013 MiB memory | 33 GiB free | Independent arm64 Oracle k3s Ready; all PantryBot stateless/API/overlay capacity plus the writable PostgreSQL authority Ready | Do not add guaranteed requests: current scheduled requests are 1,900m CPU (95% of allocatable) and 3,500 MiB memory |
| `discordmusicbot` | 2 vCPU | 969 MiB total, 578 MiB available at check | 3.3 GiB free | x86_64 observer host; no swap, k3s, or Docker active; OS Login SSH and passwordless sudo verified | Observer-only; any coordination witness must be lightweight and pass a measured memory/network test |
| `chasebot` | 2 allocatable CPU | 308m CPU / 1,026 MiB memory in use; 3,342,604 KiB allocatable | Local-path storage only; current PVCs are RWO and node-local | Ready second home k3s node; no current PantryBot application or database pods | Available for future bounded stateless capacity, but not an independent failure domain and never the preferred database authority |

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
KiB allocatable memory. Kubernetes reports 308m CPU and 1,026 MiB memory in
use on that node. No PantryBot application or database pod is currently
scheduled there; its local-path RWO storage confirms that it adds compute
capacity inside the home failure domain, not independent state redundancy.

Oracle k3s is also currently a single Ready arm64 control-plane node with 2
allocatable CPU and about 12 GiB total memory. After deploying the stateless
PantryBot public/private UI, API, overlay, worker, gateway, and dispatcher
capacity plus the persistent authority, the live host check reports 470m CPU
(23%) and 5,013 MiB memory (42%), with 33 GiB free on the root filesystem.
Oracle currently carries the writable PantryBot database and all
side-effecting application capacity. This confirms available host headroom,
but scheduled CPU requests are already at 95%; do not add guaranteed Oracle
workloads without a new budget review.

The home cluster currently reports `chasebot` at 308m CPU and 1,026Mi memory
(15% and 30%) and `minecraftmachine` at 752m CPU and 10,980Mi memory (5% and
89%). Home PantryBot application capacity and the return standby are currently
scheduled on `minecraftmachine`; Oracle is the writable database authority.
This preserves the requested home-primary compute placement, but means loss of
`minecraftmachine` requires Oracle application/database failover. These are
point-in-time readings and do not declare the home database redundant.

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
- Oracle Always Free currently documents the first 1,500 OCPU-hours and 9,000
  GB-hours monthly for Ampere A1, equivalent to 2 total OCPUs and 12 GB of
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

- Oracle is the practical second application site and currently has memory headroom, but its 2 OCPU Always Free allowance is nearly full by scheduled CPU requests; worker and database limits are mandatory.
- GCP has enough observed headroom for monitoring, but not enough to assume a full database, k3s, or general-purpose coordination workload. Keep it observer-first; a lightweight witness candidate still needs a measured memory/network test and account-level billing/egress verification.
- The home tower has ample storage but is not a second failure domain. Its large disk does not make Minecraft or home-local state highly available.
- The initial design should use PostgreSQL queue/state on the two application sites and only add a third coordination participant after validating GCP memory, disk, and network impact.

## Free-cost gates

Before production reliance, record:

1. Google Cloud project, region, billing, disk, and monthly egress eligibility; host SSH access is now verified, but account-level eligibility is not.
2. Oracle Always Free tenancy/region eligibility, current A1 usage, and idle reclamation status.
3. R2 object bytes, request volume, retention growth, and backup egress.
4. Cloudflare plan and route behavior; paid Load Balancing remains excluded from the baseline.
5. Peak PantryBot CPU/memory/egress on both sites after the first non-production deployment; the current Oracle point sample is 470m CPU / 5,013Mi memory with 1,900m CPU requested.

The design must stop before a paid service, instance resize, quota increase, or unexpected egress cost. Free-tier eligibility is not inferred from host size alone.

## Access gap

The local key `/home/chase/.ssh/mini-pc` did not authenticate as `chase@192.168.40.200` during the original measurement. Kubernetes now confirms the node is reachable and Ready, but direct SSH identity/key validation remains a management-access follow-up. This is not a reason to change the active architecture; the existing GitHub issue for ChaseBot access/recovery should record the correct username/key path before any direct host migration.
