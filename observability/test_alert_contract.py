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
