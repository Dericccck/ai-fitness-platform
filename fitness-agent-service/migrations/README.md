# Database migrations

Agent 服务的结构化数据、Memory、RAG 文档和向量索引统一使用 PostgreSQL + pgvector。
正式业务表落地前，所有表结构必须通过 Alembic migration 管理，不允许依赖自动建表。

当前已建立版本化知识文档、父节点和检索切片表。执行：

```bash
make agent-migrate
```

后续将继续增加长期 Memory、评测记录和索引重建任务表；训练计划领域表仍由 Java/MySQL
业务迁移管理。

文档写入由 Agent 服务的 `DocumentIngestionService` 执行：先清洗和切片，再调用真实 Embedding，
最后通过 Repository 在事务内发布文档版本和切片；相同 checksum 会跳过索引，旧的已发布版本
会在新版本提交时归档。

父节点不单独生成向量，子节点通过 `parent_id` 关联父节点；检索只召回子节点，随后带回父
上下文，避免父节点向量占用额外索引空间。
