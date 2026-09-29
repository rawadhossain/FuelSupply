#!/usr/bin/env bash
# Build images, load them into a local kind cluster, install kube-prometheus-stack,
# then install the fuel-platform chart. Idempotent: re-running skips cluster
# creation if it already exists.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLUSTER_NAME="${CLUSTER_NAME:-fuel-platform}"

if ! kind get clusters 2>/dev/null | grep -qx "$CLUSTER_NAME"; then
  echo "Creating kind cluster '$CLUSTER_NAME'..."
  kind create cluster --name "$CLUSTER_NAME" --config "$ROOT/deploy/kind/kind-config.yaml"
else
  echo "kind cluster '$CLUSTER_NAME' already exists, reusing."
fi

echo "Building images..."
docker build -t fuel-platform/core:dev -f "$ROOT/core/Dockerfile" "$ROOT"
docker build -t fuel-platform/intelligence:dev -f "$ROOT/intelligence/Dockerfile" "$ROOT"
docker build -t fuel-platform/frontend:dev -f "$ROOT/frontend/Dockerfile" "$ROOT/frontend"
docker build -t fuel-platform/sim-exporter:dev -f "$ROOT/ops/sim-exporter/Dockerfile" "$ROOT/ops/sim-exporter"

echo "Loading images into kind..."
for img in fuel-platform/core:dev fuel-platform/intelligence:dev fuel-platform/frontend:dev fuel-platform/sim-exporter:dev; do
  kind load docker-image "$img" --name "$CLUSTER_NAME"
done

echo "Installing metrics-server..."
kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml
kubectl patch deployment metrics-server -n kube-system --type=json \
  -p '[{"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--kubelet-insecure-tls"}]' || true

echo "Installing kube-prometheus-stack..."
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts >/dev/null 2>&1 || true
helm repo update >/dev/null
helm upgrade --install kube-prometheus-stack prometheus-community/kube-prometheus-stack \
  -n monitoring --create-namespace \
  -f "$ROOT/deploy/monitoring/kube-prometheus-stack-values.yaml" \
  --wait --timeout 5m

if [ "${LOGS:-0}" = "1" ]; then
  echo "Installing Loki + Alloy (LOGS=1)..."
  helm repo add grafana https://grafana.github.io/helm-charts >/dev/null 2>&1 || true
  helm repo update >/dev/null
  helm upgrade --install loki grafana/loki -n monitoring -f "$ROOT/deploy/monitoring/loki-values.yaml" --wait --timeout 3m || \
    echo "Loki install failed/skipped, continuing without logs."
fi

echo "Installing fuel-platform chart..."
helm upgrade --install fuel-platform "$ROOT/deploy/helm/fuel-platform" \
  -f "$ROOT/deploy/helm/fuel-platform/values-kind.yaml" \
  --atomic --wait --timeout 5m

echo
echo "=== URLs ==="
echo "Frontend:    http://localhost:8080"
echo "Grafana:     http://localhost:3000"
echo "Prometheus:  http://localhost:9090"
