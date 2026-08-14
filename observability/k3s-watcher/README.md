# k3s-watcher

This is the tracked source for the host `k3s-watcher` systemd service. It
queries Loki for restart and log-error events and sends Discord DMs.

Cloudflared QUIC teardown messages are suppressed only when the exact
cloudflared stream is matched and the connector's `/ready` endpoint responds
successfully. Readiness failures, DNS errors, dial errors, origin failures,
and generic timeouts remain alertable. Alert keys normalize volatile
`connIndex`, `event`, and `ip` fields and use a 15-minute default cooldown.

The Discord token, user ID, Loki URL, kubeconfig, and systemd unit remain
host-local secrets/configuration. Install this directory on the host and
point `k3s-watcher.service` at `watcher.py`; do not commit `.env` files.

Run the unit tests with:

```sh
PYTHONPATH=. python3 test_watcher.py
```
