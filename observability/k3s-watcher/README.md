# k3s-watcher

This is the tracked source for the host `k3s-watcher` systemd service. It
queries Loki for restart and log-error events and sends Discord DMs.

Cloudflared QUIC teardown messages are suppressed only when the exact
cloudflared stream is matched and the connector's `/ready` endpoint responds
successfully. Readiness failures, DNS errors, dial errors, origin failures,
and generic timeouts remain alertable. Alert keys normalize volatile
`connIndex`, `event`, and `ip` fields. A fingerprint must recur continuously
for five minutes before its first alert, and then uses a 15-minute cooldown;
the pending incident resets after 90 seconds without a matching line.
For the host service, set `CLOUDFLARED_READY_URL` to the current Cloudflared
Service ClusterIP because host processes cannot resolve cluster DNS; the
source default is suitable for an in-cluster run. During a detected Deployment
rollout, expected startup/origin-refused errors
are suppressed for 180 seconds after the rollout completes as well as while
it is active; set `ROLLOUT_POST_SUPPRESSION_SECONDS` to tune that grace period.

The Discord token, user ID, Loki URL, kubeconfig, and systemd unit remain
host-local secrets/configuration. Install this directory on the host and
point `k3s-watcher.service` at `watcher.py`; do not commit `.env` files.

Run the unit tests with:

```sh
PYTHONPATH=. python3 test_watcher.py
```
