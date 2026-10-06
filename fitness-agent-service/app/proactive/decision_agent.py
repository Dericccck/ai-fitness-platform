"""受控的主动式健身决策 Agent。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from app.infrastructure.model_gateway import JsonModelTurn, ModelGateway

from .candidates import (
    CandidateRecord,
    CandidateType,
    ProactiveAgentOutput,
    ProactiveDecisionType,
    validate_message_safety,
)

PROMPT_VERSION = "proactive-fitness-v1"
POLICY_VERSION = "proactive-policy-v1"


@dataclass(frozen=True)
class DecisionResult:
    output: ProactiveAgentOutput
    safety_status: str
    input_digest: str
    output_digest: str
    input_tokens: int | None
    output_tokens: int | None
    content_source: str


class ProactiveDecisionAgent:
    """LLM 负责模糊判断与表达；服务端负责身份、权限、渠道和执行。"""

    def __init__(
        self,
        models: ModelGateway,
        *,
        max_output_tokens: int = 700,
        min_confidence: float = 0.68,
        model_required: bool = False,
    ) -> None:
        self.models = models
        self.max_output_tokens = max_output_tokens
        self.min_confidence = min_confidence
        self.model_required = model_required

    async def decide(self, candidate: CandidateRecord) -> DecisionResult:
        messages = _messages(candidate)
        encoded_input = json.dumps(messages, ensure_ascii=False, sort_keys=True)
        input_digest = hashlib.sha256(encoded_input.encode()).hexdigest()
        turn: JsonModelTurn | None = None
        try:
            turn = await self.models.chat_json_with_usage(
                messages, max_output_tokens=self.max_output_tokens, temperature=0.1
            )
            output = ProactiveAgentOutput.model_validate_json(turn.content)
            allowed_evidence = set(_evidence_refs(candidate))
            if not set(output.evidence_refs).issubset(allowed_evidence):
                raise ValueError("PROACTIVE_UNTRUSTED_EVIDENCE")
            if output.confidence < self.min_confidence and output.decision not in {
                ProactiveDecisionType.IGNORE,
                ProactiveDecisionType.DEFER,
            }:
                raise ValueError("PROACTIVE_LOW_CONFIDENCE")
            safety_status = validate_message_safety(output)
            content_source = "LLM"
        except Exception:
            if self.model_required:
                raise
            output = deterministic_fallback(candidate)
            safety_status = "FALLBACK"
            if turn is None:
                turn = JsonModelTurn(content=output.model_dump_json())
            content_source = "FALLBACK"
            validate_message_safety(output)
        output_json = output.model_dump_json()
        return DecisionResult(
            output=output,
            safety_status=safety_status,
            input_digest=input_digest,
            output_digest=hashlib.sha256(output_json.encode()).hexdigest(),
            input_tokens=turn.input_tokens,
            output_tokens=turn.output_tokens,
            content_source=content_source,
        )


def _messages(candidate: CandidateRecord) -> list[dict[str, str]]:
    safe_context = {
        "candidate_type": candidate.candidate_type,
        "reason_code": candidate.reason_code,
        "aggregate_type": candidate.aggregate_type,
        "aggregate_id": candidate.aggregate_id,
        "priority": candidate.priority,
        "facts": candidate.facts,
        "evidence_refs": _evidence_refs(candidate),
    }
    return [
        {
            "role": "system",
            "content": (
                "你是主动式健身服务决策 Agent。只根据给定事实判断是否联系用户。"
                "禁止诊断、治疗、药物、伤病康复结论、羞辱或效果保证。"
                "不得输出用户ID、机构ID、权限、渠道或工具参数。"
                "需要修改计划、预约或业务事实时只能建议，并使用 PROPOSE_ACTION。"
                "输出严格 JSON，字段为 decision、reason_code、strategy、recommended_action、"
                "confidence、defer_minutes、title、body、evidence_refs。"
            ),
        },
        {"role": "user", "content": json.dumps(safe_context, ensure_ascii=False)},
    ]


def _evidence_refs(candidate: CandidateRecord) -> tuple[str, ...]:
    refs = [
        f"candidate:{candidate.id}",
        f"{candidate.aggregate_type.lower()}:{candidate.aggregate_id}",
    ]
    refs.extend(f"event:{event_id}" for event_id in candidate.trigger_event_ids)
    context = candidate.facts.get("agent_context")
    if isinstance(context, dict):
        recent_events = context.get("recent_events")
        if isinstance(recent_events, list):
            for item in recent_events:
                if isinstance(item, dict):
                    reference = item.get("event_ref")
                    if isinstance(reference, str) and reference.startswith("event:"):
                        refs.append(reference)
    return tuple(dict.fromkeys(refs))


def deterministic_fallback(candidate: CandidateRecord) -> ProactiveAgentOutput:
    """模型不可用时仍提供安全、有用且可审计的保守结果。"""

    candidate_type = CandidateType(candidate.candidate_type)
    evidence = _evidence_refs(candidate)
    values: dict[CandidateType, dict[str, Any]] = {
        CandidateType.TRAINING_ADHERENCE: {
            "decision": "ASK_USER",
            "strategy": "SUPPORTIVE_CHECK_IN",
            "action": "RESUME_TRAINING",
            "title": "最近的训练安排还合适吗？",
            "body": "看到最近的训练节奏有所中断。如果目前时间比较紧，可以一起把训练频率或单次时长调整得更容易坚持。",
        },
        CandidateType.TRAINING_PLAN_MISMATCH: {
            "decision": "PROPOSE_ACTION",
            "strategy": "ADJUST_PLAN_LOAD",
            "action": "ADJUST_TRAINING_PLAN",
            "title": "可以考虑调整训练计划",
            "body": "最近的执行情况与当前计划节奏不太匹配。你可以先查看计划，也可以让我协助生成一个更容易完成的调整方案。",
        },
        CandidateType.TRAINING_CYCLE_REVIEW: {
            "decision": "NOTIFY",
            "strategy": "CYCLE_REVIEW",
            "action": "OPEN_TRAINING_PLAN",
            "title": "训练周期复盘已准备好",
            "body": "本周期的训练记录已经汇总。可以查看完成情况，并决定下一周期是保持节奏还是调整计划。",
        },
        CandidateType.COACH_STUDENT_ATTENTION: {
            "decision": "NOTIFY",
            "strategy": "COACH_ATTENTION",
            "action": "REVIEW_STUDENTS",
            "title": "有学员可能需要关注",
            "body": "部分学员近期的计划执行出现连续中断，请查看受控摘要并决定是否跟进。",
        },
        CandidateType.COACH_DAILY_DIGEST: {
            "decision": "NOTIFY",
            "strategy": "COACH_DIGEST",
            "action": "REVIEW_STUDENTS",
            "title": "今日教练待办摘要",
            "body": "今日需要关注的训练计划、审核和学员执行情况已经汇总。",
        },
        CandidateType.OPERATIONS_DAILY_DIGEST: {
            "decision": "NOTIFY",
            "strategy": "OPERATIONS_DIGEST",
            "action": "REVIEW_OPERATIONS",
            "title": "今日运营摘要",
            "body": "预约、训练计划和主动服务的机构级摘要已经生成，请查看需要处理的事项。",
        },
        CandidateType.APPOINTMENT_FOLLOW_UP: {
            "decision": "ASK_USER",
            "strategy": "BOOKING_FOLLOW_UP",
            "action": "BOOK_APPOINTMENT",
            "title": "需要重新安排训练时间吗？",
            "body": "最近的预约发生了变化。如果需要，可以查看可用时间并重新安排。",
        },
        CandidateType.CUSTOMER_SERVICE_FOLLOW_UP: {
            "decision": "ASK_USER",
            "strategy": "SERVICE_FOLLOW_UP",
            "action": "OPEN_CUSTOMER_SERVICE",
            "title": "问题是否已经解决？",
            "body": "相关服务事项已经更新。如果问题仍未解决，可以继续反馈。",
        },
    }
    selected = values[candidate_type]
    return ProactiveAgentOutput(
        decision=selected["decision"],
        reason_code=candidate.reason_code,
        strategy=selected["strategy"],
        recommended_action=selected["action"],
        confidence=0.7,
        title=selected["title"],
        body=selected["body"],
        evidence_refs=evidence,
    )
