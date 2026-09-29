# Kubernetes/kind targets. Included from the root Makefile via `-include deploy/k8s.mk`.
CHART := deploy/helm/fuel-platform
T ?= smoke
S ?= intelligence

.PHONY: k8s-up k8s-down k8s-status k8s-deploy k8s-lint k8s-load k8s-hpa-watch k8s-kill k8s-scale

k8s-up: ## Create kind cluster, build/load images, install monitoring + chart
	bash scripts/k8s-up.sh

k8s-down: ## Delete the kind cluster
	bash scripts/k8s-down.sh

k8s-status: ## Show pods, hpa, top
	bash scripts/k8s-status.sh

k8s-deploy: ## helm upgrade only (assumes cluster + images already present)
	helm upgrade --install fuel-platform $(CHART) -f $(CHART)/values-kind.yaml --wait --timeout 5m

k8s-lint: ## helm lint + helm template with both values files
	helm lint $(CHART) -f $(CHART)/values-kind.yaml
	helm lint $(CHART) -f $(CHART)/values-eks.yaml
	helm template $(CHART) -f $(CHART)/values-kind.yaml >/dev/null
	helm template $(CHART) -f $(CHART)/values-eks.yaml >/dev/null

k8s-load: ## Run k6 test T=smoke|load|stress|spike against the kind NodePort
	bash scripts/loadtest.sh $(T)

k8s-hpa-watch: ## Watch HPA status
	kubectl get hpa -w

k8s-kill: ## Kill a pod to demo resilience, e.g. make k8s-kill S=intelligence
	kubectl delete pod -l app=$(S)

k8s-scale: ## Scale a deployment, e.g. make k8s-scale S=intelligence N=4
	kubectl scale deployment/$(S) --replicas=$(N)
