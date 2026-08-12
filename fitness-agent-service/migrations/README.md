# Database migrations

Agent 服务的结构化数据、Memory、RAG 文档和向量索引统一使用 PostgreSQL + pgvector。
正式业务表落地前，所有表结构必须通过 Alembic migration 管理，不允许依赖自动建表。

当前已建立版本化知识文档和检索切片表。执行：

```bash
make agent-migrate
```

后续将继续增加长期 Memory、评测记录和索引重建任务表；训练计划领域表仍由 Java/MySQL
业务迁移管理。
