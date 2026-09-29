#!/usr/bin/env bash
set -euo pipefail
echo "=== pods ==="
kubectl get pods -o wide
echo
echo "=== hpa ==="
kubectl get hpa
echo
echo "=== top pods ==="
kubectl top pods || echo "(metrics-server not ready yet)"
