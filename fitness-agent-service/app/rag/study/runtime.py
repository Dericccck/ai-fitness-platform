"""有界只读评测；复用线上仓储和排序函数，但不调用聊天模型或业务写工具。"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from time import perf_counter
from types import SimpleNamespace
from typing import Any, cast

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.config import Settings
from app.infrastructure.database import Database
from app.infrastructure.model_gateway import ModelGateway
from app.infrastructure.reranker import RerankerClient
from app.rag.models import RetrievalScope
from app.rag.repository import KnowledgeRepository
from app.rag.service import _fuse_candidates, _select_ranked_chunks

from .contracts import Protocol, digest, now, seal, unseal

METHODS = ("vector", "keyword", "hybrid", "hybrid_rerank")


class SnapshotChanged(ValueError):
    """重新采集与复核，不能在更换知识快照后沿用原金标。"""


class BoundEngine:
    def __init__(self, connection: AsyncConnection) -> None:
        self.connection = connection

    @asynccontextmanager
    async def connect(self) -> AsyncIterator[AsyncConnection]:
        yield self.connection


async def catalog(connection: AsyncConnection, scope: RetrievalScope) -> list[dict[str, Any]]:
    if not scope.organization_ids or not scope.roles:
        return []
    # 与 KnowledgeRepository 的有效期、发布状态、机构、所有权、角色谓词保持一致。
    roles = " OR ".join(
        f"c.allowed_roles && ARRAY[:role_{i}]::text[]" for i, _ in enumerate(sorted(scope.roles))
    )
    statement = text(
        """
        SELECT c.id, c.document_id, c.content, d.title, d.version, d.source_uri,
               c.metadata, c.parent_id, p.content AS parent_content,
               p.section_path, md5(c.embedding::text) AS embedding_digest,
               c.search_vector::text AS search_vector,
               c.visibility, c.organization_id, c.owner_user_id, c.allowed_roles,
               c.effective_from::text, c.effective_to::text, d.publication_fingerprint
        FROM knowledge_chunks c
        JOIN knowledge_documents d ON d.id=c.document_id
        LEFT JOIN knowledge_parents p ON p.id=c.parent_id AND p.document_id=c.document_id
        WHERE d.status='PUBLISHED' AND c.visibility=d.visibility
          AND c.effective_from<=CURRENT_TIMESTAMP
          AND (c.effective_to IS NULL OR c.effective_to>CURRENT_TIMESTAMP)
          AND (c.visibility='GLOBAL'
            OR (c.visibility='ORGANIZATION' AND c.organization_id IN :organizations)
            OR (c.visibility='PRIVATE' AND c.owner_user_id=:subject))
          AND (cardinality(c.allowed_roles)=0 OR ("""
        + roles
        + """))
        ORDER BY c.id LIMIT 10001
    """
    ).bindparams(bindparam("organizations", expanding=True))
    result = await connection.execute(
        statement,
        {
            "organizations": sorted(scope.organization_ids),
            "subject": scope.subject,
            **{f"role_{i}": role for i, role in enumerate(sorted(scope.roles))},
        },
    )
    rows = [dict(row) for row in result.mappings()]
    if len(rows) > 10000:
        raise ValueError("授权目录超过10000分块，请先缩小研究范围；不允许截断目录")
    return rows


class StudyRuntime:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.database = Database(settings)
        self.models = ModelGateway(settings)
        self.reranker = RerankerClient(settings)

    async def close(self) -> None:
        await self.models.close()
        await self.database.close()

    def provenance(self) -> dict[str, Any]:
        root = Path(__file__).resolve().parents[2]
        paths = [
            root / "rag/repository.py",
            root / "rag/service.py",
            Path(__file__),
            root / "infrastructure/model_gateway.py",
            root / "infrastructure/reranker.py",
        ]
        paths.extend(path for path in Path(__file__).parent.glob("*.py") if path not in paths)
        return {
            "source_sha256": {
                str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths
            },
            "embedding_backend": self.settings.embedding_backend,
            "embedding_model": self.settings.embedding_model
            or Path(self.settings.embedding_model_path).name,
            "embedding_dimensions": self.settings.embedding_dimensions,
            "reranker_backend": self.settings.reranker_backend,
            "reranker_model": self.settings.reranker_model
            or Path(self.settings.reranker_model_path).name,
            "model_revision_limit": "模型名不能证明远端权重不变；正式实验需固定服务版本/权重",
        }

    async def retrieve(
        self,
        query: str,
        grant: dict[str, Any],
        protocol: Protocol,
        *,
        expected: str | None = None,
        pooling: bool = False,
    ) -> dict[str, Any]:
        start = perf_counter()
        embeddings = await self.models.embed([query])
        if len(embeddings) != 1 or len(embeddings[0]) != self.settings.embedding_dimensions:
            raise ValueError("embedding数量或维度不匹配")
        scope = RetrievalScope(
            grant["subject"], frozenset(grant["organization_ids"]), frozenset(grant["roles"])
        )
        # 计算 embedding 在事务外；单问题快照下完成目录、双路读取，随后释放连接再重排。
        async with self.database.engine.connect() as connection, connection.begin():
            await connection.execute(
                text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            )
            await connection.execute(text("SET LOCAL statement_timeout = '15s'"))
            entries = await catalog(connection, scope)
            fingerprint = digest(entries)
            if expected is not None and expected != fingerprint:
                raise SnapshotChanged("授权语料快照发生变化，请重新建池和复核")
            repository = KnowledgeRepository(
                cast(Database, SimpleNamespace(engine=BoundEngine(connection)))
            )
            vectors = await repository.search_candidates(
                embeddings[0],
                scope,
                limit=protocol.pool_depth if pooling else protocol.candidate_limit,
            )
            keywords = await repository.search_keyword_candidates(
                query,
                scope,
                limit=protocol.pool_depth if pooling else protocol.keyword_candidate_limit,
            )
        fused = _fuse_candidates(
            vectors[: protocol.candidate_limit],
            keywords[: protocol.keyword_candidate_limit],
            vector_weight=protocol.vector_weight,
            keyword_weight=protocol.keyword_weight,
            rrf_k=protocol.rrf_k,
            limit=protocol.candidate_limit,
        )
        selected = []
        if fused:
            ranked = await self.reranker.rerank(
                query, [chunk.content for chunk in fused], top_n=protocol.top_k
            )
            selected = _select_ranked_chunks(fused, ranked, protocol.top_k)
        results = {
            "vector": [c.id for c in vectors[: protocol.top_k]],
            "keyword": [c.id for c in keywords[: protocol.top_k]],
            "hybrid": [c.id for c in fused[: protocol.top_k]],
            "hybrid_rerank": [c.id for c in selected],
        }
        allowed = {entry["id"] for entry in entries}
        if any(not set(ids) <= allowed for ids in results.values()):
            raise SnapshotChanged("仓储检索返回快照授权范围外的分块，禁止生成分数")
        return {
            "catalog": entries,
            "catalog_sha256": fingerprint,
            "results": results,
            "candidate_ids": sorted({c.id for c in vectors + keywords + selected}),
            "combined_execution_ms": (perf_counter() - start) * 1000,
        }

    async def build_pool(self, artifact: dict[str, Any]) -> dict[str, Any]:
        prepared = unseal(artifact, "prepared")
        protocol = Protocol.model_validate(prepared["protocol"])
        rows = []
        for case in prepared["cases"]:
            result = await self.retrieve(
                case["retrieval_query"],
                prepared["scopes"][case["participant_id"]],
                protocol,
                pooling=True,
            )
            rows.append({"case": case, **result})
        return seal(
            "pool",
            {
                "prepared": prepared,
                "cases": rows,
                "runtime": self.provenance(),
                "created_at": now(),
            },
        )

    async def evaluate(self, artifact: dict[str, Any], split: str) -> dict[str, Any]:
        if split not in {"dev", "test"}:
            raise ValueError("必须明确dev或test")
        frozen = unseal(artifact, "frozen")
        prepared = frozen["prepared"]
        protocol = Protocol.model_validate(prepared["protocol"])
        rows = []
        for entry in frozen["cases"]:
            if entry["split"] != split:
                continue
            case = entry["case"]
            try:
                result = await self.retrieve(
                    case["retrieval_query"],
                    prepared["scopes"][case["participant_id"]],
                    protocol,
                    expected=entry["catalog_sha256"],
                )
                rows.append(
                    {
                        **entry,
                        "results": result["results"],
                        "error": None,
                        "combined_execution_ms": result["combined_execution_ms"],
                    }
                )
            except SnapshotChanged:
                raise
            except Exception as error:  # noqa: BLE001 -- 将运行失败作为失败样本保留
                # 不泄露供应商错误正文、URL、原问题；失败用例不得从分母删除。
                rows.append(
                    {
                        **entry,
                        "results": {name: [] for name in METHODS},
                        "error": type(error).__name__,
                        "combined_execution_ms": None,
                    }
                )
        return seal(
            "run",
            {
                "frozen_sha256": artifact["sha256"],
                "protocol": prepared["protocol"],
                "reviewer_kinds": frozen["reviewer_kinds"],
                "split": split,
                "cases": rows,
                "runtime": self.provenance(),
                "created_at": now(),
            },
        )
