from argparse import Namespace

import httpx
import pytest

from scripts.gateway_customer_service_role_live_check import (
    CustomerServiceRoleLiveCheckError,
    RoleFixture,
    RoleProbe,
    _is_loopback_url,
    _run_probe,
    build_probes,
    validate_args,
)


def _args(**changes: object) -> Namespace:
    values = {
        "gateway_url": "http://127.0.0.1:8081",
        "internal_token": "internal-token",
        "context_signing_secret": "context-secret",
        "organization_id": "org-1",
        "student_id": "student-1",
        "coach_id": "coach-1",
        "admin_id": "admin-1",
        "timeout_seconds": 10.0,
    }
    values.update(changes)
    return Namespace(**values)


def test_role_live_check_only_accepts_loopback_gateway() -> None:
    assert _is_loopback_url("http://127.0.0.1:8081")
    assert _is_loopback_url("http://localhost:8081")
    assert not _is_loopback_url("https://gateway.example.com")


def test_role_live_check_requires_local_issuer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FITNESS_DEV_CONTEXT_ISSUER", raising=False)

    with pytest.raises(CustomerServiceRoleLiveCheckError, match="FITNESS_DEV_CONTEXT_ISSUER"):
        validate_args(_args())


def test_role_live_check_builds_explicit_permission_matrix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FITNESS_DEV_CONTEXT_ISSUER", "1")

    probes = build_probes(_args())

    assert [(probe.name, probe.expected_status) for probe in probes] == [
        ("student-own", 200),
        ("student-other", 403),
        ("coach-own", 200),
        ("coach-other", 403),
        ("admin-other", 200),
        ("student-outside-organization", 403),
    ]


def test_role_live_check_requires_all_three_role_subjects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FITNESS_DEV_CONTEXT_ISSUER", "1")

    with pytest.raises(CustomerServiceRoleLiveCheckError, match="coach_id"):
        validate_args(_args(coach_id=""))


@pytest.mark.asyncio
async def test_role_probe_uses_get_and_does_not_send_write_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FITNESS_DEV_CONTEXT_ISSUER", "1")
    seen: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["request_id"] = request.headers.get("X-Request-ID")
        seen["internal_token"] = request.headers.get("X-Internal-Service-Token")
        return httpx.Response(200, json=[])

    args = _args()
    probe = RoleProbe(
        "student-own",
        RoleFixture("student-1", "student-1", "STUDENT"),
        "student-1",
        "org-1",
        200,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await _run_probe(client, args, probe)

    assert seen["method"] == "GET"
    assert seen["path"] == "/internal/agent-tools/v1/customer-service/tickets"
    assert str(seen["request_id"]).startswith("customer-service-role-live-check-")
    assert seen["internal_token"] == "internal-token"


@pytest.mark.asyncio
async def test_role_probe_rejects_unexpected_status(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FITNESS_DEV_CONTEXT_ISSUER", "1")

    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    probe = RoleProbe(
        "student-other",
        RoleFixture("student-1", "student-1", "STUDENT"),
        "coach-1",
        "org-1",
        403,
    )
    with pytest.raises(CustomerServiceRoleLiveCheckError, match="预期 HTTP 403"):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await _run_probe(client, _args(), probe)
