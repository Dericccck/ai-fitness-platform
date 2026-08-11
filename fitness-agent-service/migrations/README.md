# Database migrations

Agent 服务的结构化数据、Memory、RAG 文档和向量索引统一使用 PostgreSQL + pgvector。
正式业务表落地前，所有表结构必须通过 Alembic migration 管理，不允许依赖自动建表。

下一步将先建立租户、会话、Memory、知识文档和检索片段表，再接入训练计划领域表。
