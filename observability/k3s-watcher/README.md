# k3s-watcher

This is the tracked source for the host `k3s-watcher` systemd service. It
queries Loki for restart and log-error events, checks Kubernetes workload and
configured application health, and sends Discord DMs. Kubernetes waiting states such as
`ImagePullBackOff` do not produce container logs, so the watcher also polls
pod container states and Deployment availability directly.

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

Image-pull/configuration failures and Deployment replica gaps must persist for
five minutes (`WORKLOAD_CONFIRMATION_SECONDS`) before an alert is sent. This
keeps normal merge deploys quiet while still notifying when a replacement pod
cannot start. The JMusicBot dashboard's **Pod Status** and **Ready Containers**
panels expose the same Kubernetes state in Grafana; Loki remains the
application-log view only.

Application health endpoints can catch failures that leave a pod's basic
readiness probe green. Configure the host service with a comma-separated
`namespace=url` list, for example:

```ini
FUNCTIONAL_HEALTH_URLS=pantry-bot=http://10.43.170.195/health
```

Each endpoint must return HTTP 200 when the application's important
dependencies are usable. A non-200 response or connection failure must remain
present for five minutes before the watcher sends a critical alert; recovery
is also reported. Use the Service ClusterIP for host-side checks because the
host cannot resolve cluster-local DNS names.

Node health is checked cluster-wide (not per-namespace): if any node's
`Ready` condition is false for `WORKLOAD_CONFIRMATION_SECONDS` (default 5
minutes, shared with the workload checks above), it alerts. This is separate
from and complementary to the pod/Deployment checks — those catch a
workload failing wherever it's scheduled, but say nothing if the node itself
(e.g. the Oracle failover node, `chayzx/pantry-bot-infra`) drops off the
cluster before its pods are evicted. Node alerts always go to the primary
`DISCORD_USER_ID` only, never `EXTRA_ALERT_RECIPIENTS` — a node outage isn't
namespace-scoped the way a workload failure is.

The Discord token, user ID, Loki URL, kubeconfig, and systemd unit remain
host-local secrets/configuration. Install this directory on the host and
point `k3s-watcher.service` at `watcher.py`; do not commit `.env` files.

Run the unit tests with:

```sh
PYTHONPATH=. python3 test_watcher.py
```
