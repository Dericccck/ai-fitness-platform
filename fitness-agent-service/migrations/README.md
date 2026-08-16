# Database migrations

Agent 服务的结构化数据、Memory、RAG 文档和向量索引统一使用 PostgreSQL + pgvector。
正式业务表落地前，所有表结构必须通过 Alembic migration 管理，不允许依赖自动建表。

当前已建立版本化知识文档、父节点、检索切片和可审核索引任务表。执行：

```bash
make agent-migrate
```

0015/0016 迁移增加 `agent_memories` 及表级说明。它只保存用户明确提供并确认过的低敏健身偏好、目标、器械、
时间安排和沟通偏好。保存、覆盖和撤销由 Agent 的确认链路保护；表内保留状态、版本、过期时间和
来源请求 ID，便于幂等、并发控制和审计。当前不保存医疗诊断、疾病、药物、支付凭证或合同预约事实。

0017 迁移增加 `agent_memory_candidates`，模型提取的候选在用户批准前以 PENDING 状态保存，正文使用
AES-GCM 加密，支持去重、机构隔离和过期状态。0018 增加 `agent_memory_candidate_events` 不可变事件表，
在同一事务内记录候选创建、批准、拒绝和过期；事件只保存状态和正文摘要，不保存候选明文或确认凭证。
0019 增加 `agent_memory_events` 正式 Memory 不可变事件表，在同一事务内记录保存、撤销和自动过期；
事件只保存主体/机构快照、状态和版本，不保存 Memory 正文。保存和撤销均以 `operation_id` 做幂等，
支持管理页面重试而不重复递增版本或产生重复审计事件。
0020 增加 `agent_notification_outbox` 事务性通知 Outbox。Memory 候选进入 `PENDING` 时，候选状态、候选审计
事件和通知任务在同一个事务中提交；Outbox 只保存候选 ID 等路由参数，不保存候选正文。下游发布器通过
`FOR UPDATE SKIP LOCKED` 抢占任务，并使用 `PUBLISHED`、`RETRYABLE_FAILED`、`DEAD` 状态和有限重试控制可靠投递。
0021 增加 `agent_in_app_notifications` 站内通知收件箱。Outbox Worker 将待发布任务原子写入收件箱并标记为
`PUBLISHED`，收件箱使用相同去重键避免重复通知；用户只能读取和标记自己签名机构范围内的通知。

0022 增加 `agent_notification_preferences` 用户通知偏好表，并扩展 Outbox 的 `DEFERRED`、`SUPPRESSED` 状态。
Worker 在发布事务内执行授权、安静时间和频率限制判断；未配置偏好默认允许，用户关闭或频率限制会留下可审计
的抑制结果，安静时间会延迟到下一次允许时间。

0025 增加 `agent_notification_templates` 版本化通知模板表，并为站内通知增加标题、正文和模板版本快照。模板状态
按 `DRAFT`、`APPROVED`、`PUBLISHED`、`RETIRED` 流转，Worker 只渲染已发布版本；历史收件箱内容不会随着模板升级
而变化。当前只启用 `IN_APP` 渠道，短信、Push 和 RabbitMQ 仍由后续渠道适配器接入。

0026 增加 `agent_notification_template_events` 不可变模板生命周期审计表。创建草稿、审核和发布都必须携带唯一
`operation_id`，同一操作重试返回原版本，不重复生成状态或审计事件；审计表只保存模板键、版本、操作者和状态快照，
不保存通知标题、正文和变量内容。

0027 增加 `agent_notification_delivery_attempts` 渠道投递尝试表。每次真实渠道投递记录 `STARTED`、`SUCCEEDED`、
`RETRYABLE_FAILED` 或 `FINAL_FAILED`，并保存受控错误码和渠道消息 ID；该表通过外键保护 Outbox，清理 Outbox 前必须先
按保留策略处理投递尝试，不能无审计删除父事件。

0028 增加 `agent_operations_query_audits` 管理员经营查询审计表。每次固定指标查询的当前周期和上一等长周期分别记录
主体、角色快照、机构、指标、时间桶、日期范围、聚合行数、成功/失败状态和请求链路；失败时日期或行数可以为空。表中
禁止保存 SQL、Prompt、模型输出、预约明细和签名上下文，查询成功但审计写入失败时 Agent 采用 fail-closed，不向模型返回
未形成持久化审计的经营结果。

0029 增加 `SAME_PERIOD_LAST_YEAR` 审计角色，记录正式同比查询的上一自然年同期日期范围。同比边界由 Agent 按相同月日
映射计算；2 月 29 日映射到非闰年的 2 月 28 日，实际范围仍写入审计，不能由模型自行推断。

0023 增加正式 Memory 和候选的正文保留期限字段、脱敏标记和 `REDACTED` 审计事件。正式 Memory 进入
`REVOKED/EXPIRED` 后默认保留 90 天，候选进入 `APPROVED/REJECTED/EXPIRED` 后默认保留 30 天；期限到达后，
独立 Memory Retention Worker 将正式 Memory 内容替换为脱敏标记、清空候选密文，但不删除类型、状态、主体范围
和不可变生命周期审计。期限可以通过部署配置调整，历史终态数据在迁移时补齐默认保留截止时间。

训练计划领域表仍由 Java/MySQL 业务迁移管理。

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

0005 迁移增加 `tsvector` 全文索引和 `pg_trgm` 三元组索引。关键词召回和向量召回分别完成
权限过滤后再由服务层做 RRF 融合；索引只是召回加速器，不能替代 SQL 中的 ACL 条件。

0006 迁移增加批量索引重建任务和文档明细；0007 为每条明细补充已审核源文件引用与文档元数据快照，
执行期间不会因为同一 `source_uri` 的新版本上传而改变重建范围。

0008 迁移增加独立的 malware verdict 字段，包括扫描状态、扫描器、病毒签名和扫描时间。结构
扫描仍保留在 `safety_status`/`scanner_name` 中，不能用结构检查结果冒充杀毒结果。

0011 迁移增加 `knowledge_review_reports` 不可变审核报告表。每份报告绑定上传任务、原始文件
SHA-256、解析器/管线/审核策略版本，并保存质量指标、PDF 页级画像、阻断结论、审核领域、
建议角色和尚未被现有 RBAC 表达的专业资质要求。旧任务如果没有报告会被默认拒绝审批，必须重新
解析，不能用管理员备注补造审核证据。

RAG 评测目前使用仓库内的黄金结果回归集，不读取线上用户数据。CI 门禁要求最低 Recall@K、MRR
并且禁止出现越权文档 ID；评测集扩大后再接入带脱敏数据的离线评测数据库。

0013 迁移增加 `agent_action_confirmations` 和 `agent_action_confirmation_events`。前者保存写操作
的不可变动作摘要、规范化参数哈希、加密参数密文、授权状态、执行状态、TTL、版本和一次性 JTI；后者
保存创建、批准、拒绝、过期、撤销、签发、领取、消费、重排和执行结果事件。确认 Token 本身不入库，确认单的
`payload_ciphertext` 必须由上层加密边界生成。仓储使用数据库幂等约束和 `SELECT ... FOR UPDATE`，
避免重复确认、重复消费和并发覆盖。

0013 只完成确认事实的持久化基础，不等于 LangGraph 人机确认闭环已经完成；后续仍需接入
`interrupt()`、确认/拒绝 API、服务端凭证签发和 `Command(resume=...)` 恢复。
