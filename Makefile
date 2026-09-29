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

.PHONY: help up down nuke logs ps test lint smoke demo-reset

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
