#!/usr/bin/env bash
# Real Java booking service/repository writes plus a separate-process read-only
# observer, scoped to a fresh disposable MySQL instance; no Agent/model involved.
set -euo pipefail
cd "$(dirname "$0")/.."
service_root="$PWD"
repo_root="$(cd .. && pwd)"
run_id="$(openssl rand -hex 8)"
container_id=""
log_file="$(mktemp "${TMPDIR:-/tmp}/fitness-eval-booking.XXXXXX")"
export MYSQL_ROOT_PASSWORD="$(openssl rand -hex 24)"
cleanup() {
    local status=$?
    trap - EXIT
    [[ -z "$container_id" ]] || docker rm -f -v "$container_id" >/dev/null
    if [[ "$status" -ne 0 ]]; then printf 'Java integration log: %s\n' "$log_file" >&2; else rm -f "$log_file"; fi
    exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT TERM
container_id="$(docker run -d --rm --name "fitness-eval-mysql-$run_id" \
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
export TASK_EVAL_RUN_ID="$run_id" TASK_EVAL_MYSQL_CONTAINER_ID="$container_id" TASK_EVAL_MYSQL_PORT="$port"
export TASK_EVAL_PYTHON="$service_root/.venv/bin/python" TASK_EVAL_SERVICE_ROOT="$service_root"
if [[ "$(uname -s)" == Darwin && "$(uname -m)" == arm64 ]]; then
    export TASK_EVAL_OBSERVER_ARCH=arm64
fi
"$TASK_EVAL_PYTHON" -m app.evaluation.task_eval.mysql_sandbox_preflight
# The fixture contains only synthetic data; it is loaded after the empty-schema check.
docker exec -i "$container_id" sh -c 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysql --protocol=socket -uroot fitness_eval' \
    < "$repo_root/fitness-core-gateway/src/test/resources/mcp/mysql-legacy-fixture.sql"
export BOOKING_IT_ENABLED=true BOOKING_IT_DB_USERNAME=root BOOKING_IT_DB_PASSWORD="$MYSQL_ROOT_PASSWORD"
export BOOKING_IT_DB_URL="jdbc:mysql://127.0.0.1:$port/fitness_eval?useSSL=false&allowPublicKeyRetrieval=true&serverTimezone=UTC&characterEncoding=UTF-8"
export TASK_EVAL_OBSERVER_ENABLED=1
cd "$repo_root"
if ! mvn -q -f "$repo_root/fitness-booking-service/pom.xml" \
    '-Dtest=BookingRepositoryIntegrationTest#createsReschedulesCancelsAndRollsBackWhenConfirmationJtiIsReused' test >"$log_file" 2>&1; then
    exit 1
fi
"$TASK_EVAL_PYTHON" - "$repo_root/fitness-booking-service/target/surefire-reports/TEST-com.shuyiwa.fitness.booking.repository.BookingRepositoryIntegrationTest.xml" <<'PY'
import sys
import xml.etree.ElementTree as ET
root = ET.parse(sys.argv[1]).getroot()
cases = root.findall("testcase")
if (root.get("tests") != "1" or root.get("failures") != "0" or root.get("errors") != "0"
        or root.get("skipped") != "0" or len(cases) != 1
        or cases[0].get("name") != "createsReschedulesCancelsAndRollsBackWhenConfirmationJtiIsReused"
        or cases[0].find("skipped") is not None):
    raise SystemExit("Expected Java integration test was not executed successfully")
PY
printf '%s\n' '{"status":"PASS","origin":"java_booking_repository_real_mysql_independent_observer","model":"not_exercised","gateway_http":"not_exercised","audit_complete":false}'
