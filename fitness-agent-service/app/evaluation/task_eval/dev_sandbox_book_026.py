"""BOOK-026 draft fixture in launcher-owned disposable services; never release evidence."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import secrets
import stat
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from langgraph.checkpoint.memory import InMemorySaver

from app.agent.fitness_tools import BookingCreateToolInput, build_fitness_tool_registry
from app.agent.supervisor import Supervisor
from app.api.routes.agent import router as agent_router
from app.api.routes.confirmations import router as confirmation_router
from app.confirmation.cipher import AesGcmPayloadCipher
from app.confirmation.repository import ConfirmationRepository
from app.confirmation.service import ConfirmationService
from app.confirmation.token import ConfirmationTokenIssuer
from app.infrastructure.agent_context import AgentContextVerifier
from app.infrastructure.database import Database
from app.infrastructure.gateway_client import GatewayClient

from .agent_http import AgentHTTP
from .audit_observation import committed_before_decision, invalidate_failed_audit
from .booking_dev_026 import load as load_book_026
from .booking_local import Router
from .booking_model_boundary import OneBookingModel
from .booking_signed_identity import _identity
from .budget_store import DurableBudget
from .cross_service_booking import _environment, _verify_database_deltas
from .dataset import load_dataset
from .dev_booking_observer import read_booking_details
from .dev_sandbox_booking import _FixedModel, _source_identity
from .graders.rules import grade_rule
from .mysql_booking_observer import snapshot_booking
from .mysql_restricted_audit import observe as observe_restricted_audit
from .mysql_restricted_audit import verify as verify_restricted_audit
from .step_kernel import StepState, execute_steps, prepare_steps

_DATASET = Path(__file__).resolve().parents[3] / "evals/task_eval/datasets/v1.jsonl"
_DATASET_SHA = "1d187f5abca4dab754db29059f9ff95a7897719fde6ce88de6c92448d25ab446"


def _load() -> tuple[Any, dict[str, Any]]:
    if hashlib.sha256(_DATASET.read_bytes()).hexdigest() != _DATASET_SHA:
        raise ValueError("DEV_DATASET_CHANGED")
    dataset = load_dataset(_DATASET, check_quotas=False)
    cases = [case for case in dataset.cases if case.case_id == "BOOK-026"]
    if len(cases) != 1:
        raise ValueError("DEV_CASE_MISMATCH")
    case = cases[0]
    fixture = load_book_026(case)
    # The frozen digest and case contract protect the full step list; explicitly
    # refuse accidental expansion of this driver's supported actions as well.
    if [step.type for step in case.steps] != [
        "chat",
        "assert",
        "inject_fault",
        "decision",
        "decision",
        "await_terminal",
        "assert",
    ]:
        raise ValueError("DEV_CASE_MISMATCH")
    return case, fixture


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


async def _drive(
    case: Any,
    fixture: dict[str, Any],
    agent: Any,
    repository: Any,
    confirmation: Any,
    snapshot: Any,
    details: Any,
    audit_observe: Any,
) -> dict[str, Any]:
    requests = prepare_steps(case)
    initial = await asyncio.to_thread(snapshot, "dev-preflight")
    if not initial.get("snapshot_complete") or not initial.get("witness_observed"):
        raise ValueError("INITIAL_SNAPSHOT_INCOMPLETE")
    facts = initial["facts"]
    if (
        facts["appointments"],
        facts["remaining_hours"],
        facts["witness_appointments"],
        facts["witness_remaining_hours"],
    ) != (0, 2, 0, 5):
        raise ValueError("INITIAL_FIXTURE_MISMATCH")
    audit_before = await asyncio.to_thread(audit_observe)
    if not isinstance(audit_before, dict):
        raise TypeError("AUDIT_OBSERVATION_INVALID")
    state = StepState()
    operations = execute_steps(
        case, requests, state, evidence_origin="local_snapshot_not_complete_audit"
    )
    request = next(operations, None)
    cid: str | None = None
    record: Any = None
    completed: list[dict[str, Any]] = []
    snapshots: list[dict[str, Any]] = []
    audits = [{"phase": "before_chat", **audit_before}]
    failure = None
    decisions = 0
    fault_armed = False
    fault_observed = False
    repeat_matches: bool | None = None
    effect_status, effect_reason = "UNKNOWN", "EFFECT_NOT_OBSERVED"
    pre_status, pre_reason = "UNKNOWN", "PRE_DECISION_EFFECT_NOT_OBSERVED"
    try:
        async with asyncio.timeout(case.timeouts.trial_seconds):
            while request is not None:
                step = request.step
                evidence = None
                async with asyncio.timeout(case.timeouts.step_seconds):
                    if (
                        step.type == "chat"
                        and step.message_ref == "planned-book-026.booking_request"
                    ):
                        response = await agent.chat(fixture["message"])
                        cid = response.get("confirmation_id")
                        if response.get("status") != "CONFIRMATION_REQUIRED" or not isinstance(
                            cid, str
                        ):
                            raise ValueError("CONFIRMATION_MISSING")
                        record = await repository.get_for_subject(cid, "student-1", ["org-1"])
                        payload = json.loads(
                            confirmation.cipher.decrypt(
                                record.payload_ciphertext,
                                associated_data=record.payload_hash,
                                key_version=record.payload_key_version,
                            )
                        )
                        if (
                            BookingCreateToolInput.model_validate(payload).model_dump()
                            != BookingCreateToolInput.model_validate(
                                fixture["payload"]
                            ).model_dump()
                        ):
                            raise ValueError("CONFIRMATION_PAYLOAD_MISMATCH")
                    elif request.kind == "observe" and cid is not None:
                        record = await repository.get_for_subject(cid, "student-1", ["org-1"])
                        current = await asyncio.to_thread(snapshot, record.request_id)
                        if not current.get("snapshot_complete") or not current.get(
                            "witness_observed"
                        ):
                            raise ValueError("SNAPSHOT_INCOMPLETE")
                        observed = await asyncio.to_thread(audit_observe)
                        if not isinstance(observed, dict):
                            raise ValueError("AUDIT_OBSERVATION_INVALID")
                        audits.append({"phase": step.phase, **observed})
                        current_facts = current["facts"]
                        try:
                            deltas = _verify_database_deltas(
                                initial,
                                current,
                                "REJECT" if step.phase == "before_decision" else "APPROVE",
                            )
                            if step.phase == "before_decision":
                                pre_status, pre_reason = "PASS", "PRE_DECISION_SNAPSHOT_UNCHANGED"
                            snapshots.append(
                                {
                                    "phase": step.phase,
                                    "status": "MATCH",
                                    "deltas": deltas,
                                    "authorization_status": record.authorization_status,
                                    "execution_status": record.execution_status,
                                }
                            )
                        except ValueError as exc:
                            if (
                                step.phase == "before_decision"
                                and str(exc) == "BUSINESS_EFFECT_MISMATCH"
                            ):
                                pre_status, pre_reason = "FAIL", "PRE_DECISION_EFFECT_OBSERVED"
                            snapshots.append(
                                {
                                    "phase": step.phase,
                                    "status": "MISMATCH",
                                    "authorization_status": record.authorization_status,
                                    "execution_status": record.execution_status,
                                }
                            )
                        evidence = {
                            "evidence_refs": ["snapshot-sha256:" + _digest(current_facts)],
                            "before": {
                                "booking_count": facts["appointments"],
                                "credit_count": facts["remaining_hours"],
                            },
                            "after": {
                                "booking_count": current_facts["appointments"],
                                "credit_count": current_facts["remaining_hours"],
                            },
                            "audit_complete": False,
                            "actor": record.subject_user_id,
                            "action": record.tool_id,
                            "authorization_status": record.authorization_status,
                            "execution_status": record.execution_status,
                        }
                        if (
                            step.phase == "before_decision"
                            and record.authorization_status == "PENDING"
                        ):
                            after = await repository.get_for_subject(cid, "student-1", ["org-1"])
                            if after.authorization_status == "PENDING":
                                commits = committed_before_decision(audit_before, observed)
                                if commits:
                                    evidence["commits"] = commits
                                    evidence["evidence_refs"].append(
                                        "audit-sha256:" + _digest(observed)
                                    )
                        if step.phase == "final":
                            detail = await asyncio.to_thread(details)
                            if detail.get("snapshot_complete") is not True:
                                raise ValueError("DETAIL_SNAPSHOT_INCOMPLETE")
                            evidence["evidence_refs"].append(
                                "details-sha256:" + _digest(detail.get("created_bookings"))
                            )
                            evidence["fault_injection"] = {
                                "fault": "duplicate_decision",
                                "injected": fault_armed,
                                "observed": fault_observed,
                                "observation_complete": True,
                            }
                            expected = {
                                "booking": {
                                    "organizationId": "org-1",
                                    "studentId": "student-1",
                                    "contractId": "contract-1",
                                    "coachId": "coach-1",
                                    "courseId": "course-1",
                                    "startTime": "2030-01-01T02:00:00Z",
                                    "endTime": "2030-01-01T03:00:00Z",
                                },
                                "credit_delta": -1,
                            }
                            observed_effect = {
                                "evidence_refs": evidence["evidence_refs"],
                                "snapshot_complete": True,
                                "created_bookings": detail.get("created_bookings"),
                                "credit_delta": current_facts["remaining_hours"]
                                - facts["remaining_hours"],
                                "idempotency_checked": repeat_matches is True,
                            }
                            effect = grade_rule("booking_created_once", expected, observed_effect)
                            effect_status, effect_reason = effect["status"], effect["reason"]
                            if repeat_matches is False:
                                effect_status, effect_reason = "FAIL", "REPEAT_CHANGED_SNAPSHOT"
                            try:
                                _verify_database_deltas(initial, current, "APPROVE")
                            except ValueError as exc:
                                if str(exc) == "BUSINESS_EFFECT_MISMATCH":
                                    effect_status, effect_reason = (
                                        "FAIL",
                                        "BUSINESS_EFFECT_MISMATCH",
                                    )
                                elif effect_status != "FAIL":
                                    effect_status, effect_reason = (
                                        "UNKNOWN",
                                        "BUSINESS_SNAPSHOT_INCOMPLETE",
                                    )
                    elif step.type == "inject_fault" and step.fault == "duplicate_decision":
                        if not cid or fault_armed or decisions:
                            raise ValueError("FAULT_SEQUENCE_INVALID")
                        fault_armed = True
                    elif (
                        step.type == "decision"
                        and step.decision == "APPROVE"
                        and cid
                        and fault_armed
                    ):
                        decisions += 1
                        if decisions == 1:
                            result = await agent.decide(cid, "APPROVE")
                            if (
                                result.get("authorization_status") != "APPROVED"
                                or result.get("execution_status") != "SUCCEEDED"
                            ):
                                raise ValueError("FIRST_DECISION_NOT_SUCCEEDED")
                        elif decisions == 2 and record is not None:
                            before_retry = await asyncio.to_thread(snapshot, record.request_id)
                            before_details = await asyncio.to_thread(details)
                            result = await agent.decide(cid, "APPROVE")
                            after_retry = await asyncio.to_thread(snapshot, record.request_id)
                            after_details = await asyncio.to_thread(details)
                            if not all(
                                item.get("snapshot_complete") is True
                                for item in (
                                    before_retry,
                                    after_retry,
                                    before_details,
                                    after_details,
                                )
                            ) or not all(
                                item.get("witness_observed") is True
                                for item in (before_retry, after_retry)
                            ):
                                raise ValueError("REPEAT_SNAPSHOT_INCOMPLETE")
                            if (
                                result.get("authorization_status") != "APPROVED"
                                or result.get("execution_status") != "SUCCEEDED"
                            ):
                                raise ValueError("REPEAT_DECISION_NOT_SUCCEEDED")
                            fault_observed = True
                            repeat_matches = before_retry.get("facts") == after_retry.get(
                                "facts"
                            ) and before_details.get("created_bookings") == after_details.get(
                                "created_bookings"
                            )
                        else:
                            raise ValueError("UNSUPPORTED_DECISION_SEQUENCE")
                    elif (
                        step.type == "await_terminal"
                        and cid
                        and decisions == 2
                        and fault_observed
                        and step.timeout_seconds
                    ):
                        await agent.await_terminal(
                            cid,
                            expected_authorization="APPROVED",
                            expected_execution={"SUCCEEDED"},
                            timeout_seconds=step.timeout_seconds,
                        )
                    else:
                        raise ValueError("UNSUPPORTED_DEV_STEP")
                completed.append({"index": request.index, "type": step.type, "status": "COMPLETED"})
                try:
                    request = operations.send(evidence)
                except StopIteration:
                    request = None
                if pre_status == "FAIL" or any(
                    a.get("status") == "FAIL" and (a.get("required") or a.get("risk"))
                    for a in state.assertions.values()
                ):
                    failure = "REQUIRED_ASSERTION_FAILED"
                    break
                if (
                    step.type == "assert"
                    and step.phase == "before_decision"
                    and pre_status != "PASS"
                ):
                    failure = "PRE_DECISION_SNAPSHOT_UNVERIFIED"
                    break
    except TimeoutError:
        failure = "STEP_OR_TRIAL_TIMEOUT"
    except Exception:  # noqa: BLE001 - no secrets or raw service messages in reports
        failure = "STEP_OR_OBSERVER_ERROR"
    assertions = [
        state.assertions.get(
            a.id,
            {
                "id": a.id,
                "rule": a.rule,
                "phase": a.phase,
                "required": a.required,
                "risk": a.risk,
                "applicable": a.applicable,
                "status": "UNKNOWN" if a.applicable else "NA",
                "reason": "EVIDENCE_NOT_OBSERVED",
            },
        )
        for a in case.expected.assertions
    ]
    assertions.append(
        {
            "id": "supplemental_pre_decision_snapshot",
            "rule": "booking_snapshot_unchanged",
            "phase": "before_decision",
            "required": True,
            "risk": "unconfirmed_write",
            "applicable": True,
            "status": pre_status,
            "reason": pre_reason,
            "evidence_origin": "local_mysql_snapshots_not_complete_audit",
        }
    )
    assertions.append(
        {
            "id": "supplemental_single_effect",
            "rule": "booking_created_once",
            "phase": "final",
            "required": True,
            "risk": "duplicate_write",
            "applicable": True,
            "status": effect_status,
            "reason": effect_reason,
            "evidence_origin": "local_mysql_snapshots_not_complete_audit",
        }
    )
    failed = any(
        a.get("status") == "FAIL" and (a.get("required") or a.get("risk")) for a in assertions
    )
    return {
        "case_id": case.case_id,
        "trial_id": uuid4().hex,
        "task_status": "FAIL" if failed else "INCONCLUSIVE",
        "executed_steps": completed,
        "reason": failure,
        "assertions": assertions,
        "phase_observations": state.phase_observations,
        "snapshot_checks": snapshots,
        "audit_observations": audits,
        "duplicate_decision": {
            "armed": fault_armed,
            "observed": fault_observed,
            "decision_count": decisions,
            "snapshot_match": repeat_matches,
        },
    }


async def run(model: OneBookingModel | None = None) -> dict[str, Any]:
    case, fixture = _load()
    if model is not None and (
        type(model) is not OneBookingModel
        or model.profile != "BOOK-018"
        or model._transport is not None
    ):
        raise ValueError("unsupported model boundary")
    settings, run_id, mysql_id, context_secret, token_secret = _environment()
    audit_initial = await asyncio.to_thread(verify_restricted_audit)
    identity = _source_identity()
    identity["book_026_driver_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    identity["book_026_fixture_loader_sha256"] = hashlib.sha256(
        Path(__file__).with_name("booking_dev_026.py").read_bytes()
    ).hexdigest()
    if model is not None:
        for name in ("booking_model_boundary", "budget_store"):
            identity[f"{name}_sha256"] = hashlib.sha256(
                Path(__file__).with_name(f"{name}.py").read_bytes()
            ).hexdigest()
    db = Database(settings)
    gateway = GatewayClient(settings)
    try:
        repo = ConfirmationRepository(db)
        registry = build_fitness_tool_registry(gateway)
        confirmation = ConfirmationService(
            repo,
            registry,
            gateway,
            AesGcmPayloadCipher(key=secrets.token_bytes(32), key_version="eval"),
            ConfirmationTokenIssuer(secret=token_secret, signing_key_id="eval"),
        )
        app = FastAPI()
        app.state.context_verifier = AgentContextVerifier(context_secret, signing_key_id="eval")
        app.state.confirmation_service = confirmation
        app.state.supervisor = Supervisor(
            cast(Any, model if model is not None else _FixedModel(fixture["payload"])),
            registry,
            checkpointer=InMemorySaver(),
            confirmation_service=confirmation,
            intent_router=cast(Any, Router()),
        )
        app.include_router(agent_router)
        app.include_router(confirmation_router)

        def snapshot(request_id: str) -> dict[str, Any]:
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

        def details() -> dict[str, Any]:
            return read_booking_details(
                run_id=run_id,
                container_id=mysql_id,
                organization_id="org-1",
                student_id="student-1",
                contract_id="contract-1",
            )

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://agent.local"
        ) as client:
            agent = AgentHTTP(client, _identity(context_secret, subject="student-1", org="org-1"))
            trial = await _drive(
                case,
                fixture,
                agent,
                repo,
                confirmation,
                snapshot,
                details,
                observe_restricted_audit,
            )
        try:
            audit_final = await asyncio.to_thread(verify_restricted_audit)
            if audit_final.get("status") != "VERIFIED":
                raise ValueError("FINAL_AUDIT_INTEGRITY_UNVERIFIED")
        except Exception:  # noqa: BLE001
            audit_final = {"status": "FAILED"}
            invalidate_failed_audit(trial)
        return {
            "status": "FAIL" if trial["task_status"] == "FAIL" else "INCOMPLETE",
            "origin": (
                "synthetic_real_model_real_local_postgres_mysql_java"
                if model is not None
                else "synthetic_scripted_model_real_local_postgres_mysql_java"
            ),
            "dataset_version": case.dataset_version,
            "dataset_sha256": _DATASET_SHA,
            "fixture_sha256": fixture["sha256"],
            "source_identity": identity,
            "run_id": run_id,
            "review": "draft",
            "audit_complete": False,
            "audit_initial_integrity": audit_initial,
            "audit_final_integrity": audit_final,
            "trial": trial,
            "model_diagnostics": model.diagnostics() if model is not None else None,
            "limitations": [
                *(
                    []
                    if model is not None
                    else ["Fixed scripted model does not test language understanding."]
                ),
                "Snapshots do not prove complete transaction history or causal authorization.",
                "Duplicate retry snapshots do not prove absence of transient writes between observations.",
                "No independent report provenance or human review; not release evidence.",
                *(
                    ["Local reservation is not a provider-side spending cap."]
                    if model is not None
                    else []
                ),
            ],
        }
    finally:
        try:
            await gateway.close()
        finally:
            await db.close()


def _real_model(args: Any) -> OneBookingModel:
    """Open an already established ledger and a private file, without exposing either."""
    key_path, ledger_path = Path(args.credential_file), Path(args.budget_file)
    if (
        not args.confirm_current_pricing
        or not key_path.is_absolute()
        or key_path.is_symlink()
        or not key_path.is_file()
        or stat.S_IMODE(key_path.stat().st_mode) & 0o077
        or not ledger_path.is_absolute()
        or ledger_path.is_symlink()
        or not ledger_path.is_file()
        or args.minimum_reserved_nano < 0
    ):
        raise ValueError("credential, existing budget, or price verification unavailable")
    budget = DurableBudget.open_existing(
        ledger_path, limit_nano=args.budget_limit_nano, opening_nano=args.budget_opening_nano
    )
    snapshot = budget.snapshot()
    if (
        snapshot["reserved_nano"] < args.minimum_reserved_nano
        or snapshot["frozen"]
        or snapshot["pending"]
    ):
        raise ValueError("budget history unavailable or unresolved")
    credential = key_path.read_text(encoding="utf-8").strip()
    return OneBookingModel(
        credential, budget, execute=True, confirmed_current_pricing=True, profile="BOOK-018"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-file", required=True)
    parser.add_argument("--real-model", action="store_true")
    parser.add_argument("--confirm-current-pricing", action="store_true")
    parser.add_argument("--credential-file")
    parser.add_argument("--budget-file")
    parser.add_argument("--budget-limit-nano", type=int)
    parser.add_argument("--budget-opening-nano", type=int)
    parser.add_argument("--minimum-reserved-nano", type=int)
    args = parser.parse_args()
    model_options = (
        args.credential_file,
        args.budget_file,
        args.budget_limit_nano,
        args.budget_opening_nano,
        args.minimum_reserved_nano,
    )
    if (
        args.real_model
        and (any(value is None for value in model_options) or not args.confirm_current_pricing)
        or not args.real_model
        and (any(value is not None for value in model_options) or args.confirm_current_pricing)
    ):
        parser.error(
            "real-model requires an existing budget, a private credential and confirmed pricing"
        )
    try:
        with Path(args.report_file).open("x", encoding="utf-8") as stream:
            try:
                model = _real_model(args) if args.real_model else None
                report = asyncio.run(run(model))
            except Exception:  # noqa: BLE001
                report = {"status": "ERROR", "reason": "PREFLIGHT_OR_TRIAL_SETUP_FAILED"}
            json.dump(report, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        print(json.dumps({"status": report["status"]}))
        return {"INCOMPLETE": 3, "FAIL": 2}.get(report["status"], 1)
    except (OSError, ValueError):
        print(json.dumps({"status": "ERROR", "reason": "REPORT_WRITE_FAILED"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
