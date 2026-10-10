# Oracle host security changes (issue #413)

## Current live state (2026-10-10 UTC)

Oracle is a single k3s host. SSH administration currently depends on the
MinecraftMachine Tailscale jump (`ssh oracle`); OCI console or another
independent recovery path has not been verified. Do not narrow SSH ingress or
change Tailscale access policy until that path exists and is tested. The k3s
API binds to loopback and Oracle's Tailscale address, not wildcard addresses.

Node-exporter listens on TCP/9100 on all interfaces. Prometheus runs in the
Oracle pod network (`10.42.0.200`) and scrapes the node target through the
Kubernetes Service. The following native nftables table drops port 9100 unless
the IPv4 source belongs to the verified pod CIDR, and drops IPv6 sources other
than loopback. Its `inet` input hook has priority `-10`, before iptables' 0
priority hooks, kube-router's marked accept, and Tailscale's broad
`tailscale0` accept.

The rule and systemd unit are tracked under `ops/oracle/`. Live files:

- `/etc/pantrybot-node-exporter-guard.nft`
- `/usr/local/sbin/pantrybot-node-exporter-guard.sh`
- `/etc/systemd/system/pantrybot-node-exporter-guard.service`

The unit is enabled and active. It owns only the `inet pantrybot_guard`
table; it does not flush or reload K3s or Tailscale's iptables tables. A live
test from MinecraftMachine returned HTTP 200 before the rule and timed out
after. A fresh Prometheus query after the rule returned `up{job="node-oracle"}
= 1`. The service applies its rules as one nftables batch, atomically replacing
only its own table. Stopping the unit intentionally leaves the guard installed
so a service stop cannot silently reopen node-exporter.

PostgreSQL listens on wildcard addresses because the pod and Grafana clients
use different interfaces. The same early input chain now permits TCP/5432 only
from the Oracle pod CIDR (`10.42.0.0/24`) and MinecraftMachine's verified
Tailscale IPv4 address (`100.84.89.87`); other IPv4 sources and non-loopback
IPv6 sources are dropped before kube-router and Tailscale accept rules. The
PostgreSQL HBA remains the second control and still requires the `pantry`
or `pantry_grafana` SCRAM role on their documented sources. The guard is live:
fresh connections from a PantryBot worker and MinecraftMachine succeeded; an
Oracle loopback TCP probe was dropped and incremented the nftables counter.
MinecraftMachine's TCP/5432 connection was verified. Retest the actual Grafana
datasource after any firewall or HBA change.

Install or refresh from a checkout containing these files:

```sh
scp -o BatchMode=yes ops/oracle/pantrybot-node-exporter-guard.nft oracle:/tmp/pantrybot-node-exporter-guard.nft
scp -o BatchMode=yes ops/oracle/pantrybot-node-exporter-guard.sh oracle:/tmp/pantrybot-node-exporter-guard.sh
scp -o BatchMode=yes ops/oracle/pantrybot-node-exporter-guard.service oracle:/tmp/pantrybot-node-exporter-guard.service
ssh -o BatchMode=yes oracle
sudo nft -c -f /tmp/pantrybot-node-exporter-guard.nft
sudo install -o root -g root -m 0644 /tmp/pantrybot-node-exporter-guard.nft /etc/pantrybot-node-exporter-guard.nft
sudo install -o root -g root -m 0755 /tmp/pantrybot-node-exporter-guard.sh /usr/local/sbin/pantrybot-node-exporter-guard.sh
sudo install -o root -g root -m 0644 /tmp/pantrybot-node-exporter-guard.service /etc/systemd/system/pantrybot-node-exporter-guard.service
sudo systemd-analyze verify /etc/systemd/system/pantrybot-node-exporter-guard.service
sudo systemctl daemon-reload
sudo systemctl enable --now pantrybot-node-exporter-guard.service
```

After each change, query Prometheus at `http://localhost:9090/api/v1/query?query=up%7Bjob%3D%22node-oracle%22%7D` from the Oracle Prometheus pod and require value `1`. Probe `http://100.78.181.15:9100/metrics` from MinecraftMachine and require the connection to fail. If the Prometheus target is down, restore only this table with `sudo nft delete table inet pantrybot_guard`, then verify Prometheus returns to `up=1`. To permanently remove the guard, explicitly run `sudo nft delete table inet pantrybot_guard`, then disable the unit and delete its three files; do not flush the global nftables/iptables ruleset.

Package and host changes verified on 2026-10-10:

- `tailscale` advanced from `1.102.3` to stable `1.104.1`; `dnsmasq-base`
  advanced from `2.91-0ubuntu0.22.04.1` to `.2`. A direct public SSH path was
  verified using the same host key as the Tailscale path before the Tailscale
  update. Both direct and normal `ssh oracle` connections now work, and
  `tailscaled`, SSH, the guard, and k3s are active.
- `python3-pip` was removed after confirming it had no runtime consumer or
  dependent packages. It carried the Ubuntu Pro-only fix for CVE-2025-66471.
- `pro security-status` reports no outstanding Ubuntu security updates; the
  host is not attached to Ubuntu Pro. The installed Oracle kernel is
  `6.8.0-1062-oracle`, and apt reports no newer kernel package. Trivy's
  package-only scan still reports kernel/header/tool CVEs without fixed
  versions in its Ubuntu 22.04 feed. Keep this as an open vendor-feed review,
  not as evidence that every finding is exploitable or fixed.
- k3s remains `v1.36.5+k3s1`. At the time of this audit, upstream had released
  `v1.37.1+k3s1` but not a `v1.36.6+k3s1` patch. The 1.37 release is a minor
  upgrade and is outside the patch-only maintenance step; recheck for a 1.36
  patch before the maintenance window.
- K3s Secrets Encryption is enabled and all 23 live SQLite Secret rows carry
  the `k8s:enc:` marker. A root-only encrypted-state recovery copy passed
  SQLite integrity checks; it has not been restored into a disposable K3s
  instance.
- Oracle monitoring image updates are prepared in manifests: Prometheus
  `v3.15.0`, node-exporter `v1.12.0`, Alloy `v1.20.1`, and
  kube-state-metrics `v2.20.0`, each pinned by the multiarch digest. Apply
  these from a merged checkout and verify the Prometheus scrape targets and
  Grafana datasource after rollout.

Keep host SSH, tailnet policy, and API port 6443 restrictions out of this
procedure until an independent OCI recovery route and live route inventory
are verified. The native nft guard is specific to node-exporter and does not
change those paths.
