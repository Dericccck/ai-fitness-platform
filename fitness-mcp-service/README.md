# Fitness MCP service

Private first-party MCP adapter, official `mcp==2.3.0`, Python 3.11. Java remains the final authority for
user/resource authorization, confirmation signature/body binding, transactions and durable results.
The adapter never signs an identity or confirmation and never writes the business database.

From this directory: `uv sync --extra dev`, configure `MCP_*` environment variables, then
`uv run uvicorn app.main:app --host 0.0.0.0 --port 8001`.
Build from the repository root: `docker build -f fitness-mcp-service/Dockerfile -t fitness-mcp-service .`.

`POST /mcp` is real Streamable HTTP with request-scoped SSE and protocol `2026-07-28` only. The low-level
SDK Server declares and validates the fixed catalogue; the app lifespan runs its session manager.
Legacy requests are rejected, and no MCP session or sticky routing is used. There is no sampling,
roots, remote confirmation, registry auto-import or arbitrary URL tool.

Every MCP request needs a Bearer service token. Configure only its SHA-256 in
`MCP_SERVICE_CREDENTIALS_JSON`, bound to exact `MCP_RESOURCE_URL`, a principal, read/write scopes and
expiry. Empty configuration disables `/mcp` and readiness. This is a private opaque-token integration,
not third-party OAuth. Separate Java service credentials are injected on outbound requests; MCP access
tokens are never forwarded to Java.

Every tool call additionally needs trusted transport headers `X-Agent-Context`, `X-Request-ID`,
`X-Tool-Contract-Version: 1`; optional `X-Trace-ID`, `X-Tool-Deadline` (finite Unix seconds). Confirmed
writes require `X-Confirmation-Token`. None are tool arguments. Identity verification defaults to RS256
with a configured public-key ring or fixed JWKS URL. Production JWKS must be HTTPS. HS256 requires an
explicit development/test-only opt-in. Unknown keys/algorithms fail closed. Do not install signing keys
in the adapter.

Configure exact Host allowlists (only `:*` port wildcard is accepted) and origins. If TLS ends at a
proxy, configure uvicorn's trusted forwarded proxy addresses. Disable reverse-proxy response buffering
and set its idle timeout above the controlled call deadline. The MCP client must keep SSE enabled to
propagate cancellation to the async HTTP handler.

The server caps deadlines independently and performs one outbound attempt. Writes with a lost response,
5xx/408/429, malformed/oversized output or transport uncertainty return `isError` + `UNKNOWN`; the Agent
must reconcile the original operation, never switch transport or create a new operation automatically.
Cancellation stops waiting; it does not roll back Java. Errors never echo credentials, raw arguments,
upstream payloads or stack traces.

Success `structuredContent` is `{schema_version:'1', data:<typed DTO or list>, error_code:null,
execution_state:'SUCCEEDED'}`. Errors set `isError: true`, `data:null`, stable `error_code` and
`execution_state: REJECTED | NOT_SENT | UNKNOWN`. Only valid, explicit success can finish a confirmation.

`/health/live` reports process liveness, `/health/ready` configuration/startup readiness without an
upstream request, `/health` aliases readiness. `/metrics` exports per-fixed-tool success/rejected/unknown/
protocol-error/cancelled counters, latency and inflight; it contains no user, tenant or request labels.
These endpoints are for the private deployment network. Gateway availability is observed by calls;
readiness does not make a business-health claim.

Run `uv run --extra dev pytest -q tests ../packages/fitness-tool-contracts/tests`. Integration tests start
a real TCP uvicorn server and official SDK clients; Java transport is a controlled stub. Production
release still requires real Java permission/confirmation/transaction/reconciliation and failure tests.
