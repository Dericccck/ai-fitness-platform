"""OCR v1 对外请求/响应模型。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class OcrSourceRegion(BaseModel):
    """页面内的归一化来源区域，四个值都在 0 到 1 之间。"""

    model_config = ConfigDict(extra="forbid")

    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)

    @property
    def within_page(self) -> bool:
        """判断区域右下角是否仍在页面范围内。"""

        return self.x + self.width <= 1 and self.y + self.height <= 1

    @model_validator(mode="after")
    def validate_page_bounds(self) -> "OcrSourceRegion":
        if not self.within_page:
            raise ValueError("source_region must stay within the page")
        return self


class OcrBlock(BaseModel):
    """返回给 Agent 入库流程的结构化内容块。"""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["TEXT", "TABLE"]
    content: str = Field(min_length=1)
    heading_path: list[str] = Field(default_factory=list)
    source_page: int = Field(ge=1)
    # 置信度和归一化区域是 Agent 解除 OCR 阻断、生成可回溯引用的必要证据。
    confidence: float = Field(ge=0, le=1)
    source_region: OcrSourceRegion
    table_index: int | None = Field(default=None, ge=0)
    row_start: int | None = Field(default=None, ge=1)
    row_end: int | None = Field(default=None, ge=1)
    metadata: dict[str, str | int | bool] = Field(default_factory=dict)


class OcrResponse(BaseModel):
    """供 Agent 的 ``HttpPdfOcrProvider`` 消费的稳定响应。"""

    model_config = ConfigDict(extra="forbid")

    # 契约版本必须跟着响应一起返回。这样 Agent 可以在进入分块、Embedding
    # 和知识库写入前拒绝未知版本，避免 OCR 服务升级后出现静默字段漂移。
    contract_version: Literal["ocr-service-v1"] = "ocr-service-v1"
    media_type: Literal["application/pdf"] = "application/pdf"
    warnings: list[str] = Field(default_factory=list)
    blocks: list[OcrBlock] = Field(min_length=1)
