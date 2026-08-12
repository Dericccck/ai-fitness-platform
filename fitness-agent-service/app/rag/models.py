"""与 PostgreSQL 或具体向量厂商解耦的稳定 RAG 领域对象。"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class RetrievalScope:
    """用于构建服务端检索过滤条件的已验证身份范围。

    这些值绝不由模型或 HTTP 请求直接提供。API 从签名 AgentContext 中推导范围，
    仓储层再将其转换为 SQL 过滤条件。
    """

    subject: str
    organization_ids: frozenset[str]
    roles: frozenset[str]


@dataclass(frozen=True)
class KnowledgeChunk:
    """包含足够来源信息、可生成用户可见引用的检索分块。"""

    id: str
    document_id: str
    chunk_index: int
    content: str
    source_uri: str
    title: str
    document_type: str
    version: int
    similarity: float
    metadata: dict[str, Any]
    parent_id: str | None = None
    parent_content: str | None = None
    parent_section_path: tuple[str, ...] = ()


@dataclass(frozen=True)
class KnowledgeCitation:
    """仅根据已授权分块来源信息组装的稳定用户可见引用。"""

    citation_id: str
    title: str
    source_uri: str
    document_type: str
    version: int
    chunk_index: int
    section_path: tuple[str, ...]
    source_page: int | None
    source_sheet: str | None
    table_index: int | None
    row_start: int | None
    row_end: int | None
    snippet: str
    score: float


@dataclass(frozen=True)
class KnowledgeDocumentInput:
    """拥有一个或多个分块、可发布的文档版本。"""

    id: str
    organization_id: str | None
    title: str
    source_uri: str
    document_type: str
    visibility: str
    applicable_roles: tuple[str, ...]
    version: int
    status: str
    checksum: str
    effective_from: datetime
    effective_to: datetime | None


@dataclass(frozen=True)
class KnowledgeDocumentSnapshot:
    """当前持久化版本，用于判断是否需要重新建立索引。"""

    id: str
    source_uri: str
    checksum: str
    version: int
    status: str


@dataclass(frozen=True)
class KnowledgeParentInput:
    """不单独生成向量、在子节点召回后展开的上下文父节点。"""

    id: str
    document_id: str
    content: str
    section_path: tuple[str, ...]
    source_page: int | None
    table_index: int | None
    row_start: int | None
    row_end: int | None
    metadata: dict[str, Any]


@dataclass(frozen=True)
class KnowledgeChunkInput:
    """持久化和生成 Embedding 前的分块输入。"""

    id: str
    document_id: str
    chunk_index: int
    content: str
    content_hash: str
    organization_id: str | None
    owner_user_id: str | None
    visibility: str
    allowed_roles: tuple[str, ...]
    document_type: str
    effective_from: datetime
    effective_to: datetime | None
    metadata: dict[str, Any]
    parent_id: str | None = None
