"""Isolated real Gateway/Booking HTTP experiments with synthetic inputs.

The entry point accepts only services launched by the disposable local harness.
Scripted, mock and opt-in real model results remain distinct and do not establish
complete task success or independent database transaction audit coverage.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import secrets
import stat
import subprocess
import tempfile
import traceback
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy.engine import make_url

from app.agent.fitness_tools import build_fitness_tool_registry
from app.agent.supervisor import Supervisor
from app.api.routes.agent import router as agent_router
from app.api.routes.confirmations import router as confirmation_router
from app.confirmation.cipher import AesGcmPayloadCipher
from app.confirmation.repository import ConfirmationRepository
from app.confirmation.service import ConfirmationService
from app.confirmation.token import ConfirmationTokenIssuer
from app.core.config import Settings
from app.infrastructure.agent_context import AgentContextVerifier
from app.infrastructure.database import Database
from app.infrastructure.gateway_client import GatewayClient

from . import booking_local
from .agent_http import AgentHTTP
from .booking_model_boundary import PAYLOAD, PROMPT, OneBookingModel, expected_payload
from .booking_signed_identity import _identity
from .budget_store import DurableBudget
from .mysql_booking_observer import snapshot_booking
from .mysql_sandbox_preflight import verify_container
from .sandbox_preflight import PreflightError, _inspect, _verify_container


def _environment() -> tuple[Settings, str, str, str, str]:
    run_id = os.environ.get("TASK_EVAL_RUN_ID", "")
    mysql_id = os.environ.get("TASK_EVAL_MYSQL_CONTAINER_ID", "")
    pg_id = os.environ.get("TASK_EVAL_POSTGRES_CONTAINER_ID", "")
    pg_port = _verify_container(_inspect(pg_id), run_id=run_id, kind="pg")
    verify_container(_inspect(mysql_id), run_id=run_id)
    settings: Settings = cast(Any, Settings)(_env_file=None)
    url = make_url(settings.database_url)
    address = urlsplit(settings.gateway_base_url)
    pid = os.environ.get("TASK_EVAL_GATEWAY_PID", "")
    launcher_pid = os.environ.get("TASK_EVAL_LAUNCHER_PID", "")
    if (
        url.drivername != "postgresql+asyncpg"
        or url.host != "127.0.0.1"
        or url.port != pg_port
        or url.username != "fitness_eval"
        or url.database != f"fitness_cross_{run_id}"
        or not url.password
        or url.query
        or address.scheme != "http"
        or address.hostname != "127.0.0.1"
        or not address.port
        or address.path not in ("", "/")
        or address.query
        or address.fragment
        or address.username is not None
        or not re.fullmatch(r"[1-9][0-9]*", pid)
        or not re.fullmatch(r"[1-9][0-9]*", launcher_pid)
        or int(launcher_pid) != os.getppid()
    ):
        raise PreflightError("UNSAFE_SERVICE_ENDPOINT")
    try:
        parent = subprocess.run(
            ["ps", "-o", "ppid=", "-p", pid],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        listener = subprocess.run(
            ["lsof", "-nP", "-a", "-p", pid, f"-iTCP:{address.port}", "-sTCP:LISTEN", "-F", "p"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise PreflightError("GATEWAY_LISTENER_NOT_OWNED") from exc
    if parent.stdout.strip() != launcher_pid or f"p{pid}" not in listener.stdout.splitlines():
        raise PreflightError("GATEWAY_LISTENER_NOT_OWNED")
    context_secret = os.environ.get("TASK_EVAL_CONTEXT_SECRET", "")
    token_secret = os.environ.get("TASK_EVAL_CONFIRMATION_SECRET", "")
    if (
        min(
            len(context_secret),
            len(token_secret),
            len(settings.gateway_http_execution_service_token),
        )
        < 32
    ):
        raise PreflightError("TEST_CREDENTIALS_MISSING")
    return settings, run_id, mysql_id, context_secret, token_secret


async def evaluate(
    *,
    model: OneBookingModel | None = None,
    model_decision: str = "REJECT",
    approve_synthetic_fixture: bool = False,
) -> dict[str, Any]:
    if not __debug__:
        raise RuntimeError("ASSERTIONS_REQUIRED")
    if model is not None and (
        not isinstance(model, OneBookingModel)
        or model_decision not in {"REJECT", "APPROVE"}
        or (model_decision == "APPROVE" and approve_synthetic_fixture is not True)
    ):
        raise ValueError("only an explicitly approved synthetic fixture may be written")
    # The model-bound case is fixed; the pre-existing scripted-only case may
    # choose a future date dynamically because it never leaves the machine.
    start = (
        datetime.fromisoformat(PAYLOAD["start_time"])
        if model is not None
        else (datetime.now(UTC) + timedelta(days=7)).replace(minute=0, second=0, microsecond=0)
    )
    if model is not None and start <= datetime.now(UTC) + timedelta(days=1):
        raise ValueError("synthetic fixture date needs review")
    settings, run_id, mysql_id, context_secret, token_secret = _environment()
    database = Database(settings)
    repository = ConfirmationRepository(database)
    gateway = GatewayClient(settings)
    registry = build_fitness_tool_registry(gateway)
    confirmation = ConfirmationService(
        repository,
        registry,
        gateway,
        AesGcmPayloadCipher(key=secrets.token_bytes(32), key_version="eval"),
        ConfirmationTokenIssuer(secret=token_secret, signing_key_id="eval"),
    )
    previous_payload = booking_local.PAYLOAD
    booking_local.PAYLOAD = (
        dict(PAYLOAD)
        if model is not None
        else {
            "organization_id": "org-1",
            "student_id": "student-1",
            "contract_id": "contract-1",
            "coach_id": "coach-1",
            "course_id": "course-1",
            "start_time": start.isoformat(),
            "end_time": (start + timedelta(hours=1)).isoformat(),
        }
    )
    results: dict[str, Any] = {}
    try:
        for decision in (model_decision,) if model is not None else ("REJECT", "APPROVE"):
            app = FastAPI()
            app.state.context_verifier = AgentContextVerifier(context_secret, signing_key_id="eval")
            app.state.confirmation_service = confirmation
            supervisor = Supervisor(
                cast(Any, model if model is not None else booking_local.Model()),
                registry,
                checkpointer=InMemorySaver(),
                confirmation_service=confirmation,
                intent_router=cast(Any, booking_local.Router()),
            )
            app.state.supervisor = supervisor
            app.include_router(agent_router)
            app.include_router(confirmation_router)
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://agent.local"
            ) as http:
                identity = _identity(context_secret, subject="student-1", org="org-1")
                agent = AgentHTTP(http, identity)
                response = await agent.chat(PROMPT if model is not None else "请帮我预约课程")
                assert response["status"] == "CONFIRMATION_REQUIRED"
                cid = response["confirmation_id"]
                record = await repository.get_for_subject(cid, "student-1", ["org-1"])
                if model is not None:
                    pending_payload = json.loads(
                        confirmation.cipher.decrypt(
                            record.payload_ciphertext,
                            associated_data=record.payload_hash,
                            key_version=record.payload_key_version,
                        )
                    )
                    if not expected_payload(pending_payload):
                        raise ValueError("confirmed payload does not match synthetic fixture")
                    # Never send provider a second turn containing business results.
                    # Any post-decision text is explicitly scripted and is not evaluated.
                    followup = booking_local.Model()
                    followup.calls = 1
                    supervisor.models = cast(Any, followup)

                def snapshot(request_id: str = record.request_id) -> dict[str, Any]:
                    return snapshot_booking(
                        run_id=run_id,
                        container_id=mysql_id,
                        organization_id="org-1",
                        student_id="student-1",
                        contract_id="contract-1",
                        request_id=request_id,
                    )

                before = await asyncio.to_thread(snapshot)
                assert before["snapshot_complete"] and not before["audit_complete"]
                assert before["facts"]["appointments"] == 0
                assert before["facts"]["remaining_hours"] == 2
                assert before["facts"]["audit_count"] == 0
                stranger = _identity(context_secret, subject="other", org="org-1")
                denied = await http.post(
                    f"/api/v1/agent/confirmations/{cid}/decisions",
                    headers={"X-Agent-Context": stranger},
                    json={"decision": "APPROVE", "decision_request_id": uuid4().hex},
                )
                assert denied.status_code == 404
                async with AsyncClient(
                    base_url=settings.gateway_base_url,
                    trust_env=False,
                    follow_redirects=False,
                    timeout=5,
                ) as direct:
                    unconfirmed = await direct.post(
                        "/internal/agent-tools/v1/appointments",
                        headers={
                            "X-Agent-Context": identity,
                            "X-Internal-Service-Token": settings.gateway_http_execution_service_token,
                            "X-Request-ID": uuid4().hex,
                            "X-Confirmation-Token": "invalid",
                        },
                        json={
                            "organizationId": "org-1",
                            "studentId": "student-1",
                            "contractId": "contract-1",
                            "coachId": "coach-1",
                            "courseId": "course-1",
                            "startTime": start.astimezone(UTC).isoformat().replace("+00:00", "Z"),
                            "endTime": (start + timedelta(hours=1))
                            .astimezone(UTC)
                            .isoformat()
                            .replace("+00:00", "Z"),
                        },
                    )
                    assert unconfirmed.status_code in {401, 403}
                assert (await asyncio.to_thread(snapshot))["facts"] == before["facts"]
                first = await agent.decide(cid, cast(Any, decision))
                repeat = await agent.decide(cid, cast(Any, decision))
                expected = (
                    ("REJECTED", "NOT_STARTED")
                    if decision == "REJECT"
                    else ("APPROVED", "SUCCEEDED")
                )
                assert (first["authorization_status"], first["execution_status"]) == expected
                assert (repeat["authorization_status"], repeat["execution_status"]) == expected
                after = await asyncio.to_thread(snapshot)
                facts = after["facts"]
                assert facts["appointments"] == (0 if decision == "REJECT" else 1)
                assert facts["remaining_hours"] == (2 if decision == "REJECT" else 1)
                assert facts["audit_count"] == (0 if decision == "REJECT" else 1)
                assert facts["consumption_count"] == (0 if decision == "REJECT" else 1)
                assert facts["linked_audit_and_consumption"] == (0 if decision == "REJECT" else 1)
                events = [event.event_type for event in await repository.list_events(cid)]
                assert events[0] == "CREATED"
                if decision == "REJECT":
                    assert events == ["CREATED", "REJECTED"]
                else:
                    assert (
                        events.count("APPROVED")
                        == events.count("CONSUMED")
                        == events.count("EXECUTION_SUCCEEDED")
                        == 1
                    )
                results[decision.lower()] = {"status": "PASS", "audit_complete": False}
        if model is not None:
            return {
                "status": "INCOMPLETE",
                "task_status": "UNKNOWN",
                "component_status": "OBSERVED",
                "origin": "one_model_tool_call_then_scripted_followup_real_postgres_mysql",
                "cases": results,
                "model_attempts": model.ledger.attempts,
                "budget": model.budget.snapshot(),
                "provider_cap_verified": False,
                "complete_independent_database_audit": False,
                "human_review": "NOT_VERIFIED",
            }
        return {
            "status": "PASS",
            "origin": "scripted_model_asgi_agent_java_http_real_postgres_mysql",
            "cases": results,
        }
    finally:
        booking_local.PAYLOAD = previous_payload
        await gateway.close()
        await database.close()


def _mock_model(directory: str) -> OneBookingModel:
    """Never leaves the process; used to exercise the model-integrated chain."""
    ledger = DurableBudget(
        os.path.join(directory, "mock-budget.sqlite"),
        limit_nano=10_000_000_000,
        opening_nano=46_080_000,
    )
    ledger.reserve("prior-synthetic-reservation", 102_400_000)
    ledger.finish("prior-synthetic-reservation", usage_known=True)

    def reply(request: httpx.Request) -> httpx.Response:
        if str(request.url) != "https://api.deepseek.com/v1/chat/completions":
            raise ValueError("unexpected model URL")
        args = {
            "studentId": "student-1",
            "contractId": "contract-1",
            "coachId": "coach-1",
            "courseId": "course-1",
            "startTime": "2030-01-07T09:00:00+08:00",
            "endTime": "2030-01-07T10:00:00+08:00",
        }
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "mock-call-1",
                                    "type": "function",
                                    "function": {
                                        "name": "fitness_booking_create_v1",
                                        "arguments": json.dumps(args),
                                    },
                                }
                            ],
                        },
                    }
                ],
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 80,
                    "total_tokens": 180,
                    "prompt_cache_hit_tokens": 0,
                    "prompt_cache_miss_tokens": 100,
                },
            },
        )

    return OneBookingModel(
        "synthetic-mock-credential",
        ledger,
        transport=httpx.MockTransport(reply),
        execute=True,
        confirmed_current_pricing=True,
    )


def _real_model(
    *,
    credential_file: str,
    budget_file: str,
    limit_nano: int,
    opening_nano: int,
    minimum_reserved_nano: int,
    confirmed_current_pricing: bool,
) -> OneBookingModel:
    """Load an existing ledger and secret without printing or forwarding them."""
    key_path, ledger_path = Path(credential_file), Path(budget_file)
    if (
        not confirmed_current_pricing
        or key_path.is_symlink()
        or not key_path.is_file()
        or stat.S_IMODE(key_path.stat().st_mode) & 0o077
        or ledger_path.is_symlink()
        or not ledger_path.is_file()
        or minimum_reserved_nano < 0
    ):
        raise ValueError("credential, existing budget, or price verification unavailable")
    budget = DurableBudget(ledger_path, limit_nano=limit_nano, opening_nano=opening_nano)
    snapshot = budget.snapshot()
    if (
        snapshot["reserved_nano"] < minimum_reserved_nano
        or snapshot["frozen"]
        or snapshot["pending"]
    ):
        raise ValueError("budget history unavailable or unresolved")
    key = key_path.read_text(encoding="utf-8").strip()
    return OneBookingModel(key, budget, execute=True, confirmed_current_pricing=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    models = parser.add_mutually_exclusive_group()
    models.add_argument("--mock-model", action="store_true")
    models.add_argument("--real-model", action="store_true")
    parser.add_argument("--approve-synthetic-fixture", action="store_true")
    parser.add_argument("--confirm-current-pricing", action="store_true")
    parser.add_argument("--credential-file")
    parser.add_argument("--budget-file")
    parser.add_argument("--budget-limit-nano", type=int)
    parser.add_argument("--budget-opening-nano", type=int)
    parser.add_argument("--minimum-reserved-nano", type=int)
    args = parser.parse_args()
    if args.approve_synthetic_fixture and not (args.mock_model or args.real_model):
        parser.error("synthetic approval requires an explicit model mode")
    if args.real_model and (
        not args.credential_file
        or not args.budget_file
        or args.budget_limit_nano is None
        or args.budget_opening_nano is None
        or args.minimum_reserved_nano is None
        or not args.confirm_current_pricing
    ):
        parser.error(
            "real-model requires credential, existing budget and confirmed current pricing"
        )
    model: OneBookingModel | None = None
    directory: tempfile.TemporaryDirectory[str] | None = None
    try:
        directory = tempfile.TemporaryDirectory(prefix="booking-mock-budget-")
        model = (
            _mock_model(directory.name)
            if args.mock_model
            else _real_model(
                credential_file=args.credential_file,
                budget_file=args.budget_file,
                limit_nano=args.budget_limit_nano,
                opening_nano=args.budget_opening_nano,
                minimum_reserved_nano=args.minimum_reserved_nano,
                confirmed_current_pricing=args.confirm_current_pricing,
            )
            if args.real_model
            else None
        )
        result = asyncio.run(
            evaluate(
                model=model,
                model_decision="APPROVE" if args.approve_synthetic_fixture else "REJECT",
                approve_synthetic_fixture=args.approve_synthetic_fixture,
            )
        )
    except Exception as exc:  # noqa: BLE001 - avoid exposing secret-bearing network errors
        frames = traceback.extract_tb(exc.__traceback__)
        locations = [f"{f.name}:{f.lineno}" for f in frames if f.filename == __file__]
        error_type = type(exc).__name__
        if error_type not in {
            "AssertionError",
            "BookingModelProbeError",
            "BudgetError",
            "PreflightError",
            "ValueError",
        }:
            error_type = "OTHER_ERROR"
        result = {"status": "ERROR", "error_type": error_type, "locations": locations}
        if model is not None:
            result["model_observation"] = model.diagnostics()
    finally:
        if directory is not None:
            directory.cleanup()
    print(json.dumps(result))
    if result["status"] == "PASS":
        return 0
    if result["status"] == "INCOMPLETE":
        return 3
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
