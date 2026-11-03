# Isolated MySQL acceptance

From the repository root, with Docker, Maven and `openssl` available:

```sh
bash fitness-core-gateway/scripts/isolated-mysql-acceptance.sh
```

The script creates a new `mysql:8.0` container with a random name, password, and
localhost-only port. It accepts no application database URL and never selects an
existing container. Data lives in a temporary filesystem; the exit trap removes
only the container ID returned by this run. Logs remain in the printed temporary
directory.

The 27 methods cover:

- Gateway's two existing MySQL JDBC integration tests against a minimal legacy
  column fixture with a synthetic organization and course.
- Booking's two existing real-transaction integration tests, including concurrent
  slot contention, retry, JTI rollback, rescheduling, cancellation, outbox and UTF-8.
- All nine tools' operation-evidence SQL, absent/partial evidence, actor/org/
  confirmation/hash mismatch and transaction rollback across the three domains.
- Stable operation/confirmation/org/actor IDs starting with punctuation are
  preserved; slash, percent, standalone `.`/`..`, empty and oversized IDs are rejected.
- All three domains' operation ID binding SQL and rejection of changed payload,
  tool, action, actor, organization, confirmation or resource.
- The training operation unique-key migration: reject cross-tool reuse and abort
  on historical duplicates without deleting evidence or recording migration success.

The script also applies all checked-in training and customer-service migration SQL
on fresh random databases; Booking's existing schema initializer runs during its
integration test. The new evidence/binding tests use minimal column fixtures to
isolate these query invariants, so this does not certify a live legacy schema or
external RabbitMQ delivery.

New JDBC tests remain H2-based by default. Only the script sets
`MCP_MYSQL_IT_ENABLED=true`, the random `MCP_MYSQL_IT_PORT`, and
`MCP_MYSQL_IT_PASSWORD`; each test then creates and drops its own random database.
Do not set these variables to an application server.

Before applying `V20261027_010` to an existing training database, run:

```sql
SELECT request_id, COUNT(*)
FROM training_confirmation_consumption
GROUP BY request_id
HAVING COUNT(*) > 1;
```

Any returned rows require investigation of the original business/audit facts.
Do not delete confirmation evidence to force the migration through. The migration
adds a unique key and therefore intentionally fails when duplicates exist.
