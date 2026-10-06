"""经营决策平台的 PostgreSQL 仓储。"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

from sqlalchemy import text

from .models import (
    DecisionCaseRecord,
    DecisionCaseStatus,
    DecisionOptionInput,
    DecisionOptionRecord,
    MetricSnapshotInput,
    ObjectiveInput,
    StrategyPolicyInput,
    validate_case_transition,
)


class DecisionConflictError(RuntimeError):
    """并发版本、幂等键或状态不允许当前操作。"""


class DecisionNotFoundError(LookupError):
    """目标不存在于当前授权机构范围。"""


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class DecisionRepository:
    async def create_objective(
        self,
        connection: Any,
        *,
        organization_id: str,
        payload: ObjectiveInput,
        actor_id: str,
    ) -> dict[str, Any]:
        objective_id = uuid4().hex
        row = (
            (
                await connection.execute(
                    text(
                        """INSERT INTO agent_business_objectives (
                            id, organization_id, objective_key, name, metric, direction,
                            target_value, guardrail_metrics, created_by
                        ) VALUES (
                            :id, :organization_id, :objective_key, :name, :metric, :direction,
                            :target_value, CAST(:guardrails AS JSONB), :actor_id
                        ) RETURNING *"""
                    ),
                    {
                        "id": objective_id,
                        "organization_id": organization_id,
                        "objective_key": payload.objective_key,
                        "name": payload.name,
                        "metric": payload.metric,
                        "direction": payload.direction,
                        "target_value": payload.target_value,
                        "guardrails": _json(payload.guardrail_metrics),
                        "actor_id": actor_id,
                    },
                )
            )
            .mappings()
            .one()
        )
        return dict(row)

    async def list_objectives(
        self, connection: Any, *, organization_ids: tuple[str, ...], limit: int = 100
    ) -> list[dict[str, Any]]:
        rows = (
            (
                await connection.execute(
                    text(
                        """SELECT * FROM agent_business_objectives
                           WHERE organization_id = ANY(CAST(:organizations AS TEXT[]))
                           ORDER BY updated_at DESC LIMIT :limit"""
                    ),
                    {"organizations": list(organization_ids), "limit": limit},
                )
            )
            .mappings()
            .all()
        )
        return [dict(row) for row in rows]

    async def create_policy(
        self,
        connection: Any,
        *,
        organization_id: str,
        objective_id: str,
        payload: StrategyPolicyInput,
        actor_id: str,
    ) -> dict[str, Any]:
        policy_id = uuid4().hex
        row = (
            (
                await connection.execute(
                    text(
                        """INSERT INTO agent_strategy_policies (
                            id, organization_id, objective_id, strategy_key, version, mode,
                            risk_level, config, allowed_actions, budget_limit,
                            max_affected_subjects, valid_to, created_by
                        )
                        SELECT :id, objective.organization_id, objective.id, :strategy_key,
                               COALESCE((SELECT MAX(existing.version) + 1
                                 FROM agent_strategy_policies AS existing
                                 WHERE existing.organization_id=objective.organization_id
                                   AND existing.strategy_key=:strategy_key), 1),
                               :mode, :risk_level, CAST(:config AS JSONB),
                               CAST(:allowed_actions AS JSONB), :budget_limit,
                               :max_affected_subjects, :valid_to, :actor_id
                        FROM agent_business_objectives AS objective
                        WHERE objective.id=:objective_id
                          AND objective.organization_id=:organization_id
                          AND objective.status='ACTIVE'
                        RETURNING *"""
                    ),
                    {
                        "id": policy_id,
                        "organization_id": organization_id,
                        "objective_id": objective_id,
                        "strategy_key": payload.strategy_key,
                        "mode": payload.mode.value,
                        "risk_level": payload.risk_level.value,
                        "config": _json(payload.config),
                        "allowed_actions": _json(payload.allowed_actions),
                        "budget_limit": payload.budget_limit,
                        "max_affected_subjects": payload.max_affected_subjects,
                        "valid_to": payload.valid_to,
                        "actor_id": actor_id,
                    },
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise DecisionNotFoundError("经营目标不存在或已停用")
        return dict(row)

    async def list_policies(
        self, connection: Any, *, organization_ids: tuple[str, ...], limit: int = 100
    ) -> list[dict[str, Any]]:
        rows = (
            (
                await connection.execute(
                    text(
                        """SELECT policy.*, objective.name AS objective_name,
                                  objective.metric AS objective_metric
                           FROM agent_strategy_policies AS policy
                           JOIN agent_business_objectives AS objective ON objective.id=policy.objective_id
                           WHERE policy.organization_id = ANY(CAST(:organizations AS TEXT[]))
                           ORDER BY policy.created_at DESC LIMIT :limit"""
                    ),
                    {"organizations": list(organization_ids), "limit": limit},
                )
            )
            .mappings()
            .all()
        )
        return [dict(row) for row in rows]

    async def update_policy_status(
        self,
        connection: Any,
        *,
        policy_id: str,
        organization_ids: tuple[str, ...],
        status: str,
    ) -> bool:
        result = await connection.execute(
            text(
                """UPDATE agent_strategy_policies SET status=:status
                   WHERE id=:id AND organization_id = ANY(CAST(:organizations AS TEXT[]))"""
            ),
            {"id": policy_id, "organizations": list(organization_ids), "status": status},
        )
        return int(result.rowcount or 0) == 1

    async def upsert_metric_snapshot(
        self,
        connection: Any,
        *,
        organization_id: str,
        payload: MetricSnapshotInput,
    ) -> str:
        snapshot_id = uuid4().hex
        row = await connection.execute(
            text(
                """INSERT INTO agent_metric_snapshots (
                    id, organization_id, metric, dimension_type, dimension_id, bucket,
                    period_start, period_end, value, sample_size, catalog_version,
                    source_digest, quality_status, metadata
                ) VALUES (
                    :id, :organization_id, :metric, :dimension_type, :dimension_id, :bucket,
                    :period_start, :period_end, :value, :sample_size, :catalog_version,
                    :source_digest, :quality_status, CAST(:metadata AS JSONB)
                ) ON CONFLICT (
                    organization_id, metric, dimension_type, dimension_id,
                    bucket, period_start, catalog_version
                ) DO UPDATE SET
                    source_digest=EXCLUDED.source_digest
                WHERE agent_metric_snapshots.source_digest=EXCLUDED.source_digest
                RETURNING id"""
            ),
            {
                "id": snapshot_id,
                "organization_id": organization_id,
                **payload.model_dump(),
                "metadata": _json(payload.metadata),
            },
        )
        existing_id = row.scalar_one_or_none()
        if existing_id is None:
            raise DecisionConflictError("相同指标周期已存在不同来源摘要")
        return str(existing_id)

    async def metric_history(
        self,
        connection: Any,
        *,
        organization_id: str,
        metric: str,
        dimension_type: str,
        dimension_id: str,
        bucket: str,
        before: datetime,
        limit: int = 28,
    ) -> list[float]:
        values = (
            (
                await connection.execute(
                    text(
                        """SELECT value FROM agent_metric_snapshots
                       WHERE organization_id=:organization_id AND metric=:metric
                         AND dimension_type=:dimension_type AND dimension_id=:dimension_id
                         AND bucket=:bucket AND period_start < :before
                         AND quality_status='PASSED'
                       ORDER BY period_start DESC LIMIT :limit"""
                    ),
                    {
                        "organization_id": organization_id,
                        "metric": metric,
                        "dimension_type": dimension_type,
                        "dimension_id": dimension_id,
                        "bucket": bucket,
                        "before": before,
                        "limit": limit,
                    },
                )
            )
            .scalars()
            .all()
        )
        return [float(value) for value in reversed(values)]

    async def get_active_policy_for_metric(
        self, connection: Any, *, organization_id: str, metric: str
    ) -> dict[str, Any] | None:
        row = (
            (
                await connection.execute(
                    text(
                        """SELECT policy.*, objective.metric, objective.name AS objective_name,
                                  objective.target_value, objective.direction
                           FROM agent_strategy_policies AS policy
                           JOIN agent_business_objectives AS objective ON objective.id=policy.objective_id
                           WHERE policy.organization_id=:organization_id
                             AND objective.metric=:metric AND objective.status='ACTIVE'
                             AND policy.status='ACTIVE' AND policy.valid_from <= CURRENT_TIMESTAMP
                             AND (policy.valid_to IS NULL OR policy.valid_to > CURRENT_TIMESTAMP)
                           ORDER BY policy.version DESC, policy.valid_from DESC, policy.id DESC
                           LIMIT 1"""
                    ),
                    {"organization_id": organization_id, "metric": metric},
                )
            )
            .mappings()
            .first()
        )
        return dict(row) if row else None

    async def create_anomaly_and_case(
        self,
        connection: Any,
        *,
        organization_id: str,
        policy: dict[str, Any],
        snapshot_id: str,
        anomaly_type: str,
        severity: str,
        actual_value: float,
        expected_value: float | None,
        deviation_percent: float | None,
        detector_version: str,
        evidence: dict[str, Any],
        period_key: str,
        trace_id: str | None,
    ) -> tuple[str, str, bool]:
        anomaly_id = uuid4().hex
        case_id = uuid4().hex
        dedupe_key = hashlib.sha256(
            f"{policy['id']}:{snapshot_id}:{anomaly_type}:{period_key}".encode()
        ).hexdigest()
        anomaly_row = (
            await connection.execute(
                text(
                    """INSERT INTO agent_anomaly_events (
                            id, organization_id, objective_id, policy_id, metric_snapshot_id,
                            anomaly_type, severity, actual_value, expected_value,
                            deviation_percent, detector_version, evidence, dedupe_key
                        ) VALUES (
                            :id, :organization_id, :objective_id, :policy_id, :snapshot_id,
                            :anomaly_type, :severity, :actual, :expected, :deviation,
                            :detector_version, CAST(:evidence AS JSONB), :dedupe_key
                        ) ON CONFLICT (dedupe_key) DO NOTHING RETURNING id"""
                ),
                {
                    "id": anomaly_id,
                    "organization_id": organization_id,
                    "objective_id": policy["objective_id"],
                    "policy_id": policy["id"],
                    "snapshot_id": snapshot_id,
                    "anomaly_type": anomaly_type,
                    "severity": severity,
                    "actual": actual_value,
                    "expected": expected_value,
                    "deviation": deviation_percent,
                    "detector_version": detector_version,
                    "evidence": _json(evidence),
                    "dedupe_key": dedupe_key,
                },
            )
        ).scalar_one_or_none()
        if anomaly_row is None:
            existing = (
                (
                    await connection.execute(
                        text(
                            """SELECT anomaly.id, decision.id AS case_id
                           FROM agent_anomaly_events AS anomaly
                           JOIN agent_decision_cases AS decision ON decision.anomaly_id=anomaly.id
                           WHERE anomaly.dedupe_key=:dedupe_key"""
                        ),
                        {"dedupe_key": dedupe_key},
                    )
                )
                .mappings()
                .one()
            )
            return str(existing["id"]), str(existing["case_id"]), False
        expires_at = datetime.now(UTC) + timedelta(days=7)
        await connection.execute(
            text(
                """INSERT INTO agent_decision_cases (
                    id, organization_id, objective_id, policy_id, anomaly_id, title,
                    risk_level, diagnosis, trace_id, expires_at
                ) VALUES (
                    :id, :organization_id, :objective_id, :policy_id, :anomaly_id,
                    :title, :risk_level, CAST(:diagnosis AS JSONB), :trace_id, :expires_at
                )"""
            ),
            {
                "id": case_id,
                "organization_id": organization_id,
                "objective_id": policy["objective_id"],
                "policy_id": policy["id"],
                "anomaly_id": anomaly_id,
                "title": f"{policy['objective_name']}出现{severity}级异常",
                "risk_level": policy["risk_level"],
                "diagnosis": _json({"status": "PENDING", "evidence": evidence}),
                "trace_id": trace_id,
                "expires_at": expires_at,
            },
        )
        await self._append_case_event(
            connection,
            case_id=case_id,
            from_status=None,
            to_status=DecisionCaseStatus.DETECTED.value,
            actor_id="system:detector",
            reason_code="ANOMALY_DETECTED",
            metadata={"detector_version": detector_version},
        )
        return anomaly_id, case_id, True

    async def add_options(
        self,
        connection: Any,
        *,
        case_id: str,
        organization_ids: tuple[str, ...],
        options: tuple[DecisionOptionInput, ...],
    ) -> list[DecisionOptionRecord]:
        case = await self.get_case(connection, case_id=case_id, organization_ids=organization_ids)
        current = DecisionCaseStatus(case.status)
        if current == DecisionCaseStatus.DETECTED:
            await self.transition_case(
                connection,
                case=case,
                target=DecisionCaseStatus.DIAGNOSING,
                actor_id="system:diagnosis",
                reason_code="DIAGNOSIS_STARTED",
            )
            case = await self.get_case(
                connection, case_id=case_id, organization_ids=organization_ids
            )
        created: list[DecisionOptionRecord] = []
        for option in options:
            option_id = uuid4().hex
            row = (
                (
                    await connection.execute(
                        text(
                            """INSERT INTO agent_decision_options (
                                id, case_id, option_key, title, action_type, action_payload,
                                evidence_refs, expected_benefit, estimated_cost, risk_penalty,
                                uncertainty_penalty, score, confidence, model_name, prompt_version
                            ) VALUES (
                                :id, :case_id, :option_key, :title, :action_type,
                                CAST(:action_payload AS JSONB), CAST(:evidence_refs AS JSONB),
                                :expected_benefit, :estimated_cost, :risk_penalty,
                                :uncertainty_penalty, :score, :confidence, :model_name, :prompt_version
                            ) ON CONFLICT (case_id, option_key) DO UPDATE SET
                                title=EXCLUDED.title, action_type=EXCLUDED.action_type,
                                action_payload=EXCLUDED.action_payload,
                                evidence_refs=EXCLUDED.evidence_refs,
                                expected_benefit=EXCLUDED.expected_benefit,
                                estimated_cost=EXCLUDED.estimated_cost,
                                risk_penalty=EXCLUDED.risk_penalty,
                                uncertainty_penalty=EXCLUDED.uncertainty_penalty,
                                score=EXCLUDED.score, confidence=EXCLUDED.confidence,
                                model_name=EXCLUDED.model_name, prompt_version=EXCLUDED.prompt_version
                            RETURNING *"""
                        ),
                        {
                            "id": option_id,
                            "case_id": case_id,
                            **option.model_dump(),
                            "action_payload": _json(option.action_payload),
                            "evidence_refs": _json(option.evidence_refs),
                            "score": option.score,
                        },
                    )
                )
                .mappings()
                .one()
            )
            created.append(_option_from_row(row))
        refreshed = await self.get_case(
            connection, case_id=case_id, organization_ids=organization_ids
        )
        if DecisionCaseStatus(refreshed.status) == DecisionCaseStatus.DIAGNOSING:
            await self.transition_case(
                connection,
                case=refreshed,
                target=DecisionCaseStatus.OPTIONS_READY,
                actor_id="system:option-builder",
                reason_code="OPTIONS_GENERATED",
                metadata={"option_count": len(created)},
            )
        return created

    async def save_diagnosis(
        self,
        connection: Any,
        *,
        case_id: str,
        organization_ids: tuple[str, ...],
        diagnosis: dict[str, Any],
    ) -> None:
        case = await self.get_case(connection, case_id=case_id, organization_ids=organization_ids)
        changed = await connection.execute(
            text(
                """UPDATE agent_decision_cases
                   SET diagnosis=CAST(:diagnosis AS JSONB), updated_at=CURRENT_TIMESTAMP
                   WHERE id=:id"""
            ),
            {"id": case.id, "diagnosis": _json(diagnosis)},
        )
        if int(changed.rowcount or 0) != 1:
            raise DecisionConflictError("保存诊断证据失败")
        await connection.execute(
            text(
                """INSERT INTO agent_decision_case_events (
                    case_id, event_type, from_status, to_status, actor_id, reason_code, metadata
                ) VALUES (
                    :case_id, 'DIAGNOSIS_UPDATED', :status, :status,
                    'system:diagnosis', 'STATISTICAL_DIAGNOSIS_COMPLETED',
                    CAST(:metadata AS JSONB)
                )"""
            ),
            {
                "case_id": case.id,
                "status": case.status,
                "metadata": _json(
                    {
                        "classification": diagnosis.get("classification"),
                        "causality_status": diagnosis.get("causality_status"),
                    }
                ),
            },
        )

    async def list_cases(
        self,
        connection: Any,
        *,
        organization_ids: tuple[str, ...],
        status: str | None = None,
        limit: int = 100,
    ) -> list[DecisionCaseRecord]:
        rows = (
            (
                await connection.execute(
                    text(
                        """SELECT * FROM agent_decision_cases
                           WHERE organization_id = ANY(CAST(:organizations AS TEXT[]))
                             AND (:status IS NULL OR status=:status)
                           ORDER BY created_at DESC LIMIT :limit"""
                    ),
                    {"organizations": list(organization_ids), "status": status, "limit": limit},
                )
            )
            .mappings()
            .all()
        )
        return [_case_from_row(row) for row in rows]

    async def get_case(
        self, connection: Any, *, case_id: str, organization_ids: tuple[str, ...]
    ) -> DecisionCaseRecord:
        row = (
            (
                await connection.execute(
                    text(
                        """SELECT * FROM agent_decision_cases
                           WHERE id=:id AND organization_id = ANY(CAST(:organizations AS TEXT[]))"""
                    ),
                    {"id": case_id, "organizations": list(organization_ids)},
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise DecisionNotFoundError("决策案例不存在")
        return _case_from_row(row)

    async def list_options(
        self, connection: Any, *, case_id: str, organization_ids: tuple[str, ...]
    ) -> list[DecisionOptionRecord]:
        await self.get_case(connection, case_id=case_id, organization_ids=organization_ids)
        rows = (
            (
                await connection.execute(
                    text(
                        "SELECT * FROM agent_decision_options WHERE case_id=:case_id ORDER BY score DESC, id"
                    ),
                    {"case_id": case_id},
                )
            )
            .mappings()
            .all()
        )
        return [_option_from_row(row) for row in rows]

    async def list_case_events(
        self, connection: Any, *, case_id: str, organization_ids: tuple[str, ...]
    ) -> list[dict[str, Any]]:
        await self.get_case(connection, case_id=case_id, organization_ids=organization_ids)
        rows = (
            (
                await connection.execute(
                    text(
                        """SELECT id, event_type, from_status, to_status, actor_id,
                                  reason_code, metadata, created_at
                           FROM agent_decision_case_events
                           WHERE case_id=:case_id ORDER BY id"""
                    ),
                    {"case_id": case_id},
                )
            )
            .mappings()
            .all()
        )
        return [dict(row) for row in rows]

    async def list_case_actions(
        self, connection: Any, *, case_id: str, organization_ids: tuple[str, ...]
    ) -> list[dict[str, Any]]:
        await self.get_case(connection, case_id=case_id, organization_ids=organization_ids)
        rows = (
            (
                await connection.execute(
                    text(
                        """SELECT id, case_id, option_id, organization_id, action_type,
                                  status, attempt_count, last_error_code, result,
                                  started_at, completed_at, created_at, updated_at
                           FROM agent_action_runs WHERE case_id=:case_id
                           ORDER BY created_at, id"""
                    ),
                    {"case_id": case_id},
                )
            )
            .mappings()
            .all()
        )
        return [dict(row) for row in rows]

    async def get_option(
        self,
        connection: Any,
        *,
        option_id: str,
        case_id: str,
        organization_ids: tuple[str, ...],
    ) -> DecisionOptionRecord:
        await self.get_case(connection, case_id=case_id, organization_ids=organization_ids)
        row = (
            (
                await connection.execute(
                    text("SELECT * FROM agent_decision_options WHERE id=:id AND case_id=:case_id"),
                    {"id": option_id, "case_id": case_id},
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise DecisionNotFoundError("候选方案不存在")
        return _option_from_row(row)

    async def transition_case(
        self,
        connection: Any,
        *,
        case: DecisionCaseRecord,
        target: DecisionCaseStatus,
        actor_id: str,
        reason_code: str,
        metadata: dict[str, Any] | None = None,
        selected_option_id: str | None = None,
    ) -> None:
        current = DecisionCaseStatus(case.status)
        validate_case_transition(current, target)
        result = await connection.execute(
            text(
                """UPDATE agent_decision_cases
                   SET status=:target, selected_option_id=COALESCE(:selected_option_id, selected_option_id),
                       updated_at=CURRENT_TIMESTAMP
                   WHERE id=:id AND status=:current"""
            ),
            {
                "id": case.id,
                "current": current.value,
                "target": target.value,
                "selected_option_id": selected_option_id,
            },
        )
        if int(result.rowcount or 0) != 1:
            raise DecisionConflictError("决策案例状态已被其他请求修改")
        await self._append_case_event(
            connection,
            case_id=case.id,
            from_status=current.value,
            to_status=target.value,
            actor_id=actor_id,
            reason_code=reason_code,
            metadata=metadata or {},
        )

    async def record_approval(
        self,
        connection: Any,
        *,
        case: DecisionCaseRecord,
        option_id: str,
        decision: str,
        decision_request_id: str,
        actor_id: str,
        comment: str | None,
    ) -> str:
        existing = (
            (
                await connection.execute(
                    text(
                        """SELECT id, option_id, decision, actor_id
                           FROM agent_decision_approvals
                           WHERE case_id=:case_id AND decision_request_id=:request_id"""
                    ),
                    {"case_id": case.id, "request_id": decision_request_id},
                )
            )
            .mappings()
            .first()
        )
        if existing is not None:
            if (
                str(existing["option_id"]) != option_id
                or str(existing["decision"]) != decision
                or str(existing["actor_id"]) != actor_id
            ):
                raise DecisionConflictError("决定请求 ID 已被不同决定使用")
            return str(existing["id"])
        if case.status != DecisionCaseStatus.WAITING_APPROVAL.value:
            raise DecisionConflictError("决策案例当前不等待审批")
        valid_option = await connection.execute(
            text("SELECT 1 FROM agent_decision_options WHERE id=:id AND case_id=:case_id"),
            {"id": option_id, "case_id": case.id},
        )
        if valid_option.scalar_one_or_none() is None:
            raise DecisionNotFoundError("候选方案不存在")
        approval_id = uuid4().hex
        row = await connection.execute(
            text(
                """INSERT INTO agent_decision_approvals (
                    id, case_id, option_id, decision, decision_request_id, actor_id, comment
                ) VALUES (
                    :id, :case_id, :option_id, :decision, :request_id, :actor_id, :comment
                ) ON CONFLICT (case_id, decision_request_id) DO UPDATE SET
                    decision_request_id=EXCLUDED.decision_request_id
                WHERE agent_decision_approvals.option_id=EXCLUDED.option_id
                  AND agent_decision_approvals.decision=EXCLUDED.decision
                  AND agent_decision_approvals.actor_id=EXCLUDED.actor_id
                RETURNING id"""
            ),
            {
                "id": approval_id,
                "case_id": case.id,
                "option_id": option_id,
                "decision": decision,
                "request_id": decision_request_id,
                "actor_id": actor_id,
                "comment": comment,
            },
        )
        persisted = row.scalar_one_or_none()
        if persisted is None:
            raise DecisionConflictError("决定请求 ID 已被不同决定使用")
        await self.transition_case(
            connection,
            case=case,
            target=(
                DecisionCaseStatus.APPROVED
                if decision == "APPROVE"
                else DecisionCaseStatus.APPROVAL_REJECTED
            ),
            actor_id=actor_id,
            reason_code=("OPTION_APPROVED" if decision == "APPROVE" else "OPTION_REJECTED"),
            selected_option_id=option_id,
        )
        return str(persisted)

    async def create_action_run(
        self,
        connection: Any,
        *,
        case: DecisionCaseRecord,
        option: DecisionOptionRecord,
        authorized: bool,
    ) -> str:
        action_id = uuid4().hex
        idempotency_key = f"decision:{case.id}:option:{option.id}"
        payload_json = _json(option.action_payload)
        digest = hashlib.sha256(payload_json.encode()).hexdigest()
        row = await connection.execute(
            text(
                """INSERT INTO agent_action_runs (
                    id, case_id, option_id, organization_id, action_type, payload,
                    payload_digest, idempotency_key, status, result
                ) VALUES (
                    :id, :case_id, :option_id, :organization_id, :action_type,
                    CAST(:payload AS JSONB), :digest, :idempotency_key, :status, '{}'::jsonb
                ) ON CONFLICT (idempotency_key) DO UPDATE SET
                    idempotency_key=EXCLUDED.idempotency_key RETURNING id"""
            ),
            {
                "id": action_id,
                "case_id": case.id,
                "option_id": option.id,
                "organization_id": case.organization_id,
                "action_type": option.action_type,
                "payload": payload_json,
                "digest": digest,
                "idempotency_key": idempotency_key,
                "status": "QUEUED" if authorized else "PLANNED",
            },
        )
        return str(row.scalar_one())

    async def list_actions(
        self,
        connection: Any,
        *,
        organization_ids: tuple[str, ...],
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        rows = (
            (
                await connection.execute(
                    text(
                        """SELECT id, case_id, option_id, organization_id, action_type,
                                  status, attempt_count, last_error_code, result,
                                  started_at, completed_at, created_at, updated_at
                           FROM agent_action_runs
                           WHERE organization_id = ANY(CAST(:organizations AS TEXT[]))
                             AND (:status IS NULL OR status=:status)
                           ORDER BY created_at DESC LIMIT :limit"""
                    ),
                    {"organizations": list(organization_ids), "status": status, "limit": limit},
                )
            )
            .mappings()
            .all()
        )
        return [dict(row) for row in rows]

    async def expire_waiting_cases(self, connection: Any, *, limit: int = 100) -> int:
        rows = (
            (
                await connection.execute(
                    text(
                        """SELECT * FROM agent_decision_cases
                           WHERE status='WAITING_APPROVAL'
                             AND expires_at <= CURRENT_TIMESTAMP
                           ORDER BY expires_at, id FOR UPDATE SKIP LOCKED LIMIT :limit"""
                    ),
                    {"limit": limit},
                )
            )
            .mappings()
            .all()
        )
        for row in rows:
            await self.transition_case(
                connection,
                case=_case_from_row(row),
                target=DecisionCaseStatus.EXPIRED,
                actor_id="system:decision-expiry",
                reason_code="APPROVAL_WINDOW_EXPIRED",
            )
        return len(rows)

    async def claim_actions(
        self,
        connection: Any,
        *,
        worker_id: str,
        limit: int,
        lock_timeout_seconds: int = 300,
    ) -> list[dict[str, Any]]:
        rows = (
            (
                await connection.execute(
                    text(
                        """WITH picked AS (
                            SELECT id FROM agent_action_runs
                            WHERE (status IN ('QUEUED','RETRYABLE_FAILED')
                                   AND available_at <= CURRENT_TIMESTAMP)
                               OR (status='RUNNING' AND locked_at <= CURRENT_TIMESTAMP
                                   - (:lock_timeout * INTERVAL '1 second'))
                            ORDER BY created_at, id FOR UPDATE SKIP LOCKED LIMIT :limit
                        )
                        UPDATE agent_action_runs AS action
                           SET status='RUNNING', locked_by=:worker_id,
                               locked_at=CURRENT_TIMESTAMP,
                               started_at=COALESCE(started_at, CURRENT_TIMESTAMP),
                               attempt_count=attempt_count + 1,
                               updated_at=CURRENT_TIMESTAMP
                          FROM picked WHERE action.id=picked.id RETURNING action.*"""
                    ),
                    {
                        "worker_id": worker_id,
                        "limit": limit,
                        "lock_timeout": lock_timeout_seconds,
                    },
                )
            )
            .mappings()
            .all()
        )
        return [dict(row) for row in rows]

    async def complete_action(
        self,
        connection: Any,
        *,
        action_id: str,
        worker_id: str,
        result: dict[str, Any],
    ) -> bool:
        changed = await connection.execute(
            text(
                """UPDATE agent_action_runs SET status='SUCCEEDED',
                   result=CAST(:result AS JSONB), completed_at=CURRENT_TIMESTAMP,
                   locked_by=NULL, locked_at=NULL, last_error_code=NULL,
                   updated_at=CURRENT_TIMESTAMP
                   WHERE id=:id AND status='RUNNING' AND locked_by=:worker_id"""
            ),
            {"id": action_id, "worker_id": worker_id, "result": _json(result)},
        )
        return int(changed.rowcount or 0) == 1

    async def fail_action(
        self,
        connection: Any,
        *,
        action_id: str,
        worker_id: str,
        error_code: str,
        max_attempts: int,
        retry_delay_seconds: int,
    ) -> str:
        row = (
            await connection.execute(
                text(
                    """UPDATE agent_action_runs SET
                           status=CASE WHEN attempt_count >= :max_attempts
                                       THEN 'FINAL_FAILED' ELSE 'RETRYABLE_FAILED' END,
                           available_at=CURRENT_TIMESTAMP + (:delay * INTERVAL '1 second'),
                           last_error_code=:error_code, locked_by=NULL, locked_at=NULL,
                           completed_at=CASE WHEN attempt_count >= :max_attempts
                                             THEN CURRENT_TIMESTAMP ELSE NULL END,
                           updated_at=CURRENT_TIMESTAMP
                           WHERE id=:id AND status='RUNNING' AND locked_by=:worker_id
                           RETURNING status"""
                ),
                {
                    "id": action_id,
                    "worker_id": worker_id,
                    "error_code": error_code[:128],
                    "max_attempts": max_attempts,
                    "delay": retry_delay_seconds,
                },
            )
        ).scalar_one_or_none()
        if row is None:
            raise DecisionConflictError("动作锁已丢失")
        return str(row)

    async def create_operations_task(
        self,
        connection: Any,
        *,
        action_id: str,
        organization_id: str,
        title: str,
        assignee_id: str | None = None,
    ) -> str:
        task_id = uuid4().hex
        row = await connection.execute(
            text(
                """INSERT INTO agent_operations_tasks (
                    id, action_run_id, organization_id, title, assignee_id
                ) VALUES (:id, :action_id, :organization_id, :title, :assignee_id)
                ON CONFLICT (action_run_id) DO UPDATE SET
                    action_run_id=EXCLUDED.action_run_id RETURNING id"""
            ),
            {
                "id": task_id,
                "action_id": action_id,
                "organization_id": organization_id,
                "title": title[:200],
                "assignee_id": assignee_id,
            },
        )
        return str(row.scalar_one())

    async def cancel_action(
        self,
        connection: Any,
        *,
        action_id: str,
        organization_ids: tuple[str, ...],
    ) -> str | None:
        changed = await connection.execute(
            text(
                """UPDATE agent_action_runs SET status='CANCELLED',
                   completed_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP
                   WHERE id=:id
                     AND organization_id = ANY(CAST(:organizations AS TEXT[]))
                     AND status IN ('PLANNED','AUTHORIZED','QUEUED','RETRYABLE_FAILED')
                   RETURNING case_id"""
            ),
            {"id": action_id, "organizations": list(organization_ids)},
        )
        case_id = changed.scalar_one_or_none()
        return str(case_id) if case_id is not None else None

    async def retry_final_action(
        self,
        connection: Any,
        *,
        action_id: str,
        organization_ids: tuple[str, ...],
    ) -> bool:
        changed = await connection.execute(
            text(
                """UPDATE agent_action_runs SET status='QUEUED', attempt_count=0,
                   available_at=CURRENT_TIMESTAMP, last_error_code=NULL,
                   completed_at=NULL, updated_at=CURRENT_TIMESTAMP
                   WHERE id=:id
                     AND organization_id = ANY(CAST(:organizations AS TEXT[]))
                     AND status='FINAL_FAILED'"""
            ),
            {"id": action_id, "organizations": list(organization_ids)},
        )
        return int(changed.rowcount or 0) == 1

    async def create_experiment(
        self,
        connection: Any,
        *,
        organization_id: str,
        policy_id: str,
        name: str,
        treatment_percent: int,
        primary_metric: str,
        guardrail_metrics: tuple[str, ...],
        observation_days: int,
        actor_id: str,
    ) -> dict[str, Any]:
        experiment_id = uuid4().hex
        salt = uuid4().hex
        row = (
            (
                await connection.execute(
                    text(
                        """INSERT INTO agent_experiments (
                            id, organization_id, policy_id, name, treatment_percent,
                            primary_metric, guardrail_metrics, observation_days, salt, created_by
                        ) SELECT :id, :organization_id, policy.id, :name, :treatment_percent,
                                 :primary_metric, CAST(:guardrails AS JSONB), :observation_days,
                                 :salt, :actor_id
                          FROM agent_strategy_policies AS policy
                         WHERE policy.id=:policy_id AND policy.organization_id=:organization_id
                        RETURNING *"""
                    ),
                    {
                        "id": experiment_id,
                        "organization_id": organization_id,
                        "policy_id": policy_id,
                        "name": name,
                        "treatment_percent": treatment_percent,
                        "primary_metric": primary_metric,
                        "guardrails": _json(guardrail_metrics),
                        "observation_days": observation_days,
                        "salt": salt,
                        "actor_id": actor_id,
                    },
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise DecisionNotFoundError("策略不存在")
        return dict(row)

    async def save_assignment(
        self,
        connection: Any,
        *,
        experiment_id: str,
        subject_key_hash: str,
        group_name: str,
    ) -> str:
        row = await connection.execute(
            text(
                """INSERT INTO agent_experiment_assignments (
                    experiment_id, subject_key_hash, group_name
                ) VALUES (:experiment_id, :subject_hash, :group_name)
                ON CONFLICT (experiment_id, subject_key_hash) DO UPDATE SET
                    subject_key_hash=EXCLUDED.subject_key_hash
                WHERE agent_experiment_assignments.group_name=EXCLUDED.group_name
                RETURNING group_name"""
            ),
            {
                "experiment_id": experiment_id,
                "subject_hash": subject_key_hash,
                "group_name": group_name,
            },
        )
        value = row.scalar_one_or_none()
        if value is None:
            raise DecisionConflictError("实验对象的分组不能变更")
        return str(value)

    async def list_experiments(
        self,
        connection: Any,
        *,
        organization_ids: tuple[str, ...],
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        rows = (
            (
                await connection.execute(
                    text(
                        """SELECT id, organization_id, policy_id, name, status,
                                  treatment_percent, primary_metric, guardrail_metrics,
                                  observation_days, created_at
                           FROM agent_experiments
                           WHERE organization_id = ANY(CAST(:organizations AS TEXT[]))
                           ORDER BY created_at DESC LIMIT :limit"""
                    ),
                    {"organizations": list(organization_ids), "limit": limit},
                )
            )
            .mappings()
            .all()
        )
        return [dict(row) for row in rows]

    async def update_experiment_status(
        self,
        connection: Any,
        *,
        experiment_id: str,
        organization_ids: tuple[str, ...],
        target: str,
    ) -> dict[str, Any]:
        row = (
            (
                await connection.execute(
                    text(
                        """SELECT id, status FROM agent_experiments
                           WHERE id=:id
                             AND organization_id = ANY(CAST(:organizations AS TEXT[]))
                           FOR UPDATE"""
                    ),
                    {"id": experiment_id, "organizations": list(organization_ids)},
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise DecisionNotFoundError("实验不存在")
        allowed = {
            "DRAFT": {"RUNNING", "STOPPED"},
            "RUNNING": {"STOPPED", "COMPLETED"},
            "STOPPED": {"RUNNING", "COMPLETED"},
            "COMPLETED": set(),
        }
        current = str(row["status"])
        if target not in allowed[current]:
            raise DecisionConflictError(f"不允许的实验状态转换：{current}->{target}")
        updated = (
            (
                await connection.execute(
                    text(
                        """UPDATE agent_experiments SET status=:target
                           WHERE id=:id AND status=:current RETURNING *"""
                    ),
                    {"id": experiment_id, "current": current, "target": target},
                )
            )
            .mappings()
            .one()
        )
        return dict(updated)

    async def save_outcome(
        self,
        connection: Any,
        *,
        case_id: str,
        organization_ids: tuple[str, ...],
        action_run_id: str | None,
        experiment_id: str | None,
        metric: str,
        group_name: str,
        value: float,
        sample_size: int,
        window_start: datetime,
        window_end: datetime,
        source_digest: str,
    ) -> str:
        case = await self.get_case(connection, case_id=case_id, organization_ids=organization_ids)
        if action_run_id is not None:
            action_exists = await connection.execute(
                text(
                    """SELECT 1 FROM agent_action_runs
                       WHERE id=:id AND case_id=:case_id AND organization_id=:organization_id"""
                ),
                {
                    "id": action_run_id,
                    "case_id": case.id,
                    "organization_id": case.organization_id,
                },
            )
            if action_exists.scalar_one_or_none() is None:
                raise DecisionNotFoundError("动作不属于当前决策案例")
        if experiment_id is not None:
            experiment_exists = await connection.execute(
                text(
                    """SELECT 1 FROM agent_experiments
                       WHERE id=:id AND organization_id=:organization_id"""
                ),
                {"id": experiment_id, "organization_id": case.organization_id},
            )
            if experiment_exists.scalar_one_or_none() is None:
                raise DecisionNotFoundError("实验不属于当前机构")
            if group_name == "ALL":
                raise DecisionConflictError("实验观察必须标记 TREATMENT 或 CONTROL")
        observation_id = uuid4().hex
        row = await connection.execute(
            text(
                """INSERT INTO agent_outcome_observations (
                    id, case_id, action_run_id, experiment_id, metric, group_name,
                    value, sample_size, window_start, window_end, source_digest
                ) VALUES (
                    :id, :case_id, :action_run_id, :experiment_id, :metric, :group_name,
                    :value, :sample_size, :window_start, :window_end, :source_digest
                ) ON CONFLICT (
                    case_id, metric, group_name, window_start, window_end
                ) DO UPDATE SET source_digest=EXCLUDED.source_digest
                WHERE agent_outcome_observations.source_digest=EXCLUDED.source_digest
                RETURNING id"""
            ),
            {
                "id": observation_id,
                "case_id": case.id,
                "action_run_id": action_run_id,
                "experiment_id": experiment_id,
                "metric": metric,
                "group_name": group_name,
                "value": value,
                "sample_size": sample_size,
                "window_start": window_start,
                "window_end": window_end,
                "source_digest": source_digest,
            },
        )
        persisted = row.scalar_one_or_none()
        if persisted is None:
            raise DecisionConflictError("相同观察窗口已存在不同来源摘要")
        return str(persisted)

    async def list_outcomes(
        self,
        connection: Any,
        *,
        case_id: str,
        organization_ids: tuple[str, ...],
    ) -> list[dict[str, Any]]:
        await self.get_case(connection, case_id=case_id, organization_ids=organization_ids)
        rows = (
            (
                await connection.execute(
                    text(
                        """SELECT id, case_id, action_run_id, experiment_id, metric,
                                  group_name, value, sample_size, window_start, window_end,
                                  source_digest, created_at
                           FROM agent_outcome_observations
                           WHERE case_id=:case_id ORDER BY metric, group_name, window_start"""
                    ),
                    {"case_id": case_id},
                )
            )
            .mappings()
            .all()
        )
        return [dict(row) for row in rows]

    async def _append_case_event(
        self,
        connection: Any,
        *,
        case_id: str,
        from_status: str | None,
        to_status: str,
        actor_id: str,
        reason_code: str,
        metadata: dict[str, Any],
    ) -> None:
        await connection.execute(
            text(
                """INSERT INTO agent_decision_case_events (
                    case_id, event_type, from_status, to_status, actor_id, reason_code, metadata
                ) VALUES (
                    :case_id, 'STATUS_CHANGED', :from_status, :to_status,
                    :actor_id, :reason_code, CAST(:metadata AS JSONB)
                )"""
            ),
            {
                "case_id": case_id,
                "from_status": from_status,
                "to_status": to_status,
                "actor_id": actor_id,
                "reason_code": reason_code,
                "metadata": _json(metadata),
            },
        )


def _number(value: Decimal | float) -> float:
    return float(value)


def _case_from_row(row: Any) -> DecisionCaseRecord:
    return DecisionCaseRecord(
        id=str(row["id"]),
        organization_id=str(row["organization_id"]),
        objective_id=str(row["objective_id"]),
        policy_id=str(row["policy_id"]),
        anomaly_id=str(row["anomaly_id"]),
        title=str(row["title"]),
        status=str(row["status"]),
        risk_level=str(row["risk_level"]),
        diagnosis=dict(row["diagnosis"]),
        selected_option_id=str(row["selected_option_id"]) if row["selected_option_id"] else None,
        trace_id=str(row["trace_id"]) if row["trace_id"] else None,
        expires_at=row["expires_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _option_from_row(row: Any) -> DecisionOptionRecord:
    return DecisionOptionRecord(
        id=str(row["id"]),
        case_id=str(row["case_id"]),
        option_key=str(row["option_key"]),
        title=str(row["title"]),
        action_type=str(row["action_type"]),
        action_payload=dict(row["action_payload"]),
        evidence_refs=tuple(row["evidence_refs"]),
        expected_benefit=_number(row["expected_benefit"]),
        estimated_cost=_number(row["estimated_cost"]),
        risk_penalty=_number(row["risk_penalty"]),
        uncertainty_penalty=_number(row["uncertainty_penalty"]),
        score=_number(row["score"]),
        confidence=float(row["confidence"]),
    )
