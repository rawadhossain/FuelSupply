#!/usr/bin/env bash
set -euo pipefail
CLUSTER_NAME="${CLUSTER_NAME:-fuel-platform}"
kind delete cluster --name "$CLUSTER_NAME"
