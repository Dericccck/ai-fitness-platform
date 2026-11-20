#!/usr/bin/env bash
# Real Java booking service/repository writes plus a separate-process read-only
# observer, scoped to a fresh disposable MySQL instance; no Agent/model involved.
set -euo pipefail
test_method=createsReschedulesCancelsAndRollsBackWhenConfirmationJtiIsReused
if [[ "${1:-}" == "--coach-slot-concurrency" && "$#" -eq 1 ]]; then
    test_method=serializesTwoStudentsCompetingForTheSameCoachSlot
elif [[ "$#" -ne 0 ]]; then
    printf 'Unsupported booking observation mode.\n' >&2
    exit 1
fi
cd "$(dirname "$0")/.."
service_root="$PWD"
repo_root="$(cd .. && pwd)"
run_id="$(openssl rand -hex 8)"
container_id=""
build_dir="$(mktemp -d "${TMPDIR:-/tmp}/fitness-eval-booking-build.XXXXXX")"
log_file="$(mktemp "${TMPDIR:-/tmp}/fitness-eval-booking.XXXXXX")"
export MYSQL_ROOT_PASSWORD="$(openssl rand -hex 24)"
cleanup() {
    local status=$?
    trap - EXIT
    if [[ -n "$container_id" ]] && ! docker rm -f -v "$container_id" >/dev/null; then
        status=1
    fi
    if ! rm -rf "$build_dir"; then
        status=1
    fi
    if [[ "$status" -ne 0 ]]; then printf 'Java integration log: %s\n' "$log_file" >&2; else rm -f "$log_file"; fi
    exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT TERM
docker_host="${DOCKER_HOST:-$(docker context inspect --format '{{.Endpoints.docker.Host}}')}"
[[ "$docker_host" == unix:///* && -S "${docker_host#unix://}" ]] || {
    printf 'A local Docker Unix socket is required.\n' >&2
    exit 1
}
export DOCKER_HOST="$docker_host"
unset DOCKER_CONTEXT
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
mkdir -p "$build_dir/src/test/java/com/shuyiwa/fitness/booking/repository" "$build_dir/src"
cp "$repo_root/fitness-booking-service/pom.xml" "$build_dir/pom.xml"
cp -R "$repo_root/fitness-booking-service/src/main" "$build_dir/src/main"
cp "$repo_root/fitness-booking-service/src/test/java/com/shuyiwa/fitness/booking/repository/BookingRepositoryIntegrationTest.java" \
   "$build_dir/src/test/java/com/shuyiwa/fitness/booking/repository/BookingRepositoryIntegrationTest.java"
fingerprint() {
    "$TASK_EVAL_PYTHON" - "$1" <<'PY'
import hashlib
from pathlib import Path
import sys
root = Path(sys.argv[1])
test = root / 'src/test/java/com/shuyiwa/fitness/booking/repository/BookingRepositoryIntegrationTest.java'
paths = [root / 'pom.xml', test, *(p for p in (root / 'src/main').rglob('*') if p.is_file())]
h = hashlib.sha256()
for path in sorted(paths, key=lambda p: p.relative_to(root).as_posix()):
    h.update(path.relative_to(root).as_posix().encode())
    h.update(b'\0')
    h.update(hashlib.sha256(path.read_bytes()).digest())
print(h.hexdigest())
PY
}
source_sha="$(fingerprint "$repo_root/fitness-booking-service")"
[[ "$source_sha" == "$(fingerprint "$build_dir")" ]] || {
    printf 'Java source snapshot differs from the checked source.\n' >&2
    exit 1
}
if ! mvn -q -o -s "$repo_root/.mvn/settings.xml" "-Dmaven.repo.local=$repo_root/.mvn/repository" -f "$build_dir/pom.xml" \
    "-Dtest=BookingRepositoryIntegrationTest#$test_method" test >"$log_file" 2>&1; then
    exit 1
fi
"$TASK_EVAL_PYTHON" - "$build_dir/target/surefire-reports/TEST-com.shuyiwa.fitness.booking.repository.BookingRepositoryIntegrationTest.xml" "$test_method" <<'PY'
import sys
import xml.etree.ElementTree as ET
root = ET.parse(sys.argv[1]).getroot()
cases = root.findall("testcase")
if (root.get("tests") != "1" or root.get("failures") != "0" or root.get("errors") != "0"
        or root.get("skipped") != "0" or len(cases) != 1
        or cases[0].get("name") != sys.argv[2]
        or cases[0].find("skipped") is not None):
    raise SystemExit("Expected Java integration test was not executed successfully")
PY
[[ "$source_sha" == "$(fingerprint "$build_dir")" && "$source_sha" == "$(fingerprint "$repo_root/fitness-booking-service")" ]] || {
    printf 'Java sources changed during the observation.\n' >&2
    exit 1
}
if [[ "$test_method" == serializesTwoStudentsCompetingForTheSameCoachSlot ]]; then
    "$TASK_EVAL_PYTHON" - "$source_sha" <<'PY'
import json, sys
print(json.dumps({'status':'PASS','scope':'java_private_coach_slot_concurrency',
 'origin':'java_booking_service_real_mysql','source_sha256':sys.argv[1],
 'source_attestation':'NOT_INDEPENDENTLY_VERIFIED','model':'not_exercised',
 'gateway_http':'not_exercised','audit_complete':False,'task_status':'UNKNOWN'}))
PY
else
    "$TASK_EVAL_PYTHON" - "$source_sha" <<'PY'
import json, sys
print(json.dumps({'status':'PASS','origin':'java_booking_repository_real_mysql_independent_observer',
 'source_sha256':sys.argv[1],'source_attestation':'NOT_INDEPENDENTLY_VERIFIED',
 'model':'not_exercised','gateway_http':'not_exercised','audit_complete':False}))
PY
fi
