"""支持权限过滤向量检索的 PostgreSQL 仓储。"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from sqlalchemy import bindparam, text

from app.infrastructure.database import Database

from .models import (
    KnowledgeChunk,
    KnowledgeChunkInput,
    KnowledgeDocumentInput,
    KnowledgeDocumentSnapshot,
    KnowledgeParentInput,
    RetrievalScope,
)


class KnowledgeRepository:
    """持久化并检索 Agent 知识，不向 Agent 暴露原始数据库。

    PostgreSQL 仍是已索引知识元数据的事实来源。
    向量列仅用于加速检索；每次搜索都会在将候选发送给 Reranker 或 LLM 前，在 SQL 中应用
    租户、角色、所有权、发布状态和生效时间约束。
    """

    def __init__(self, database: Database) -> None:
        self._database = database

    async def get_current_document(
        self,
        source_uri: str,
        *,
        organization_id: str | None,
        owner_user_id: str | None,
        visibility: str,
    ) -> KnowledgeDocumentSnapshot | None:
        """返回指定租户/所有者来源当前发布的版本。"""

        statement = text(
            """
            SELECT id, source_uri, checksum, version, status,
                   organization_id, owner_user_id, visibility, publication_fingerprint
            FROM knowledge_documents
            WHERE source_uri = :source_uri
              AND status = 'PUBLISHED'
              AND visibility = :visibility
              AND organization_id IS NOT DISTINCT FROM :organization_id
              AND owner_user_id IS NOT DISTINCT FROM :owner_user_id
            ORDER BY version DESC
            LIMIT 1
            """
        )
        async with self._database.engine.connect() as connection:
            row = (
                (
                    await connection.execute(
                        statement,
                        {
                            "source_uri": source_uri,
                            "organization_id": organization_id,
                            "owner_user_id": owner_user_id,
                            "visibility": visibility,
                        },
                    )
                )
                .mappings()
                .first()
            )
        if row is None:
            return None
        return KnowledgeDocumentSnapshot(
            id=str(row["id"]),
            source_uri=str(row["source_uri"]),
            checksum=str(row["checksum"]),
            version=int(row["version"]),
            status=str(row["status"]),
            organization_id=(
                str(row["organization_id"]) if row["organization_id"] is not None else None
            ),
            owner_user_id=(str(row["owner_user_id"]) if row["owner_user_id"] is not None else None),
            visibility=str(row["visibility"]),
            publication_fingerprint=(
                str(row["publication_fingerprint"])
                if row["publication_fingerprint"] is not None
                else None
            ),
        )

    async def archive_source(
        self,
        source_uri: str,
        *,
        organization_id: str | None,
        owner_user_id: str | None,
        visibility: str,
    ) -> tuple[int, int, int]:
        """下线一个有界来源并删除其派生父子节点，保留文档版本审计记录。"""

        select_statement = text(
            """
            SELECT id
            FROM knowledge_documents
            WHERE source_uri = :source_uri
              AND visibility = :visibility
              AND organization_id IS NOT DISTINCT FROM :organization_id
              AND owner_user_id IS NOT DISTINCT FROM :owner_user_id
              AND status = 'PUBLISHED'
            FOR UPDATE
            """
        )
        async with self._database.engine.begin() as connection:
            document_ids = [
                str(row["id"])
                for row in (
                    await connection.execute(
                        select_statement,
                        {
                            "source_uri": source_uri,
                            "organization_id": organization_id,
                            "owner_user_id": owner_user_id,
                            "visibility": visibility,
                        },
                    )
                ).mappings()
            ]
            if not document_ids:
                return 0, 0, 0
            document_id_params = {"document_ids": tuple(document_ids)}
            chunk_result = await connection.execute(
                text("DELETE FROM knowledge_chunks WHERE document_id IN :document_ids").bindparams(
                    bindparam("document_ids", expanding=True)
                ),
                document_id_params,
            )
            parent_result = await connection.execute(
                text("DELETE FROM knowledge_parents WHERE document_id IN :document_ids").bindparams(
                    bindparam("document_ids", expanding=True)
                ),
                document_id_params,
            )
            document_result = await connection.execute(
                text(
                    """
                    UPDATE knowledge_documents
                    SET status = 'ARCHIVED', updated_at = CURRENT_TIMESTAMP
                    WHERE id IN :document_ids
                    """
                ).bindparams(bindparam("document_ids", expanding=True)),
                document_id_params,
            )
        return (
            int(document_result.rowcount or 0),
            int(parent_result.rowcount or 0),
            int(chunk_result.rowcount or 0),
        )

    async def insert_chunks(
        self,
        chunks: Sequence[KnowledgeChunkInput],
        embeddings: Sequence[Sequence[float]],
    ) -> None:
        """在一个事务中插入已生成 Embedding 的内容块。

        调用方负责在将文档暴露给检索前发布文档版本。``executemany`` 使一个文档批次保持
        原子性，因此部分 Embedding 写入不会产生半索引文档。
        """

        if len(chunks) != len(embeddings):
            raise ValueError("chunks 和 embeddings 的长度必须相同")
        if not chunks:
            return

        statement = text(
            """
            INSERT INTO knowledge_chunks (
                id, document_id, chunk_index, content, content_hash, embedding,
                organization_id, owner_user_id, visibility, allowed_roles, parent_id,
                document_type, effective_from, effective_to, metadata
            ) VALUES (
                :id, :document_id, :chunk_index, :content, :content_hash,
                CAST(:embedding AS vector), :organization_id, :owner_user_id,
                :visibility, :allowed_roles, :parent_id, :document_type, :effective_from,
                :effective_to, CAST(:metadata AS json)
            )
            """
        )
        params = _chunk_params(chunks, embeddings)
        async with self._database.engine.begin() as connection:
            await connection.execute(statement, params)

    async def update_document_publication(
        self,
        *,
        document_id: str,
        organization_id: str | None,
        owner_user_id: str | None,
        title: str,
        document_type: str,
        visibility: str,
        allowed_roles: Sequence[str],
        effective_from: Any,
        effective_to: Any,
        publication_fingerprint: str,
    ) -> None:
        """原子更新正文未变文档的发布属性，并同步子节点 ACL。

        权限和生效时间同时复制在 Chunk 上，是因为向量/关键词检索的 SQL 会直接过滤
        Chunk 元数据。该路径不触碰 Embedding 和父节点正文。
        """

        document_statement = text(
            """
            UPDATE knowledge_documents
            SET organization_id = :organization_id,
                owner_user_id = :owner_user_id,
                title = :title,
                document_type = :document_type,
                visibility = :visibility,
                applicable_roles = :allowed_roles,
                publication_fingerprint = :publication_fingerprint,
                effective_from = :effective_from,
                effective_to = :effective_to,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = :document_id AND status = 'PUBLISHED'
            """
        )
        chunk_statement = text(
            """
            UPDATE knowledge_chunks
            SET organization_id = :organization_id,
                owner_user_id = :owner_user_id,
                visibility = :visibility,
                allowed_roles = :allowed_roles,
                document_type = :document_type,
                effective_from = :effective_from,
                effective_to = :effective_to
            WHERE document_id = :document_id
            """
        )
        params = {
            "document_id": document_id,
            "organization_id": organization_id,
            "owner_user_id": owner_user_id,
            "title": title,
            "document_type": document_type,
            "visibility": visibility,
            "allowed_roles": list(allowed_roles),
            "publication_fingerprint": publication_fingerprint,
            "effective_from": effective_from,
            "effective_to": effective_to,
        }
        async with self._database.engine.begin() as connection:
            result = await connection.execute(document_statement, params)
            if result.rowcount != 1:
                raise ValueError("当前文档不存在或已不再是 PUBLISHED")
            await connection.execute(chunk_statement, params)

    async def replace_document(
        self,
        document: KnowledgeDocumentInput,
        chunks: Sequence[KnowledgeChunkInput],
        embeddings: Sequence[Sequence[float]],
        *,
        parents: Sequence[KnowledgeParentInput] = (),
    ) -> None:
        """写入或更新一个文档版本，并原子替换其内容块。

        重新索引有意放在单个数据库事务中。事务提交前，已发布文档仍暴露之前的分块；提交后，
        不会检索到新旧混合的版本。
        """

        if len(chunks) != len(embeddings):
            raise ValueError("chunks 和 embeddings 的长度必须相同")
        if not chunks:
            raise ValueError("知识文档至少必须包含一个 chunk")

        document_statement = text(
            """
            INSERT INTO knowledge_documents (
                id, organization_id, title, source_uri, document_type, visibility,
                applicable_roles, version, status, checksum, effective_from, effective_to,
                owner_user_id, publication_fingerprint
            ) VALUES (
                :id, :organization_id, :title, :source_uri, :document_type,
                :visibility, :applicable_roles, :version, :status, :checksum,
                :effective_from, :effective_to, :owner_user_id, :publication_fingerprint
            )
            ON CONFLICT (id) DO UPDATE SET
                organization_id = EXCLUDED.organization_id,
                title = EXCLUDED.title,
                source_uri = EXCLUDED.source_uri,
                document_type = EXCLUDED.document_type,
                visibility = EXCLUDED.visibility,
                applicable_roles = EXCLUDED.applicable_roles,
                version = EXCLUDED.version,
                status = EXCLUDED.status,
                checksum = EXCLUDED.checksum,
                owner_user_id = EXCLUDED.owner_user_id,
                publication_fingerprint = EXCLUDED.publication_fingerprint,
                effective_from = EXCLUDED.effective_from,
                effective_to = EXCLUDED.effective_to,
                updated_at = CURRENT_TIMESTAMP
            """
        )
        delete_chunks_statement = text(
            "DELETE FROM knowledge_chunks WHERE document_id = :document_id"
        )
        delete_parents_statement = text(
            "DELETE FROM knowledge_parents WHERE document_id = :document_id"
        )
        parent_statement = text(
            """
            INSERT INTO knowledge_parents (
                id, document_id, content, section_path, source_page, table_index,
                row_start, row_end, metadata
            ) VALUES (
                :id, :document_id, :content, :section_path, :source_page,
                :table_index, :row_start, :row_end, CAST(:metadata AS json)
            )
            """
        )
        archive_statement = text(
            """
            UPDATE knowledge_documents
            SET status = 'ARCHIVED', updated_at = CURRENT_TIMESTAMP
            WHERE source_uri = :source_uri
              AND id <> :document_id
              AND visibility = :visibility
              AND organization_id IS NOT DISTINCT FROM :organization_id
              AND owner_user_id IS NOT DISTINCT FROM :owner_user_id
              AND status = 'PUBLISHED'
            """
        )
        version_guard_statement = text(
            """
            SELECT id, checksum, publication_fingerprint, status
            FROM knowledge_documents
            WHERE source_uri = :source_uri
              AND visibility = :visibility
              AND organization_id IS NOT DISTINCT FROM :organization_id
              AND owner_user_id IS NOT DISTINCT FROM :owner_user_id
              AND version = :version
            FOR UPDATE
            """
        )
        chunk_statement = text(
            """
            INSERT INTO knowledge_chunks (
                id, document_id, chunk_index, content, content_hash, embedding,
                organization_id, owner_user_id, visibility, allowed_roles, parent_id,
                document_type, effective_from, effective_to, metadata
            ) VALUES (
                :id, :document_id, :chunk_index, :content, :content_hash,
                CAST(:embedding AS vector), :organization_id, :owner_user_id,
                :visibility, :allowed_roles, :parent_id, :document_type, :effective_from,
                :effective_to, CAST(:metadata AS json)
            )
            """
        )
        document_params = {
            "id": document.id,
            "organization_id": document.organization_id,
            "title": document.title,
            "source_uri": document.source_uri,
            "document_type": document.document_type,
            "visibility": document.visibility,
            "applicable_roles": list(document.applicable_roles),
            "version": document.version,
            "status": document.status,
            "checksum": document.checksum,
            "owner_user_id": document.owner_user_id,
            "publication_fingerprint": document.publication_fingerprint,
            "effective_from": document.effective_from,
            "effective_to": document.effective_to,
        }
        async with self._database.engine.begin() as connection:
            # 应用层的 current/version 检查只负责快速失败；最终一致性必须在写事务内
            # 再锁一次。同一来源作用域和版本号如果已经被不同内容/发布属性占用，晚到
            # 的任务必须失败，不能用 ON CONFLICT 覆盖先完成的发布。
            existing_version = (
                (
                    await connection.execute(
                        version_guard_statement,
                        {
                            "source_uri": document.source_uri,
                            "organization_id": document.organization_id,
                            "owner_user_id": document.owner_user_id,
                            "visibility": document.visibility,
                            "version": document.version,
                        },
                    )
                )
                .mappings()
                .first()
            )
            if existing_version is not None and (
                str(existing_version["id"]) != document.id
                or str(existing_version["checksum"]) != document.checksum
                or existing_version["publication_fingerprint"] != document.publication_fingerprint
            ):
                raise ValueError("同一来源版本已经由另一份内容或发布属性占用")
            # 同一来源同时只能有一个版本可检索。该操作与新版本写入处于同一事务，
            # 因此 Embedding 写入失败不会隐藏此前已发布的版本。
            await connection.execute(
                archive_statement,
                {
                    "source_uri": document.source_uri,
                    "document_id": document.id,
                    "organization_id": document.organization_id,
                    "owner_user_id": document.owner_user_id,
                    "visibility": document.visibility,
                },
            )
            await connection.execute(document_statement, document_params)
            await connection.execute(delete_chunks_statement, {"document_id": document.id})
            await connection.execute(delete_parents_statement, {"document_id": document.id})
            if parents:
                await connection.execute(parent_statement, _parent_params(parents))
            await connection.execute(chunk_statement, _chunk_params(chunks, embeddings))

    async def search_candidates(
        self,
        embedding: Sequence[float],
        scope: RetrievalScope,
        *,
        limit: int,
    ) -> list[KnowledgeChunk]:
        """使用 pgvector 余弦距离召回已授权候选。"""

        if not scope.organization_ids or not scope.roles:
            return []

        role_clauses = [
            f"allowed_roles && ARRAY[:role_{index}]::text[]"
            for index, _ in enumerate(sorted(scope.roles))
        ]
        role_parameters = {f"role_{index}": role for index, role in enumerate(sorted(scope.roles))}
        statement = text(
            """
            SELECT
                c.id,
                c.document_id,
                c.chunk_index,
                c.content,
                d.source_uri,
                d.title,
                c.document_type,
                d.version,
                1 - (c.embedding <=> CAST(:embedding AS vector)) AS similarity,
                c.metadata,
                c.parent_id,
                p.content AS parent_content,
                p.section_path AS parent_section_path
            FROM knowledge_chunks c
            JOIN knowledge_documents d ON d.id = c.document_id
            LEFT JOIN knowledge_parents p ON p.id = c.parent_id AND p.document_id = c.document_id
            WHERE d.status = 'PUBLISHED'
              AND c.embedding IS NOT NULL
              AND c.visibility = d.visibility
              AND c.effective_from <= CURRENT_TIMESTAMP
              AND (c.effective_to IS NULL OR c.effective_to > CURRENT_TIMESTAMP)
              AND (
                    c.visibility = 'GLOBAL'
                    OR (c.visibility = 'ORGANIZATION' AND c.organization_id IN :organization_ids)
                    OR (c.visibility = 'PRIVATE' AND c.owner_user_id = :subject)
              )
              AND (
                    cardinality(c.allowed_roles) = 0
                    OR :role_filter_enabled = false
                    OR ({role_filter})
              )
            ORDER BY c.embedding <=> CAST(:embedding AS vector)
            LIMIT :limit
            """.format(role_filter=" OR ".join(role_clauses))
        ).bindparams(bindparam("organization_ids", expanding=True))
        params: dict[str, Any] = {
            "embedding": _vector_literal(embedding),
            "organization_ids": sorted(scope.organization_ids),
            "subject": scope.subject,
            "role_filter_enabled": bool(role_clauses),
            "limit": limit,
            **role_parameters,
        }
        async with self._database.engine.connect() as connection:
            result = await connection.execute(statement, params)
            rows = result.mappings().all()
        return _rows_to_chunks(rows)

    async def search_keyword_candidates(
        self,
        query: str,
        scope: RetrievalScope,
        *,
        limit: int,
    ) -> list[KnowledgeChunk]:
        """使用全文检索和 pg_trgm 相似度召回已授权词法候选。"""

        if not query.strip() or not scope.organization_ids or not scope.roles:
            return []
        role_clauses = [
            f"c.allowed_roles && ARRAY[:keyword_role_{index}]::text[]"
            for index, _ in enumerate(sorted(scope.roles))
        ]
        role_parameters = {
            f"keyword_role_{index}": role for index, role in enumerate(sorted(scope.roles))
        }
        statement = text(
            """
            SELECT
                c.id, c.document_id, c.chunk_index, c.content,
                d.source_uri, d.title, c.document_type, d.version,
                GREATEST(
                    ts_rank_cd(c.search_vector, websearch_to_tsquery('simple', :query)),
                    similarity(c.content, :query)
                ) AS similarity,
                c.metadata, c.parent_id,
                p.content AS parent_content,
                p.section_path AS parent_section_path
            FROM knowledge_chunks c
            JOIN knowledge_documents d ON d.id = c.document_id
            LEFT JOIN knowledge_parents p ON p.id = c.parent_id AND p.document_id = c.document_id
            WHERE d.status = 'PUBLISHED'
              AND c.effective_from <= CURRENT_TIMESTAMP
              AND (c.effective_to IS NULL OR c.effective_to > CURRENT_TIMESTAMP)
              AND (
                    c.visibility = 'GLOBAL'
                    OR (c.visibility = 'ORGANIZATION' AND c.organization_id IN :organization_ids)
                    OR (c.visibility = 'PRIVATE' AND c.owner_user_id = :subject)
              )
              AND (
                    cardinality(c.allowed_roles) = 0
                    OR :role_filter_enabled = false
                    OR ({role_filter})
              )
              AND (
                    c.search_vector @@ websearch_to_tsquery('simple', :query)
                    OR c.content ILIKE '%' || :query || '%'
              )
            ORDER BY similarity DESC
            LIMIT :limit
            """.format(role_filter=" OR ".join(role_clauses))
        ).bindparams(bindparam("organization_ids", expanding=True))
        params: dict[str, Any] = {
            "query": query.strip(),
            "organization_ids": sorted(scope.organization_ids),
            "subject": scope.subject,
            "role_filter_enabled": bool(role_clauses),
            "limit": limit,
            **role_parameters,
        }
        async with self._database.engine.connect() as connection:
            rows = (await connection.execute(statement, params)).mappings().all()
        return _rows_to_chunks(rows)


def _vector_literal(values: Sequence[float]) -> str:
    """为显式 PostgreSQL 类型转换序列化向量。

    显式类型转换使仓储不依赖 SQLAlchemy ORM 模型状态，并让查询类型清晰可见。值会验证为
    有限数值，避免格式错误的模型输出进入数据库驱动。
    """

    if not values:
        raise ValueError("embedding 不能为空")
    return "[" + ",".join(str(float(value)) for value in values) + "]"


def _rows_to_chunks(rows: Sequence[Any]) -> list[KnowledgeChunk]:
    """将向量和词法查询结果行映射为统一来源模型。"""

    return [
        KnowledgeChunk(
            id=str(row["id"]),
            document_id=str(row["document_id"]),
            chunk_index=int(row["chunk_index"]),
            content=str(row["content"]),
            source_uri=str(row["source_uri"]),
            title=str(row["title"]),
            document_type=str(row["document_type"]),
            version=int(row["version"]),
            similarity=float(row["similarity"]),
            metadata=dict(row["metadata"] or {}),
            parent_id=str(row["parent_id"]) if row["parent_id"] else None,
            parent_content=str(row["parent_content"]) if row["parent_content"] else None,
            parent_section_path=tuple(row["parent_section_path"] or ()),
        )
        for row in rows
    ]


def _chunk_params(
    chunks: Sequence[KnowledgeChunkInput],
    embeddings: Sequence[Sequence[float]],
) -> list[dict[str, Any]]:
    """为插入和替换操作构建一致的参数结构。"""

    return [
        {
            "id": chunk.id,
            "document_id": chunk.document_id,
            "chunk_index": chunk.chunk_index,
            "content": chunk.content,
            "content_hash": chunk.content_hash,
            "embedding": _vector_literal(embedding),
            "organization_id": chunk.organization_id,
            "owner_user_id": chunk.owner_user_id,
            "visibility": chunk.visibility,
            "allowed_roles": list(chunk.allowed_roles),
            "document_type": chunk.document_type,
            "effective_from": chunk.effective_from,
            "effective_to": chunk.effective_to,
            "metadata": json.dumps(chunk.metadata, ensure_ascii=False),
            "parent_id": chunk.parent_id,
        }
        for chunk, embedding in zip(chunks, embeddings, strict=True)
    ]


def _parent_params(parents: Sequence[KnowledgeParentInput]) -> list[dict[str, Any]]:
    """为一个文档事务序列化父节点上下文数据行。"""

    return [
        {
            "id": parent.id,
            "document_id": parent.document_id,
            "content": parent.content,
            "section_path": list(parent.section_path),
            "source_page": parent.source_page,
            "table_index": parent.table_index,
            "row_start": parent.row_start,
            "row_end": parent.row_end,
            "metadata": json.dumps(parent.metadata, ensure_ascii=False),
        }
        for parent in parents
    ]
