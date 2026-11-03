#!/usr/bin/env bash
# Runs only against the container created here. Requires Docker, openssl, and Maven on PATH.
set -euo pipefail
repo_root="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$repo_root"
run_id="$(openssl rand -hex 8)"
container_name="codex-mcp-java-it-$run_id"
legacy_database="codex_mcp_legacy_$run_id"
container_id=""
log_dir="$(mktemp -d "${TMPDIR:-/tmp}/codex-mcp-mysql.XXXXXX")"
export MYSQL_ROOT_PASSWORD="$(openssl rand -hex 24)"
cleanup() {
    local status=$?
    trap - EXIT
    if [[ -n "$container_id" ]]; then
        # Delete only the immutable ID returned by this invocation of docker run.
        docker rm -f -v "$container_id" >/dev/null
        printf 'Removed isolated MySQL container %s\n' "$container_name"
    fi
    printf 'Acceptance logs: %s\n' "$log_dir"
    exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT TERM
container_id="$(docker run -d --rm --name "$container_name" \
    --label "codex.acceptance=mcp-java-$run_id" \
    --tmpfs /var/lib/mysql -p 127.0.0.1::3306 \
    -e MYSQL_ROOT_PASSWORD -e MYSQL_ROOT_HOST=% mysql:8.0 \
    --character-set-server=utf8mb4 --collation-server=utf8mb4_bin)"
ready=false
for attempt in $(seq 1 90); do
    if docker exec "$container_id" sh -c 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql -h127.0.0.1 -uroot -e "SELECT 1"' > /dev/null 2>&1; then
        ready=true
        break
    fi
    sleep 1
done
if [[ "$ready" != true ]]; then
    docker logs "$container_id" > "$log_dir/mysql-startup.log" 2>&1
    exit 1
fi
port="$(docker port "$container_id" 3306/tcp | sed -n 's/^127\.0\.0\.1://p')"
[[ "$port" =~ ^[0-9]+$ ]]
mysql_sql() {
    docker exec -i "$container_id" sh -c 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql --default-character-set=utf8mb4 -uroot "$@"' sh "$@"
}
printf 'CREATE DATABASE %s CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;\n' "$legacy_database" | mysql_sql
mysql_sql "$legacy_database" < fitness-core-gateway/src/test/resources/mcp/mysql-legacy-fixture.sql

# Apply all real training/customer migrations on an empty isolated database too.
# BookingDatabaseSchema is exercised by the existing BookingRepositoryIntegrationTest.
for domain in training customer; do
    database="codex_mcp_${domain}_$run_id"
    printf 'CREATE DATABASE %s CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;\n' "$database" | mysql_sql
    if [[ "$domain" == training ]]; then module=fitness-training-service; else module=fitness-customer-service; fi
    for migration in "$module"/src/main/resources/db/migration/*.sql; do
        mysql_sql "$database" < "$migration" >> "$log_dir/$domain-migrations.log" 2>&1
    done
done
export MCP_MYSQL_IT_ENABLED=true MCP_MYSQL_IT_PORT="$port" MCP_MYSQL_IT_PASSWORD="$MYSQL_ROOT_PASSWORD"
export GATEWAY_IT_ENABLED=true GATEWAY_IT_DB_USERNAME=root GATEWAY_IT_DB_PASSWORD="$MYSQL_ROOT_PASSWORD"
export GATEWAY_IT_ORGANIZATION_ID=mysql-it-org
export GATEWAY_IT_DB_URL="jdbc:mysql://127.0.0.1:$port/$legacy_database?useSSL=false&allowPublicKeyRetrieval=true&serverTimezone=UTC&characterEncoding=UTF-8"
export BOOKING_IT_ENABLED=true BOOKING_IT_DB_URL="$GATEWAY_IT_DB_URL" BOOKING_IT_DB_USERNAME=root BOOKING_IT_DB_PASSWORD="$MYSQL_ROOT_PASSWORD"
for module in fitness-core-gateway fitness-booking-service fitness-training-service fitness-customer-service; do
    case "$module" in
        fitness-core-gateway) tests='JdbcFitnessReadRepositoryIntegrationTest,JdbcOperationsReadRepositoryIntegrationTest' ;;
        fitness-booking-service) tests='BookingRepositoryIntegrationTest,ToolOperationQueryServiceTest,OperationBindingTest' ;;
        fitness-training-service) tests='ToolOperationQueryServiceTest,OperationBindingTest,OperationUniquenessMigrationTest' ;;
        fitness-customer-service) tests='ToolOperationQueryServiceTest,OperationBindingTest' ;;
    esac
    printf 'Running %s on isolated MySQL 8 at 127.0.0.1:%s\n' "$module" "$port"
    if ! mvn -q -f "$module/pom.xml" "-Dtest=$tests" test > "$log_dir/$module.log" 2>&1; then
        tail -n 60 "$log_dir/$module.log"
        exit 1
    fi
    printf 'PASS %s\n' "$module"
done
# New JDBC tests must have removed every database they created.
remaining="$(printf "SELECT COUNT(*) FROM information_schema.schemata WHERE schema_name LIKE 'codex_mcp_it_%%';" | mysql_sql -N -B)"
[[ "$remaining" == 0 ]]
printf 'PASS selected MySQL test methods, real migrations, and per-test database cleanup\n'
