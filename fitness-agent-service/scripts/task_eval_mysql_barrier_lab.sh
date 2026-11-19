#!/usr/bin/env bash
# Standalone barrier lab, restricted to a newly created local disposable MySQL.
set -euo pipefail
service_root="$(cd "$(dirname "$0")/.." && pwd)"
run_id="$(openssl rand -hex 8)"
container_id=""
cleanup() {
    local status=$?
    trap - EXIT
    if [[ -n "$container_id" ]]; then
        docker rm -f -v "$container_id" >/dev/null || status=1
    fi
    exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT TERM
docker_host="$(docker context inspect --format '{{.Endpoints.docker.Host}}')"
[[ "$docker_host" == unix:///* && -S "${docker_host#unix://}" ]] || {
    printf 'A local Docker Unix socket is required.\n' >&2
    exit 1
}
export DOCKER_HOST="$docker_host"
unset DOCKER_CONTEXT
export MYSQL_ROOT_PASSWORD="$(openssl rand -hex 24)"
container_id="$(docker run -d --pull=never --rm --name "fitness-eval-mysql-$run_id" \
    --label "fitness.eval=$run_id" --tmpfs /var/lib/mysql -p 127.0.0.1::3306 \
    -e MYSQL_ROOT_PASSWORD -e MYSQL_ROOT_HOST=% -e MYSQL_DATABASE=fitness_eval mysql:8.0 \
    --character-set-server=utf8mb4 --collation-server=utf8mb4_bin)"
ready=false
for attempt in $(seq 1 90); do
    if docker exec "$container_id" sh -c 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql --protocol=socket -uroot -e "SELECT 1"' >/dev/null 2>&1; then
        ready=true; break
    fi
    sleep 1
done
[[ "$ready" == true ]]
port="$(docker port "$container_id" 3306/tcp | sed -n 's/^127\.0\.0\.1://p')"
[[ "$port" =~ ^[0-9]+$ ]]
cd "$service_root"
TASK_EVAL_RUN_ID="$run_id" TASK_EVAL_MYSQL_CONTAINER_ID="$container_id" TASK_EVAL_MYSQL_PORT="$port" \
    "$service_root/.venv/bin/python" -m app.evaluation.task_eval.mysql_barrier_lab
