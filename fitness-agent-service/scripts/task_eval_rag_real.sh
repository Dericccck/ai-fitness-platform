#!/usr/bin/env bash
# One private, disposable pgvector container. All model inputs are fixed synthetic data.
set -euo pipefail
cd "$(dirname "$0")/.."
# Docker mutations are permitted only through the active local Unix socket.
case "${DOCKER_HOST:-}" in
    ""|unix:///*) ;;
    *) printf 'Remote Docker host is not allowed.\n' >&2; exit 1 ;;
esac
context_name="$(docker context show)"
context_host="$(docker context inspect --format '{{.Endpoints.docker.Host}}' "$context_name")"
case "$context_host" in
    unix:///*) ;;
    *) printf 'Remote Docker context is not allowed.\n' >&2; exit 1 ;;
esac
run_id="$(openssl rand -hex 8)"
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
export POSTGRES_PASSWORD="$(openssl rand -hex 24)"
pg_id=""
network_id=""
cleanup() {
    local status=$?
    trap - EXIT
    if [[ -n "$pg_id" ]] && ! docker rm -f -v "$pg_id" >/dev/null; then
        printf 'Failed to remove the evaluation container.\n' >&2
        status=1
    fi
    if [[ -n "$network_id" ]] && ! docker network rm "$network_id" >/dev/null; then
        printf 'Failed to remove the evaluation network.\n' >&2
        status=1
    fi
    exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT TERM
network_id="$(docker network create --label "fitness.eval.rag=$run_id" "fitness-eval-rag-$run_id")"
pg_id="$(docker run -d --rm --name "fitness-eval-rag-pg-$run_id" \
    --network "$network_id" --label "fitness.eval.rag=$run_id" \
    --tmpfs /var/lib/postgresql/data -p 127.0.0.1::5432 \
    -e POSTGRES_PASSWORD -e POSTGRES_USER=fitness_eval -e POSTGRES_DB=postgres \
    pgvector/pgvector:pg16)"
ready=false
for attempt in $(seq 1 90); do
    if docker exec "$pg_id" pg_isready -U fitness_eval -d postgres >/dev/null 2>&1; then
        ready=true
        break
    fi
    sleep 1
done
if [[ "$ready" != true ]]; then
    printf 'Isolated PostgreSQL did not become ready.\n' >&2
    exit 1
fi
pg_port="$(docker port "$pg_id" 5432/tcp | sed -n 's/^127\.0\.0\.1://p')"
[[ "$pg_port" =~ ^[0-9]+$ ]]
export AGENT_DATABASE_URL="postgresql+asyncpg://fitness_eval:$POSTGRES_PASSWORD@127.0.0.1:$pg_port/postgres"
export TASK_EVAL_RAG_PG_ID="$pg_id" TASK_EVAL_RAG_NETWORK_ID="$network_id" TASK_EVAL_RAG_RUN_ID="$run_id"
export TASK_EVAL_RAG_PG_PORT="$pg_port"
"${TASK_EVAL_PYTHON:-.venv/bin/python}" -m app.evaluation.task_eval.dev_real_rag "$@"
