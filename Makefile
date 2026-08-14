SHELL := /bin/sh

AGENT_DIR := fitness-agent-service
UV_CACHE_DIR := $(CURDIR)/.cache/uv
COMPOSE_FILE := deployment/docker-compose.agent-infra.yml

.PHONY: help infra-up infra-up-storage infra-up-security infra-up-ocr infra-down observability-up agent-lock agent-sync agent-migrate agent-format agent-check agent-eval agent-session-summary-eval agent-security-check agent-run agent-reindex-worker agent-memory-expiry-worker agent-memory-retention-worker agent-session-summary-worker agent-notification-worker agent-image knowledge-manifest knowledge-validate knowledge-quality-gate knowledge-validate-ocr knowledge-submit-review knowledge-approve-safe knowledge-retire-reference ocr-sync ocr-check ocr-run ocr-image gateway-check gateway-run training-check training-run legacy-java-diagnostic check

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
	@echo "  agent-session-summary-eval Run deterministic session summary security gates"
	@echo "  agent-run    Start the Agent API locally"
	@echo "  agent-reindex-worker Start the knowledge index rebuild worker locally"
	@echo "  agent-memory-expiry-worker Start the Memory candidate expiry worker locally"
	@echo "  agent-memory-retention-worker Start the Memory content retention worker locally"
	@echo "  agent-session-summary-worker Start the short-term session summary cleanup worker locally"
	@echo "  agent-notification-worker Start the in-app notification Outbox worker locally"
	@echo "  agent-image  Build the production Agent container image"
	@echo "  knowledge-manifest  Generate the local source and SHA-256 manifest"
	@echo "  knowledge-validate  Validate PDF/DOCX parsing and report warnings"
	@echo "  knowledge-quality-gate  Check parsed documents and parent/child quality thresholds"
	@echo "  knowledge-validate-ocr  Validate sources through the real OCR endpoint"
	@echo "  gateway-check Build and test the independent fitness core Gateway"
	@echo "  gateway-run  Start the fitness core Gateway locally"
	@echo "  training-check Build and test the structured training service"
	@echo "  training-run  Start the structured training service locally"
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

agent-session-summary-eval:
	cd $(AGENT_DIR) && UV_CACHE_DIR=$(UV_CACHE_DIR) uv run python -m app.session_summary_evaluation \
		--cases evals/session_summary_samples.json --thresholds evals/session_summary_thresholds.json

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

agent-memory-expiry-worker:
	cd $(AGENT_DIR) && UV_CACHE_DIR=$(UV_CACHE_DIR) uv run python -m app.candidate_expiry_worker_main

agent-memory-retention-worker:
	cd $(AGENT_DIR) && UV_CACHE_DIR=$(UV_CACHE_DIR) uv run python -m app.memory_retention_worker_main

agent-session-summary-worker:
	cd $(AGENT_DIR) && UV_CACHE_DIR=$(UV_CACHE_DIR) uv run python -m app.session_summary_worker_main

agent-notification-worker:
	cd $(AGENT_DIR) && UV_CACHE_DIR=$(UV_CACHE_DIR) uv run python -m app.notification_worker_main

agent-image:
	docker build --file $(AGENT_DIR)/Dockerfile --tag fitness-agent-service:local $(AGENT_DIR)

knowledge-manifest:
	cd $(AGENT_DIR) && UV_CACHE_DIR=$(UV_CACHE_DIR) uv run python scripts/build_knowledge_manifest.py

knowledge-validate:
	cd $(AGENT_DIR) && UV_CACHE_DIR=$(UV_CACHE_DIR) uv run python scripts/validate_knowledge_sources.py

knowledge-quality-gate:
	cd $(AGENT_DIR) && UV_CACHE_DIR=$(UV_CACHE_DIR) uv run python scripts/validate_document_quality.py

knowledge-validate-ocr:
	@test -n "$(KNOWLEDGE_OCR_ENDPOINT)" || (echo "请先设置 KNOWLEDGE_OCR_ENDPOINT，例如 http://127.0.0.1:8091/v1/parse"; exit 1)
	cd $(AGENT_DIR) && KNOWLEDGE_OCR_ENDPOINT=$(KNOWLEDGE_OCR_ENDPOINT) KNOWLEDGE_OCR_API_KEY=$(KNOWLEDGE_OCR_API_KEY) UV_CACHE_DIR=$(UV_CACHE_DIR) uv run python scripts/validate_knowledge_sources.py

knowledge-submit-review:
	cd $(AGENT_DIR) && UV_CACHE_DIR=$(UV_CACHE_DIR) uv run python scripts/submit_knowledge_review.py

knowledge-approve-safe:
	cd $(AGENT_DIR) && UV_CACHE_DIR=$(UV_CACHE_DIR) uv run python scripts/approve_knowledge_review.py

knowledge-retire-reference:
	@test -n "$(KNOWLEDGE_FILE_NAME)" || (echo "请设置 KNOWLEDGE_FILE_NAME，例如 Physical_Activity_Guidelines_2nd_edition_Presentation.pdf"; exit 1)
	cd $(AGENT_DIR) && UV_CACHE_DIR=$(UV_CACHE_DIR) uv run python scripts/retire_knowledge_source.py --file-name "$(KNOWLEDGE_FILE_NAME)"

gateway-check:
	./mvnw --batch-mode -f fitness-core-gateway/pom.xml clean test

gateway-run:
	./mvnw --batch-mode -f fitness-core-gateway/pom.xml spring-boot:run

training-check:
	./mvnw --batch-mode -f fitness-training-service/pom.xml -s .mvn/settings.xml -Dmaven.repo.local=.mvn/repository clean test

training-run:
	./mvnw --batch-mode -f fitness-training-service/pom.xml -s .mvn/settings.xml -Dmaven.repo.local=.mvn/repository spring-boot:run

legacy-java-diagnostic:
	./mvnw --batch-mode -DskipTests clean compile

check: agent-check ocr-check gateway-check
