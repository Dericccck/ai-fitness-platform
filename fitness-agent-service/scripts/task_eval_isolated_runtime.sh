#!/usr/bin/env bash
# Run runtime acceptance using only fresh, disposable loopback-bound containers.
set -euo pipefail
cd "$(dirname "$0")/.."
run_id="$(openssl rand -hex 8)"
export POSTGRES_PASSWORD="$(openssl rand -hex 24)"
pg_id=""
redis_id=""
cleanup() {
    local status=$?
    trap - EXIT
    [[ -z "$pg_id" ]] || docker rm -f -v "$pg_id" >/dev/null
    [[ -z "$redis_id" ]] || docker rm -f -v "$redis_id" >/dev/null
    exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT TERM
pg_id="$(docker run -d --rm --name "fitness-eval-pg-$run_id" \
    --label "fitness.eval=$run_id" --tmpfs /var/lib/postgresql/data \
    -p 127.0.0.1::5432 -e POSTGRES_PASSWORD -e POSTGRES_USER=fitness_eval \
    -e POSTGRES_DB=postgres pgvector/pgvector:pg16)"
redis_id="$(docker run -d --rm --name "fitness-eval-redis-$run_id" \
    --label "fitness.eval=$run_id" --tmpfs /data -p 127.0.0.1::6379 \
    redis:7.4-alpine redis-server --save '' --appendonly no)"
ready=false
for attempt in $(seq 1 90); do
    if docker exec "$pg_id" pg_isready -U fitness_eval -d postgres >/dev/null 2>&1 \
        && [[ "$(docker exec "$redis_id" redis-cli ping 2>/dev/null)" == "PONG" ]]; then
        ready=true
        break
    fi
    sleep 1
done
if [[ "$ready" != true ]]; then
    printf 'Isolated PostgreSQL or Redis did not become ready.\n' >&2
    exit 1
fi
pg_port="$(docker port "$pg_id" 5432/tcp | sed -n 's/^127\.0\.0\.1://p')"
redis_port="$(docker port "$redis_id" 6379/tcp | sed -n 's/^127\.0\.0\.1://p')"
[[ "$pg_port" =~ ^[0-9]+$ && "$redis_port" =~ ^[0-9]+$ ]]
export AGENT_DATABASE_URL="postgresql+asyncpg://fitness_eval:$POSTGRES_PASSWORD@127.0.0.1:$pg_port/postgres"
export AGENT_REDIS_URL="redis://127.0.0.1:$redis_port/0"
export TASK_EVAL_RUN_ID="$run_id"
export TASK_EVAL_POSTGRES_CONTAINER_ID="$pg_id"
export TASK_EVAL_REDIS_CONTAINER_ID="$redis_id"
"${TASK_EVAL_PYTHON:-.venv/bin/python}" -m app.evaluation.task_eval.sandbox_preflight
"${TASK_EVAL_PYTHON:-.venv/bin/python}" scripts/runtime_delivery_live_check.py --execute --isolated
