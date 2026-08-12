SHELL := /bin/sh

AGENT_DIR := fitness-agent-service
UV_CACHE_DIR := $(CURDIR)/.cache/uv
COMPOSE_FILE := deployment/docker-compose.agent-infra.yml

.PHONY: help infra-up infra-up-storage infra-up-security infra-up-ocr infra-down observability-up agent-lock agent-sync agent-migrate agent-format agent-check agent-eval agent-security-check agent-run agent-reindex-worker agent-image knowledge-manifest knowledge-validate ocr-sync ocr-check ocr-run ocr-image gateway-check gateway-run legacy-java-diagnostic check

help:
	@echo "Available targets:"
	@echo "  infra-up     Start PostgreSQL/pgvector and Redis"
	@echo "  infra-up-storage Start PostgreSQL/Redis and optional MinIO object storage"
	@echo "  infra-up-security Start PostgreSQL/Redis and ClamAV security service"
	@echo "  infra-up-ocr  Build/start the independent PaddleOCR service"
	@echo "  infra-down   Stop local Agent infrastructure without deleting data"
	@echo "  observability-up Start the local OpenTelemetry Collector"
	@echo "  agent-lock   Resolve and update the Python dependency lock file"
	@echo "  agent-sync   Install exact locked Python dependencies"
	@echo "  agent-migrate Apply Agent PostgreSQL migrations"
	@echo "  agent-format Format Python code"
	@echo "  agent-check  Run Python lint, type checks, and tests"
	@echo "  agent-eval   Run deterministic RAG quality and permission gates"
	@echo "  agent-run    Start the Agent API locally"
	@echo "  agent-reindex-worker Start the knowledge index rebuild worker locally"
	@echo "  agent-image  Build the production Agent container image"
	@echo "  knowledge-manifest  Generate the local source and SHA-256 manifest"
	@echo "  knowledge-validate  Validate PDF/DOCX parsing and report warnings"
	@echo "  gateway-check Build and test the independent fitness core Gateway"
	@echo "  gateway-run  Start the fitness core Gateway locally"
	@echo "  legacy-java-diagnostic Reproduce the incomplete legacy Java build (expected to fail)"
	@echo "  check        Run Agent and fitness core Gateway quality gates"

infra-up:
	docker compose -f $(COMPOSE_FILE) up -d

infra-up-storage:
	docker compose -f $(COMPOSE_FILE) --profile storage up -d

infra-up-security:
	docker compose -f $(COMPOSE_FILE) --profile security up -d

infra-up-ocr:
	docker compose -f $(COMPOSE_FILE) --profile ocr up -d --build agent-ocr

infra-down:
	docker compose -f $(COMPOSE_FILE) down

observability-up:
	docker compose -f $(COMPOSE_FILE) --profile observability up -d agent-otel-collector

agent-lock:
	cd $(AGENT_DIR) && UV_CACHE_DIR=$(UV_CACHE_DIR) uv lock --python 3.11

agent-sync:
	cd $(AGENT_DIR) && UV_CACHE_DIR=$(UV_CACHE_DIR) uv sync --locked --all-extras --dev

agent-migrate:
	cd $(AGENT_DIR) && UV_CACHE_DIR=$(UV_CACHE_DIR) uv run alembic upgrade head

agent-format:
	cd $(AGENT_DIR) && UV_CACHE_DIR=$(UV_CACHE_DIR) uv run ruff format .

agent-check:
	cd $(AGENT_DIR) && UV_CACHE_DIR=$(UV_CACHE_DIR) uv run ruff check .
	cd $(AGENT_DIR) && UV_CACHE_DIR=$(UV_CACHE_DIR) uv run ruff format --check .
	cd $(AGENT_DIR) && UV_CACHE_DIR=$(UV_CACHE_DIR) uv run mypy app
	cd $(AGENT_DIR) && UV_CACHE_DIR=$(UV_CACHE_DIR) uv run pytest

agent-eval:
	cd $(AGENT_DIR) && UV_CACHE_DIR=$(UV_CACHE_DIR) uv run python -m app.rag.evaluation_cli \
		--cases evals/rag_smoke.json --thresholds evals/rag_thresholds.json

agent-security-check:
	cd $(AGENT_DIR) && UV_CACHE_DIR=$(UV_CACHE_DIR) uv run python -m app.rag.security_cli

ocr-sync:
	cd fitness-ocr-service && UV_CACHE_DIR=$(UV_CACHE_DIR) uv sync --extra dev

ocr-check:
	cd fitness-ocr-service && UV_CACHE_DIR=$(UV_CACHE_DIR) uv run ruff check .
	cd fitness-ocr-service && UV_CACHE_DIR=$(UV_CACHE_DIR) uv run ruff format --check .
	cd fitness-ocr-service && UV_CACHE_DIR=$(UV_CACHE_DIR) uv run mypy app
	cd fitness-ocr-service && UV_CACHE_DIR=$(UV_CACHE_DIR) uv run pytest

ocr-run:
	cd fitness-ocr-service && UV_CACHE_DIR=$(UV_CACHE_DIR) uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8091

ocr-image:
	docker build --platform linux/amd64 --file fitness-ocr-service/Dockerfile --tag fitness-ocr-service:local fitness-ocr-service

agent-run:
	cd $(AGENT_DIR) && UV_CACHE_DIR=$(UV_CACHE_DIR) uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8090

agent-reindex-worker:
	cd $(AGENT_DIR) && UV_CACHE_DIR=$(UV_CACHE_DIR) uv run python -m app.reindex_worker_main

agent-image:
	docker build --file $(AGENT_DIR)/Dockerfile --tag fitness-agent-service:local $(AGENT_DIR)

knowledge-manifest:
	cd $(AGENT_DIR) && UV_CACHE_DIR=$(UV_CACHE_DIR) uv run python scripts/build_knowledge_manifest.py

knowledge-validate:
	cd $(AGENT_DIR) && UV_CACHE_DIR=$(UV_CACHE_DIR) uv run python scripts/validate_knowledge_sources.py

gateway-check:
	./mvnw --batch-mode -f fitness-core-gateway/pom.xml clean test

gateway-run:
	./mvnw --batch-mode -f fitness-core-gateway/pom.xml spring-boot:run

legacy-java-diagnostic:
	./mvnw --batch-mode -DskipTests clean compile

check: agent-check ocr-check gateway-check
