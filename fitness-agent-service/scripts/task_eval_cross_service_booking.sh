#!/usr/bin/env bash
# Scripted model -> Python Agent/PG -> Gateway HTTP -> Booking HTTP/MySQL.
# All services and identities are newly created for this run; never use .env.
set -euo pipefail
umask 077
single_development_trial=false
book_real_model=false
broker_duplicate_trial=false
booking_capabilities_trial=false
development_module=app.evaluation.task_eval.dev_sandbox_booking
positive_profile=""
if [[ "${1:-}" == "--booking-positive-real-model-trial" ]]; then
    [[ "$#" -ge 3 ]] || { printf 'Provide a supported case and a new absolute report path.\n' >&2; exit 1; }
    case "$2" in
        BOOK-001|BOOK-002|BOOK-003|BOOK-005|BOOK-007) positive_profile="$2" ;;
        *) printf 'Unsupported positive booking case.\n' >&2; exit 1 ;;
    esac
    shift 2
    set -- --book-004-real-model-trial "$@"
fi
if [[ "${1:-}" == "--booking-capabilities-trial" ]]; then
    [[ "$#" -eq 2 && "$2" == /* && ! -e "$2" && ! -L "$2" ]] || {
        printf 'Provide a new absolute report path for the capabilities trial.\n' >&2
        exit 1
    }
    booking_capabilities_trial=true
    single_development_trial=true
    single_report="$2"
fi
if [[ "${1:-}" == "--broker-duplicate-trial" ]]; then
    [[ "$#" -eq 2 && "$2" == /* && ! -e "$2" ]] || {
        printf 'Provide a new absolute report path for the broker trial.\n' >&2
        exit 1
    }
    broker_duplicate_trial=true
    broker_report="$2"
    set -- --real-broker --report-file "$broker_report"
fi
if [[ "${1:-}" == "--book-009-exposed-test-diagnostic" || "${1:-}" == "--book-006-real-model-trial" || "${1:-}" == "--book-004-real-model-trial" || "${1:-}" == "--single-development-trial" || "${1:-}" == "--book-018-development-trial" || "${1:-}" == "--book-018-real-model-trial" || "${1:-}" == "--book-026-development-trial" || "${1:-}" == "--book-026-real-model-trial" || "${1:-}" == "--book-027-development-trial" || "${1:-}" == "--book-027-real-model-trial" ]]; then
    [[ "$#" -ge 2 && "$2" == /* && ! -e "$2" ]] || {
        printf 'Provide a new absolute report path for the development trial.\n' >&2
        exit 1
    }
    [[ "$1" == "--book-009-exposed-test-diagnostic" || "$1" == "--book-006-real-model-trial" || "$1" == "--book-004-real-model-trial" || "$1" == "--book-018-real-model-trial" || "$1" == "--book-026-real-model-trial" || "$1" == "--book-027-real-model-trial" || "$#" -eq 2 ]] || exit 1
    single_development_trial=true
    single_report="$2"
    if [[ "$1" == "--book-004-real-model-trial" || "$1" == "--book-006-real-model-trial" || "$1" == "--book-009-exposed-test-diagnostic" ]]; then
        development_module=app.evaluation.task_eval.dev_sandbox_book_004
    fi
    context_profile="${positive_profile:-BOOK-004}"
    context_diagnostic=false
    if [[ "$1" == "--book-006-real-model-trial" ]]; then
        context_profile=BOOK-006
    fi
    if [[ "$1" == "--book-009-exposed-test-diagnostic" ]]; then
        context_profile=BOOK-009
        context_diagnostic=true
    fi
    if [[ "$1" == "--book-018-development-trial" || "$1" == "--book-018-real-model-trial" ]]; then
        development_module=app.evaluation.task_eval.dev_sandbox_book_018
    fi
    if [[ "$1" == "--book-026-development-trial" || "$1" == "--book-026-real-model-trial" ]]; then
        development_module=app.evaluation.task_eval.dev_sandbox_book_026
    fi
    if [[ "$1" == "--book-027-development-trial" || "$1" == "--book-027-real-model-trial" ]]; then
        development_module=app.evaluation.task_eval.dev_sandbox_book_027
    fi
    if [[ "$1" == "--book-009-exposed-test-diagnostic" || "$1" == "--book-006-real-model-trial" || "$1" == "--book-004-real-model-trial" || "$1" == "--book-018-real-model-trial" || "$1" == "--book-026-real-model-trial" || "$1" == "--book-027-real-model-trial" ]]; then
        book_real_model=true
        shift 2
        real_model_args=("$@")
        has_credential=false has_budget=false has_limit=false has_opening=false has_minimum=false has_pricing=false
        while [[ "$#" -gt 0 ]]; do
            case "$1" in
                --confirm-current-pricing)
                    [[ "$has_pricing" == false ]] || exit 1
                    has_pricing=true; shift ;;
                --credential-file|--budget-file|--budget-limit-nano|--budget-opening-nano|--minimum-reserved-nano)
                    [[ "$#" -ge 2 && -n "$2" && "$2" != --* ]] || exit 1
                    case "$1" in
                        --credential-file) [[ "$has_credential" == false ]] || exit 1; has_credential=true ;;
                        --budget-file) [[ "$has_budget" == false ]] || exit 1; has_budget=true ;;
                        --budget-limit-nano) [[ "$has_limit" == false ]] || exit 1; has_limit=true ;;
                        --budget-opening-nano) [[ "$has_opening" == false ]] || exit 1; has_opening=true ;;
                        --minimum-reserved-nano) [[ "$has_minimum" == false ]] || exit 1; has_minimum=true ;;
                    esac
                    shift 2 ;;
                *) printf 'Unsupported real-model option.\n' >&2; exit 1 ;;
            esac
        done
        [[ "$has_credential" == true && "$has_budget" == true && "$has_limit" == true && "$has_opening" == true && "$has_minimum" == true && "$has_pricing" == true ]] || {
            printf 'Real-model trial requires private credentials, existing budget and confirmed pricing.\n' >&2
            exit 1
        }
    fi
fi
service_root="$(cd "$(dirname "$0")/.." && pwd)"
repo_root="$(cd "$service_root/.." && pwd)"
python="$service_root/.venv/bin/python"
run_id="$(openssl rand -hex 8)"
run_dir="$(mktemp -d "${TMPDIR:-/tmp}/fitness-cross-eval.XXXXXX")"
pg_id="" mysql_id="" rabbit_id="" gateway_pid="" booking_pid=""
cleanup() {
    local status=$?
    local cleanup_failed=0
    trap - EXIT
    [[ -z "$gateway_pid" ]] || { kill "$gateway_pid" 2>/dev/null || true; wait "$gateway_pid" 2>/dev/null || true; }
    [[ -z "$booking_pid" ]] || { kill "$booking_pid" 2>/dev/null || true; wait "$booking_pid" 2>/dev/null || true; }
    [[ -z "$pg_id" ]] || docker rm -f -v "$pg_id" >/dev/null || cleanup_failed=1
    [[ -z "$mysql_id" ]] || docker rm -f -v "$mysql_id" >/dev/null || cleanup_failed=1
    [[ -z "$rabbit_id" ]] || docker rm -f -v "$rabbit_id" >/dev/null || cleanup_failed=1
    if [[ "$cleanup_failed" -ne 0 ]]; then
        printf 'Failed to remove one or more isolated containers.\n' >&2
        status=1
    fi
    if [[ "$status" -eq 0 || "$status" -eq 2 || "$status" -eq 3 ]]; then rm -rf "$run_dir"; else printf 'Isolated run logs: %s\n' "$run_dir" >&2; fi
    exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT TERM
cd "$repo_root"
# Refuse remote Docker contexts and pin every subsequent Docker call to the
# resolved local Unix socket, including subprocesses with a clean HOME.
docker_host="$(docker context inspect --format '{{.Endpoints.docker.Host}}')"
[[ "$docker_host" == unix:///* && -S "${docker_host#unix://}" ]] || {
    printf 'A local Docker Unix socket is required.\n' >&2
    exit 1
}
export DOCKER_HOST="$docker_host"
unset DOCKER_CONTEXT
# Rebuild the checked-out sources. Offline mode prevents implicit dependency egress.
mvn -q -o -f fitness-core-gateway/pom.xml -DskipTests package >"$run_dir/build.log" 2>&1
mvn -q -o -f fitness-booking-service/pom.xml -DskipTests package >>"$run_dir/build.log" 2>&1
export POSTGRES_PASSWORD="$(openssl rand -hex 24)" MYSQL_ROOT_PASSWORD="$(openssl rand -hex 24)"
pg_id="$(docker run -d --pull=never --rm --name "fitness-eval-pg-$run_id" --label "fitness.eval=$run_id" \
    --tmpfs /var/lib/postgresql/data -p 127.0.0.1::5432 -e POSTGRES_PASSWORD \
    -e POSTGRES_USER=fitness_eval -e POSTGRES_DB=postgres pgvector/pgvector:pg16)"
mysql_id="$(docker run -d --pull=never --rm --name "fitness-eval-mysql-$run_id" --label "fitness.eval=$run_id" \
    --tmpfs /var/lib/mysql -p 127.0.0.1::3306 -e MYSQL_ROOT_PASSWORD -e MYSQL_ROOT_HOST=% \
    -e MYSQL_DATABASE=fitness_eval mysql:8.0 --character-set-server=utf8mb4 --collation-server=utf8mb4_bin)"
if [[ "$broker_duplicate_trial" == true ]]; then
    export RABBITMQ_DEFAULT_USER=fitness_eval RABBITMQ_DEFAULT_PASS="$(openssl rand -hex 24)"
    rabbit_id="$(docker run -d --pull=never --rm --name "fitness-eval-rabbit-$run_id" --label "fitness.eval=$run_id" \
        --tmpfs /var/lib/rabbitmq -p 127.0.0.1::5672 -e RABBITMQ_DEFAULT_USER -e RABBITMQ_DEFAULT_PASS \
        -e RABBITMQ_SERVER_ADDITIONAL_ERL_ARGS='+S 2:2 +A 4' rabbitmq:3.13-management-alpine)"
fi
ready=false
for attempt in $(seq 1 90); do
    if docker exec "$pg_id" pg_isready -U fitness_eval -d postgres >/dev/null 2>&1 \
        && docker exec "$mysql_id" sh -c 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql --protocol=tcp -h127.0.0.1 -uroot -e "SELECT 1"' >/dev/null 2>&1 \
        && { [[ "$broker_duplicate_trial" == false ]] || docker exec "$rabbit_id" rabbitmq-diagnostics -q check_running >/dev/null 2>&1; }; then
        ready=true; break
    fi
    sleep 1
done
[[ "$ready" == true ]]
pg_port="$(docker port "$pg_id" 5432/tcp | sed -n 's/^127\.0\.0\.1://p')"
mysql_port="$(docker port "$mysql_id" 3306/tcp | sed -n 's/^127\.0\.0\.1://p')"
[[ "$pg_port" =~ ^[0-9]+$ && "$mysql_port" =~ ^[0-9]+$ ]]
export TASK_EVAL_RUN_ID="$run_id" TASK_EVAL_POSTGRES_CONTAINER_ID="$pg_id"
export TASK_EVAL_MYSQL_CONTAINER_ID="$mysql_id" TASK_EVAL_MYSQL_PORT="$mysql_port"
cd "$service_root"
"$python" -m app.evaluation.task_eval.mysql_sandbox_preflight
publisher_enabled=false rabbit_port=1
if [[ "$broker_duplicate_trial" == true ]]; then
    # Bind the route before the Java scheduler can publish; the helper verifies
    # the owned tmpfs-backed local container before connecting to its port.
    export TASK_EVAL_RABBIT_CONTAINER_ID="$rabbit_id" TASK_EVAL_RABBIT_PASSWORD="$RABBITMQ_DEFAULT_PASS"
    rabbit_port="$("$python" -m app.evaluation.task_eval.booking_rabbit_delivery)"
    [[ "$rabbit_port" =~ ^[0-9]+$ ]]
    publisher_enabled=true
fi
# Verify PostgreSQL is our tagged disposable container before issuing CREATE DATABASE.
"$python" - "$pg_id" "$run_id" "$pg_port" <<'PY'
import sys
from app.evaluation.task_eval.sandbox_preflight import _inspect, _verify_container
if _verify_container(_inspect(sys.argv[1]), run_id=sys.argv[2], kind="pg") != int(sys.argv[3]):
    raise RuntimeError("POSTGRES_ENDPOINT_MISMATCH")
PY
docker exec "$pg_id" createdb -U fitness_eval "fitness_cross_$run_id"
docker exec -i "$mysql_id" sh -c 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysql --protocol=socket -uroot fitness_eval' \
    < "$repo_root/fitness-core-gateway/src/test/resources/mcp/mysql-legacy-fixture.sql"
docker exec -i "$mysql_id" sh -c 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysql --protocol=socket -uroot fitness_eval' <<'SQL'
INSERT INTO login_user(id,name,phone) VALUES ('student-1','test student','test-student'),('student-2','witness student','witness-student'),('coach-1','test coach','test-coach');
INSERT INTO organization(id,name,organization_type) VALUES ('org-1','test organization','FITNESS'),('org-2','witness organization','FITNESS');
INSERT INTO course(id,name,status,organization_id) VALUES ('course-1','test course',1,'org-1'),('course-2','witness course',1,'org-2');
INSERT INTO login_user_authority(id,login_user_id,authority,entity_id) VALUES ('authority-1','coach-1','COACH','org-1');
INSERT INTO user_and_coach(id,user_id,coach_id,organization_id,status,deleted) VALUES ('relation-1','student-1','coach-1','org-1',1,0);
INSERT INTO contract(id,organization_id,user_id,course_id,contract_create_time,contract_end_time,remaining_class_hours,status,deleted) VALUES ('contract-1','org-1','student-1','course-1','2000-01-01','2099-12-31',2,1,0),('contract-2','org-2','student-2','course-2','2000-01-01','2099-12-31',5,1,0);
SQL
booking_db_user=root gateway_db_user=root
booking_db_password="$MYSQL_ROOT_PASSWORD" gateway_db_password="$MYSQL_ROOT_PASSWORD"
booking_schema_init=true
if [[ "$single_development_trial" == true ]]; then
    export TASK_EVAL_BOOKING_DB_PASSWORD="$(openssl rand -hex 24)"
    export TASK_EVAL_GATEWAY_DB_PASSWORD="$(openssl rand -hex 24)"
    "$python" -m app.evaluation.task_eval.mysql_restricted_audit setup
    booking_db_user=eval_booking gateway_db_user=eval_gateway
    booking_db_password="$TASK_EVAL_BOOKING_DB_PASSWORD"
    gateway_db_password="$TASK_EVAL_GATEWAY_DB_PASSWORD"
    booking_schema_init=false
fi
context_secret="$(openssl rand -hex 32)" confirmation_secret="$(openssl rand -hex 32)"
execution_token="$(openssl rand -hex 32)" booking_token="$(openssl rand -hex 32)"
execution_hash="$(printf '%s' "$execution_token" | openssl dgst -sha256 | awk '{print $NF}')"
expires_at="$(( $(date +%s) + 1200 ))"
mysql_url="jdbc:mysql://127.0.0.1:$mysql_port/fitness_eval?useSSL=false&allowPublicKeyRetrieval=true&serverTimezone=UTC&characterEncoding=UTF-8"
cd "$run_dir"
env -i PATH="$PATH" HOME="$run_dir" TMPDIR="$run_dir" \
    BOOKING_DB_URL="$mysql_url" BOOKING_DB_USERNAME="$booking_db_user" BOOKING_DB_PASSWORD="$booking_db_password" \
    BOOKING_SCHEMA_INIT_ENABLED="$booking_schema_init" \
    BOOKING_SERVER_PORT=0 BOOKING_INTERNAL_SERVICE_TOKEN="$booking_token" \
    BOOKING_OUTBOX_PUBLISHER_ENABLED="$publisher_enabled" \
    BOOKING_RABBITMQ_HOST=127.0.0.1 BOOKING_RABBITMQ_PORT="$rabbit_port" \
    BOOKING_RABBITMQ_USERNAME=fitness_eval BOOKING_RABBITMQ_PASSWORD="${RABBITMQ_DEFAULT_PASS:-unused}" \
    BOOKING_OUTBOX_EXCHANGE="fitness-eval-events-$run_id" \
    java -jar "$repo_root/fitness-booking-service/target/fitness-booking-service.jar" \
    --server.address=127.0.0.1 >"$run_dir/booking.log" 2>&1 &
booking_pid=$!
wait_port() {
    local pid="$1" file="$2" port=""
    for attempt in $(seq 1 90); do
        kill -0 "$pid" 2>/dev/null || return 1
        port="$(sed -n 's/.*Tomcat started on port(s): \([0-9][0-9]*\).*/\1/p' "$file" | tail -1)"
        if [[ "$port" =~ ^[0-9]+$ ]] && curl --noproxy '*' -fsS --max-time 1 "http://127.0.0.1:$port/health/runtime-ready" >/dev/null 2>&1; then
            lsof -nP -a -p "$pid" -iTCP:"$port" -sTCP:LISTEN -F p | grep -qx "p$pid" || return 1
            printf '%s' "$port"; return 0
        fi
        sleep 1
    done
    return 1
}
booking_port="$(wait_port "$booking_pid" "$run_dir/booking.log")"
env -i PATH="$PATH" HOME="$run_dir" TMPDIR="$run_dir" \
    GATEWAY_DB_URL="$mysql_url" GATEWAY_DB_USERNAME="$gateway_db_user" GATEWAY_DB_PASSWORD="$gateway_db_password" \
    GATEWAY_SERVER_PORT=0 GATEWAY_AGENT_EXECUTION_TOKEN_SHA256="$execution_hash" \
    GATEWAY_AGENT_EXECUTION_TOKEN_EXPIRES_AT="$expires_at" GATEWAY_CONTEXT_SIGNING_SECRET="$context_secret" \
    GATEWAY_CONTEXT_SIGNING_KEY_ID=eval GATEWAY_CONFIRMATION_SIGNING_SECRET="$confirmation_secret" \
    GATEWAY_CONFIRMATION_SIGNING_KEY_ID=eval GATEWAY_BOOKING_SERVICE_BASE_URL="http://127.0.0.1:$booking_port" \
    GATEWAY_BOOKING_SERVICE_TOKEN="$booking_token" GATEWAY_TRAINING_SERVICE_BASE_URL=http://127.0.0.1:1 \
    GATEWAY_CUSTOMER_SERVICE_BASE_URL=http://127.0.0.1:1 \
    java -jar "$repo_root/fitness-core-gateway/target/fitness-core-gateway.jar" \
    --server.address=127.0.0.1 >"$run_dir/gateway.log" 2>&1 &
gateway_pid=$!
gateway_port="$(wait_port "$gateway_pid" "$run_dir/gateway.log")"
if [[ "$single_development_trial" == true ]]; then
    "$python" -m app.evaluation.task_eval.mysql_restricted_audit verify
fi
"$python" - "$service_root" "$run_dir/alembic.ini" <<'PY'
from pathlib import Path
import sys
source = Path(sys.argv[1])
ini = (source / 'alembic.ini').read_text()
ini = ini.replace('script_location = migrations', f'script_location = {source / "migrations"}')
ini = ini.replace('prepend_sys_path = .', f'prepend_sys_path = {source}')
Path(sys.argv[2]).write_text(ini)
PY
runtime_env=(env -i "PATH=$PATH" "HOME=$run_dir" "TMPDIR=$run_dir" "PYTHONPATH=$service_root"
    "DOCKER_HOST=$docker_host"
    "AGENT_ENVIRONMENT=test"
    "AGENT_DATABASE_URL=postgresql+asyncpg://fitness_eval:$POSTGRES_PASSWORD@127.0.0.1:$pg_port/fitness_cross_$run_id"
    "AGENT_GATEWAY_BASE_URL=http://127.0.0.1:$gateway_port"
    "AGENT_GATEWAY_HTTP_EXECUTION_SERVICE_TOKEN=$execution_token"
    "TASK_EVAL_RUN_ID=$run_id" "TASK_EVAL_POSTGRES_CONTAINER_ID=$pg_id"
    "TASK_EVAL_MYSQL_CONTAINER_ID=$mysql_id" "TASK_EVAL_GATEWAY_PID=$gateway_pid"
    "TASK_EVAL_LAUNCHER_PID=$$"
    "TASK_EVAL_CONTEXT_SECRET=$context_secret" "TASK_EVAL_CONFIRMATION_SECRET=$confirmation_secret")
if [[ "$single_development_trial" == true ]]; then
    runtime_env+=("TASK_EVAL_BOOKING_DB_PASSWORD=$TASK_EVAL_BOOKING_DB_PASSWORD"
        "TASK_EVAL_GATEWAY_DB_PASSWORD=$TASK_EVAL_GATEWAY_DB_PASSWORD")
fi
if [[ "$broker_duplicate_trial" == true ]]; then
    runtime_env+=("TASK_EVAL_RABBIT_CONTAINER_ID=$rabbit_id" "TASK_EVAL_RABBIT_PASSWORD=$RABBITMQ_DEFAULT_PASS")
fi
"${runtime_env[@]}" "$python" -m alembic -c "$run_dir/alembic.ini" upgrade head >"$run_dir/migrations.log" 2>&1
if [[ "$booking_capabilities_trial" == true ]]; then
    "${runtime_env[@]}" "$python" -m app.evaluation.task_eval.mysql_booking_capabilities --report-file "$single_report"
elif [[ "$single_development_trial" == true ]]; then
    if [[ "$book_real_model" == true ]]; then
        if [[ "$development_module" == "app.evaluation.task_eval.dev_sandbox_book_004" ]]; then
            if [[ "$context_diagnostic" == true ]]; then
                "${runtime_env[@]}" "$python" -m "$development_module" --report-file "$single_report" --real-model --case-profile "$context_profile" --allow-exposed-test-diagnostic "${real_model_args[@]}"
            else
                "${runtime_env[@]}" "$python" -m "$development_module" --report-file "$single_report" --real-model --case-profile "$context_profile" "${real_model_args[@]}"
            fi
        else
            "${runtime_env[@]}" "$python" -m "$development_module" --report-file "$single_report" --real-model "${real_model_args[@]}"
        fi
    else
        "${runtime_env[@]}" "$python" -m "$development_module" --report-file "$single_report"
    fi
else
    "${runtime_env[@]}" "$python" -m app.evaluation.task_eval.cross_service_booking "$@"
fi
