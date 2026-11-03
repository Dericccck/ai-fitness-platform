"""显式开启的隔离PG契约验收；这里使用固定向量，不计入模型质量成绩。"""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text

from app.core.config import Settings
from app.infrastructure.database import Database
from app.rag.models import RetrievalScope
from app.rag.repository import KnowledgeRepository
from app.rag.study.contracts import Protocol, digest
from app.rag.study.runtime import SnapshotChanged, StudyRuntime, catalog
from app.rag.study.synthetic import generate
from scripts.project_evidence_benchmark import isolated_database, seed

pytestmark = pytest.mark.skipif(
    os.getenv("AGENT_RUN_RAG_STUDY_POSTGRES_TESTS") != "1",
    reason="需要显式开启隔离PostgreSQL验收",
)


async def test_acl_snapshot_and_readonly_transaction_on_real_postgres():
    settings = Settings()
    vector = [1.0] + [0.0] * (settings.embedding_dimensions - 1)

    async def embed(texts):
        return [vector for _ in texts]

    async with isolated_database(settings) as scoped:
        database = Database(scoped)
        try:
            await seed(KnowledgeRepository(database), SimpleNamespace(embed=embed))
            runtime = object.__new__(StudyRuntime)
            runtime.database = database
            runtime.settings = scoped
            runtime.models = SimpleNamespace(embed=AsyncMock(return_value=[vector]))
            runtime.reranker = SimpleNamespace(rerank=AsyncMock(return_value=[]))
            protocol = Protocol.model_validate(generate("pg-contract")["protocol"])
            grant = {
                "subject": "test-user",
                "organization_ids": ["evidence-org"],
                "roles": ["STUDENT"],
            }
            result = await runtime.retrieve("BK-204", grant, protocol)
            ids = {row["id"] for row in result["catalog"]}
            assert len(ids) == 30
            assert not any(identifier.startswith("hidden_") for identifier in ids)
            assert result["results"]["keyword"] == ["booking"]
            async with database.engine.connect() as connection:
                private = await catalog(
                    connection,
                    RetrievalScope(
                        "other-user", frozenset({"evidence-org"}), frozenset({"STUDENT"})
                    ),
                )
                assert "hidden_private" in {row["id"] for row in private}
                external = await catalog(
                    connection,
                    RetrievalScope("test-user", frozenset({"other-org"}), frozenset({"STUDENT"})),
                )
                assert {row["id"] for row in external} == {"hidden_org"}
                finance = await catalog(
                    connection,
                    RetrievalScope(
                        "test-user", frozenset({"evidence-org"}), frozenset({"FINANCE"})
                    ),
                )
                assert {row["id"] for row in finance} == {"hidden_role"}
            # 固定向量的SQL契约验证：知识内容变化即使ID未变，也必须拒绝沿用旧金标。
            async with database.engine.begin() as connection:
                await connection.execute(
                    text(
                        "UPDATE knowledge_chunks SET content=content || ' changed' WHERE id='booking'"
                    )
                )
            with pytest.raises(SnapshotChanged):
                await runtime.retrieve("BK-204", grant, protocol, expected=result["catalog_sha256"])
            assert result["catalog_sha256"] == digest(result["catalog"])
        finally:
            await database.close()
