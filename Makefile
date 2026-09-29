# Dev/ops shortcuts. Requires docker (with compose v2) and GNU make.
# On Windows run from Git Bash/WSL. `make test` and `make lint` run in Docker,
# so no local Python/Node toolchain is needed.

.DEFAULT_GOAL := help
SERVICE ?=
PYIMG   := python:3.11-slim-bookworm
NODEIMG := node:20-alpine
# MSYS_NO_PATHCONV stops Git Bash on Windows from mangling container paths.
# Runs as the caller's uid so files written into the mount are not root-owned.
DRUN    := MSYS_NO_PATHCONV=1 docker run --rm -u $(shell id -u):$(shell id -g) -e HOME=/tmp            -e PYTHONDONTWRITEBYTECODE=1 -v "$(CURDIR)":/src -w /src

.PHONY: help up down nuke logs ps test lint smoke demo-reset demo-load demo-load-chaos

help: ## List targets
	@grep -E '^[a-z]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  make %-8s %s\n", $$1, $$2}'

up: ## Build images and start the stack (detached)
	@test -f .env || cp .env.example .env
	docker compose up -d --build

down: ## Stop the stack (keeps volumes)
	docker compose down

nuke: ## Stop the stack and DELETE volumes (postgres, grafana data)
	docker compose down -v

logs: ## Follow logs; limit with SERVICE=core
	docker compose logs -f --tail=100 $(SERVICE)

ps: ## Show service status
	docker compose ps

test: ## Backend pytest for core and intelligence, in Docker
	@for svc in core intelligence; do \
	  echo "== pytest $$svc =="; \
	  $(DRUN) $(PYIMG) sh -c "python -m venv /tmp/v && . /tmp/v/bin/activate && cp -r shared /tmp/shared && pip install -q /tmp/shared -r $$svc/requirements.txt pytest && python -m pytest -p no:cacheprovider $$svc -q" || exit 1; \
	done

lint: ## ruff (backend) and eslint (frontend), in Docker
	$(DRUN) $(PYIMG) sh -c "python -m venv /tmp/v && . /tmp/v/bin/activate && pip install -q ruff && ruff check --no-cache core intelligence shared"
	$(DRUN) -w /src/frontend $(NODEIMG) sh -c "npm ci --no-audit --no-fund && npm run lint"

smoke: ## Health-check the running stack (waits up to 120s)
	bash scripts/smoke.sh

demo-reset: ## Reset the simulator for a fresh demo run (reset, clear faults, run)
	curl -sf -X POST http://localhost:$${SIMULATOR_PORT:-8000}/admin/reset >/dev/null
	curl -sf -X POST http://localhost:$${SIMULATOR_PORT:-8000}/admin/faults/clear >/dev/null
	curl -sf -X POST http://localhost:$${SIMULATOR_PORT:-8000}/admin/run >/dev/null
	@echo "Simulator reset and running."

scenario: ## Run a scenario: make scenario S=demand-spike ARGS="--seconds 30"
	python ops/scenario-runner/scenario.py $(S) $(ARGS)

scenarios: ## List available scenarios
	python ops/scenario-runner/scenario.py list

chaos-on: ## Route core's simulator calls through the chaos proxy
	SIMULATOR_BASE_URL=http://chaos-proxy:8000 docker compose up -d --no-deps core
	@echo "core now routes through chaos-proxy. Control it: curl -X POST localhost:$${CHAOS_PROXY_CONTROL_PORT:-8001}/__chaos -d '{\"mode\":\"corrupt_json\"}'"

chaos-off: ## Restore core's direct connection to the simulator
	docker compose up -d --no-deps core
	@echo "core restored to direct simulator connection."

evidence: ## Print files under docs/evidence
	@find docs/evidence -type f 2>/dev/null | sort

dispatch: ## Run the temporary baseline-dispatch stand-in
	python ops/scenario-runner/scenario.py baseline-dispatch $(ARGS)

demo-load: ## Section 17 one-command load-test demo (~2 min, k6 via docker)
	bash scripts/demo-load.sh $(ARGS)

demo-load-chaos: ## Same as demo-load, plus a 15s intelligence outage mid-run
	bash scripts/demo-load.sh --chaos $(ARGS)
