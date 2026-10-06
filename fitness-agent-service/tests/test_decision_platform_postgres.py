"""需要显式开启的经营决策 PostgreSQL 真实闭环测试。"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.core.config import Settings
from app.decision.action_worker import DecisionActionWorker
from app.decision.models import (
    DecisionCaseStatus,
    MetricSnapshotInput,
    ObjectiveInput,
    PolicyMode,
    RiskLevel,
    StrategyPolicyInput,
)
from app.decision.repository import DecisionRepository
from app.decision.service import DecisionPlatformService
from app.infrastructure.database import Database
from app.notifications.outbox import NotificationOutboxRepository

pytestmark = pytest.mark.skipif(
    os.getenv("AGENT_RUN_POSTGRES_TESTS") != "1",
    reason="需要显式设置 AGENT_RUN_POSTGRES_TESTS=1，并提供已迁移的 PostgreSQL",
)


@pytest.mark.asyncio
async def test_snapshot_to_autonomous_action_and_observation_is_recoverable() -> None:
    database = Database(Settings())
    repository = DecisionRepository()
    service = DecisionPlatformService(database, repository)
    token = uuid4().hex
    organization_id = f"decision-postgres-{token}"
    metric = f"TEST_METRIC_{token.upper()}"
    base = datetime(2026, 1, 1, tzinfo=UTC)
    case_id: str | None = None
    try:
        async with database.engine.begin() as connection:
            objective = await repository.create_objective(
                connection,
                organization_id=organization_id,
                payload=ObjectiveInput(
                    objective_key=f"OBJECTIVE_{token.upper()}",
                    name="集成测试目标",
                    metric=metric,
                    direction="INCREASE",
                ),
                actor_id="test",
            )
            await repository.create_policy(
                connection,
                organization_id=organization_id,
                objective_id=str(objective["id"]),
                payload=StrategyPolicyInput(
                    strategy_key=f"POLICY_{token.upper()}",
                    mode=PolicyMode.AUTONOMOUS,
                    risk_level=RiskLevel.L1,
                    config={
                        "minimum_samples": 3,
                        "history_periods": 7,
                        "relative_threshold": 0.15,
                        "z_threshold": 3.5,
                        "minimum_confidence": 0.68,
                    },
                    allowed_actions=("CREATE_OPERATIONS_TASK",),
                    budget_limit=0,
                    max_affected_subjects=1,
                ),
                actor_id="test",
            )

        last_snapshot: MetricSnapshotInput | None = None
        for index, value in enumerate((100.0, 100.0, 100.0, 50.0)):
            start = base + timedelta(days=index)
            last_snapshot = MetricSnapshotInput(
                metric=metric,
                dimension_id=organization_id,
                bucket="DAY",
                period_start=start,
                period_end=start + timedelta(days=1),
                value=value,
                sample_size=100,
                catalog_version="integration-v1",
                source_digest=f"{index + 1:064x}",
            )
            result = await service.ingest_snapshot(
                organization_id=organization_id,
                payload=last_snapshot,
                trace_id=f"trace-{token}",
            )
        assert result.anomaly_detected is True
        assert result.reason_code == "AUTONOMOUS_ALLOWED"
        assert result.case_id is not None
        case_id = result.case_id

        worker = DecisionActionWorker(
            database,
            repository,
            NotificationOutboxRepository(),
            batch_size=5,
            max_attempts=2,
            worker_id=f"integration-{token}",
        )
        worker_result = await worker.run_once()
        assert worker_result.succeeded == 1
        assert last_snapshot is not None
        replay = await service.ingest_snapshot(
            organization_id=organization_id,
            payload=last_snapshot,
            trace_id=f"trace-replay-{token}",
        )
        assert replay.reason_code == "ANOMALY_ALREADY_PROCESSED"
        assert replay.case_id == case_id

        async with database.engine.connect() as connection:
            case = await repository.get_case(
                connection, case_id=case_id, organization_ids=(organization_id,)
            )
            actions = await repository.list_case_actions(
                connection, case_id=case_id, organization_ids=(organization_id,)
            )
            tasks = (
                await connection.execute(
                    text("SELECT COUNT(*) FROM agent_operations_tasks WHERE organization_id=:org"),
                    {"org": organization_id},
                )
            ).scalar_one()
        assert case.status == DecisionCaseStatus.OBSERVING.value
        assert case.diagnosis["status"] == "COMPLETED"
        assert case.diagnosis["causality_status"] == "NOT_ESTABLISHED"
        assert [row["status"] for row in actions] == ["SUCCEEDED"]
        assert tasks == 1
    finally:
        async with database.engine.begin() as connection:
            statements = (
                (
                    "DELETE FROM agent_outcome_observations WHERE case_id IN "
                    "(SELECT id FROM agent_decision_cases WHERE organization_id=:org)"
                ),
                (
                    "DELETE FROM agent_experiment_assignments WHERE experiment_id IN "
                    "(SELECT id FROM agent_experiments WHERE organization_id=:org)"
                ),
                "DELETE FROM agent_experiments WHERE organization_id=:org",
                "DELETE FROM agent_operations_tasks WHERE organization_id=:org",
                "DELETE FROM agent_action_runs WHERE organization_id=:org",
                (
                    "DELETE FROM agent_decision_approvals WHERE case_id IN "
                    "(SELECT id FROM agent_decision_cases WHERE organization_id=:org)"
                ),
                (
                    "DELETE FROM agent_decision_options WHERE case_id IN "
                    "(SELECT id FROM agent_decision_cases WHERE organization_id=:org)"
                ),
                (
                    "DELETE FROM agent_decision_case_events WHERE case_id IN "
                    "(SELECT id FROM agent_decision_cases WHERE organization_id=:org)"
                ),
                "DELETE FROM agent_decision_cases WHERE organization_id=:org",
                "DELETE FROM agent_anomaly_events WHERE organization_id=:org",
                "DELETE FROM agent_metric_snapshots WHERE organization_id=:org",
                "DELETE FROM agent_strategy_policies WHERE organization_id=:org",
                "DELETE FROM agent_business_objectives WHERE organization_id=:org",
            )
            for statement in statements:
                await connection.execute(text(statement), {"org": organization_id})
        await database.close()
