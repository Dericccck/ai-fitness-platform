SHELL := /bin/sh

AGENT_DIR := fitness-agent-service
UV_CACHE_DIR := $(CURDIR)/.cache/uv
COMPOSE_FILE := deployment/docker-compose.agent-infra.yml

.PHONY: help infra-up infra-down agent-lock agent-sync agent-format agent-check agent-run java-check check

help:
	@echo "Available targets:"
	@echo "  infra-up     Start PostgreSQL/pgvector and Redis"
	@echo "  infra-down   Stop local Agent infrastructure without deleting data"
	@echo "  agent-lock   Resolve and update the Python dependency lock file"
	@echo "  agent-sync   Install exact locked Python dependencies"
	@echo "  agent-format Format Python code"
	@echo "  agent-check  Run Python lint, type checks, and tests"
	@echo "  agent-run    Start the Agent API locally"
	@echo "  java-check   Compile the legacy Java backend"
	@echo "  check        Run Java and Python checks"

infra-up:
	docker compose -f $(COMPOSE_FILE) up -d

infra-down:
	docker compose -f $(COMPOSE_FILE) down

agent-lock:
	cd $(AGENT_DIR) && UV_CACHE_DIR=$(UV_CACHE_DIR) uv lock --python 3.11

agent-sync:
	cd $(AGENT_DIR) && UV_CACHE_DIR=$(UV_CACHE_DIR) uv sync --locked --all-extras --dev

agent-format:
	cd $(AGENT_DIR) && UV_CACHE_DIR=$(UV_CACHE_DIR) uv run ruff format .

agent-check:
	cd $(AGENT_DIR) && UV_CACHE_DIR=$(UV_CACHE_DIR) uv run ruff check .
	cd $(AGENT_DIR) && UV_CACHE_DIR=$(UV_CACHE_DIR) uv run ruff format --check .
	cd $(AGENT_DIR) && UV_CACHE_DIR=$(UV_CACHE_DIR) uv run mypy app
	cd $(AGENT_DIR) && UV_CACHE_DIR=$(UV_CACHE_DIR) uv run pytest

agent-run:
	cd $(AGENT_DIR) && UV_CACHE_DIR=$(UV_CACHE_DIR) uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8090

java-check:
	mvn --batch-mode -DskipTests compile

check: agent-check java-check
