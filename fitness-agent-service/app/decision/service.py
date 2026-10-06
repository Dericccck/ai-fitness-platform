"""从指标快照到可靠动作的经营决策应用服务。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.metrics import HttpMetrics
from app.infrastructure.database import Database

from .engine import DETECTOR_VERSION, assess_anomaly, evaluate_policy
from .models import (
    DecisionCaseStatus,
    DecisionOptionInput,
    MetricSnapshotInput,
    PolicyMode,
    RiskLevel,
)
from .repository import DecisionRepository


@dataclass(frozen=True)
class SnapshotDecisionResult:
    snapshot_id: str
    anomaly_detected: bool
    anomaly_id: str | None
    case_id: str | None
    reason_code: str


class DecisionPlatformService:
    def __init__(
        self,
        database: Database,
        repository: DecisionRepository,
        *,
        metrics: HttpMetrics | None = None,
    ) -> None:
        self.database = database
        self.repository = repository
        self.metrics = metrics

    async def ingest_snapshot(
        self,
        *,
        organization_id: str,
        payload: MetricSnapshotInput,
        trace_id: str | None = None,
    ) -> SnapshotDecisionResult:
        async with self.database.engine.begin() as connection:
            snapshot_id = await self.repository.upsert_metric_snapshot(
                connection, organization_id=organization_id, payload=payload
            )
            policy = await self.repository.get_active_policy_for_metric(
                connection, organization_id=organization_id, metric=payload.metric
            )
            if policy is None:
                self._record("snapshot", "no_policy")
                return SnapshotDecisionResult(snapshot_id, False, None, None, "NO_ACTIVE_POLICY")
            config = dict(policy["config"])
            history = await self.repository.metric_history(
                connection,
                organization_id=organization_id,
                metric=payload.metric,
                dimension_type=payload.dimension_type,
                dimension_id=payload.dimension_id,
                bucket=payload.bucket,
                before=payload.period_start,
                limit=int(config.get("history_periods", 28)),
            )
            assessment = assess_anomaly(
                payload.value,
                history,
                minimum_samples=int(config.get("minimum_samples", 7)),
                relative_threshold=float(config.get("relative_threshold", 0.15)),
                z_threshold=float(config.get("z_threshold", 3.5)),
            )
            if payload.quality_status != "PASSED":
                self._record("snapshot", "quality_rejected")
                return SnapshotDecisionResult(
                    snapshot_id, False, None, None, "DATA_QUALITY_REJECTED"
                )
            if not assessment.anomalous:
                self._record("anomaly", "normal")
                return SnapshotDecisionResult(
                    snapshot_id, False, None, None, assessment.reason_code
                )
            anomaly_id, case_id, case_created = await self.repository.create_anomaly_and_case(
                connection,
                organization_id=organization_id,
                policy=policy,
                snapshot_id=snapshot_id,
                anomaly_type="STATISTICAL",
                severity=assessment.severity,
                actual_value=assessment.actual,
                expected_value=assessment.expected,
                deviation_percent=assessment.deviation_percent,
                detector_version=DETECTOR_VERSION,
                evidence=assessment.evidence,
                period_key=payload.period_start.isoformat(),
                trace_id=trace_id,
            )
            if not case_created:
                self._record("anomaly", "duplicate")
                return SnapshotDecisionResult(
                    snapshot_id, True, anomaly_id, case_id, "ANOMALY_ALREADY_PROCESSED"
                )
            await self.repository.save_diagnosis(
                connection,
                case_id=case_id,
                organization_ids=(organization_id,),
                diagnosis={
                    "status": "COMPLETED",
                    "classification": "STATISTICAL_DEVIATION",
                    "summary": (
                        f"{payload.metric} 当前值 {assessment.actual:g}，"
                        f"相对稳健基线偏差 {assessment.deviation_percent or 0:g}%"
                    ),
                    "actual_value": assessment.actual,
                    "expected_value": assessment.expected,
                    "deviation_percent": assessment.deviation_percent,
                    "evidence": assessment.evidence,
                    "causality_status": "NOT_ESTABLISHED",
                    "causality_notice": "当前证据只证明统计偏离，不将相关性描述为业务因果。",
                },
            )
            options = self._default_options(policy, assessment.deviation_percent)
            created = await self.repository.add_options(
                connection,
                case_id=case_id,
                organization_ids=(organization_id,),
                options=options,
            )
            case = await self.repository.get_case(
                connection, case_id=case_id, organization_ids=(organization_id,)
            )
            if case.status == DecisionCaseStatus.OPTIONS_READY.value:
                await self.repository.transition_case(
                    connection,
                    case=case,
                    target=DecisionCaseStatus.POLICY_EVALUATED,
                    actor_id="system:policy-engine",
                    reason_code="POLICY_EVALUATED",
                )
            case = await self.repository.get_case(
                connection, case_id=case_id, organization_ids=(organization_id,)
            )
            if not created:
                self._record("option", "none_supported")
                return SnapshotDecisionResult(
                    snapshot_id, True, anomaly_id, case_id, "NO_ALLOWED_OPTIONS"
                )
            selected = created[0]
            action_payload = selected.action_payload
            evaluation = evaluate_policy(
                mode=PolicyMode(str(policy["mode"])),
                risk_level=RiskLevel(str(policy["risk_level"])),
                action_type=selected.action_type,
                allowed_actions=tuple(policy["allowed_actions"]),
                affected_subjects=int(action_payload.get("affected_subjects", 1)),
                max_affected_subjects=int(policy["max_affected_subjects"]),
                estimated_cost=selected.estimated_cost,
                budget_limit=(
                    float(policy["budget_limit"]) if policy["budget_limit"] is not None else None
                ),
                data_quality_passed=True,
                confidence=selected.confidence,
                minimum_confidence=float(config.get("minimum_confidence", 0.68)),
            )
            if not evaluation.allowed:
                await self.repository.transition_case(
                    connection,
                    case=case,
                    target=DecisionCaseStatus.POLICY_REJECTED,
                    actor_id="system:policy-engine",
                    reason_code=evaluation.reason_code,
                    selected_option_id=selected.id,
                )
                self._record("policy", "rejected")
            elif evaluation.effective_mode in {PolicyMode.REPLAY, PolicyMode.SHADOW}:
                # 影子和回放模式保留在 POLICY_EVALUATED，供控制台比较，但永不创建动作。
                self._record("policy", "shadow")
            elif evaluation.requires_approval:
                await self.repository.transition_case(
                    connection,
                    case=case,
                    target=DecisionCaseStatus.WAITING_APPROVAL,
                    actor_id="system:policy-engine",
                    reason_code=evaluation.reason_code,
                    selected_option_id=selected.id,
                )
                self._record("policy", "waiting_approval")
            else:
                await self.repository.transition_case(
                    connection,
                    case=case,
                    target=DecisionCaseStatus.APPROVED,
                    actor_id="system:policy-engine",
                    reason_code=evaluation.reason_code,
                    selected_option_id=selected.id,
                )
                approved_case = await self.repository.get_case(
                    connection, case_id=case_id, organization_ids=(organization_id,)
                )
                await self.repository.create_action_run(
                    connection, case=approved_case, option=selected, authorized=True
                )
                self._record("policy", "autonomous_action")
            self._record("anomaly", "case_created")
            return SnapshotDecisionResult(
                snapshot_id, True, anomaly_id, case_id, evaluation.reason_code
            )

    async def decide_case(
        self,
        *,
        case_id: str,
        organization_ids: tuple[str, ...],
        option_id: str,
        decision: str,
        decision_request_id: str,
        actor_id: str,
        comment: str | None,
    ) -> tuple[str, str | None]:
        async with self.database.engine.begin() as connection:
            case = await self.repository.get_case(
                connection, case_id=case_id, organization_ids=organization_ids
            )
            approval_id = await self.repository.record_approval(
                connection,
                case=case,
                option_id=option_id,
                decision=decision,
                decision_request_id=decision_request_id,
                actor_id=actor_id,
                comment=comment,
            )
            if decision != "APPROVE":
                self._record("approval", "rejected")
                return approval_id, None
            updated = await self.repository.get_case(
                connection, case_id=case_id, organization_ids=organization_ids
            )
            option = await self.repository.get_option(
                connection,
                option_id=option_id,
                case_id=case_id,
                organization_ids=organization_ids,
            )
            action_id = await self.repository.create_action_run(
                connection, case=updated, option=option, authorized=True
            )
            self._record("approval", "approved")
            return approval_id, action_id

    def _record(self, event: str, outcome: str) -> None:
        if self.metrics is not None:
            self.metrics.record_business_decision_event(event, outcome)

    @staticmethod
    def _default_options(
        policy: dict[str, Any], deviation_percent: float | None
    ) -> tuple[DecisionOptionInput, ...]:
        # 聚合经营指标没有具体会员或预约主键，只能生成组织级、可幂等执行的动作。
        supported = {"CREATE_OPERATIONS_TASK", "CREATE_COACH_FOLLOW_UP"}
        actions = tuple(
            str(value) for value in policy["allowed_actions"] if str(value) in supported
        )
        magnitude = abs(deviation_percent or 0)
        result: list[DecisionOptionInput] = []
        for index, action in enumerate(actions):
            # 分数只用于同一案例内排序；真实金额收益必须由业务模型或实验结果提供。
            benefit = round(max(1.0, magnitude) * (1 - index * 0.05), 4)
            default_cost = (
                0.0 if action in {"CREATE_OPERATIONS_TASK", "CREATE_COACH_FOLLOW_UP"} else 1.0
            )
            result.append(
                DecisionOptionInput(
                    option_key=f"OPTION_{index + 1}",
                    title=_action_title(action),
                    action_type=action,
                    action_payload={
                        "organization_id": str(policy["organization_id"]),
                        "objective_id": str(policy["objective_id"]),
                        "affected_subjects": 1,
                        "title": _action_title(action),
                    },
                    evidence_refs=(f"policy:{policy['id']}",),
                    expected_benefit=benefit,
                    estimated_cost=default_cost,
                    risk_penalty=index * 0.25,
                    uncertainty_penalty=max(0.0, 5.0 - magnitude / 10),
                    confidence=min(0.95, 0.68 + magnitude / 500),
                )
            )
        return tuple(sorted(result, key=lambda option: option.score, reverse=True))


def _action_title(action: str) -> str:
    return {
        "CREATE_OPERATIONS_TASK": "创建运营跟进任务",
        "CREATE_COACH_FOLLOW_UP": "创建教练跟进任务",
        "SEND_IN_APP_REMINDER": "发送受控站内提醒",
        "PROPOSE_BOOKING_ACTION": "提出预约调整建议",
        "PROPOSE_TRAINING_PLAN_ADJUSTMENT": "提出训练计划调整建议",
        "OPEN_CUSTOMER_SERVICE_FOLLOW_UP": "创建客服跟进事项",
    }.get(action, action)
