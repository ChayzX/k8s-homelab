from pathlib import Path


ROOT = Path(__file__).parent


def uncommented(path: Path) -> str:
    return "\n".join(
        line for line in path.read_text().splitlines() if not line.lstrip().startswith("#")
    )


prometheus = uncommented(ROOT / "prometheus-config.yaml")
loki = uncommented(ROOT / "loki-config.yaml")
grafana = uncommented(ROOT / "grafana-provisioning.yaml")
watcher = (ROOT / "k3s-watcher" / "watcher.py").read_text()
external_monitor = (ROOT / "external-monitor" / "monitor.py").read_text()
contract = (ROOT.parent / "docs" / "recovery" / "ALERT-EVALUATION-CONTRACT.md").read_text()

# Prometheus/Loki collect and retain; they do not independently evaluate
# alerts in the tracked configuration.
assert "rule_files:" not in prometheus
assert "alertmanager" not in prometheus
assert "ruler:" not in loki
assert "alertmanager" not in loki

# Grafana has routing policy and, since ChayzX/pantry-bot#346, provisioned
# PantryBot rule groups (Grafana is the only rule evaluator).
assert "groups:" in grafana
for uid in ("pantry-oracle-target-down", "pantry-oracle-runtime-failures", "pantry-oracle-metrics-absent"):
    assert uid in grafana, uid

# Grafana delivers through opsbot's authenticated relay, not a Discord
# webhook, and the token is only ever an env reference.
assert "type: webhook" in grafana
assert "type: discord" not in grafana
assert "http://opsbot-health.opsbot.svc.cluster.local:9091/alerts/grafana" in grafana
assert "authorization_credentials: $OPSBOT_ALERT_TOKEN" in grafana
assert "receiver: opsbot-dm" in grafana
# Grafana env-interpolates provisioned strings: `$labels` must be `$$labels`.
import re
assert not re.search(r"(?<!\$)\$labels", grafana), "unescaped $labels in alerting provisioning"

# The Grafana Deployment actually mounts the alerting provisioning and has
# the token env (required secretKeyRef).
grafana_deploy = uncommented(ROOT / "grafana.yaml")
assert "mountPath: /etc/grafana/provisioning/alerting" in grafana_deploy
assert "name: grafana-provisioning-alerting" in grafana_deploy
assert "name: OPSBOT_ALERT_TOKEN" in grafana_deploy
assert "name: opsbot-alert-token" in grafana_deploy

# The two actual evaluators expose stable identity/ownership mechanisms.
assert "fcntl.flock" in watcher
assert '"eventKey"' in watcher
assert "def alert_identity" in external_monitor
assert '"active_alerts": active_alerts' in external_monitor

# UptimeRobot is intentionally external to the repository and is named as an
# independent public-edge observer rather than silently treated as a second
# in-cluster evaluator.
assert "UptimeRobot" in contract
assert "GCP `homelab-external-monitor`" in contract

print("test_alert_contract: all assertions passed")
