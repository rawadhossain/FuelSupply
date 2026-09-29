#!/usr/bin/env bash
# Copies ops/ Prometheus rules and Grafana dashboards into the Helm chart's
# files/ folders so they can be rendered as PrometheusRule/ConfigMap resources.
# Plain file copy only — no cluster access, no templating.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CHART="$ROOT/deploy/helm/fuel-platform"

mkdir -p "$CHART/files/rules" "$CHART/files/dashboards"

cp "$ROOT/ops/prometheus/alerts.yml" "$CHART/files/rules/alerts.yml"
cp "$ROOT/ops/prometheus/recording.yml" "$CHART/files/rules/recording.yml"

cp "$ROOT/ops/grafana/provisioning/dashboards/"*.json "$CHART/files/dashboards/"

# Datasource uid "prometheus" matches kube-prometheus-stack's default Prometheus
# datasource uid. If dashboards reference a Loki datasource uid for log panels,
# that uid must also be provisioned (see deploy/monitoring/loki-values.yaml,
# LOGS=1) or those panels will show "datasource not found".
echo "Synced rules and dashboards into $CHART/files/"
