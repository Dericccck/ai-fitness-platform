SHELL := /bin/sh

.PHONY: agent-jwks-check agent-proactive-worker agent-proactive-reliability-check agent-proactive-reliability-live-check agent-postgres-backup-restore-check agent-recovery-check agent-rate-limit-load-check agent-capacity-check agent-release-rollback-check agent-migration-check agent-proactive-live-check gateway-training-proactive-preflight gateway-training-proactive-live-check customer-service-check customer-service-run agent-customer-service-preflight agent-customer-service-live-check agent-customer-service-write-live-check gateway-customer-service-role-live-check release-check

AGENT_DIR := fitness-agent-service
UV_CACHE_DIR := $(CURDIR)/.cache/uv
COMPOSE_FILE := deployment/docker-compose.agent-infra.yml

.PHONY: help infra-up infra-up-storage infra-up-security infra-up-ocr infra-up-messaging infra-down observability-up agent-lock agent-sync agent-migrate agent-format agent-check agent-eval agent-operations-eval agent-operations-comparison-eval agent-operations-policy-eval agent-session-summary-eval agent-security-check agent-run agent-dev-context agent-business-live-preflight agent-operations-live-preflight agent-operations-live-check agent-booking-live-check agent-fitness-live-check agent-customer-service-preflight agent-customer-service-live-check agent-customer-service-write-live-check gateway-customer-service-role-live-check agent-reindex-worker agent-memory-expiry-worker agent-memory-retention-worker agent-session-summary-worker agent-notification-worker agent-proactive-reliability-check agent-proactive-reliability-live-check agent-postgres-backup-restore-check agent-recovery-check agent-rate-limit-load-check agent-capacity-check agent-release-rollback-check agent-image knowledge-manifest knowledge-validate knowledge-quality-gate knowledge-validate-ocr knowledge-submit-review knowledge-approve-safe knowledge-retire-reference ocr-sync ocr-check ocr-run ocr-image gateway-check gateway-run gateway-training-role-live-check gateway-training-write-live-check gateway-training-workflow-live-check gateway-training-proactive-preflight gateway-training-proactive-live-check training-check training-run training-role-live-check training-role-visibility-live-check booking-check booking-it booking-run customer-service-check customer-service-run legacy-java-diagnostic release-check check

help:
	@echo "Available targets:"
	@echo "  infra-up     Start PostgreSQL/pgvector and Redis"
	@echo "  infra-up-storage Start PostgreSQL/Redis and optional MinIO object storage"
	@echo "  infra-up-security Start PostgreSQL/Redis and ClamAV security service"
	@echo "  infra-up-ocr  Build/start the independent PaddleOCR service"
	@echo "  infra-up-messaging Start local RabbitMQ for cross-service Outbox events"
	@echo "  infra-down   Stop local Agent infrastructure without deleting data"
	@echo "  observability-up Start the local OpenTelemetry Collector"
	@echo "  agent-lock   Resolve and update the Python dependency lock file"
	@echo "  agent-sync   Install exact locked Python dependencies"
	@echo "  agent-migrate Apply Agent PostgreSQL migrations"
	@echo "  agent-format Format Python code"
	@echo "  agent-check  Run Python lint, type checks, and tests"
	@echo "  agent-eval   Run deterministic RAG quality and permission gates"
	@echo "  agent-operations-eval Run deterministic Operations trend explanation gates"
	@echo "  agent-operations-comparison-eval Run deterministic Operations comparison gates"
	@echo "  agent-operations-policy-eval Run deterministic Operations query policy gates"
	@echo "  agent-operations-live-preflight Check Agent/Gateway readiness before real smoke test"
	@echo "  agent-operations-live-check Run the opt-in real DeepSeek/Java Gateway Operations smoke check"
	@echo "  agent-business-live-preflight Check Agent/Gateway readiness before Booking/Fitness live checks"
	@echo "  agent-booking-live-check Check Booking confirmation flow; --execute enables real appointment write"
	@echo "  agent-fitness-live-check Check Fitness draft confirmation flow; --execute enables real draft write"
	@echo "  agent-customer-service-preflight Check Agent/Gateway/Customer Service readiness without writes"
	@echo "  agent-customer-service-live-check Check customer ticket confirmation flow; always rejects and does not write"
	@echo "  agent-customer-service-write-live-check Run opt-in customer ticket write acceptance with exact cleanup"
	@echo "  gateway-customer-service-role-live-check Check customer ticket read permissions for admin/coach/student"
	@echo "  agent-dev-context Sign a 5-minute local-only organization admin AgentContext"
	@echo "  agent-session-summary-eval Run deterministic session summary security gates"
	@echo "  agent-jwks-check Verify a real authentication service JWKS URL and kid"
	@echo "  agent-run    Start the Agent API locally"
	@echo "  agent-reindex-worker Start the knowledge index rebuild worker locally"
	@echo "  agent-memory-expiry-worker Start the Memory candidate expiry worker locally"
	@echo "  agent-memory-retention-worker Start the Memory content retention worker locally"
	@echo "  agent-session-summary-worker Start the short-term session summary cleanup worker locally"
	@echo "  agent-notification-worker Start the in-app notification Outbox worker locally"
	@echo "  agent-proactive-worker Start the RabbitMQ proactive event worker locally"
	@echo "  agent-proactive-reliability-check Verify proactive message deduplication, retry and recovery boundaries"
	@echo "  agent-proactive-reliability-live-check Verify real RabbitMQ duplicate delivery and Worker restart recovery"
	@echo "  agent-postgres-backup-restore-check Verify Agent PostgreSQL logical backup and temporary database restore"
	@echo "  agent-recovery-check Check Agent, Redis, Checkpoint and messaging recovery after restart"
	@echo "  agent-rate-limit-load-check Verify concurrent Redis rate limiting without business writes"
	@echo "  agent-capacity-check Verify Agent HTTP capacity baseline without business writes"
	@echo "  agent-release-rollback-check Verify Agent version, liveness and readiness after release/rollback"
	@echo "  agent-migration-check Verify Alembic migration chain and bidirectional contract"
	@echo "  agent-proactive-live-check Verify Booking -> RabbitMQ -> Agent Inbox -> IN_APP chain"
	@echo "  agent-image  Build the production Agent container image"
	@echo "  knowledge-manifest  Generate the local source and SHA-256 manifest"
	@echo "  knowledge-validate  Validate PDF/DOCX parsing and report warnings"
	@echo "  knowledge-quality-gate  Check parsed documents and parent/child quality thresholds"
	@echo "  knowledge-validate-ocr  Validate sources through the real OCR endpoint"
	@echo "  gateway-check Build and test the independent fitness core Gateway"
	@echo "  gateway-run  Start the fitness core Gateway locally"
	@echo "  gateway-training-role-live-check  Check Gateway-to-Training role visibility without writes"
	@echo "  gateway-training-write-live-check  Verify Gateway training confirmation write, idempotency and JTI replay"
	@echo "  gateway-training-workflow-live-check  Verify full Gateway training review/publish workflow with exact cleanup"
	@echo "  gateway-training-proactive-preflight  Check Training proactive dependencies without writes"
	@echo "  gateway-training-proactive-live-check  Verify Training events through RabbitMQ to Agent IN_APP notifications"
	@echo "  training-check Build and test the structured training service"
	@echo "  training-run  Start the structured training service locally"
	@echo "  training-role-live-check  Check training health and student draft denial without writes"
	@echo "  training-role-visibility-live-check  Check Training Service role visibility with explicit fixtures"
	@echo "  booking-check Build and test the appointment write service"
	@echo "  booking-it    Run the opt-in real MySQL Booking create/reschedule/cancel integration test"
	@echo "  booking-run  Start the appointment write service locally"
	@echo "  customer-service-check Build and test the customer service"
	@echo "  customer-service-run  Start the customer service locally"
	@echo "  release-check  Run the complete deterministic release quality gate; no business writes"
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

infra-up-messaging:
	docker compose -f $(COMPOSE_FILE) --profile messaging up -d agent-rabbitmq

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

agent-operations-eval:
	cd $(AGENT_DIR) && UV_CACHE_DIR=$(UV_CACHE_DIR) uv run python -m app.agent.operations_evaluation \
		--cases evals/operations_trend_smoke.json --thresholds evals/operations_trend_thresholds.json

agent-operations-comparison-eval:
	cd $(AGENT_DIR) && UV_CACHE_DIR=$(UV_CACHE_DIR) uv run python -m app.agent.operations_comparison_evaluation \
		--cases evals/operations_comparison_smoke.json --thresholds evals/operations_comparison_thresholds.json

agent-operations-policy-eval:
	cd $(AGENT_DIR) && UV_CACHE_DIR=$(UV_CACHE_DIR) uv run python -m app.agent.operations_policy_evaluation \
		--cases evals/operations_policy_smoke.json --thresholds evals/operations_policy_thresholds.json

agent-session-summary-eval:
	cd $(AGENT_DIR) && UV_CACHE_DIR=$(UV_CACHE_DIR) uv run python -m app.session_summary_evaluation \
		--cases evals/session_summary_samples.json --thresholds evals/session_summary_thresholds.json

agent-security-check:
	cd $(AGENT_DIR) && AGENT_RAG_MALWARE_SCANNER_BACKEND=clamav UV_CACHE_DIR=$(UV_CACHE_DIR) uv run python -m app.rag.security_cli

agent-jwks-check:
	@test -n "$$JWKS_URL" || (echo "请先设置 JWKS_URL（认证服务标准 JWKS 地址）"; exit 1)
	@test -n "$$JWKS_KID" || (echo "请先设置 JWKS_KID（当前认证签名 kid）"; exit 1)
	cd $(AGENT_DIR) && UV_CACHE_DIR=$(UV_CACHE_DIR) uv run python scripts/jwks_live_check.py

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

agent-dev-context:
	@test -n "$$DEV_AGENT_ORG_ID" || (echo "请先设置 DEV_AGENT_ORG_ID（本地 MySQL 中真实存在的机构 ID）"; exit 1)
	@cd $(AGENT_DIR) && FITNESS_DEV_CONTEXT_ISSUER=1 UV_CACHE_DIR=$(UV_CACHE_DIR) uv run python scripts/issue_dev_agent_context.py \
		--subject "$${DEV_AGENT_SUBJECT:-local-operations-admin}" \
		--role "$${DEV_AGENT_ROLE:-ORGANIZATION_ADMIN}"

agent-operations-live-preflight:
	@test -n "$$AGENT_LIVE_AGENT_CONTEXT" || (echo "请先设置 AGENT_LIVE_AGENT_CONTEXT（认证服务签发的组织管理员 Token）"; exit 1)
	cd $(AGENT_DIR) && UV_CACHE_DIR=$(UV_CACHE_DIR) uv run python scripts/operations_live_preflight.py

agent-operations-live-check: agent-operations-live-preflight
	cd $(AGENT_DIR) && UV_CACHE_DIR=$(UV_CACHE_DIR) uv run python scripts/operations_live_check.py

agent-business-live-preflight:
	@test -n "$$AGENT_LIVE_AGENT_CONTEXT" || (echo "请先设置 AGENT_LIVE_AGENT_CONTEXT（认证服务签发的业务用户 Token）"; exit 1)
	cd $(AGENT_DIR) && UV_CACHE_DIR=$(UV_CACHE_DIR) uv run python scripts/operations_live_preflight.py \
		--booking-url "$${AGENT_BOOKING_SERVICE_URL:-http://127.0.0.1:8083}" \
		--verify-current-user

agent-booking-live-check: agent-business-live-preflight
	cd $(AGENT_DIR) && UV_CACHE_DIR=$(UV_CACHE_DIR) uv run python scripts/booking_live_check.py $(ARGS)

agent-fitness-live-check: agent-business-live-preflight
	cd $(AGENT_DIR) && UV_CACHE_DIR=$(UV_CACHE_DIR) uv run python scripts/fitness_live_check.py $(ARGS)

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

agent-proactive-worker:
	cd $(AGENT_DIR) && UV_CACHE_DIR=$(UV_CACHE_DIR) uv run python -m app.proactive_worker_main

agent-proactive-reliability-check:
	cd $(AGENT_DIR) && UV_CACHE_DIR=$(UV_CACHE_DIR) uv run pytest tests/test_proactive_reliability.py

agent-proactive-reliability-live-check:
	cd $(AGENT_DIR) && UV_CACHE_DIR=$(UV_CACHE_DIR) uv run python scripts/proactive_reliability_live_check.py $(ARGS)

agent-postgres-backup-restore-check:
	cd $(AGENT_DIR) && UV_CACHE_DIR=$(UV_CACHE_DIR) uv run python scripts/postgres_backup_restore_check.py $(ARGS)

agent-recovery-check:
	cd $(AGENT_DIR) && UV_CACHE_DIR=$(UV_CACHE_DIR) uv run python scripts/service_recovery_check.py $(ARGS)

agent-rate-limit-load-check:
	cd $(AGENT_DIR) && UV_CACHE_DIR=$(UV_CACHE_DIR) uv run python scripts/rate_limit_load_check.py $(ARGS)

agent-capacity-check:
	cd $(AGENT_DIR) && UV_CACHE_DIR=$(UV_CACHE_DIR) uv run python scripts/agent_capacity_check.py $(ARGS)

agent-release-rollback-check:
	cd $(AGENT_DIR) && UV_CACHE_DIR=$(UV_CACHE_DIR) uv run python scripts/release_rollback_check.py $(ARGS)

agent-migration-check:
	cd $(AGENT_DIR) && UV_CACHE_DIR=$(UV_CACHE_DIR) uv run python scripts/migration_contract_check.py

agent-proactive-live-check:
	@test -n "$$AGENT_LIVE_AGENT_CONTEXT" || (echo "请先设置 AGENT_LIVE_AGENT_CONTEXT（认证服务签发的业务用户 Token）"; exit 1)
	cd $(AGENT_DIR) && UV_CACHE_DIR=$(UV_CACHE_DIR) uv run python scripts/proactive_booking_live_check.py $(ARGS)

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

training-role-live-check:
	@test -n "$$TRAINING_INTERNAL_SERVICE_TOKEN" || (echo "请先设置 TRAINING_INTERNAL_SERVICE_TOKEN"; exit 1)
	@test -n "$$TRAINING_LIVE_ORGANIZATION_ID" || (echo "请先设置 TRAINING_LIVE_ORGANIZATION_ID"; exit 1)
	@test -n "$$TRAINING_LIVE_STUDENT_ID" || (echo "请先设置 TRAINING_LIVE_STUDENT_ID"; exit 1)
	@test -n "$$TRAINING_LIVE_COACH_ID" || (echo "请先设置 TRAINING_LIVE_COACH_ID"; exit 1)
	cd $(AGENT_DIR) && UV_CACHE_DIR=$(UV_CACHE_DIR) uv run python scripts/training_role_live_check.py

training-role-visibility-live-check:
	@test -n "$$TRAINING_INTERNAL_SERVICE_TOKEN" || (echo "请先设置 TRAINING_INTERNAL_SERVICE_TOKEN"; exit 1)
	@test -n "$$TRAINING_LIVE_ORGANIZATION_ID" || (echo "请先设置 TRAINING_LIVE_ORGANIZATION_ID"; exit 1)
	@test -n "$$TRAINING_LIVE_STUDENT_ID" || (echo "请先设置 TRAINING_LIVE_STUDENT_ID"; exit 1)
	@test -n "$$TRAINING_LIVE_COACH_ID" || (echo "请先设置 TRAINING_LIVE_COACH_ID"; exit 1)
	@test -n "$$TRAINING_LIVE_DRAFT_PLAN_ID" || (echo "请先设置 TRAINING_LIVE_DRAFT_PLAN_ID"; exit 1)
	@test -n "$$TRAINING_LIVE_PUBLISHED_PLAN_ID" || (echo "请先设置 TRAINING_LIVE_PUBLISHED_PLAN_ID"; exit 1)
	cd $(AGENT_DIR) && UV_CACHE_DIR=$(UV_CACHE_DIR) uv run python scripts/training_role_live_check.py

gateway-training-role-live-check:
	@test -n "$$GATEWAY_INTERNAL_SERVICE_TOKEN" || (echo "请先设置 GATEWAY_INTERNAL_SERVICE_TOKEN"; exit 1)
	@test -n "$$GATEWAY_CONTEXT_SIGNING_SECRET" || (echo "请先设置 GATEWAY_CONTEXT_SIGNING_SECRET"; exit 1)
	@test -n "$$TRAINING_LIVE_ORGANIZATION_ID" || (echo "请先设置 TRAINING_LIVE_ORGANIZATION_ID"; exit 1)
	@test -n "$$TRAINING_LIVE_STUDENT_ID" || (echo "请先设置 TRAINING_LIVE_STUDENT_ID"; exit 1)
	@test -n "$$TRAINING_LIVE_COACH_ID" || (echo "请先设置 TRAINING_LIVE_COACH_ID"; exit 1)
	@test -n "$$TRAINING_LIVE_DRAFT_PLAN_ID" || (echo "请先设置 TRAINING_LIVE_DRAFT_PLAN_ID"; exit 1)
	@test -n "$$TRAINING_LIVE_PUBLISHED_PLAN_ID" || (echo "请先设置 TRAINING_LIVE_PUBLISHED_PLAN_ID"; exit 1)
	@test "$$FITNESS_DEV_CONTEXT_ISSUER" = "1" || (echo "请设置 FITNESS_DEV_CONTEXT_ISSUER=1"; exit 1)
	cd $(AGENT_DIR) && UV_CACHE_DIR=$(UV_CACHE_DIR) uv run python scripts/gateway_training_role_live_check.py

gateway-training-write-live-check:
	@test "$$GATEWAY_LIVE_EXECUTE_WRITES" = "1" || (echo "默认禁止写入，请设置 GATEWAY_LIVE_EXECUTE_WRITES=1"; exit 1)
	@test -n "$$GATEWAY_INTERNAL_SERVICE_TOKEN" || (echo "请先设置 GATEWAY_INTERNAL_SERVICE_TOKEN"; exit 1)
	@test -n "$$GATEWAY_CONTEXT_SIGNING_SECRET" || (echo "请先设置 GATEWAY_CONTEXT_SIGNING_SECRET"; exit 1)
	@test -n "$$GATEWAY_CONFIRMATION_SIGNING_SECRET" || (echo "请先设置 GATEWAY_CONFIRMATION_SIGNING_SECRET"; exit 1)
	@test -n "$$TRAINING_LIVE_ORGANIZATION_ID" || (echo "请先设置 TRAINING_LIVE_ORGANIZATION_ID"; exit 1)
	@test -n "$$TRAINING_LIVE_STUDENT_ID" || (echo "请先设置 TRAINING_LIVE_STUDENT_ID"; exit 1)
	@test -n "$$TRAINING_LIVE_COACH_ID" || (echo "请先设置 TRAINING_LIVE_COACH_ID"; exit 1)
	@test "$$FITNESS_DEV_CONTEXT_ISSUER" = "1" || (echo "请设置 FITNESS_DEV_CONTEXT_ISSUER=1"; exit 1)
	cd $(AGENT_DIR) && UV_CACHE_DIR=$(UV_CACHE_DIR) uv run python scripts/gateway_training_write_live_check.py

gateway-training-workflow-live-check:
	@test "$$GATEWAY_LIVE_EXECUTE_WORKFLOW_WRITES" = "1" || (echo "默认禁止工作流写入，请设置 GATEWAY_LIVE_EXECUTE_WORKFLOW_WRITES=1"; exit 1)
	@test -n "$$GATEWAY_INTERNAL_SERVICE_TOKEN" || (echo "请先设置 GATEWAY_INTERNAL_SERVICE_TOKEN"; exit 1)
	@test -n "$$GATEWAY_CONTEXT_SIGNING_SECRET" || (echo "请先设置 GATEWAY_CONTEXT_SIGNING_SECRET"; exit 1)
	@test -n "$$GATEWAY_CONFIRMATION_SIGNING_SECRET" || (echo "请先设置 GATEWAY_CONFIRMATION_SIGNING_SECRET"; exit 1)
	@test -n "$$GATEWAY_DB_USERNAME" || (echo "请先设置 GATEWAY_DB_USERNAME 供精确清理"; exit 1)
	@test -n "$$GATEWAY_DB_PASSWORD" || (echo "请先设置 GATEWAY_DB_PASSWORD 供精确清理"; exit 1)
	@test "$$FITNESS_DEV_CONTEXT_ISSUER" = "1" || (echo "请设置 FITNESS_DEV_CONTEXT_ISSUER=1"; exit 1)
	cd $(AGENT_DIR) && UV_CACHE_DIR=$(UV_CACHE_DIR) uv run python scripts/gateway_training_workflow_live_check.py

gateway-training-proactive-preflight:
	@test -n "$$AGENT_DATABASE_URL" || (echo "请先设置 AGENT_DATABASE_URL"; exit 1)
	cd $(AGENT_DIR) && UV_CACHE_DIR=$(UV_CACHE_DIR) uv run python scripts/gateway_training_workflow_live_check.py --verify-proactive-chain --preflight-only

gateway-training-proactive-live-check:
	@test "$$GATEWAY_LIVE_EXECUTE_WORKFLOW_WRITES" = "1" || (echo "默认禁止工作流写入，请设置 GATEWAY_LIVE_EXECUTE_WORKFLOW_WRITES=1"; exit 1)
	@test -n "$$GATEWAY_INTERNAL_SERVICE_TOKEN" || (echo "请先设置 GATEWAY_INTERNAL_SERVICE_TOKEN"; exit 1)
	@test -n "$$GATEWAY_CONTEXT_SIGNING_SECRET" || (echo "请先设置 GATEWAY_CONTEXT_SIGNING_SECRET"; exit 1)
	@test -n "$$GATEWAY_CONFIRMATION_SIGNING_SECRET" || (echo "请先设置 GATEWAY_CONFIRMATION_SIGNING_SECRET"; exit 1)
	@test -n "$$GATEWAY_DB_USERNAME" || (echo "请先设置 GATEWAY_DB_USERNAME 供精确清理"; exit 1)
	@test -n "$$GATEWAY_DB_PASSWORD" || (echo "请先设置 GATEWAY_DB_PASSWORD 供精确清理"; exit 1)
	@test -n "$$TRAINING_LIVE_ORGANIZATION_ID" || (echo "请先设置 TRAINING_LIVE_ORGANIZATION_ID"; exit 1)
	@test -n "$$TRAINING_LIVE_STUDENT_ID" || (echo "请先设置 TRAINING_LIVE_STUDENT_ID"; exit 1)
	@test -n "$$TRAINING_LIVE_COACH_ID" || (echo "请先设置 TRAINING_LIVE_COACH_ID"; exit 1)
	@test "$$FITNESS_DEV_CONTEXT_ISSUER" = "1" || (echo "请设置 FITNESS_DEV_CONTEXT_ISSUER=1"; exit 1)
	@test -n "$$AGENT_DATABASE_URL" || (echo "请先设置 AGENT_DATABASE_URL"; exit 1)
	cd $(AGENT_DIR) && UV_CACHE_DIR=$(UV_CACHE_DIR) uv run python scripts/gateway_training_workflow_live_check.py --verify-proactive-chain --mysql-container "$${GATEWAY_MYSQL_CONTAINER:-fitness-mysql}"

booking-check:
	./mvnw --batch-mode -f fitness-booking-service/pom.xml -s .mvn/settings.xml -Dmaven.repo.local=.mvn/repository clean test

booking-it:
	./mvnw --batch-mode -f fitness-booking-service/pom.xml -s .mvn/settings.xml -Dmaven.repo.local=.mvn/repository -Dtest=BookingRepositoryIntegrationTest test

booking-run:
	./mvnw --batch-mode -f fitness-booking-service/pom.xml -s .mvn/settings.xml -Dmaven.repo.local=.mvn/repository spring-boot:run

customer-service-check:
	./mvnw --batch-mode -f fitness-customer-service/pom.xml -s .mvn/settings.xml -Dmaven.repo.local=.mvn/repository clean test

customer-service-run:
	./mvnw --batch-mode -f fitness-customer-service/pom.xml -s .mvn/settings.xml -Dmaven.repo.local=.mvn/repository spring-boot:run

# 发布门禁只组合确定性测试、离线评测和静态安全检查，不启动服务、不调用 DeepSeek，
# 也不会执行预约、训练计划、客服工单等任何真实业务写入。真实 HTTP 验收仍必须
# 使用各业务专用的、显式授权的 live-check 命令，避免把发布检查误当成生产演练。
release-check: agent-check agent-migration-check agent-eval agent-operations-eval agent-operations-comparison-eval agent-operations-policy-eval agent-session-summary-eval agent-security-check ocr-check gateway-check training-check booking-check customer-service-check

agent-customer-service-preflight:
	@test -n "$$AGENT_LIVE_AGENT_CONTEXT" || (echo "请先设置 AGENT_LIVE_AGENT_CONTEXT（认证服务签发的业务用户 Token）"; exit 1)
	cd $(AGENT_DIR) && UV_CACHE_DIR=$(UV_CACHE_DIR) uv run python scripts/customer_service_live_preflight.py

agent-customer-service-live-check:
	@test -n "$$AGENT_LIVE_AGENT_CONTEXT" || (echo "请先设置 AGENT_LIVE_AGENT_CONTEXT（认证服务签发的业务用户 Token）"; exit 1)
	cd $(AGENT_DIR) && UV_CACHE_DIR=$(UV_CACHE_DIR) uv run python scripts/customer_service_live_check.py

agent-customer-service-write-live-check:
	@test "$$CUSTOMER_SERVICE_LIVE_ALLOW_WRITE" = "1" || (echo "默认禁止写入，请设置 CUSTOMER_SERVICE_LIVE_ALLOW_WRITE=1"; exit 1)
	@test "$$CUSTOMER_SERVICE_LIVE_CLEANUP" = "1" || (echo "必须设置 CUSTOMER_SERVICE_LIVE_CLEANUP=1 才允许写入"; exit 1)
	@test -n "$$AGENT_LIVE_AGENT_CONTEXT" || (echo "请先设置 AGENT_LIVE_AGENT_CONTEXT（认证服务签发的业务用户 Token）"; exit 1)
	@test -n "$$GATEWAY_DB_USERNAME" || (echo "请先设置 GATEWAY_DB_USERNAME 供精确清理"; exit 1)
	@test -n "$$GATEWAY_DB_PASSWORD" || (echo "请先设置 GATEWAY_DB_PASSWORD 供精确清理"; exit 1)
	cd $(AGENT_DIR) && UV_CACHE_DIR=$(UV_CACHE_DIR) uv run python scripts/customer_service_write_live_check.py --execute

gateway-customer-service-role-live-check:
	@test -n "$$GATEWAY_INTERNAL_SERVICE_TOKEN" || (echo "请先设置 GATEWAY_INTERNAL_SERVICE_TOKEN"; exit 1)
	@test -n "$$GATEWAY_CONTEXT_SIGNING_SECRET" || (echo "请先设置 GATEWAY_CONTEXT_SIGNING_SECRET"; exit 1)
	@test -n "$$CUSTOMER_SERVICE_LIVE_ORGANIZATION_ID" || (echo "请先设置 CUSTOMER_SERVICE_LIVE_ORGANIZATION_ID"; exit 1)
	@test -n "$$CUSTOMER_SERVICE_LIVE_STUDENT_ID" || (echo "请先设置 CUSTOMER_SERVICE_LIVE_STUDENT_ID"; exit 1)
	@test -n "$$CUSTOMER_SERVICE_LIVE_COACH_ID" || (echo "请先设置 CUSTOMER_SERVICE_LIVE_COACH_ID"; exit 1)
	@test -n "$$CUSTOMER_SERVICE_LIVE_ADMIN_ID" || (echo "请先设置 CUSTOMER_SERVICE_LIVE_ADMIN_ID"; exit 1)
	@test "$$FITNESS_DEV_CONTEXT_ISSUER" = "1" || (echo "请设置 FITNESS_DEV_CONTEXT_ISSUER=1"; exit 1)
	cd $(AGENT_DIR) && UV_CACHE_DIR=$(UV_CACHE_DIR) uv run python scripts/gateway_customer_service_role_live_check.py

legacy-java-diagnostic:
	./mvnw --batch-mode -DskipTests clean compile

check: agent-check ocr-check gateway-check booking-check customer-service-check
