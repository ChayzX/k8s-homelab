# k3s-watcher

This is the tracked source for the host `k3s-watcher` systemd service. It
queries Loki for restart and log-error events, sends Discord DMs, and can
independently mirror actionable alerts into the private Operations inbox.

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

Kubernetes waiting states such as `ImagePullBackOff` do not produce container
logs, so the watcher also polls pod container states and Deployment
availability directly. Image-pull/configuration failures and replica gaps must
persist for five minutes (`WORKLOAD_CONFIRMATION_SECONDS`) before alerting;
this keeps normal merge deploys quiet while still reporting a replacement pod
that cannot start. The JMusicBot dashboard's Pod Status and Ready Containers
panels expose the same Kubernetes state in Grafana.

Keep `pantry-bot` in `WATCH_NAMESPACES` so these generic checks cover the
replacement PantryBot workloads. Do not add the retired monolith Service to
`FUNCTIONAL_HEALTH_URLS`: its Deployment is intentionally scaled to zero, so
the Service has no endpoint and a dedicated health probe can only generate a
false incident. Functional probes remain configured for JMusicBot and Opsbot.

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

## Operations dual delivery

Discord is always attempted first. Operations delivery has a separate bounded
in-memory spool, so a Discord cooldown does not discard a pending inbox event.
Connection errors, timeouts, HTTP 429, and HTTP 5xx responses retry with a
bounded exponential delay and the original event key and occurrence timestamp.
Other HTTP 4xx responses are logged without their response bodies and dropped.
Restart and log fingerprints are normalized and SHA-256 hashed before leaving
the host. The informational watcher-online DM is never ingested.

Configure every `OPERATIONS_*` value shown in `.env.example`, or configure none
of them to retain Discord-only behavior. The ingest key is independent from the
Cloudflare Access service token. Keep the host systemd EnvironmentFile mode
`0600`; never put either credential in the repository, logs, or alert text.

Reconciliation runs only after both the Kubernetes pod collection and Loki
query complete successfully. A partial pass cannot resolve alerts. On process
startup it also waits for the longer of the restart window and log-error
confirmation window before its first reconciliation, allowing active state to
be rebuilt without falsely resolving inbox entries from the prior process.
Reconciliation is skipped rather than truncated if more than the API's 200-key
limit is active. Because the
pending spool is intentionally process-local, restart the service only after
its log shows no pending delivery failures when practical.

Rollout order: deploy and validate the Operations API first, install the
watcher-specific Access service token and ingest key in the host environment,
then restart this service and generate one controlled alert. Confirm both the
Discord DM and one inbox occurrence. To roll back, remove all `OPERATIONS_*`
values and restart the watcher; Discord delivery remains unchanged.

Run the unit tests with:

```sh
PYTHONPATH=. python3 test_watcher.py
```

To re-enable the host watcher after this change is merged and installed, run
the test above from the installed directory, then have an operator run:

```sh
sudo systemctl restart k3s-watcher.service
systemctl is-active k3s-watcher.service
journalctl -u k3s-watcher.service --since '5 minutes ago' --no-pager
```

Confirm the service is active, the startup message still lists the
`pantry-bot` namespace, and no PantryBot functional-health probe appears. A
service restart was not part of this repository change.
