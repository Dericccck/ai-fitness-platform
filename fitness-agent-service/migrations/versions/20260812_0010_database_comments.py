"""为 Agent 数据库表和字段补充中文数据字典注释。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

revision: str = "20260812_0010"
down_revision: str | None = "20260812_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# 注释由迁移文件集中维护，既能在 Navicat 中展示，也能随环境初始化自动复现。
TABLE_COMMENTS: dict[str, str] = {
    "alembic_version": "Alembic 业务数据库迁移版本记录表",
    "checkpoint_migrations": "LangGraph Checkpoint 官方表结构迁移记录表",
    "checkpoint_blobs": "LangGraph Checkpoint 大型二进制状态数据表",
    "checkpoint_writes": "LangGraph Checkpoint 执行过程中的中间写入表",
    "checkpoints": "LangGraph Agent 会话状态快照表",
    "knowledge_documents": "RAG 知识文档主表，保存来源、版本、状态和权限范围",
    "knowledge_parents": "RAG 父节点表，保存章节级完整上下文，不直接生成向量",
    "knowledge_chunks": "RAG 子节点表，保存实际检索文本、Embedding 和权限元数据",
    "knowledge_ingestion_jobs": "知识资料上传、审核、解析和索引任务表",
    "knowledge_reindex_jobs": "知识库批量索引重建任务主表",
    "knowledge_reindex_items": "索引重建任务中的文档明细和处理状态表",
}

COLUMN_COMMENTS: dict[str, dict[str, str]] = {
    "alembic_version": {
        "version_num": "当前已执行的 Alembic 迁移版本号",
    },
    "checkpoint_migrations": {
        "v": "LangGraph Checkpoint 内部迁移版本号",
    },
    "checkpoint_blobs": {
        "thread_id": "Agent 会话线程唯一标识",
        "checkpoint_ns": "Checkpoint 命名空间",
        "channel": "状态数据所属的 LangGraph 通道",
        "version": "通道状态版本号",
        "type": "序列化数据类型",
        "blob": "序列化后的状态二进制内容",
    },
    "checkpoint_writes": {
        "thread_id": "Agent 会话线程唯一标识",
        "checkpoint_ns": "Checkpoint 命名空间",
        "checkpoint_id": "所属 Checkpoint 唯一标识",
        "task_id": "产生该中间写入的 Agent 任务标识",
        "idx": "同一任务内的写入顺序号",
        "channel": "写入目标的 LangGraph 通道",
        "type": "序列化数据类型",
        "blob": "序列化后的中间写入二进制内容",
        "task_path": "Agent 任务在图中的执行路径",
    },
    "checkpoints": {
        "thread_id": "Agent 会话线程唯一标识，按用户身份范围隔离",
        "checkpoint_ns": "Checkpoint 命名空间",
        "checkpoint_id": "当前会话状态快照唯一标识",
        "parent_checkpoint_id": "父级会话状态快照标识，用于状态链路恢复",
        "type": "Checkpoint 序列化类型",
        "checkpoint": "Agent 会话状态快照 JSON 内容",
        "metadata": "Checkpoint 运行元数据，例如来源节点和写入信息",
    },
    "knowledge_documents": {
        "id": "知识文档唯一标识",
        "organization_id": "组织范围标识；全局文档为空",
        "title": "知识文档标题",
        "source_uri": "稳定的知识来源 URI，用于版本和幂等识别",
        "document_type": "文档业务类型，例如 FITNESS_GUIDE 或 MEDICAL_EXERCISE_GUIDELINE",
        "visibility": "可见范围：GLOBAL 全局、ORGANIZATION 组织、PRIVATE 私有",
        "applicable_roles": "允许访问该文档的角色列表",
        "version": "同一 source_uri 的文档版本号",
        "status": "文档状态：DRAFT 草稿、PUBLISHED 已发布、ARCHIVED 已归档",
        "checksum": "规范化文档内容校验和，用于判断内容是否变化",
        "effective_from": "文档权限和内容开始生效时间",
        "effective_to": "文档权限和内容失效时间，为空表示长期有效",
        "created_at": "文档版本创建时间",
        "updated_at": "文档版本最后更新时间",
    },
    "knowledge_parents": {
        "id": "父节点唯一标识",
        "document_id": "所属知识文档标识",
        "content": "章节级完整上下文内容",
        "section_path": "文档标题层级路径",
        "source_page": "来源 PDF 页码，为空表示非 PDF 或无页码",
        "table_index": "来源页面内的表格序号",
        "row_start": "表格起始行号",
        "row_end": "表格结束行号",
        "metadata": "父节点解析元数据，例如解析器和来源坐标",
        "created_at": "父节点创建时间",
    },
    "knowledge_chunks": {
        "id": "子节点唯一标识",
        "document_id": "所属知识文档标识",
        "chunk_index": "文档内子节点顺序号",
        "content": "用于关键词召回、重排序和模型引用的文本片段",
        "content_hash": "子节点规范化内容校验和",
        "embedding": "子节点的 1024 维 BGE-M3 向量",
        "organization_id": "组织权限过滤字段；全局文档为空",
        "owner_user_id": "私有文档所属用户标识",
        "visibility": "子节点可见范围",
        "allowed_roles": "允许访问子节点的角色列表",
        "document_type": "子节点继承的文档业务类型",
        "effective_from": "子节点开始生效时间",
        "effective_to": "子节点失效时间，为空表示长期有效",
        "metadata": "子节点来源坐标和解析元数据，例如页码、表格和工作表",
        "created_at": "子节点创建时间",
        "parent_id": "关联的父节点标识，用于召回后补充完整上下文",
        "search_vector": "PostgreSQL 全文检索向量，由子节点文本生成",
    },
    "knowledge_ingestion_jobs": {
        "id": "知识资料索引任务唯一标识",
        "source_uri": "待处理资料的稳定来源 URI",
        "original_filename": "用户或资料目录中的原始文件名",
        "storage_key": "暂存文件的不透明存储键",
        "content_type": "文件 MIME 类型",
        "size_bytes": "文件大小，单位为字节",
        "title": "待入库文档标题",
        "document_type": "待入库文档业务类型",
        "organization_id": "组织范围标识",
        "owner_user_id": "私有资料所属用户标识",
        "visibility": "资料可见范围",
        "allowed_roles": "允许访问资料的角色列表",
        "effective_from": "资料开始生效时间",
        "effective_to": "资料失效时间",
        "requested_version": "本次请求要创建的文档版本号",
        "submitted_by": "提交资料的用户或系统主体标识",
        "status": "任务状态：PENDING_REVIEW 待审核、QUEUED 排队、INDEXING 索引中、SUCCEEDED 成功、FAILED 失败、REJECTED 拒绝",
        "attempt_count": "已执行索引尝试次数",
        "max_attempts": "允许的最大索引尝试次数",
        "reviewer_id": "执行审核决定的管理员标识",
        "review_comment": "管理员审核备注",
        "error_code": "最后一次失败的异常类型或错误编码",
        "error_message": "最后一次失败的脱敏错误信息",
        "document_id": "索引成功后生成的知识文档标识",
        "reviewed_at": "审核决定时间",
        "started_at": "Worker 开始处理时间",
        "finished_at": "任务完成或失败时间",
        "created_at": "任务创建时间",
        "updated_at": "任务最后更新时间",
        "content_sha256": "原始文件 SHA-256 校验和",
        "safety_status": "结构安全扫描结果",
        "scanner_name": "结构安全扫描器名称和版本",
        "malware_status": "恶意软件扫描结果",
        "malware_scanner": "恶意软件扫描器名称和版本",
        "malware_signature": "命中的恶意软件签名，为空表示未命中",
        "malware_scanned_at": "恶意软件扫描时间",
    },
    "knowledge_reindex_jobs": {
        "id": "索引重建批次唯一标识",
        "requested_by": "发起索引重建的管理员或系统主体",
        "organization_id": "重建任务限定的组织范围",
        "target_document_id": "指定重建的文档标识，为空表示按范围批量重建",
        "status": "重建状态：QUEUED 排队、INDEXING 索引中、SUCCEEDED 成功、FAILED 失败",
        "total_documents": "本次重建涉及的文档总数",
        "processed_documents": "已经处理的文档数",
        "succeeded_documents": "成功重建的文档数",
        "skipped_documents": "因内容未变化等原因跳过的文档数",
        "failed_documents": "重建失败的文档数",
        "attempt_count": "批次重试次数",
        "max_attempts": "批次允许的最大重试次数",
        "error_message": "批次级脱敏错误信息",
        "created_at": "重建任务创建时间",
        "updated_at": "重建任务最后更新时间",
        "started_at": "重建任务开始时间",
        "finished_at": "重建任务完成或失败时间",
    },
    "knowledge_reindex_items": {
        "id": "索引重建明细唯一标识",
        "job_id": "所属索引重建批次标识",
        "document_id": "待重建知识文档标识",
        "source_uri": "文档稳定来源 URI 快照",
        "title": "文档标题快照",
        "document_type": "文档业务类型快照",
        "organization_id": "文档组织范围快照",
        "owner_user_id": "文档所属用户快照",
        "visibility": "文档可见范围快照",
        "allowed_roles": "文档允许角色快照",
        "effective_from": "文档生效时间快照",
        "effective_to": "文档失效时间快照",
        "version": "待重建文档版本号",
        "storage_key": "待重建源文件的不透明存储键快照",
        "original_filename": "待重建源文件名快照",
        "content_type": "待重建文件 MIME 类型快照",
        "status": "明细状态：PENDING 待处理、INDEXING 索引中、SUCCEEDED 成功、SKIPPED 跳过、FAILED 失败",
        "attempt_count": "该文档已重建尝试次数",
        "max_attempts": "该文档允许的最大重建尝试次数",
        "error_message": "该文档最后一次脱敏错误信息",
        "created_at": "明细创建时间",
        "updated_at": "明细最后更新时间",
        "started_at": "明细开始处理时间",
        "finished_at": "明细完成或失败时间",
    },
}


def _sql_literal(value: str) -> str:
    """将注释文本转为 PostgreSQL 安全字符串字面量。"""

    return "'" + value.replace("'", "''") + "'"


def _apply_comments() -> None:
    """执行表级和字段级 COMMENT，注释不会修改任何业务数据。"""

    # 这一步需要查询 information_schema 判断旧环境是否存在对应表；离线模式没有查询结果，
    # 不能把 None 当成 Result 使用。真实在线升级仍完整执行数据字典注释。
    if context.is_offline_mode():
        return
    connection = op.get_bind()
    for table, comment in TABLE_COMMENTS.items():
        table_exists = connection.execute(
            sa.text("SELECT to_regclass(:table_name)"),
            {"table_name": f"public.{table}"},
        ).scalar_one()
        if table_exists is None:
            continue
        connection.execute(sa.text(f"COMMENT ON TABLE {table} IS {_sql_literal(comment)}"))
    for table, columns in COLUMN_COMMENTS.items():
        for column, comment in columns.items():
            column_exists = connection.execute(
                sa.text(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = :table_name "
                    "AND column_name = :column_name"
                ),
                {"table_name": table, "column_name": column},
            ).scalar()
            if column_exists is not None:
                connection.execute(
                    sa.text(f"COMMENT ON COLUMN {table}.{column} IS {_sql_literal(comment)}")
                )


def upgrade() -> None:
    """为当前 Agent 数据库的全部业务表和 Checkpoint 表添加中文数据字典。"""

    _apply_comments()


def downgrade() -> None:
    """移除本迁移写入的表级和字段级注释，不删除任何表或数据。"""

    if context.is_offline_mode():
        return
    connection = op.get_bind()
    for table, columns in COLUMN_COMMENTS.items():
        for column in columns:
            column_exists = connection.execute(
                sa.text(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = :table_name "
                    "AND column_name = :column_name"
                ),
                {"table_name": table, "column_name": column},
            ).scalar()
            if column_exists is not None:
                connection.execute(sa.text(f"COMMENT ON COLUMN {table}.{column} IS NULL"))
    for table in TABLE_COMMENTS:
        table_exists = connection.execute(
            sa.text("SELECT to_regclass(:table_name)"),
            {"table_name": f"public.{table}"},
        ).scalar_one()
        if table_exists is not None:
            connection.execute(sa.text(f"COMMENT ON TABLE {table} IS NULL"))
