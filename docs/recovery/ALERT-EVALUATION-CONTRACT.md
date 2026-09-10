# Alert evaluation and continuity contract

This is the observability contract for issue #203. A collector or history
store is not an alert evaluator, and an external observer is not evidence that
an in-cluster condition is healthy.

| Signal | Collection/history | Single evaluator | Stable identity / continuity |
|---|---|---|---|
| Workload, node, restart-loop, and Loki log conditions | Prometheus and Loki | Host `k3s-watcher` | Hashed `eventKey`; singleton lock prevents two watcher processes; Operations reconciliation resolves only after a complete collection pass. |
| Public HTTP/API/R2 continuity from outside home | GCP `homelab-external-monitor` | GCP monitor | `external-monitor:<check>` is persisted in `active_alerts`; notifications are emitted only when that per-check identity fires or recovers. |
| Independent public reachability observation | UptimeRobot account configuration (not stored here) | UptimeRobot | Provider-side monitor identity; its notification is independent evidence, not a second source for the watcher’s workload alerts. |
| Dashboards and log exploration | Grafana | None | Grafana provisioning currently contains no alert rules; dashboards are views only. |

Prometheus scrapes and retains metrics; Loki ingests and retains logs. Neither
has `rule_files` or an Alertmanager configuration in this repository. Grafana
has a notification policy for rules that may be created later, but no rules
are provisioned. If alert rules are added, they must name their owning
evaluator and must not reproduce a `k3s-watcher` condition without an explicit
dedupe design.

The GCP monitor and UptimeRobot intentionally remain independent public-edge
observations. They may both detect the same outage, but the GCP monitor's
messages are now per-check and durable across process restarts; they are not
treated as watcher events and must not be copied into the in-cluster alert
stream. A human-notification receipt test is still required before claiming
end-to-end alert continuity.

Continuity limits:

- Prometheus history is bounded to 7 days / 15 GB; Loki is the longer-lived
  log store. This is acceptable history loss, not replicated history.
- The GCP monitor requires three consecutive failed cycles by default before
  an identity fires (`MONITOR_FAILURE_THRESHOLD`). Its JSON state file is the
  durable alert ledger; losing that file can cause one repeat firing.
- A failed monitor process is not silently healthy: service supervision and
  the independent UptimeRobot observation remain outside this process.

Tests must keep this contract executable by checking the GCP identity and
transition behavior, the watcher's stable `eventKey`/reconciliation behavior,
and the absence of Prometheus/Grafana rule evaluators in the tracked config.
