"""经营目标、策略、决策、动作与实验治理 API。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, cast

from fastapi import APIRouter, Header, HTTPException, Path, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.decision.engine import incremental_effect, stable_experiment_group
from app.decision.models import (
    DecisionCaseStatus,
    MetricSnapshotInput,
    ObjectiveInput,
    StrategyPolicyInput,
)
from app.decision.repository import (
    DecisionConflictError,
    DecisionNotFoundError,
    DecisionRepository,
)
from app.decision.service import DecisionPlatformService
from app.infrastructure.agent_context import AgentContextVerificationError, AgentIdentity

router = APIRouter(prefix="/api/v1/admin/decisions", tags=["admin-decisions"])

_ADMIN_ROLES = frozenset({"SYSTEM_ADMIN", "ORGANIZATION_ADMIN", "ADMIN", "SUPER_ADMIN"})
_PLATFORM_ROLES = frozenset({"SYSTEM_ADMIN", "ADMIN", "SUPER_ADMIN"})


class ObjectiveRequest(ObjectiveInput):
    organization_id: str = Field(min_length=1, max_length=128)


class PolicyRequest(StrategyPolicyInput):
    organization_id: str = Field(min_length=1, max_length=128)


class SnapshotRequest(MetricSnapshotInput):
    organization_id: str = Field(min_length=1, max_length=128)


class CaseDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    option_id: str = Field(min_length=1, max_length=128)
    decision: Literal["APPROVE", "REJECT"]
    decision_request_id: str = Field(min_length=1, max_length=128)
    comment: str | None = Field(default=None, max_length=500)


class ExperimentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    organization_id: str = Field(min_length=1, max_length=128)
    policy_id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=120)
    treatment_percent: int = Field(default=50, ge=1, le=99)
    primary_metric: str = Field(min_length=1, max_length=64)
    guardrail_metrics: tuple[str, ...] = Field(default=(), max_length=20)
    observation_days: int = Field(default=7, ge=1, le=365)


class ExperimentAssignmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject_key: str = Field(min_length=1, max_length=256)


class ExperimentStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["RUNNING", "STOPPED", "COMPLETED"]


class OutcomeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_run_id: str | None = Field(default=None, max_length=128)
    experiment_id: str | None = Field(default=None, max_length=128)
    metric: str = Field(min_length=1, max_length=64)
    group_name: Literal["ALL", "TREATMENT", "CONTROL"] = "ALL"
    value: float
    sample_size: int = Field(ge=0)
    window_start: datetime
    window_end: datetime
    source_digest: str = Field(pattern=r"^[0-9a-fA-F]{64}$")

    @model_validator(mode="after")
    def validate_window(self) -> OutcomeRequest:
        if self.window_start.tzinfo is None or self.window_end.tzinfo is None:
            raise ValueError("观察时间必须包含时区")
        if self.window_end <= self.window_start:
            raise ValueError("观察结束时间必须晚于开始时间")
        return self


@router.post("/objectives", status_code=status.HTTP_201_CREATED)
async def create_objective(
    payload: ObjectiveRequest,
    request: Request,
    x_agent_context: str | None = Header(default=None),
) -> dict[str, Any]:
    identity = _admin(request, x_agent_context)
    _require_organization(identity, payload.organization_id)
    repository = _repository(request)
    objective = ObjectiveInput.model_validate(payload.model_dump(exclude={"organization_id"}))
    try:
        async with request.app.state.database.engine.begin() as connection:
            return await repository.create_objective(
                connection,
                organization_id=payload.organization_id,
                payload=objective,
                actor_id=identity.subject,
            )
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail="经营目标键已存在") from exc


@router.get("/objectives")
async def list_objectives(
    request: Request,
    organization_id: str | None = Query(default=None, min_length=1, max_length=128),
    limit: int = Query(default=100, ge=1, le=200),
    x_agent_context: str | None = Header(default=None),
) -> list[dict[str, Any]]:
    identity = _admin(request, x_agent_context)
    scopes = _scopes(identity, organization_id)
    async with request.app.state.database.engine.connect() as connection:
        return await _repository(request).list_objectives(
            connection, organization_ids=scopes, limit=limit
        )


@router.post("/objectives/{objective_id}/policies", status_code=status.HTTP_201_CREATED)
async def create_policy(
    payload: PolicyRequest,
    request: Request,
    objective_id: str = Path(min_length=1, max_length=128),
    x_agent_context: str | None = Header(default=None),
) -> dict[str, Any]:
    identity = _admin(request, x_agent_context)
    _require_organization(identity, payload.organization_id)
    policy = StrategyPolicyInput.model_validate(payload.model_dump(exclude={"organization_id"}))
    try:
        async with request.app.state.database.engine.begin() as connection:
            return await _repository(request).create_policy(
                connection,
                organization_id=payload.organization_id,
                objective_id=objective_id,
                payload=policy,
                actor_id=identity.subject,
            )
    except DecisionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail="相同策略版本已存在") from exc


@router.get("/policies")
async def list_policies(
    request: Request,
    organization_id: str | None = Query(default=None, min_length=1, max_length=128),
    limit: int = Query(default=100, ge=1, le=200),
    x_agent_context: str | None = Header(default=None),
) -> list[dict[str, Any]]:
    identity = _admin(request, x_agent_context)
    scopes = _scopes(identity, organization_id)
    async with request.app.state.database.engine.connect() as connection:
        return await _repository(request).list_policies(
            connection, organization_ids=scopes, limit=limit
        )


@router.post("/policies/{policy_id}/suspend", status_code=status.HTTP_204_NO_CONTENT)
async def suspend_policy(
    request: Request,
    policy_id: str = Path(min_length=1, max_length=128),
    organization_id: str | None = Query(default=None, min_length=1, max_length=128),
    x_agent_context: str | None = Header(default=None),
) -> None:
    identity = _admin(request, x_agent_context)
    scopes = _scopes(identity, organization_id)
    async with request.app.state.database.engine.begin() as connection:
        changed = await _repository(request).update_policy_status(
            connection,
            policy_id=policy_id,
            organization_ids=scopes,
            status="PAUSED",
        )
    if not changed:
        raise HTTPException(status_code=404, detail="策略不存在")


@router.post("/metric-snapshots", status_code=status.HTTP_201_CREATED)
async def ingest_metric_snapshot(
    payload: SnapshotRequest,
    request: Request,
    x_agent_context: str | None = Header(default=None),
    x_trace_id: str | None = Header(default=None),
) -> dict[str, Any]:
    identity = _admin(request, x_agent_context)
    _require_organization(identity, payload.organization_id)
    snapshot = MetricSnapshotInput.model_validate(payload.model_dump(exclude={"organization_id"}))
    try:
        result = await _service(request).ingest_snapshot(
            organization_id=payload.organization_id,
            payload=snapshot,
            trace_id=x_trace_id,
        )
    except DecisionConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "snapshot_id": result.snapshot_id,
        "anomaly_detected": result.anomaly_detected,
        "anomaly_id": result.anomaly_id,
        "case_id": result.case_id,
        "reason_code": result.reason_code,
    }


@router.get("/cases")
async def list_decision_cases(
    request: Request,
    organization_id: str | None = Query(default=None, min_length=1, max_length=128),
    case_status: str | None = Query(default=None, max_length=64),
    limit: int = Query(default=100, ge=1, le=200),
    x_agent_context: str | None = Header(default=None),
) -> list[dict[str, Any]]:
    identity = _admin(request, x_agent_context)
    scopes = _scopes(identity, organization_id)
    async with request.app.state.database.engine.connect() as connection:
        cases = await _repository(request).list_cases(
            connection, organization_ids=scopes, status=case_status, limit=limit
        )
    return [item.__dict__ for item in cases]


@router.get("/cases/{case_id}")
async def get_decision_case(
    request: Request,
    case_id: str = Path(min_length=1, max_length=128),
    organization_id: str | None = Query(default=None, min_length=1, max_length=128),
    x_agent_context: str | None = Header(default=None),
) -> dict[str, Any]:
    identity = _admin(request, x_agent_context)
    scopes = _scopes(identity, organization_id)
    try:
        async with request.app.state.database.engine.connect() as connection:
            repository = _repository(request)
            case = await repository.get_case(connection, case_id=case_id, organization_ids=scopes)
            options = await repository.list_options(
                connection, case_id=case_id, organization_ids=scopes
            )
            events = await repository.list_case_events(
                connection, case_id=case_id, organization_ids=scopes
            )
            actions = await repository.list_case_actions(
                connection, case_id=case_id, organization_ids=scopes
            )
    except DecisionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        **case.__dict__,
        "options": [option.__dict__ for option in options],
        "events": events,
        "actions": actions,
    }


@router.post("/cases/{case_id}/decisions")
async def decide_case(
    payload: CaseDecisionRequest,
    request: Request,
    case_id: str = Path(min_length=1, max_length=128),
    organization_id: str | None = Query(default=None, min_length=1, max_length=128),
    x_agent_context: str | None = Header(default=None),
) -> dict[str, str | None]:
    identity = _admin(request, x_agent_context)
    scopes = _scopes(identity, organization_id)
    try:
        approval_id, action_id = await _service(request).decide_case(
            case_id=case_id,
            organization_ids=scopes,
            option_id=payload.option_id,
            decision=payload.decision,
            decision_request_id=payload.decision_request_id,
            actor_id=identity.subject,
            comment=payload.comment,
        )
    except DecisionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (DecisionConflictError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"approval_id": approval_id, "action_run_id": action_id}


@router.get("/actions")
async def list_action_runs(
    request: Request,
    organization_id: str | None = Query(default=None, min_length=1, max_length=128),
    action_status: str | None = Query(default=None, max_length=64),
    limit: int = Query(default=100, ge=1, le=200),
    x_agent_context: str | None = Header(default=None),
) -> list[dict[str, Any]]:
    identity = _admin(request, x_agent_context)
    scopes = _scopes(identity, organization_id)
    async with request.app.state.database.engine.connect() as connection:
        return await _repository(request).list_actions(
            connection, organization_ids=scopes, status=action_status, limit=limit
        )


@router.post("/actions/{action_id}/cancel", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_action(
    request: Request,
    action_id: str = Path(min_length=1, max_length=128),
    organization_id: str | None = Query(default=None, min_length=1, max_length=128),
    x_agent_context: str | None = Header(default=None),
) -> None:
    identity = _admin(request, x_agent_context)
    scopes = _scopes(identity, organization_id)
    async with request.app.state.database.engine.begin() as connection:
        case_id = await _repository(request).cancel_action(
            connection, action_id=action_id, organization_ids=scopes
        )
        if case_id is not None:
            case = await _repository(request).get_case(
                connection, case_id=case_id, organization_ids=scopes
            )
            if case.status in {
                DecisionCaseStatus.APPROVED.value,
                DecisionCaseStatus.EXECUTING.value,
            }:
                await _repository(request).transition_case(
                    connection,
                    case=case,
                    target=DecisionCaseStatus.SUSPENDED,
                    actor_id=identity.subject,
                    reason_code="ACTION_CANCELLED",
                    metadata={"action_run_id": action_id},
                )
    if case_id is None:
        raise HTTPException(status_code=409, detail="动作不存在或当前状态不能取消")


@router.post("/actions/{action_id}/retry", status_code=status.HTTP_204_NO_CONTENT)
async def retry_action(
    request: Request,
    action_id: str = Path(min_length=1, max_length=128),
    organization_id: str | None = Query(default=None, min_length=1, max_length=128),
    x_agent_context: str | None = Header(default=None),
) -> None:
    identity = _admin(request, x_agent_context)
    scopes = _scopes(identity, organization_id)
    async with request.app.state.database.engine.begin() as connection:
        changed = await _repository(request).retry_final_action(
            connection, action_id=action_id, organization_ids=scopes
        )
    if not changed:
        raise HTTPException(status_code=409, detail="动作不存在或当前状态不能重试")


@router.post("/experiments", status_code=status.HTTP_201_CREATED)
async def create_experiment(
    payload: ExperimentRequest,
    request: Request,
    x_agent_context: str | None = Header(default=None),
) -> dict[str, Any]:
    identity = _admin(request, x_agent_context)
    _require_organization(identity, payload.organization_id)
    try:
        async with request.app.state.database.engine.begin() as connection:
            return await _repository(request).create_experiment(
                connection,
                organization_id=payload.organization_id,
                policy_id=payload.policy_id,
                name=payload.name,
                treatment_percent=payload.treatment_percent,
                primary_metric=payload.primary_metric,
                guardrail_metrics=payload.guardrail_metrics,
                observation_days=payload.observation_days,
                actor_id=identity.subject,
            )
    except DecisionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/experiments/{experiment_id}/assign")
async def assign_experiment_subject(
    payload: ExperimentAssignmentRequest,
    request: Request,
    experiment_id: str = Path(min_length=1, max_length=128),
    organization_id: str | None = Query(default=None, min_length=1, max_length=128),
    x_agent_context: str | None = Header(default=None),
) -> dict[str, str]:
    identity = _admin(request, x_agent_context)
    scopes = _scopes(identity, organization_id)
    async with request.app.state.database.engine.begin() as connection:
        row = (
            (
                await connection.execute(
                    text(
                        """SELECT id, organization_id, treatment_percent, salt
                           FROM agent_experiments WHERE id=:id AND status='RUNNING'
                             AND organization_id = ANY(CAST(:organizations AS TEXT[]))"""
                    ),
                    {"id": experiment_id, "organizations": list(scopes)},
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise HTTPException(status_code=404, detail="实验不存在")
        subject_hash, group = stable_experiment_group(
            experiment_id,
            payload.subject_key,
            str(row["salt"]),
            int(row["treatment_percent"]),
        )
        try:
            persisted = await _repository(request).save_assignment(
                connection,
                experiment_id=experiment_id,
                subject_key_hash=subject_hash,
                group_name=group,
            )
        except DecisionConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"subject_key_hash": subject_hash, "group": persisted}


@router.post("/experiments/{experiment_id}/status")
async def update_experiment_status(
    payload: ExperimentStatusRequest,
    request: Request,
    experiment_id: str = Path(min_length=1, max_length=128),
    organization_id: str | None = Query(default=None, min_length=1, max_length=128),
    x_agent_context: str | None = Header(default=None),
) -> dict[str, Any]:
    identity = _admin(request, x_agent_context)
    scopes = _scopes(identity, organization_id)
    try:
        async with request.app.state.database.engine.begin() as connection:
            return await _repository(request).update_experiment_status(
                connection,
                experiment_id=experiment_id,
                organization_ids=scopes,
                target=payload.status,
            )
    except DecisionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except DecisionConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/experiments")
async def list_experiments(
    request: Request,
    organization_id: str | None = Query(default=None, min_length=1, max_length=128),
    limit: int = Query(default=100, ge=1, le=200),
    x_agent_context: str | None = Header(default=None),
) -> list[dict[str, Any]]:
    identity = _admin(request, x_agent_context)
    scopes = _scopes(identity, organization_id)
    async with request.app.state.database.engine.connect() as connection:
        return await _repository(request).list_experiments(
            connection, organization_ids=scopes, limit=limit
        )


@router.post("/cases/{case_id}/outcomes", status_code=status.HTTP_201_CREATED)
async def record_outcome(
    payload: OutcomeRequest,
    request: Request,
    case_id: str = Path(min_length=1, max_length=128),
    organization_id: str | None = Query(default=None, min_length=1, max_length=128),
    x_agent_context: str | None = Header(default=None),
) -> dict[str, str]:
    identity = _admin(request, x_agent_context)
    scopes = _scopes(identity, organization_id)
    try:
        async with request.app.state.database.engine.begin() as connection:
            observation_id = await _repository(request).save_outcome(
                connection,
                case_id=case_id,
                organization_ids=scopes,
                action_run_id=payload.action_run_id,
                experiment_id=payload.experiment_id,
                metric=payload.metric,
                group_name=payload.group_name,
                value=payload.value,
                sample_size=payload.sample_size,
                window_start=payload.window_start,
                window_end=payload.window_end,
                source_digest=payload.source_digest,
            )
            outcomes = await _repository(request).list_outcomes(
                connection, case_id=case_id, organization_ids=scopes
            )
            comparable_groups = {
                str(row["group_name"])
                for row in outcomes
                if row["metric"] == payload.metric
                and row["window_start"] == payload.window_start
                and row["window_end"] == payload.window_end
            }
            case = await _repository(request).get_case(
                connection, case_id=case_id, organization_ids=scopes
            )
            observation_complete = payload.group_name == "ALL" or {
                "TREATMENT",
                "CONTROL",
            }.issubset(comparable_groups)
            if observation_complete and case.status == DecisionCaseStatus.OBSERVING.value:
                await _repository(request).transition_case(
                    connection,
                    case=case,
                    target=DecisionCaseStatus.EVALUATED,
                    actor_id=identity.subject,
                    reason_code="OUTCOME_OBSERVED",
                    metadata={"metric": payload.metric},
                )
    except DecisionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except DecisionConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"observation_id": observation_id}


@router.get("/cases/{case_id}/outcomes")
async def list_outcomes(
    request: Request,
    case_id: str = Path(min_length=1, max_length=128),
    organization_id: str | None = Query(default=None, min_length=1, max_length=128),
    x_agent_context: str | None = Header(default=None),
) -> list[dict[str, Any]]:
    identity = _admin(request, x_agent_context)
    scopes = _scopes(identity, organization_id)
    try:
        async with request.app.state.database.engine.connect() as connection:
            return await _repository(request).list_outcomes(
                connection, case_id=case_id, organization_ids=scopes
            )
    except DecisionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/cases/{case_id}/effects")
async def get_case_effects(
    request: Request,
    case_id: str = Path(min_length=1, max_length=128),
    organization_id: str | None = Query(default=None, min_length=1, max_length=128),
    x_agent_context: str | None = Header(default=None),
) -> list[dict[str, Any]]:
    identity = _admin(request, x_agent_context)
    scopes = _scopes(identity, organization_id)
    try:
        async with request.app.state.database.engine.connect() as connection:
            rows = await _repository(request).list_outcomes(
                connection, case_id=case_id, organization_ids=scopes
            )
    except DecisionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    grouped: dict[tuple[str, datetime, datetime], dict[str, dict[str, Any]]] = {}
    for row in rows:
        group = str(row["group_name"])
        if group not in {"TREATMENT", "CONTROL"}:
            continue
        key = (str(row["metric"]), row["window_start"], row["window_end"])
        grouped.setdefault(key, {})[group] = row
    effects: list[dict[str, Any]] = []
    for (metric, window_start, window_end), groups in grouped.items():
        if not {"TREATMENT", "CONTROL"}.issubset(groups):
            continue
        treatment = groups["TREATMENT"]
        control = groups["CONTROL"]
        try:
            effect = incremental_effect(
                float(treatment["value"]),
                int(treatment["sample_size"]),
                float(control["value"]),
                int(control["sample_size"]),
            )
        except ValueError:
            continue
        effects.append(
            {
                "metric": metric,
                "window_start": window_start,
                "window_end": window_end,
                **effect,
            }
        )
    return effects


@router.post("/cases/{case_id}/close", status_code=status.HTTP_204_NO_CONTENT)
async def close_case(
    request: Request,
    case_id: str = Path(min_length=1, max_length=128),
    organization_id: str | None = Query(default=None, min_length=1, max_length=128),
    x_agent_context: str | None = Header(default=None),
) -> None:
    identity = _admin(request, x_agent_context)
    scopes = _scopes(identity, organization_id)
    try:
        async with request.app.state.database.engine.begin() as connection:
            case = await _repository(request).get_case(
                connection, case_id=case_id, organization_ids=scopes
            )
            await _repository(request).transition_case(
                connection,
                case=case,
                target=DecisionCaseStatus.CLOSED,
                actor_id=identity.subject,
                reason_code="OUTCOME_REVIEW_COMPLETED",
            )
    except DecisionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (DecisionConflictError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _repository(request: Request) -> DecisionRepository:
    return cast(DecisionRepository, request.app.state.decision_repository)


def _service(request: Request) -> DecisionPlatformService:
    return cast(DecisionPlatformService, request.app.state.decision_service)


def _admin(request: Request, token: str | None) -> AgentIdentity:
    if not token:
        raise HTTPException(status_code=401, detail="必须提供已签名的 AgentContext")
    try:
        identity = cast(AgentIdentity, request.app.state.context_verifier.verify(token))
    except AgentContextVerificationError as exc:
        raise HTTPException(status_code=401, detail="已签名的 AgentContext 无效") from exc
    if not _ADMIN_ROLES.intersection(identity.roles):
        raise HTTPException(status_code=403, detail="需要经营管理员角色")
    return identity


def _require_organization(identity: AgentIdentity, organization_id: str) -> None:
    if organization_id not in identity.organization_ids and not _PLATFORM_ROLES.intersection(
        identity.roles
    ):
        raise HTTPException(status_code=403, detail="无权访问该机构")


def _scopes(identity: AgentIdentity, requested: str | None) -> tuple[str, ...]:
    if requested:
        _require_organization(identity, requested)
        return (requested,)
    scopes = tuple(sorted(identity.organization_ids))
    if not scopes:
        raise HTTPException(status_code=422, detail="必须指定机构范围")
    return scopes
