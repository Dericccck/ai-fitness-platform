"""健身动作分析报告：模型输出必须经过结构和来源校验。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ExerciseIdentification(StrictModel):
    name: str = Field(default="", max_length=100)
    identification: Literal["IDENTIFIED", "UNCERTAIN"]

    @model_validator(mode="after")
    def validate_name(self) -> ExerciseIdentification:
        if self.identification == "IDENTIFIED" and not self.name:
            raise ValueError("已识别动作必须提供名称")
        return self


class TimeRange(StrictModel):
    start_seconds: float = Field(ge=0, allow_inf_nan=False)
    end_seconds: float = Field(ge=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_order(self) -> TimeRange:
        if self.end_seconds < self.start_seconds:
            raise ValueError("结束时间不能早于开始时间")
        return self


class Observation(StrictModel):
    id: str = Field(min_length=1, max_length=80)
    asset_id: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=500)
    evidence_type: Literal["IMAGE", "VIDEO"]
    time_range: TimeRange | None = None


class Adjustment(StrictModel):
    advice: str = Field(min_length=1, max_length=500)
    related_observation_ids: list[str] = Field(default_factory=list, max_length=5)
    basis_type: Literal["OBSERVATION", "GENERAL_GUIDANCE"]
    citation_ids: list[str] = Field(default_factory=list, max_length=4)

    @model_validator(mode="after")
    def validate_basis(self) -> Adjustment:
        if self.basis_type == "OBSERVATION" and not self.related_observation_ids:
            raise ValueError("画面相关建议必须关联观察")
        return self


class ModelMovementReport(StrictModel):
    exercise: ExerciseIdentification
    assessment: Literal[
        "NEEDS_ADJUSTMENT",
        "NO_OBVIOUS_ISSUE_OBSERVED",
        "INSUFFICIENT_EVIDENCE",
        "NEEDS_CLARIFICATION",
    ]
    summary: str = Field(min_length=1, max_length=1000)
    observations: list[Observation] = Field(default_factory=list, max_length=8)
    adjustments: list[Adjustment] = Field(default_factory=list, max_length=3)
    limitations: list[str] = Field(default_factory=list, max_length=8)
    follow_up_question: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_relations(self) -> ModelMovementReport:
        ids = [item.id for item in self.observations]
        if len(ids) != len(set(ids)):
            raise ValueError("观察标识不能重复")
        if any(set(item.related_observation_ids) - set(ids) for item in self.adjustments):
            raise ValueError("建议引用了不存在的观察")
        if (
            self.assessment in {"NEEDS_ADJUSTMENT", "NO_OBVIOUS_ISSUE_OBSERVED"}
            and not self.observations
        ):
            raise ValueError("动作评价必须有画面观察")
        if self.assessment == "NEEDS_CLARIFICATION" and not self.follow_up_question:
            raise ValueError("需要澄清时必须提供问题")
        if any(not item or len(item) > 500 for item in self.limitations):
            raise ValueError("限制说明无效")
        return self


class KnowledgeReference(StrictModel):
    citation_id: str
    title: str
    version: int
    source_page: int | None = None


class SourceAsset(StrictModel):
    asset_id: str
    revision: int = Field(gt=0)


class MovementReport(ModelMovementReport):
    schema_version: Literal["movement-analysis.v1"] = "movement-analysis.v1"
    knowledge_citations: list[KnowledgeReference] = Field(default_factory=list)
    source_assets: list[SourceAsset] = Field(min_length=1, max_length=3)
    audio_used: Literal[False] = False
    model_version: str
    prompt_version: str
    knowledge_status: Literal["USED", "EMPTY", "UNAVAILABLE"]


class KnowledgePoint(StrictModel):
    text: str = Field(min_length=1, max_length=500)
    basis_type: Literal["MEDIA", "GENERAL_GUIDANCE"]
    citation_ids: list[str] = Field(default_factory=list, max_length=4)


class ModelKnowledgeReport(StrictModel):
    summary: str = Field(min_length=1, max_length=1000)
    observations: list[Observation] = Field(default_factory=list, max_length=8)
    key_points: list[KnowledgePoint] = Field(default_factory=list, max_length=6)
    limitations: list[str] = Field(default_factory=list, max_length=8)
    follow_up_question: str | None = Field(default=None, max_length=500)


class KnowledgeReport(ModelKnowledgeReport):
    schema_version: Literal["fitness-knowledge.v1"] = "fitness-knowledge.v1"
    knowledge_citations: list[KnowledgeReference] = Field(default_factory=list)
    source_assets: list[SourceAsset] = Field(min_length=1, max_length=3)
    audio_used: Literal[False] = False
    model_version: str
    prompt_version: str
    knowledge_status: Literal["USED", "EMPTY", "UNAVAILABLE"]
