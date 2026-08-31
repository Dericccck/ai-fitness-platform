from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from prometheus_client import CollectorRegistry, generate_latest

from app.core.metrics import HttpMetrics, MetricsMiddleware


def build_test_app() -> tuple[FastAPI, HttpMetrics]:
    """为每个测试创建独立 Registry，避免指标名称在进程级注册表中冲突。"""

    metrics = HttpMetrics.create(
        service_name="fitness-agent-service",
        service_version="test",
        environment="test",
        registry=CollectorRegistry(),
    )
    test_app = FastAPI()
    test_app.add_middleware(MetricsMiddleware, metrics=metrics)

    @test_app.get("/users/{user_id}")
    async def user(user_id: str) -> dict[str, str]:
        return {"user_id": user_id}

    return test_app, metrics


async def test_metrics_use_route_template_instead_of_user_value() -> None:
    app, metrics = build_test_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/users/sensitive-user-123")

    exposition = generate_latest(metrics.registry).decode()
    assert response.status_code == 200
    assert 'route="/users/{user_id}"' in exposition
    assert "sensitive-user-123" not in exposition


async def test_metrics_group_unknown_paths_under_single_label() -> None:
    app, metrics = build_test_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/does-not-exist/with-an-id")

    exposition = generate_latest(metrics.registry).decode()
    assert response.status_code == 404
    assert 'route="unmatched"' in exposition
    assert "does-not-exist" not in exposition


def test_notification_delivery_metrics_use_only_low_cardinality_labels() -> None:
    _, metrics = build_test_app()
    metrics.record_notification_delivery_attempt("IN_APP", "SUCCEEDED")
    metrics.record_notification_delivery_attempt("IN_APP", "FINAL_FAILED")

    exposition = generate_latest(metrics.registry).decode()
    assert 'channel="IN_APP",status="SUCCEEDED"' in exposition
    assert 'channel="IN_APP",status="FINAL_FAILED"' in exposition
    assert "notification_id" not in exposition


def test_operations_metrics_use_fixed_event_label_without_identifiers() -> None:
    _, metrics = build_test_app()
    metrics.record_operations_query_event("RATE_LIMITED")
    metrics.record_operations_query_event("GATEWAY_TIMEOUT")

    exposition = generate_latest(metrics.registry).decode()
    assert 'event="RATE_LIMITED"' in exposition
    assert 'event="GATEWAY_TIMEOUT"' in exposition
    assert "organization_id" not in exposition
    assert "request_id" not in exposition


def test_trulens_export_metrics_are_low_cardinality() -> None:
    _, metrics = build_test_app()
    metrics.record_trulens_export("SUCCEEDED", 3)
    metrics.record_trulens_export("FAILED", 1)

    exposition = generate_latest(metrics.registry).decode()
    assert 'status="SUCCEEDED"' in exposition
    assert 'status="FAILED"' in exposition
    assert "trace_id" not in exposition
