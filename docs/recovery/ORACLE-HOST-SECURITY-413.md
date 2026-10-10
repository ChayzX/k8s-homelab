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

Package updates applied on 2026-10-10:

- `libnetplan0`, `netplan-generator`, `netplan.io`, and `python3-netplan` advanced from Ubuntu Jammy `0.107.1-3ubuntu0.22.04.4` to `0.107.1-3ubuntu0.22.04.5`.
- `tailscale` remains `1.102.3` with `1.104.1` available; it was not upgraded because SSH recovery currently depends on that same Tailscale path and there is no verified out-of-band console.
- `dnsmasq-base` remains held back by apt; it was not forced through the solver.
- After package changes, a new SSH connection succeeded, systemd-networkd and Tailscale were active, and the k3s node remained `Ready` on `v1.36.5+k3s1`.

Keep host SSH, tailnet policy, and API port 6443 restrictions out of this
procedure until an independent OCI recovery route and live route inventory
are verified. The native nft guard is specific to node-exporter and does not
change those paths.
