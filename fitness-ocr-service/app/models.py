"""OCR v1 对外请求/响应模型。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class OcrBlock(BaseModel):
    """返回给 Agent 入库流程的结构化内容块。"""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["TEXT", "TABLE"]
    content: str = Field(min_length=1)
    heading_path: list[str] = Field(default_factory=list)
    source_page: int = Field(ge=1)
    table_index: int | None = Field(default=None, ge=0)
    row_start: int | None = Field(default=None, ge=1)
    row_end: int | None = Field(default=None, ge=1)
    metadata: dict[str, str | int | bool] = Field(default_factory=dict)


class OcrResponse(BaseModel):
    """供 Agent 的 ``HttpPdfOcrProvider`` 消费的稳定响应。"""

    model_config = ConfigDict(extra="forbid")

    media_type: Literal["application/pdf"] = "application/pdf"
    warnings: list[str] = Field(default_factory=list)
    blocks: list[OcrBlock] = Field(min_length=1)
