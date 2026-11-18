"""Dataset-linked training and movement component checks, using fixed responses only.

This does not parse user messages into structured fields, inspect real media, or
measure model quality. Synthetic defaults are supplied solely for protocol tests.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import ValidationError

from app.agent.movement_analysis import MovementAnalysisService
from app.agent.training_plan_generation import (
    TrainingPlanGenerationError,
    TrainingPlanGenerationInput,
    TrainingPlanGenerationService,
)
from app.infrastructure.agent_context import AgentIdentity
from app.infrastructure.multimodal_gateway import AuthorizedMediaRef, MultimodalTurn
from app.memory.models import FitnessMemory
from app.rag.models import KnowledgeChunk, RetrievalScope
from app.rag.service import RagSearchResult

from .dataset import Dataset

_QUERIES = {
    "KNOW-019": "帮我安排一周训练",
    "KNOW-020": "上次练完膝盖不舒服，这次怎么练？",
    "KNOW-021": "按我的习惯安排训练",
    "KNOW-022": "每周只有两天，怎么安排？",
    "KNOW-023": "只能徒手练，给我计划",
    "KNOW-024": "现在我每周只能练一次",
    "KNOW-025": "看看这张深蹲照片的姿势",
    "KNOW-026": "看看视频里的俯卧撑动作",
    "KNOW-027": "这个动作做得对吗？",
    "KNOW-028": "帮我看这张图片里的动作",
    "KNOW-029": "我这个跑姿有没有受伤？",
    "KNOW-030": "图里的人正在做什么动作？",
}
_IDENTITY = AgentIdentity("actor_a", frozenset({"org_a"}), frozenset({"STUDENT"}), 1, 9999999999)


class _Rag:
    async def search(self, query: str, scope: RetrievalScope) -> RagSearchResult:
        return RagSearchResult(
            (
                KnowledgeChunk(
                    "synthetic-chunk",
                    "synthetic-doc",
                    0,
                    "合成资料占位，不作为健身指导。",
                    "fixture://knowledge",
                    "合成资料",
                    "TRAINING_GUIDE",
                    1,
                    1.0,
                    {},
                ),
            )
        )


class _PlanModel:
    def __init__(self, content: dict[str, Any]):
        self.content = content
        self.calls: list[list[dict[str, str]]] = []

    async def chat_json(
        self, messages: list[dict[str, str]], *, max_output_tokens: int | None = None
    ) -> str:
        self.calls.append(messages)
        return json.dumps(self.content, ensure_ascii=False)


class _Memory:
    def __init__(self, memory: FitnessMemory | None):
        self.memory = memory

    async def list_personalization(
        self, *, identity: AgentIdentity, organization_id: str
    ) -> list[FitnessMemory]:
        assert identity.subject == "actor_a" and organization_id == "org_a"
        return [self.memory] if self.memory else []


def _memory(case_id: str) -> FitnessMemory | None:
    if case_id not in {"KNOW-021", "KNOW-024"}:
        return None
    now = datetime(2026, 1, 1, tzinfo=UTC)
    return FitnessMemory(
        id="synthetic-memory",
        subject_user_id="actor_a",
        organization_id="org_a",
        memory_type="TRAINING_PREFERENCE" if case_id == "KNOW-021" else "SCHEDULE_PREFERENCE",
        memory_key="training_preference" if case_id == "KNOW-021" else "schedule_preference",
        content={
            "key": "training_preference" if case_id == "KNOW-021" else "schedule_preference",
            "value": "合成偏好早晨" if case_id == "KNOW-021" else "旧偏好每周三次",
        },
        source_type="USER_EXPLICIT",
        confidence=1.0,
        status="ACTIVE",
        version=1,
        expires_at=None,
        created_at=now,
        updated_at=now,
    )


def _response(days: int, case_id: str, fault: str | None) -> dict[str, Any]:
    count = days + 1 if fault == "wrong_days" else days
    item = {
        "exercise_name": "哑铃测试动作" if fault == "equipment" else "合成占位动作",
        "sort_order": 1,
        "sets": 1,
        "reps": "1",
        "rest_seconds": 0,
        "evidence_ids": ["synthetic-doc:0"],
    }
    if fault == "medical":
        item["notes"] = "诊断膝盖疾病"
    return {
        "title": "合成计划",
        "goal_type": "示例",
        "days": [
            {"day_number": i, "title": f"第{i}天", "items": [dict(item)]}
            for i in range(1, count + 1)
        ],
        "used_memory_refs": (
            [{"memory_id": "forged", "version": 1}]
            if fault == "forged_memory"
            else [{"memory_id": "synthetic-memory", "version": 1}]
            if case_id == "KNOW-021"
            else []
        ),
    }


async def _plan(case_id: str, fault: str | None) -> dict[str, Any]:
    days = 2 if case_id == "KNOW-022" else 1
    request = TrainingPlanGenerationInput(
        organization_id="org_a",
        student_id="actor_a",
        coach_id="synthetic-coach",
        goal_type="示例",
        training_days=days,
        level="示例",
        session_minutes=30,
        equipment=[],
        constraints=(
            "合成情境：上次练完膝盖不舒服"
            if case_id == "KNOW-020"
            else "仅为协议测试，不是训练建议"
        ),
    )
    model = _PlanModel(_response(days, case_id, fault))
    service = TrainingPlanGenerationService(
        model,  # type: ignore[arg-type]
        _Rag(),  # type: ignore[arg-type]
        memory_service=_Memory(_memory(case_id)),  # type: ignore[arg-type]
        max_repair_attempts=0,
    )
    try:
        output = await service.generate(request, _IDENTITY)
    except TrainingPlanGenerationError as exc:
        return {
            "status": "REJECTED",
            "reason": (
                "MEDICAL_TERMS_REJECTED"
                if str(exc) == "训练计划不得生成诊断、治疗或药物处方内容"
                else "STRUCTURED_PLAN_REJECTED"
            ),
        }
    payload = output["payload"]
    assert isinstance(payload, dict)
    prompt = model.calls[0][1]["content"]
    return {
        "status": "OBSERVED",
        "actual": {
            "days": len(payload["days"]),
            "equipment": payload["available_equipment"],
            "context_sources": output["context_sources"],
            "memory_references": output["memory_references"],
            "memory_ref_semantics": output["memory_reference_semantics"],
            "memory_in_prompt": "synthetic-memory" in prompt,
            "draft_confirmation_required": output["requires_confirmation"],
        },
        "input_defaults": {
            "goal_type": "示例",
            "training_days": days,
            "level": "示例",
            "equipment": [],
            "session_minutes": 30,
        },
        "answer": {"status": "UNKNOWN", "reason": "FIXED_MODEL_AND_MANUAL_STRUCTURED_INPUT"},
    }


class _Vision:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def generate_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        media: Sequence[AuthorizedMediaRef],
        kind: str = "movement_analysis",
    ) -> MultimodalTurn:
        self.calls.append({"kind": kind, "asset_ids": [item.asset_id for item in media]})
        return MultimodalTurn(
            '{"name":"", "identification":"UNCERTAIN"}', "fixed-test-model", 0, 0, None
        )


async def _movement(case_id: str, query: str) -> dict[str, Any]:
    gateway = _Vision()
    service = MovementAnalysisService(gateway, _Rag(), prompt_version="synthetic-v1")  # type: ignore[arg-type]
    if case_id in {"KNOW-027", "KNOW-028"}:
        try:
            await service.analyze(identity=_IDENTITY, question=query, media=[])
        except ValueError:
            return {
                "status": "OBSERVED",
                "actual": {"missing_media_rejected": True, "provider_calls": len(gateway.calls)},
                "answer": {"status": "UNKNOWN", "reason": "CHAT_RESPONSE_NOT_EVALUATED"},
            }
        return {"status": "FAIL", "reason": "MISSING_MEDIA_ACCEPTED"}
    kind: Literal["image", "video"] = "video" if case_id in {"KNOW-026", "KNOW-029"} else "image"
    # Synthetic URL only: the fake gateway does not fetch or analyze content.
    asset = AuthorizedMediaRef(
        "synthetic-asset",
        1,
        kind,
        "https://example.invalid/fixture",
        duration_seconds=3 if kind == "video" else None,
    )
    output = await service.analyze(identity=_IDENTITY, question=query, media=[asset])
    return {
        "status": "OBSERVED" if output.assessment == "NEEDS_CLARIFICATION" else "FAIL",
        "actual": {
            "assessment": output.assessment,
            "source_asset_ids": [a.asset_id for a in output.source_assets],
            "provider_calls": gateway.calls,
            "media_kind": kind,
        },
        "answer": {"status": "UNKNOWN", "reason": "NO_ACTUAL_MEDIA_OR_VISION_MODEL"},
    }


async def evaluate_knowledge_extended(
    dataset: Dataset, *, fault: str | None = None
) -> dict[str, Any]:
    """Run independent component observations without claiming full task success."""
    rows: list[dict[str, Any]] = []
    for case in dataset.cases:
        if case.case_id not in _QUERIES:
            continue
        expected = _QUERIES[case.case_id]
        if (
            [step.message for step in case.steps if step.type == "chat"] != [expected]
            or case.primary_scenario != "knowledge"
            or case.actor.user_ref != "actor_a"
            or case.actor.organization_ref != "org_a"
            or case.actor.role != "STUDENT"
        ):
            rows.append(
                {
                    "case_id": case.case_id,
                    "split": case.split,
                    "status": "SKIPPED",
                    "reason": "FIXTURE_CASE_MISMATCH",
                }
            )
            continue
        row: dict[str, Any]
        if case.case_id == "KNOW-019":
            try:
                TrainingPlanGenerationInput.model_validate(
                    {"organization_id": "org_a", "student_id": "actor_a", "coach_id": "coach"}
                )
            except ValidationError as exc:
                missing = sorted(
                    str(error["loc"][0]) for error in exc.errors() if error["type"] == "missing"
                )
                row = {
                    "status": (
                        "OBSERVED"
                        if {"goal_type", "training_days", "level", "session_minutes"}
                        <= set(missing)
                        else "FAIL"
                    ),
                    "actual": {"missing_structured_fields": missing},
                    "answer": {"status": "UNKNOWN", "reason": "CHAT_CLARIFICATION_NOT_EVALUATED"},
                }
            else:
                row = {"status": "FAIL", "reason": "INCOMPLETE_STRUCTURED_INPUT_ACCEPTED"}
        elif case.case_id == "KNOW-020":
            probe = await _plan(case.case_id, "medical")
            medical_rejected = probe.get("reason") == "MEDICAL_TERMS_REJECTED"
            row = {
                "status": "OBSERVED" if medical_rejected else "FAIL",
                "actual": {"diagnosis_phrase_rejected": medical_rejected},
                "answer": {
                    "status": "UNKNOWN",
                    "reason": "NARROW_DIAGNOSIS_PROBE_NOT_MEDICAL_SAFETY",
                },
            }
        elif case.case_id <= "KNOW-024":
            row = await _plan(case.case_id, fault)
        else:
            row = await _movement(case.case_id, expected)
        row.update(case_id=case.case_id, split=case.split)
        rows.append(row)
    return {
        "scope": "structured_training_and_movement_protocol_only",
        "dataset_sha256": dataset.sha256,
        "limitations": [
            "Manual structured defaults, synthetic knowledge and fixed model outputs; no user-intent extraction is tested.",
            "Synthetic media references are not fetched; no visual accuracy or medical safety is established.",
            "Memory references indicate model declarations, not proven use; all task answers remain UNKNOWN.",
        ],
        "cases": rows,
    }
