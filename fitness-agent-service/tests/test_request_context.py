from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.middleware.request_context import RequestContextMiddleware, normalize_context_id


def build_test_app() -> FastAPI:
    test_app = FastAPI()
    test_app.add_middleware(RequestContextMiddleware)

    @test_app.get("/probe")
    async def probe() -> dict[str, str]:
        return {"status": "ok"}

    return test_app


async def test_preserves_valid_request_and_trace_ids() -> None:
    transport = ASGITransport(app=build_test_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/probe",
            headers={"X-Request-ID": "request-123", "X-Trace-ID": "trace-456"},
        )

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "request-123"
    assert response.headers["X-Trace-ID"] == "trace-456"


async def test_replaces_untrusted_context_ids() -> None:
    transport = ASGITransport(app=build_test_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/probe",
            headers={"X-Request-ID": "invalid id value"},
        )

    generated_request_id = response.headers["X-Request-ID"]
    assert generated_request_id != "invalid id value"
    assert normalize_context_id(generated_request_id) == generated_request_id
    assert response.headers["X-Trace-ID"] == generated_request_id
