# Kubernetes (written, not yet run)

Everything below was written but never executed in this environment — no docker/kind/
kubectl/helm/terraform/k6 command was run. Treat all of it as unverified until you run
the first-run checklist.

## Three ways to run this platform

1. **docker compose** (existing, unchanged) — `make up`. Not part of this addition.
2. **kind** (local Kubernetes) — `make -f deploy/k8s.mk k8s-up` or `bash scripts/k8s-up.sh`.
   Builds the four first-party images, loads them into a kind cluster, installs
   kube-prometheus-stack, then installs `deploy/helm/fuel-platform` with
   `values-kind.yaml`.
3. **EKS** — written-only. `deploy/terraform/eks` provisions the cluster (never
   applied, no AWS credentials exist in this environment). `.github/workflows/deploy-eks.yml`
   is `workflow_dispatch`-only and deploys via OIDC, no static keys.

## What is verified vs written-only

- **Written, not run:** the Helm chart, kind config, k8s-up/down/status scripts, k6
  scripts, `loadtest.sh`/`report.py`, `deploy/k8s.mk`, both GitHub workflows, and all
  Terraform. `scripts/sync-k8s-assets.sh` was the one thing actually run (a plain file
  copy, not a cluster operation) — it populated `deploy/helm/fuel-platform/files/`.
- **Not run:** `helm lint`, `helm template`, `kubeconform`, `terraform validate`, any
  `docker build`/`kind`/`kubectl`/`helm install`, any k6 test.

## First-run checklist

Run these, in order, before trusting any of the above:

```bash
# 1. Lint and template the chart
helm lint deploy/helm/fuel-platform -f deploy/helm/fuel-platform/values-kind.yaml
helm lint deploy/helm/fuel-platform -f deploy/helm/fuel-platform/values-eks.yaml
helm template deploy/helm/fuel-platform -f deploy/helm/fuel-platform/values-kind.yaml > /tmp/kind.yaml

# 2. Validate the rendered manifests
docker run --rm -v /tmp:/tmp ghcr.io/yannh/kubeconform:latest -summary -ignore-missing-schemas /tmp/kind.yaml

# 3. Bring up a real local cluster
make -f deploy/k8s.mk k8s-up
```

Also worth doing once: `terraform -chdir=deploy/terraform/eks init -backend=false && terraform -chdir=deploy/terraform/eks validate`.

## Demo steps (k8s)

Terminal 1:
```bash
make -f deploy/k8s.mk k8s-load T=load
```
Terminal 2:
```bash
make -f deploy/k8s.mk k8s-hpa-watch
```
Resilience beat (any terminal):
```bash
make -f deploy/k8s.mk k8s-kill S=intelligence
```
Watch the killed pod get rescheduled and the HPA react to load in terminal 2, while
terminal 1's k6 run keeps hitting the service.
