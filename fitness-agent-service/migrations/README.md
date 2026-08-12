# Database migrations

Agent 服务的结构化数据、Memory、RAG 文档和向量索引统一使用 PostgreSQL + pgvector。
正式业务表落地前，所有表结构必须通过 Alembic migration 管理，不允许依赖自动建表。

当前已建立版本化知识文档、父节点、检索切片和可审核索引任务表。执行：

```bash
make agent-migrate
```

后续将继续增加长期 Memory、评测记录和索引重建任务表；训练计划领域表仍由 Java/MySQL
业务迁移管理。

`knowledge_ingestion_jobs` 记录上传审核、索引 Claim、失败和有限重试状态。上传任务不会
直接写入可检索的 `knowledge_documents`，只有审核后的后台索引任务完成父子节点、Embedding
和发布事务后才会成为 `PUBLISHED` 版本。

任务同时保存文件 SHA-256、结构扫描结果和扫描器版本。结构扫描用于拦截明显的恶意压缩包
和格式伪装，不能替代生产环境的 ClamAV/云安全服务；外部扫描接入后应保留独立 verdict 和
审计时间，不能覆盖原始文件身份。

文档写入由 Agent 服务的 `DocumentIngestionService` 执行：先清洗和切片，再调用真实 Embedding，
最后通过 Repository 在事务内发布文档版本和切片；相同 checksum 会跳过索引，旧的已发布版本
会在新版本提交时归档。

父节点不单独生成向量，子节点通过 `parent_id` 关联父节点；检索只召回子节点，随后带回父
上下文，避免父节点向量占用额外索引空间。
