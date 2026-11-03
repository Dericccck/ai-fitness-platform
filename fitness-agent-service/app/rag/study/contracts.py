"""评测资料的边界；来源声明不是线上效果或人工审核已发生的证明。"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

Source = Literal["synthetic", "pilot", "production_log"]
Category = Literal["answerable", "no_answer", "business_tool", "needs_context", "out_of_scope"]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Protocol(Contract):
    study_id: str = Field(pattern=r"^[A-Za-z0-9_-]{3,80}$")
    source_kind: Source
    corpus_origin: Literal["synthetic", "authorized_business_documents"]
    purpose: str = Field(min_length=8, max_length=1000)
    consent_version: str = "research-v1"
    split_seed: int = 20261007
    test_fraction: float = Field(default=0.3, gt=0, lt=1)
    top_k: int = Field(default=5, ge=1, le=20)
    candidate_limit: int = Field(default=20, ge=5, le=100)
    keyword_candidate_limit: int = Field(default=20, ge=5, le=100)
    pool_depth: int = Field(default=50, ge=5, le=100)
    vector_weight: float = Field(default=0.6, ge=0, le=1)
    keyword_weight: float = Field(default=0.4, ge=0, le=1)
    rrf_k: int = Field(default=60, ge=1, le=200)
    scoring_unit: Literal["chunk"] = "chunk"
    created_at: str

    @model_validator(mode="after")
    def compatible(self) -> Self:
        if min(self.candidate_limit, self.keyword_candidate_limit, self.pool_depth) < self.top_k:
            raise ValueError("候选数量不能小于Top-K")
        if self.vector_weight + self.keyword_weight <= 0:
            raise ValueError("至少启用一个召回权重")
        return self


class Question(Contract):
    case_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,100}$")
    participant_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,100}$")
    query: str = Field(min_length=1, max_length=2000)
    context: list[str] = Field(default_factory=list, max_length=10)
    source_kind: Source
    source_reference: str = Field(min_length=3, max_length=500)
    collected_at: str
    consent: bool
    consent_version: str

    @model_validator(mode="after")
    def collected(self) -> Self:
        if not self.query.strip() or not self.consent:
            raise ValueError("需要非空问题和明确的数据使用同意")
        if any(len(turn) > 2000 for turn in self.context):
            raise ValueError("每条前文不得超过2000字符")
        moment = datetime.fromisoformat(self.collected_at)
        if moment.tzinfo is None:
            raise ValueError("采集时间需要时区")
        return self


class ScopeGrant(Contract):
    participant_id: str
    subject: str = Field(min_length=1, max_length=200)
    organization_ids: list[str] = Field(min_length=1, max_length=50)
    roles: list[str] = Field(min_length=1, max_length=10)
    verified_by: str = Field(min_length=2, max_length=100)
    verification_reference: str = Field(min_length=5, max_length=500)

    @model_validator(mode="after")
    def explicit_scope(self) -> Self:
        values = [
            self.subject,
            self.verified_by,
            self.verification_reference,
            *self.organization_ids,
            *self.roles,
        ]
        if any(not value.strip() for value in values):
            raise ValueError("授权标识和核验信息不得为空白")
        return self


class Triage(Contract):
    case_id: str
    retrieval_query: str = Field(min_length=1, max_length=2000)
    topic: str = Field(min_length=1, max_length=100)
    family_id: str = Field(default="", max_length=100)
    redaction_reviewed: bool
    source_reviewed: bool
    reviewed_by: str = Field(min_length=2, max_length=100)
    context_resolution_note: str = Field(default="", max_length=1000)


class Label(Contract):
    case_id: str
    category: Category
    relevant_ids: list[str] = Field(default_factory=list, max_length=500)
    rationale: str = Field(min_length=5, max_length=3000)
    search_notes: str = Field(min_length=5, max_length=3000)

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if len(self.relevant_ids) != len(set(self.relevant_ids)):
            raise ValueError("相关证据ID重复")
        if bool(self.relevant_ids) != (self.category == "answerable"):
            raise ValueError("仅answerable类别需要非空的相关证据ID")
        return self


class Review(Contract):
    pool_sha256: str
    reviewer_id: str = Field(min_length=2, max_length=100)
    reviewer_kind: Literal["human", "scripted_fixture"]
    independent_review_declared: bool
    labels: list[Label]


def now() -> str:
    return datetime.now(UTC).isoformat()


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def save(path: Path, value: Any) -> None:
    """只创建新文件；工作资料权限0600，不覆盖已有证据。"""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        json.dump(value, output, ensure_ascii=False, indent=2)
        output.write("\n")


def seal(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "artifact_type": kind,
        "schema_version": 1,
        "sha256": digest(payload),
        "payload": payload,
    }


def unseal(value: dict[str, Any], kind: str) -> dict[str, Any]:
    if (
        value.get("artifact_type") != kind
        or value.get("schema_version") != 1
        or value.get("sha256") != digest(value.get("payload"))
    ):
        raise ValueError("资料类型或内容摘要不一致")
    payload = value["payload"]
    if not isinstance(payload, dict):
        raise TypeError("资料主体必须是对象")
    return payload


def read(path: Path) -> Any:
    if path.stat().st_size > 100 * 1024 * 1024:
        raise ValueError("单个研究资料文件超过100MiB，请分批处理")
    return json.loads(path.read_text(encoding="utf-8"))


def normalized_query(query: str) -> str:
    return re.sub(r"[\W_]+", "", query.casefold())


def unique(rows: list[Any], key: str) -> dict[str, Any]:
    result = {getattr(row, key): row for row in rows}
    if len(result) != len(rows):
        raise ValueError(f"{key}重复")
    return result
