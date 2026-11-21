"""Observe real model behavior on draft BOOK-011..015 in disposable services."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import secrets
import stat
from pathlib import Path
from typing import Any, cast

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import text

from app.agent.fitness_tools import build_fitness_tool_registry
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
from .audit_observation import (
    bind_verified_observer,
    committed_before_decision,
    matching_final_integrity,
)
from .booking_clarification_model import INPUT_HASHES, OneClarificationModel
from .booking_contracts import matches_frozen_case
from .booking_local import Router
from .booking_signed_identity import _identity
from .budget_store import DurableBudget
from .cross_service_booking import _environment, _verify_database_deltas
from .dataset import load_dataset
from .dev_review_probe import _sanitized, _write_private
from .dev_sandbox_booking import _source_identity
from .mysql_booking_observer import snapshot_booking
from .mysql_restricted_audit import observe as observe_restricted_audit
from .mysql_restricted_audit import verify as verify_restricted_audit
from .private_credential import read_private_credential

_ROOT = Path(__file__).resolve().parents[3]
_DATASET_SHA256 = "1d187f5abca4dab754db29059f9ff95a7897719fde6ce88de6c92448d25ab446"


def _load(case_id: str) -> Any:
    path = _ROOT / "evals/task_eval/datasets/v1.jsonl"
    if (
        case_id not in INPUT_HASHES
        or hashlib.sha256(path.read_bytes()).hexdigest() != _DATASET_SHA256
    ):
        raise ValueError("DATASET_OR_CASE_CHANGED")
    cases = [case for case in load_dataset(path).cases if case.case_id == case_id]
    if (
        len(cases) != 1
        or cases[0].split != "dev"
        or cases[0].review.status != "draft"
        or not matches_frozen_case(cases[0])
        or [step.type for step in cases[0].steps] != ["chat", "assert"]
        or not cases[0].steps[0].message
    ):
        raise ValueError("CASE_CONTRACT_CHANGED")
    return cases[0]


def _net_status(before: dict[str, Any], after: dict[str, Any]) -> str:
    try:
        _verify_database_deltas(before, after, "REJECT")
        return "MATCH"
    except ValueError as exc:
        return "MISMATCH" if str(exc) == "BUSINESS_EFFECT_MISMATCH" else "UNKNOWN"


async def run(case_id: str, model: OneClarificationModel, review_dir: Path) -> dict[str, Any]:
    case = _load(case_id)
    if (
        type(model) is not OneClarificationModel
        or model.case_id != case_id
        or model.transport is not None
    ):
        raise ValueError("MODEL_BOUNDARY_INVALID")
    settings, run_id, mysql_id, context_secret, token_secret = _environment()
    initial_audit = await asyncio.to_thread(verify_restricted_audit)
    observer = bind_verified_observer(
        initial_audit, observe_restricted_audit, run_id=run_id, container_id=mysql_id
    )
    db, gateway = Database(settings), GatewayClient(settings)
    response: dict[str, Any] | None = None
    error = None
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
            cast(Any, model),
            registry,
            checkpointer=InMemorySaver(),
            confirmation_service=confirmation,
            intent_router=cast(Any, Router()),
        )
        app.include_router(agent_router)
        app.include_router(confirmation_router)

        def snapshot() -> dict[str, Any]:
            return snapshot_booking(
                run_id=run_id,
                container_id=mysql_id,
                organization_id="org-1",
                student_id="student-1",
                contract_id="contract-1",
                request_id="dev-clarification",
                witness_organization_id="org-2",
                witness_student_id="student-2",
                witness_contract_id="contract-2",
            )

        before = await asyncio.to_thread(snapshot)
        before_audit = await asyncio.to_thread(observer)
        async with db.engine.connect() as connection:
            initial_confirmations = (
                await connection.execute(text("SELECT COUNT(*) FROM agent_action_confirmations"))
            ).scalar_one()
        if (
            not before.get("snapshot_complete")
            or not before.get("witness_observed")
            or before.get("facts", {}).get("appointments") != 0
            or before.get("facts", {}).get("remaining_hours") != 2
            or before_audit.get("records") != []
            or initial_confirmations != 0
        ):
            raise ValueError("CLARIFICATION_INITIAL_STATE_INVALID")
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://agent.local"
            ) as client:
                agent = AgentHTTP(
                    client, _identity(context_secret, subject="student-1", org="org-1")
                )
                response = await agent.chat(case.steps[0].message)
        except Exception:  # noqa: BLE001 - model and service errors may contain sensitive content
            error = "CHAT_OR_MODEL_ERROR"
        after_audit = await asyncio.to_thread(observer)
        after = await asyncio.to_thread(snapshot)
        async with db.engine.connect() as connection:
            confirmation_count = (
                await connection.execute(text("SELECT COUNT(*) FROM agent_action_confirmations"))
            ).scalar_one()
        net_status = _net_status(before, after)
        try:
            final_audit = await asyncio.to_thread(verify_restricted_audit)
            integrity = matching_final_integrity(initial_audit, final_audit)
        except Exception:  # noqa: BLE001
            integrity = False
        commits = committed_before_decision(before_audit, after_audit) if integrity else None
        confirmation_observed = type(confirmation_count) is int and confirmation_count > 0
        failed = net_status == "MISMATCH" or bool(commits) or confirmation_observed
        answer = response.get("answer") if isinstance(response, dict) else None
        review_name = None
        if isinstance(answer, str) or model.reason == "MODEL_RESPONSE_RECEIVED":
            review_name = _write_private(
                review_dir,
                {
                    "case_id": case_id,
                    "answer": _sanitized(answer, model._credential)
                    if isinstance(answer, str)
                    else None,
                    "model_tool_arguments": _sanitized(
                        json.dumps(model.last_arguments, ensure_ascii=False), model._credential
                    ),
                    "review_status": "UNREVIEWED",
                    "synthetic_input": True,
                },
            )
        source_identity = _source_identity()
        source_identity["clarification_driver_sha256"] = hashlib.sha256(
            Path(__file__).read_bytes()
        ).hexdigest()
        source_identity["clarification_model_sha256"] = hashlib.sha256(
            Path(__file__).with_name("booking_clarification_model.py").read_bytes()
        ).hexdigest()
        return {
            "status": "FAIL" if failed else "INCOMPLETE",
            "case_id": case_id,
            "dataset_sha256": _DATASET_SHA256,
            "review": "draft",
            "origin": "synthetic_case_real_model_local_postgres_mysql_java",
            "source_identity": source_identity,
            "run_id": run_id,
            "task_status": "FAIL" if failed else "INCONCLUSIVE",
            "reason": ("UNEXPECTED_CONFIRMATION_OR_WRITE" if failed else error),
            "model": {
                "reason": model.reason,
                "attempts": model.attempts,
                "forced_tool": model.forced_tool,
                "tool_count": model.tool_count,
                "response_sha256": model.response_sha256,
                "input_tokens": model.usage[0],
                "output_tokens": model.usage[1],
            },
            "agent": {
                "status": response.get("status") if isinstance(response, dict) else None,
                "confirmation_observed": confirmation_observed,
                "answer_sha256": hashlib.sha256(answer.encode()).hexdigest()
                if isinstance(answer, str)
                else None,
                "review_file": review_name,
            },
            "observations": {
                "net_business_change": net_status,
                "committed_write_before_any_decision": bool(commits) if integrity else None,
                "audit_integrity_verified_locally": integrity,
                "audit_window_complete": False,
                "semantic_judgment": "UNKNOWN",
            },
            "limitations": [
                "The original planning case and semantic expectations remain unreviewed.",
                "The router is a scripted booking route and the model gets one turn only.",
                "MySQL snapshots and trigger counts do not close the audit window or drain later transactions.",
                "Real model output requires independent review; this is not release evidence.",
            ],
        }
    finally:
        await gateway.close()
        await db.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case-id", required=True, choices=sorted(INPUT_HASHES))
    parser.add_argument("--report-file", required=True)
    parser.add_argument("--review-dir", required=True)
    parser.add_argument("--real-model", action="store_true", required=True)
    parser.add_argument("--confirm-current-pricing", action="store_true", required=True)
    parser.add_argument("--credential-file", required=True)
    parser.add_argument("--budget-file", required=True)
    parser.add_argument("--budget-limit-nano", type=int, required=True)
    parser.add_argument("--budget-opening-nano", type=int, required=True)
    parser.add_argument("--minimum-reserved-nano", type=int, required=True)
    args = parser.parse_args()
    try:
        with Path(args.report_file).open("x", encoding="utf-8") as stream:
            try:
                path = Path(args.budget_file)
                review_dir = Path(args.review_dir)
                if not path.is_absolute() or path.is_symlink() or not path.is_file():
                    raise ValueError("BUDGET_UNAVAILABLE")
                if (
                    not review_dir.is_absolute()
                    or review_dir.is_symlink()
                    or not review_dir.is_dir()
                    or review_dir.stat().st_uid != os.geteuid()
                    or stat.S_IMODE(review_dir.stat().st_mode) != 0o700
                ):
                    raise ValueError("REVIEW_DIRECTORY_UNAVAILABLE")
                budget = DurableBudget.open_existing(
                    path, limit_nano=args.budget_limit_nano, opening_nano=args.budget_opening_nano
                )
                snap = budget.snapshot()
                if (
                    snap["reserved_nano"] < args.minimum_reserved_nano
                    or snap["frozen"]
                    or snap["pending"]
                ):
                    raise ValueError("BUDGET_UNAVAILABLE")
                credential = read_private_credential(args.credential_file)
                model = OneClarificationModel(args.case_id, credential, budget)
                report = asyncio.run(run(args.case_id, model, review_dir))
            except Exception:  # noqa: BLE001 - do not expose private configuration or responses
                report = {"status": "ERROR", "reason": "PREFLIGHT_OR_TRIAL_FAILED"}
            json.dump(report, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        print(json.dumps({"status": report["status"]}))
        return {"INCOMPLETE": 3, "FAIL": 2}.get(report["status"], 1)
    except OSError:
        print(json.dumps({"status": "ERROR", "reason": "REPORT_WRITE_FAILED"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
