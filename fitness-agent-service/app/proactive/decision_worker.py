"""主动候选决策 Worker：结构化 Agent 决策、安全校验并写通知 Outbox。"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

import structlog

from app.core.metrics import HttpMetrics
from app.infrastructure.database import Database
from app.notifications.outbox import NotificationOutboxRepository

from .candidate_repository import ProactiveCandidateRepository
from .candidates import (
    NOTIFICATION_TYPE_BY_CANDIDATE,
    CandidateRecord,
    CandidateType,
    ProactiveDecisionType,
)
from .context_builder import CONTEXT_VERSION, ProactiveContextBuilder
from .decision_agent import POLICY_VERSION, PROMPT_VERSION, ProactiveDecisionAgent

_logger = structlog.get_logger("proactive.decision_worker")
_WORKER_NAME = "proactive_decision"


@dataclass(frozen=True)
class ProactiveDecisionRunResult:
    claimed: int
    notified: int
    ignored: int
    deferred: int
    retried: int


class ProactiveDecisionWorker:
    def __init__(
        self,
        database: Database,
        candidates: ProactiveCandidateRepository,
        notifications: NotificationOutboxRepository,
        agent: ProactiveDecisionAgent,
        *,
        batch_size: int = 30,
        max_attempts: int = 5,
        worker_id: str | None = None,
        metrics: HttpMetrics | None = None,
        context_builder: ProactiveContextBuilder | None = None,
    ) -> None:
        self.database = database
        self.candidates = candidates
        self.notifications = notifications
        self.agent = agent
        self.batch_size = batch_size
        self.max_attempts = max_attempts
        self.worker_id = worker_id or f"proactive-decision:{uuid4()}"
        self.metrics = metrics
        self.context_builder = context_builder

    async def run_once(self) -> ProactiveDecisionRunResult:
        async with self.database.engine.begin() as connection:
            records = await self.candidates.claim_batch(
                connection, worker_id=self.worker_id, limit=self.batch_size
            )
        counters = {"notified": 0, "ignored": 0, "deferred": 0, "retried": 0}
        for record in records:
            try:
                outcome = await self._process(record)
                counters[outcome] += 1
            except Exception as exc:
                counters["retried"] += 1
                _logger.warning(
                    "proactive_decision_failed",
                    candidate_type=record.candidate_type,
                    error_type=type(exc).__name__,
                )
                async with self.database.engine.begin() as connection:
                    changed = await self.candidates.retry(
                        connection,
                        candidate=record,
                        worker_id=self.worker_id,
                        error_code=_error_code(exc),
                        max_attempts=self.max_attempts,
                        delay_seconds=min(1800, 30 * (2 ** min(record.attempt_count, 6))),
                    )
                    if not changed:
                        raise RuntimeError("重试主动候选时丢失候选锁") from exc
        if self.metrics is not None:
            self.metrics.maintenance_runs_total.labels(
                worker=_WORKER_NAME, status="succeeded"
            ).inc()
            for outcome, count in counters.items():
                self.metrics.maintenance_items_total.labels(
                    worker=_WORKER_NAME, outcome=outcome
                ).inc(count)
        return ProactiveDecisionRunResult(claimed=len(records), **counters)

    async def _process(self, candidate: CandidateRecord) -> str:
        context: dict[str, object] = {}
        decision_candidate = candidate
        if self.context_builder is not None:
            decision_candidate, context = await self.context_builder.build(candidate)
        result = await self.agent.decide(decision_candidate)
        output = result.output
        async with self.database.engine.begin() as connection:
            await self.candidates.save_context_snapshot(
                connection,
                candidate_id=candidate.id,
                context=context,
                context_version=CONTEXT_VERSION,
            )
            await self.candidates.save_decision(
                connection,
                candidate=candidate,
                output=output,
                model_name=self.agent.models.settings.llm_model,
                prompt_version=PROMPT_VERSION,
                policy_version=POLICY_VERSION,
                input_digest=result.input_digest,
                output_digest=result.output_digest,
                safety_status=result.safety_status,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
            )
            if output.decision == ProactiveDecisionType.IGNORE:
                if not await self.candidates.complete(
                    connection,
                    candidate_id=candidate.id,
                    worker_id=self.worker_id,
                    status="IGNORED",
                ):
                    raise RuntimeError("忽略主动候选时丢失候选锁")
                if self.metrics is not None:
                    self.metrics.record_proactive_decision(
                        output.decision.value, result.content_source
                    )
                return "ignored"
            if output.decision == ProactiveDecisionType.DEFER:
                if not await self.candidates.defer(
                    connection,
                    candidate_id=candidate.id,
                    worker_id=self.worker_id,
                    minutes=output.defer_minutes,
                ):
                    raise RuntimeError("延迟主动候选时丢失候选锁")
                if self.metrics is not None:
                    self.metrics.record_proactive_decision(
                        output.decision.value, result.content_source
                    )
                return "deferred"

            candidate_type = CandidateType(candidate.candidate_type)
            notification_type = NOTIFICATION_TYPE_BY_CANDIDATE[candidate_type]
            snapshot = await self.candidates.create_message_snapshot(
                connection,
                candidate=candidate,
                output=output,
                template_key=notification_type,
                cta_type=output.recommended_action.value
                if output.recommended_action.value != "NONE"
                else None,
                content_source=result.content_source,
            )
            await self.notifications.enqueue_on_connection(
                connection,
                notification_type=notification_type,
                subject_user_id=candidate.subject_user_id,
                organization_id=candidate.organization_id,
                aggregate_type=candidate.aggregate_type,
                aggregate_id=candidate.aggregate_id,
                dedupe_key=f"proactive-agent:{candidate.id}:IN_APP",
                payload={
                    "candidate_id": candidate.id,
                    "message_snapshot_id": snapshot.id,
                    "recommended_action": output.recommended_action.value,
                },
            )
            if not await self.candidates.complete(
                connection,
                candidate_id=candidate.id,
                worker_id=self.worker_id,
                status="NOTIFICATION_QUEUED",
            ):
                raise RuntimeError("主动通知入队时丢失候选锁")
        if self.metrics is not None:
            self.metrics.record_proactive_decision(output.decision.value, result.content_source)
        return "notified"


def _error_code(exc: Exception) -> str:
    text = str(exc)
    if text.startswith("PROACTIVE_"):
        return text[:128]
    return "PROACTIVE_DECISION_FAILED"
