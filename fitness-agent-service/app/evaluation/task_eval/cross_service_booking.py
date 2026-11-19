"""Isolated real Gateway/Booking HTTP experiments with synthetic inputs.

The entry point accepts only services launched by the disposable local harness.
Scripted, mock and opt-in real model results remain distinct and do not establish
complete task success or independent database transaction audit coverage.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
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
from app.agent.routing import HybridIntentRouter
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
from .booking_contracts import matches_frozen_case
from .booking_duplicate_event import run_duplicate_event_probe
from .booking_followup_probe import FOLLOWUP, FollowupModel
from .booking_model_boundary import PAYLOAD, PROMPT, OneBookingModel, expected_payload
from .booking_rabbit_delivery import BrokerFixture
from .booking_signed_identity import _identity
from .budget_store import DurableBudget
from .dataset import load_dataset
from .mysql_booking_observer import read_created_event, snapshot_booking
from .mysql_sandbox_preflight import verify_container
from .sandbox_preflight import PreflightError, _inspect, _verify_container


def _require(condition: bool, reason: str) -> None:
    """Keep acceptance checks active under optimized Python as well."""
    if not condition:
        raise ValueError(reason)


def _verify_database_deltas(
    before: dict[str, Any], after: dict[str, Any], decision: str
) -> dict[str, int]:
    """Compare isolated snapshots without claiming complete transaction history."""
    if decision not in {"APPROVE", "REJECT"} or not all(
        snapshot.get("snapshot_complete") is True and snapshot.get("witness_observed") is True
        for snapshot in (before, after)
    ):
        raise ValueError("INCOMPLETE_BUSINESS_SNAPSHOT")
    effect = 0 if decision == "REJECT" else 1
    expected = {
        "global_contracts": 0,
        "global_remaining_hours": -effect,
        "global_appointments": effect,
        "global_create_operations": effect,
        "global_reschedule_operations": 0,
        "global_cancel_operations": 0,
        "global_audits": effect,
        "global_consumptions": effect,
        "global_outbox": effect,
        "witness_contract_count": 0,
        "witness_remaining_hours": 0,
        "witness_appointments": 0,
    }
    try:
        old, new = before["facts"], after["facts"]
        if any(type(old[key]) is not int or type(new[key]) is not int for key in expected):
            raise ValueError("INCOMPLETE_BUSINESS_SNAPSHOT")
        if any(new[key] - old[key] != delta for key, delta in expected.items()):
            raise ValueError("BUSINESS_EFFECT_MISMATCH")
    except (KeyError, TypeError) as exc:
        raise ValueError("INCOMPLETE_BUSINESS_SNAPSHOT") from exc
    return expected


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


async def _run_same_session_followup(
    *, agent: AgentHTTP, supervisor: Supervisor, model: OneBookingModel, booking_id: str
) -> dict[str, Any]:
    """Ask within the existing session without exposing raw model output in reports."""
    followup = FollowupModel(
        model._credential,
        model.budget,
        booking_id,
        execute=True,
        confirmed_current_pricing=True,
    )
    previous_models, previous_router = supervisor.models, supervisor.intent_router
    response: dict[str, Any] | None = None
    try:
        supervisor.models = cast(Any, followup)
        supervisor.intent_router = HybridIntentRouter(cast(Any, followup))
        response = await agent.chat(FOLLOWUP)
    except Exception:  # noqa: BLE001 - provider and runtime exceptions can contain sensitive text
        response = None
    finally:
        supervisor.models, supervisor.intent_router = previous_models, previous_router
    result = followup.observation()
    answer = response.get("answer") if isinstance(response, dict) else None
    matched = (
        isinstance(answer, str)
        and followup.output_sha256 is not None
        and hashlib.sha256(answer.encode()).hexdigest() == followup.output_sha256
    )
    route_valid = isinstance(response, dict) and response.get("route") == "CUSTOMER_SERVICE"
    steps = response.get("tool_steps") if isinstance(response, dict) else None
    valid_steps = type(steps) is int and steps in (0, 1)
    if not matched or not route_valid or not valid_steps:
        result["component_status"] = "INCOMPLETE"
    result.update(
        supervisor_answer_hash_matches=matched,
        expected_route_observed=route_valid,
        tool_steps=steps if valid_steps else None,
    )
    return result


async def evaluate(
    *,
    model: OneBookingModel | None = None,
    model_decision: str = "REJECT",
    approve_synthetic_fixture: bool = False,
    same_session_followup: bool = False,
    broker: BrokerFixture | None = None,
) -> dict[str, Any]:
    if broker is not None and model is not None:
        raise ValueError("broker replay is restricted to the scripted component probe")
    if model is not None and (
        not isinstance(model, OneBookingModel)
        or model_decision not in {"REJECT", "APPROVE"}
        or (model_decision == "APPROVE" and approve_synthetic_fixture is not True)
    ):
        raise ValueError("only an explicitly approved synthetic fixture may be written")
    if type(same_session_followup) is not bool or (
        same_session_followup
        and (
            model is None
            or model._transport is not None
            or model_decision != "APPROVE"
            or approve_synthetic_fixture is not True
        )
    ):
        raise ValueError("followup requires the real model and an approved synthetic fixture")
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
    if broker is not None:
        if broker.run_id != run_id:
            raise PreflightError("BROKER_RUN_MISMATCH")
        broker.verified_port()
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
                _require(response["status"] == "CONFIRMATION_REQUIRED", "CONFIRMATION_MISSING")
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
                    # Confirmation itself uses the deterministic business result;
                    # the separately opted-in next user turn is evaluated below.
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
                        witness_organization_id="org-2",
                        witness_student_id="student-2",
                        witness_contract_id="contract-2",
                    )

                before = await asyncio.to_thread(snapshot)
                _require(
                    before["snapshot_complete"] is True
                    and before["audit_complete"] is False
                    and before["facts"]["appointments"] == 0
                    and before["facts"]["remaining_hours"] == 2
                    and before["facts"]["audit_count"] == 0
                    and before["witness_observed"] is True
                    and before["facts"]["witness_contract_count"] == 1
                    and before["facts"]["witness_remaining_hours"] == 5
                    and before["facts"]["witness_appointments"] == 0,
                    "INITIAL_BUSINESS_SNAPSHOT_MISMATCH",
                )
                stranger = _identity(context_secret, subject="other", org="org-1")
                denied = await http.post(
                    f"/api/v1/agent/confirmations/{cid}/decisions",
                    headers={"X-Agent-Context": stranger},
                    json={"decision": "APPROVE", "decision_request_id": uuid4().hex},
                )
                _require(denied.status_code == 404, "OTHER_SUBJECT_NOT_DENIED")
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
                    _require(unconfirmed.status_code in {401, 403}, "UNCONFIRMED_WRITE_NOT_DENIED")
                _require(
                    (await asyncio.to_thread(snapshot))["facts"] == before["facts"],
                    "BUSINESS_CHANGED_BEFORE_DECISION",
                )
                first = await agent.decide(cid, cast(Any, decision))
                repeat = await agent.decide(cid, cast(Any, decision))
                expected = (
                    ("REJECTED", "NOT_STARTED")
                    if decision == "REJECT"
                    else ("APPROVED", "SUCCEEDED")
                )
                _require(
                    (first["authorization_status"], first["execution_status"]) == expected
                    and (repeat["authorization_status"], repeat["execution_status"]) == expected,
                    "CONFIRMATION_STATE_MISMATCH",
                )
                after = await asyncio.to_thread(snapshot)
                facts = after["facts"]
                _require(
                    facts["appointments"] == (0 if decision == "REJECT" else 1)
                    and facts["remaining_hours"] == (2 if decision == "REJECT" else 1)
                    and facts["audit_count"] == (0 if decision == "REJECT" else 1)
                    and facts["consumption_count"] == (0 if decision == "REJECT" else 1)
                    and facts["linked_audit_and_consumption"] == (0 if decision == "REJECT" else 1),
                    "SCOPED_BUSINESS_SNAPSHOT_MISMATCH",
                )
                # Counts span the entire disposable business database. The
                # witness independently catches changes to another tenant's
                # seeded contract that aggregate counts alone could hide.
                expected_deltas = _verify_database_deltas(before, after, decision)
                _require(
                    facts["audit_actions"]
                    == (None if decision == "REJECT" else ["CREATE_APPOINTMENT"]),
                    "AUDIT_ACTION_MISMATCH",
                )
                events = [event.event_type for event in await repository.list_events(cid)]
                _require(bool(events) and events[0] == "CREATED", "CONFIRMATION_EVENTS_MISMATCH")
                if decision == "REJECT":
                    _require(events == ["CREATED", "REJECTED"], "CONFIRMATION_EVENTS_MISMATCH")
                else:
                    _require(
                        events.count("APPROVED")
                        == events.count("CONSUMED")
                        == events.count("EXECUTION_SUCCEEDED")
                        == 1,
                        "CONFIRMATION_EVENTS_MISMATCH",
                    )
                results[decision.lower()] = {
                    "status": "PASS",
                    "audit_complete": False,
                    "witness_unchanged": True,
                    "database_effect_deltas": expected_deltas,
                }
                if same_session_followup and model is not None:
                    try:
                        event = await asyncio.to_thread(
                            read_created_event,
                            run_id=run_id,
                            container_id=mysql_id,
                            organization_id="org-1",
                            student_id="student-1",
                            coach_id="coach-1",
                            request_id=record.request_id,
                        )
                        followup_observation = await _run_same_session_followup(
                            agent=agent,
                            supervisor=supervisor,
                            model=model,
                            booking_id=event.aggregate_id,
                        )
                    except Exception:  # noqa: BLE001 - do not expose untrusted inputs or local credentials
                        followup_observation = {
                            "component_status": "INCOMPLETE",
                            "task_status": "UNKNOWN",
                            "status": "FOLLOWUP_SETUP_OR_OBSERVER_ERROR",
                        }
                    try:
                        final = await asyncio.to_thread(snapshot)
                        _verify_database_deltas(after, final, "REJECT")
                        if final["facts"] != after["facts"]:
                            raise ValueError("UNEXPECTED_BUSINESS_CHANGE")
                        followup_observation["business_effects_after_followup"] = (
                            "NO_CHANGE_OBSERVED"
                        )
                    except Exception:  # noqa: BLE001 - report unresolved audit instead of model content
                        followup_observation["business_effects_after_followup"] = (
                            "UNVERIFIED_OR_CHANGED"
                        )
                        followup_observation["component_status"] = "INCOMPLETE"
                    results["same_session_followup"] = followup_observation
                if decision == "APPROVE" and model is None:
                    dataset_path = (
                        Path(__file__).resolve().parents[3] / "evals/task_eval/datasets/v1.jsonl"
                    )
                    dataset = load_dataset(dataset_path)
                    cases = [case for case in dataset.cases if case.case_id == "BOOK-030"]
                    _require(
                        len(cases) == 1 and matches_frozen_case(cases[0]),
                        "BOOK_030_CONTRACT_CHANGED",
                    )
                    event = await asyncio.to_thread(
                        read_created_event,
                        run_id=run_id,
                        container_id=mysql_id,
                        organization_id="org-1",
                        student_id="student-1",
                        coach_id="coach-1",
                        request_id=record.request_id,
                    )

                    async def mysql_snapshot() -> dict[str, Any]:
                        return await asyncio.to_thread(snapshot)

                    results["duplicate_event"] = await run_duplicate_event_probe(
                        database=database, event=event, mysql_snapshot=mysql_snapshot, broker=broker
                    )
        if model is not None:
            return {
                "status": "INCOMPLETE",
                "task_status": "UNKNOWN",
                "component_status": "OBSERVED",
                "origin": (
                    "model_booking_and_opt_in_same_session_followup_real_postgres_mysql"
                    if same_session_followup
                    else "one_model_tool_call_then_scripted_followup_real_postgres_mysql"
                ),
                "cases": results,
                "model_attempts": model.ledger.attempts,
                "model_observation": model.diagnostics(),
                "budget": model.budget.snapshot(),
                "provider_cap_verified": False,
                "complete_independent_database_audit": False,
                "human_review": "NOT_VERIFIED",
            }
        return {
            "status": "PASS",
            "scope": "cross_service_component_probe",
            "task_eval_status": "INCONCLUSIVE",
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
    budget = DurableBudget.open_existing(
        ledger_path, limit_nano=limit_nano, opening_nano=opening_nano
    )
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
    parser.add_argument("--real-broker", action="store_true")
    parser.add_argument("--approve-synthetic-fixture", action="store_true")
    parser.add_argument(
        "--same-session-followup",
        action="store_true",
        help="After real-model synthetic approval, run a bounded same-session read-only follow-up",
    )
    parser.add_argument("--confirm-current-pricing", action="store_true")
    parser.add_argument("--credential-file")
    parser.add_argument("--budget-file")
    parser.add_argument("--budget-limit-nano", type=int)
    parser.add_argument("--budget-opening-nano", type=int)
    parser.add_argument("--minimum-reserved-nano", type=int)
    parser.add_argument(
        "--report-file", help="Write this run's sanitized JSON result to a new file"
    )
    args = parser.parse_args()
    if args.real_broker and (args.real_model or args.mock_model):
        parser.error("real-broker is available only in the scripted component probe")
    if args.approve_synthetic_fixture and not (args.mock_model or args.real_model):
        parser.error("synthetic approval requires an explicit model mode")
    if args.same_session_followup and not (args.real_model and args.approve_synthetic_fixture):
        parser.error("same-session-followup requires real-model and synthetic approval")
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
    report_stream = None
    if args.report_file:
        try:
            # Reserve the path before any paid request or side effect.
            report_stream = Path(args.report_file).open("x", encoding="utf-8")  # noqa: SIM115 - held across the run to reserve the output path.
        except OSError:
            print(json.dumps({"status": "ERROR", "error_type": "REPORT_WRITE_FAILED"}))
            return 1
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
                same_session_followup=args.same_session_followup,
                broker=(
                    BrokerFixture(
                        run_id=os.environ.get("TASK_EVAL_RUN_ID", ""),
                        container_id=os.environ.get("TASK_EVAL_RABBIT_CONTAINER_ID", ""),
                        password=os.environ.get("TASK_EVAL_RABBIT_PASSWORD", ""),
                    )
                    if args.real_broker
                    else None
                ),
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
    encoded = json.dumps(result, ensure_ascii=False)
    if report_stream is not None:
        try:
            report_stream.write(encoded + "\n")
            report_stream.close()
        except OSError:
            report_stream.close()
            print(json.dumps({"status": "ERROR", "error_type": "REPORT_WRITE_FAILED"}))
            return 1
    print(encoded)
    if result["status"] == "PASS":
        return 0
    if result["status"] == "INCOMPLETE":
        return 3
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
