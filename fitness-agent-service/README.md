# AI Fitness Agent Service

这是健身平台的 Python Agent 服务，与现有 Java Spring Boot 业务后端并行部署。
赛事、活动运营及其遗留代码不属于本服务的业务范围。

## 责任边界

- Java 后端：用户认证、RBAC、组织数据权限、预约/合同/课时等业务事务、审计和对外业务接口。
- Agent 服务：意图识别、Supervisor 编排、RAG/Memory、模型调用和受控 Tool Calling。
- Agent 不直接写健身业务库；后续所有业务动作必须经过 Java Tool Gateway 的授权、幂等和审计。
- Agent 通过 `app.infrastructure.gateway_client.GatewayClient` 调用 Java 健身核心 Gateway；
  Client 只透传认证服务签发的 `AgentContext`，不允许根据模型输出自行生成用户身份。

## 当前基础设施

- PostgreSQL + pgvector：Agent 会话、Memory、知识库和向量索引。
- Redis：LangGraph checkpoint、短期会话状态、限流和缓存。
- LLM：DeepSeek OpenAI-compatible Chat Completions 接口，配置变量与 `learning-langchain-CN` 保持一致。
- Embedding：OpenAI-compatible Embeddings 接口，可与 LLM 使用不同服务商。
- Reranker：可配置的 HTTP 服务，不提供本地 mock 或静默降级。
- Fitness Core Gateway：Java 只读业务 Tool 服务，查询用户、机构、课程、合同、课时和预约。
- Tool Registry：版本化注册首批健身只读工具，校验输入 Schema、限制未知工具，并记录不含原始参数的调用审计。
- Operations 管理审计：管理员可通过 `/api/v1/admin/operations/query-audits` 按机构、固定指标、时间桶、比较周期、状态和创建时间分页查询经营查询审计；响应同时附带固定指标口径元数据，组织管理员只能查看签名机构范围，接口不返回 SQL、Prompt 或业务明细；指标、时间桶和比较周期的组合会按固定目录校验，不支持的组合返回 `422`。当前固定指标支持上一等长周期环比和上一自然年同期同比，日期边界与除零处理由程序确定；新增 `COMPLETED_CLASS_COUNT`，按已完成/核销成功预约统计完课量；新增 `NEW_CUSTOMER_COUNT`，按有效合同的新客标记统计去重学员数；新增 `REVENUE_AMOUNT`，按合同创建口径统计扣除退款后的净营收。`/api/v1/admin/operations/metric-catalog` 提供不含业务数据的指标能力目录，带有内容版本、ETag 和私有缓存语义，供前端动态生成筛选器和报表配置。
- Operations Agent 端到端联调：经营问题经过 Supervisor 路由、固定指标工具、Tool Registry 角色/参数校验、Java Gateway 和审计后，才由模型基于真实聚合结果生成摘要；集成测试同时验证管理员成功查询和学员越权请求在到达 Gateway 前被阻断。
- Supervisor Runtime：基于 LangGraph 执行模型 Tool Calling、工具预算、真实结果回填和业务范围护栏。
- 会话持久化：PostgreSQL 保存 LangGraph Checkpoint，Redis 负责会话互斥锁和短期状态。
- RAG 基础：Alembic 管理版本化知识文档、切片、租户/角色权限字段和 pgvector HNSW 索引；
  检索顺序固定为服务端权限过滤 → 向量/关键词混合候选召回 → RRF 融合 → 真实 Reranker
  → 带来源证据的 Agent 上下文。
- 混合召回：PostgreSQL `tsvector` 全文索引和 `pg_trgm` GIN 索引与 pgvector 并行工作；两条
  路径都独立执行租户、角色、所有者、生效时间和发布状态过滤，随后按 Reciprocal Rank Fusion
  融合，避免向量分数和关键词分数直接比较。
- 文档索引：`DocumentIngestionService` 负责统一解析 Markdown/TXT、PDF、DOCX、XLSX，执行文本
  清洗、标题/段落语义切片、checksum 去重、稳定文档/切片 ID，以及新版本发布时的旧版本归档；
  Embedding 和切片写入受批次和事务边界控制。PDF 页码、DOCX 标题层级、XLSX 工作表和表格
  行范围会随子节点保存，解析失败不会静默按纯文本入库。
- PDF 文本层清洗：针对部分演示文稿 PDF 的确定性字体映射噪声，只修复“整词每个字形重复”的
  情况，例如将 `IInnffoorrmmaattiioonn` 还原为 `Information`，不会把正常的 `coffee`、`letter`
  等英文双写字母误改；同一异常行中的重复标点和 URL 分隔符也只按窄规则修复。对被
  `pdfplumber` 误识别为“一列多行表格”的多栏文本框，回到正文文本路径；真正的多行多列表格仍保留
  表头、行范围和表格元数据。上述处理发生在父子切片和 Embedding 之前，并有真实 PDF 回归验证。
- 父子节点：子节点参与向量召回，父节点保存章节或表格完整上下文；命中后按 `parent_id`
  扩展且同一父节点只注入一次，所有格式的表格子节点都会转为带表头的 Markdown 表示，
  并记录表格序号、页码/工作表和行范围。扫描型 PDF 或混合型 PDF 的空白页会进入 OCR 服务，
  OCR 失败不会把空内容写入知识库。
- 知识库管理：管理员上传先进入私有暂存区和 `PENDING_REVIEW`，审核通过后进入 `QUEUED`；
  Worker 以数据库原子 Claim 进入 `INDEXING`，只有父子节点、Embedding 和发布事务完成后
  才标记 `SUCCEEDED`。失败任务保留错误类型、尝试次数和审核记录，支持有限次数人工重试；
  组织管理员只能看到签名组织范围内的组织知识，平台管理员才可管理全局知识。
- 文件安全与存储：上传会在解析和存储前执行 UTF-8、文件签名、Office ZIP 路径、加密包、
  符号链接、宏文件和解压大小检查，并记录 SHA-256 与扫描器版本。存储支持本地适配器和
  S3-compatible/MinIO 适配器；生产配置可通过 ClamAV `INSTREAM` 协议执行真实杀毒，扫描
  不可用时默认 fail-closed，不会把文件写入暂存区。结构检查 verdict 与 malware verdict
  分开审计。
- OCR：`HttpPdfOcrProvider` 通过独立 HTTP OCR 服务处理扫描型或混合型 PDF，只请求缺失页，
  严格校验服务返回的文本块、页码、表格和元数据后才进入父子节点切片；OCR 服务不可用或
  响应不符合契约时，上传不会进入审核队列。
- 索引 Worker：`KnowledgeIngestionWorker` 提供有界轮询入口，任务通过 PostgreSQL 原子 Claim
  防止重复执行；超过重试上限的 `FAILED` 任务即为死信状态，后续可由独立 Worker Deployment
  或队列消费者接管，不依赖单个 API 进程内存。
- Prometheus：低基数 HTTP 请求量、耗时、并发和构建信息指标。
- OpenTelemetry：可选 OTLP/HTTP Trace 导出，默认关闭且不发送 Prompt 或用户档案。
- 写操作确认：训练计划写工具会先生成确定性确认摘要和加密参数，写入 PostgreSQL 确认单后通过
  LangGraph `interrupt()` 暂停；批准接口会在服务端持久化决定后使用同一 `thread_id` 调用
  `Command(resume=...)`，从加密参数恢复并通过短时确认凭证调用 Java Gateway。当前凭证仍是与
  Java Gateway v1 兼容的 HMAC 过渡版本；一次性 JTI 已在 Agent 确认单中领取、由 Gateway
  校验并在训练服务事务中消费。AgentContext 已支持配置公钥环的 RS256 验签，RS256 私钥不进入
  Agent 服务；当前尚未接入外部 JWKS，公钥更新仍由部署配置完成。

## 本地启动

以下命令均在仓库根目录执行；本地环境模板会启用 ClamAV，模型密钥仍需按个人环境补充。

```bash
cp deployment/environments/agent.local.env.example fitness-agent-service/.env
make infra-up-security
make agent-sync
make agent-migrate
make agent-run
```

首次本地启动前必须为确认参数加密配置随机密钥；不要把真实密钥提交到 Git：

```bash
openssl rand -base64 32
```

将命令输出填入 `fitness-agent-service/.env` 的 `AGENT_CONFIRMATION_ENCRYPTION_KEY_BASE64`。
密钥缺失或长度不合法时服务会 fail-closed，不会启动一个无法安全保护写参数的确认流程。

确认凭证还需要一份与 Java Gateway 共享的服务端签名密钥。开发环境可生成：

```bash
openssl rand -hex 32
```

将结果同时注入 Agent 的 `AGENT_CONFIRMATION_SIGNING_SECRET` 和 Gateway 的
`GATEWAY_CONFIRMATION_SIGNING_SECRET`；生产环境应由 Secret Manager 注入，不能写入 `.env`、
镜像或 Git。两边的值不一致时，批准后的写操作会在 Gateway 验签阶段失败。

本地若要实际处理扫描型 PDF，还需在 Linux/GPU 或 amd64 推理节点启动独立 OCR 服务：
`make infra-up-ocr`。macOS 开发机可以只运行 Agent，并把 `AGENT_RAG_OCR_ENDPOINT_URL` 指向
远程 OCR 服务。

`/health/live` 只检查进程，`/health/ready` 检查 PostgreSQL、Redis 和三个模型能力是否均已配置并可用。
没有配置真实模型凭证时服务不会伪装成 ready。

本地真实联调身份

当前仓库尚未接入独立认证服务，因此本地联调可以使用受保护的开发签发器生成一个
5 分钟有效的 `AgentContext`。该脚本只在显式开启本地开关时运行，角色限制在
`ORGANIZATION_ADMIN`、`COACH`、`STUDENT` 白名单内，禁止签发 `SYSTEM_ADMIN`，不会作为
生产接口部署，也不会打印签名密钥。

Operations 使用机构管理员上下文：

```bash
export DEV_AGENT_ORG_ID='<本地 MySQL 中真实存在的机构 ID>'
export AGENT_LIVE_AGENT_CONTEXT="$(make agent-dev-context)"
make agent-operations-live-preflight
```

Booking/Fitness 必须使用 MySQL 中真实存在的业务用户；例如 Booking 学员 dry-run：

```bash
export DEV_AGENT_ORG_ID='<学员所属机构 ID>'
export DEV_AGENT_SUBJECT='<login_user.id，且该学员在机构内有有效合同>'
export DEV_AGENT_ROLE='STUDENT'
export AGENT_LIVE_AGENT_CONTEXT="$(make agent-dev-context)"
make agent-business-live-preflight
make agent-booking-live-check
```

业务前置检查除了服务存活和就绪状态，还会通过 Gateway `/me` 验证签名 `subject` 确实存在于
业务用户表；校验失败时不会调用 DeepSeek。不能用随意编造的用户 ID 通过这道门禁。

`agent-dev-context` 会从 `fitness-agent-service/.env` 读取
`GATEWAY_CONTEXT_SIGNING_SECRET`，因此 Agent 和 Gateway 必须使用同一份密钥。Token 只应
保存在当前终端的环境变量中，过期后重新签发；正式环境必须由认证服务签发，不能使用这个脚本。

常用质量检查：

```bash
make agent-format
make agent-check
make knowledge-quality-gate
```

`knowledge-quality-gate` 只读取 `data/knowledge/manifest.json` 中的原始资料，复用当前解析器和
父子切分逻辑，检查网页/目录噪声率、异常短碎片率、重复率、父节点完整性、表格结构完整性和
PDF 缺失页。它不会调用 LLM、生成 Embedding 或写入 PostgreSQL；出现 `BLOCKED` 时必须先修复、
OCR 或人工审核，不能直接执行知识库重建。

质量报告支持保存和前后版本比较：`python scripts/validate_document_quality.py --label <label>
--output /tmp/quality.json` 会保存来源 SHA-256、页面画像和重复字形残留等指标；
`python scripts/compare_document_quality.py --before before.json --after after.json` 要求两份报告覆盖
相同来源，并在指标方向变化或状态降级时返回非零。`REFERENCE_ONLY` 文档也会生成解析指标，便于评估
解析器升级，但始终不会因为质量通过而进入索引。
解析清洗规则或质量指标变化时会提升解析管线版本，历史审核报告和发布凭证自动失效，必须重新解析、
审核后才能发布或重建索引。

历史 Java 项目是不完整的旧源码快照，不属于 Agent 服务质量门禁。阶段 2 新增的健身核心
Tool Gateway 会拥有独立、可复现的 Java 构建和自动化测试，且不会恢复赛事、作品或活动代码。

Python 版本固定为 3.11，`uv.lock` 是依赖事实源；CI 和本地均使用 `uv sync --locked`，
禁止在未更新锁文件的情况下隐式升级依赖。HTTP 请求会返回 `X-Request-ID` 和
`X-Trace-ID`，结构化日志使用相同字段关联后续 Agent、模型和 Tool 调用。

## 可观测性

- `GET /metrics` 暴露 Prometheus 文本格式指标，生产环境必须在网关或网络策略层限制访问。
- 本地启动 OpenTelemetry Collector：`make observability-up`。
- 启用 Trace 时配置 `AGENT_OTEL_ENABLED=true` 和
  `AGENT_OTEL_EXPORTER_OTLP_TRACES_ENDPOINT`。
- OTLP 鉴权信息使用标准 `OTEL_EXPORTER_OTLP_HEADERS` 注入，不得写入环境模板。
- HTTP 指标只使用路由模板，不使用用户 ID、原始 URL、Prompt 或 Tool 参数作为标签。

环境分层和 Secret 管理规则见 `deployment/environments/README.md`。

## Java Gateway 调用

配置 `AGENT_GATEWAY_BASE_URL`、`AGENT_GATEWAY_INTERNAL_SERVICE_TOKEN` 以及超时和重试参数。
每次业务请求还必须由认证服务提供签名的 `GatewayRequestContext`；Client 对 408、429、5xx
和连接超时做有限指数退避，对 401、403、404 和参数错误不重试。完整 HTTP 契约见
`docs/contracts/fitness-core-gateway-v1.md`。
Operations 指标还通过 HTTP 契约回归测试固定路径、组织/指标/日期/时间桶参数、双层认证 Header
和 `GatewayOperationsMetric` 响应字段；缺少 `generatedAt` 等版本化字段时会 fail-closed，
不会把不完整结果交给模型。

DeepSeek 配置使用 `DEEPSEEK_API_KEY`、`DEEPSEEK_MODEL` 和 `DEEPSEEK_BASE_URL`；默认模型为
`deepseek-v4-flash`，默认关闭 thinking，以保持工具调用参数和结构化响应稳定。旧的
`AGENT_LLM_*` 变量仍可兼容读取，但新环境统一使用 `DEEPSEEK_*`。

当前本地 RAG 验证默认使用学习项目已下载的 BGE-M3 和 BGE-Reranker Base：
`AGENT_EMBEDDING_BACKEND=local`、`AGENT_EMBEDDING_MODEL_PATH` 指向 BGE-M3 快照，
`AGENT_EMBEDDING_DIMENSIONS=1024`，以及 `AGENT_RERANKER_BACKEND=local`、
`AGENT_RERANKER_MODEL_PATH` 指向 BGE-Reranker 快照。生产环境可切换为 `openai` Embedding
和 `http` Reranker，但必须同时执行全量索引重建；向量维度变更不能只修改环境变量。

Supervisor 必须通过 `app.state.tool_registry` 获取工具定义和调用入口；不能在 Prompt、
Agent 节点或模型回调中自行拼接 Gateway URL。当前 Registry 已包含健身只读工具、训练计划草案、
提交审核、审核、发布和训练日执行记录写工具；写工具具备确认标记和缺少凭证时的前置拒绝。确认单持久化、
不可变确认事件、授权/执行状态分离、数据库幂等、参数加密、Checkpoint 脱敏、`interrupt()` 暂停、
确认详情/决定 API、服务端恢复和真实 Gateway 执行已经完成。批准接口内部自动完成“持久化决定→
恢复图→调用写工具”，不会把 Token 返回给客户端。当前仅保留 Java Gateway v1 HMAC 兼容凭证；
预约写操作要等 v2 凭证消费闭环、幂等键和 Java 事务审计完成后再加入。

当前对话接口为 `POST /api/v1/agent/chat`。调用方必须传入认证服务签发的
`X-Agent-Context`，以及 `conversation_id`、`message` 和可选 `locale`；写工具的正常产品流程不
要求浏览器提交确认 Token，批准接口会在服务端恢复时注入。`X-Confirmation-Token` 仍保留为受控的
内部联调过渡通道，接口拒绝额外的用户/组织/角色字段；浏览器和模型不应接触该 Token。当前先提供
非流式稳定协议；SSE 和更完整的断线/重启故障演练将在 v2 凭证边界完成后接入。

Operations 真实服务冒烟联调使用仓库根目录的 `make agent-operations-live-check`。执行前必须
启动 Agent API 和 Java Gateway，并把认证服务签发的组织管理员 `AgentContext` 临时放入
`AGENT_LIVE_AGENT_CONTEXT` 环境变量：

```bash
export AGENT_LIVE_AGENT_CONTEXT='<认证服务签发的短时 Token>'
make agent-operations-live-check
```

该命令会先执行 `agent-operations-live-preflight`：检查 Agent/Gateway 存活探针、Agent
PostgreSQL/Redis/模型/Gateway readiness，以及管理员上下文是否存在；前置检查失败时不会
调用 DeepSeek 或业务指标接口。

脚本默认请求“2026-08-01 至 2026-08-15 的营收金额按周趋势”，验证真实请求进入
`OPERATIONS` 路由、至少完成一次固定指标工具调用并返回结果；它不会伪造身份、直接连接 MySQL，
也不会输出 AgentContext 或完整响应正文。可通过 `AGENT_LIVE_API_URL`、`AGENT_LIVE_MESSAGE`
和 `AGENT_LIVE_TIMEOUT_SECONDS` 调整地址、问题和超时时间。该检查必须使用组织管理员上下文，
学员或教练 Token 应通过权限负例测试验证被拒绝，而不是用来冒充经营查询成功。

训练服务角色真实验收

基础验收命令 `make training-role-live-check` 只检查训练服务存活和“学员创建草案被拒绝”，
不会创建训练计划。准备两条带 `[ROLE_FIXTURE]` 标记的本地夹具（一条 `DRAFT`、一条
`PUBLISHED`）后，可以通过可选环境变量追加完整读取边界验收：

```bash
export TRAINING_LIVE_DRAFT_PLAN_ID='<本地草案夹具 ID>'
export TRAINING_LIVE_PUBLISHED_PLAN_ID='<本地已发布夹具 ID>'
make training-role-visibility-live-check
```

该验收要求管理员和负责教练都能读取两种状态，计划对应学员只能读取 `PUBLISHED`，读取请求不携带
确认凭证，也不会写入训练数据库。夹具必须使用固定前缀并在验收完成后按明确 ID 清理，不能使用
通配条件删除训练计划；生产环境不应保留这类数据。

Java Gateway 到训练服务的真实验收使用 `make gateway-training-role-live-check`。它要求显式设置
`FITNESS_DEV_CONTEXT_ISSUER=1`，由本地开发签发器生成短时的管理员、教练和学员上下文，然后只访问
Gateway 的训练读取接口。除了复验上述六种可见性，还会验证缺少 `GATEWAY_INTERNAL_SERVICE_TOKEN`
时 Gateway 在入口返回 `401`。脚本不会打印上下文或 Token，也不会创建计划、消费确认凭证或写入数据库。

Gateway 训练写入验收使用 `make gateway-training-write-live-check`，默认始终拒绝执行。只有明确
确认本地夹具写入并设置 `GATEWAY_LIVE_EXECUTE_WRITES=1` 后，脚本才会通过 Gateway 创建一条
`[GATEWAY_WRITE_FIXTURE]` 草案，并验证：相同请求 ID 重试返回同一计划；相同 JTI 换请求 ID 重放返回
`409 CONFLICT`；训练服务确认消费记录与计划写入处于同一事务。脚本会输出本轮计划 ID，验收后必须
按这个明确 ID 清理，不允许用机构、学员或状态条件批量删除。该脚本使用本地密钥模拟已批准确认凭证，
不替代 Agent 的 `interrupt()` 流程；Agent 端完整确认流程仍由 `agent-fitness-live-check --execute`
单独验收。

Gateway 训练完整角色工作流验收使用 `make gateway-training-workflow-live-check`，默认始终拒绝执行。
该脚本在显式设置 `GATEWAY_LIVE_EXECUTE_WORKFLOW_WRITES=1` 后，验证
`DRAFT -> PENDING_REVIEW -> APPROVED -> PUBLISHED`，并在发布前验证学员不可见、发布后验证管理员、
负责教练和学员均可读取。脚本必须同时配置 MySQL 清理账号，结束时只按本轮生成的精确计划 ID、请求 ID
和确认消费记录清理；如果清理失败，脚本返回失败并打印计划 ID，禁止继续下一轮验收。该脚本不验证
LangGraph `interrupt()`，只验证 Gateway 到训练服务的角色和确认声明闭环。

Fitness Agent 的 dry-run 使用 `make agent-fitness-live-check`。当请求明确包含“创建/制定/生成/安排
训练计划”时，Agent 会先调用 RAG 生成并校验结构化预览，再由运行时将同一份 Payload 直接交给确认单，
不再依赖模型第二次工具调用。确认单进入 `interrupt()` 后，dry-run 会查询 `PENDING` 并自动提交
`REJECT` 清理；只有明确授权并传入 `--execute` 才会批准、恢复图并写入草案。

确认单接口为 `GET /api/v1/agent/confirmations/{confirmation_id}` 和
`POST /api/v1/agent/confirmations/{confirmation_id}/decisions`。决定请求只提交
`APPROVE`/`REJECT` 与独立 `decision_request_id`；身份、组织和角色仍从签名 AgentContext 获取。
接口不会返回完整参数、参数哈希、密文或确认凭证；`APPROVE` 会在服务端继续恢复图并执行 Java
Gateway，响应中的 `execution_status` 只反映真实工具结果。执行失败时接口返回暂时不可用或冲突，
页面可以用同一个 `decision_request_id` 重试；不能新造一个业务参数绕过原确认单。

当前版本已接入 PostgreSQL LangGraph Checkpoint 和 Redis 会话锁：同一用户/组织/角色范围
内的 `conversation_id` 会生成稳定的匿名 `thread_id`，不同身份即使使用相同会话 ID 也
无法读取同一份状态；后续请求会读取最新 Checkpoint 并恢复历史消息；同一会话的并发请求会返回 409。PostgreSQL 是会话状态事实源，Redis
只承担短租约互斥和短期状态，不作为长期会话数据的唯一存储。Supervisor State 不保存签名
AgentContext 或请求级 Token；它们通过 LangGraph 运行期 context 注入。检测到历史版本把 `request`
敏感对象写入 Checkpoint 时会拒绝恢复并要求重新建立会话。

RAG 表结构通过 `make agent-migrate` 显式升级，不在服务启动时自动创建业务表。知识切片的
`organization_id`、`visibility`、`owner_user_id`、`allowed_roles` 和生效时间由数据库查询
过滤，模型不能传入或改变这些权限条件；如果候选存在但 Reranker 未配置，检索会失败，不会
静默退回向量分数排序。

管理员知识库接口位于 `/api/v1/admin/knowledge`：上传使用 multipart 表单，审核接口为
`/jobs/{job_id}/approve`、`/reject` 和 `/retry`，任务状态可通过 `/jobs` 或具体任务 ID
查询。接口仍只接受认证服务签发的 `X-Agent-Context`，不会信任表单中的用户或角色字段。

索引重建接口为 `POST /reindex/jobs`，支持按文档、组织或平台范围创建任务；通过
`GET /reindex/jobs/{job_id}` 查询进度，失败明细可使用 `POST /reindex/jobs/{job_id}/retry`
在有限重试预算内重新执行。
本地暂存目录默认是 `./var/rag-staging`，已加入 Git 忽略；生产环境应替换为带生命周期、
加密、恶意文件扫描和访问审计的对象存储适配器。

本地可通过 `make infra-up-storage` 启动 MinIO；将 `AGENT_RAG_STORAGE_BACKEND` 改为 `s3`
并填写 S3 配置后，上传对象会写入 `knowledge/` 前缀。MinIO 账号只用于本地开发，生产环境
必须使用 Secret Manager 注入独立凭证。

本地真实验证 ClamAV：

```bash
make infra-up-security
make agent-security-check
```

如果尚未复制本地环境模板，请先执行上面的 `cp` 命令。该命令会连接本地 ClamAV，验证正常文件通过，并使用 EICAR 测试签名验证感染文件被拒绝；
EICAR 是专门用于杀毒软件测试的标准字符串，不是真实病毒。

生产环境至少需要配置：

```text
AGENT_RAG_MALWARE_SCANNER_BACKEND=clamav
AGENT_RAG_CLAMAV_HOST=<private-clamav-service>
AGENT_RAG_OCR_BACKEND=http
AGENT_RAG_OCR_ENDPOINT_URL=<private-ocr-service>/v1/parse
```

OCR 服务需要返回 `media_type`、`warnings` 和 `blocks` 数组；每个 block 至少包含 `kind` 和
`content`，可附带 `source_page`、`heading_path`、`table_index`、`row_start`、`row_end`。
当前仓库的自建 OCR 服务位于 `../fitness-ocr-service`，默认使用 PaddleOCR PP-StructureV3；完整
协议文档见 `docs/contracts/ocr-service-v1.md`，Agent 侧适配代码见 `app/rag/ocr.py`。

即使本地未启用 OCR，PDF 解析器也会生成页面级图片/文字画像，并按 `NORMAL`、`OCR_REQUIRED`、
`VISUAL_REVIEW_REQUIRED` 或 `OCR_AND_VISUAL_REVIEW_REQUIRED` 路由。图片密集页可能承载健身动作、
姿态、禁忌或风险信息，因此待 OCR/专业视觉审核页面会在 Embedding 前 fail-closed；阈值通过
`AGENT_RAG_PDF_*` 环境变量由部署侧统一管理，上传者和 LLM 不能覆盖。
`AGENT_RAG_QUALITY_*` 统一控制线上上传审核使用的噪声、碎片、重复、父节点、表格、缺页和 OCR
阈值；它们应与离线真实资料评测保持一致。上传完成后可通过
`GET /api/v1/admin/knowledge/jobs/{job_id}/review-report` 查看版本化质量报告。报告为 `PASS` 时
管理员可以直接排队；`BLOCKED` 必须重新解析/OCR；`REVIEW_REQUIRED` 必须由具备签名审核能力的
教练或专业人员处理，接口不提供强制跳过参数。专业审核工作台接口为：

- `GET /api/v1/knowledge-review/jobs/{job_id}`：读取准确领域、页码和已有决定。
- `GET /api/v1/knowledge-review/jobs/{job_id}/source`：读取 SHA-256 绑定的不可变暂存原件。
- `POST /api/v1/knowledge-review/jobs/{job_id}/decisions`：提交文档级或精确页级终局决定。

上传者不能审核自己的知识版本；普通 `COACH` 角色不自动拥有审核能力；全局资料需要
`KNOWLEDGE_REVIEW_GLOBAL`，医疗类还需要 `KNOWLEDGE_REVIEW_CLINICAL` 与已核验的
`VERIFIED_HEALTH_PROFESSIONAL`。全部必需领域通过后，数据库事务自动签发与任务、报告版本、
文件哈希、解析管线、审核策略和决定 ID 绑定的发布凭证。管理员审批和 Worker 执行时再次复核
该凭证；凭证只解除已审核的纯视觉页，不能绕过 OCR 缺失正文。部署升级使报告过期时必须重新解析。

索引重建任务只读取已审核文件，并快照原发布凭证允许的视觉页，复用当前解析、清洗、父子分块、Embedding 和原子替换流程，
用于 Embedding 模型升级、切分策略调整或索引修复，不会直接修改 Java 健身业务事实。
本地可通过 `make agent-reindex-worker` 启动独立轮询进程；生产环境应将它部署为独立 Worker，
与 API 进程分开扩缩容。

Memory 候选过期由 `MemoryCandidateExpiryWorker` 独立处理。本地可通过
`make agent-memory-expiry-worker` 启动；它按 `AGENT_MEMORY_CANDIDATE_EXPIRY_POLL_SECONDS`
轮询，每批最多处理 `AGENT_MEMORY_CANDIDATE_EXPIRY_BATCH_SIZE` 条记录，并在 `8092` 暴露低基数
Prometheus 指标。数据库使用 `FOR UPDATE SKIP LOCKED`，多个 Worker 实例可以并行运行而不会重复
处理同一候选。该 Worker 只更新 `PENDING → EXPIRED` 状态，不读取候选正文，也不会创建或修改正式 Memory。

Memory 正文保留期限由独立 `MemoryRetentionWorker` 处理，本地可通过
`make agent-memory-retention-worker` 启动。正式 Memory 进入 `REVOKED/EXPIRED` 后默认保留 90 天，候选进入
`APPROVED/REJECTED/EXPIRED` 后默认保留 30 天；到期后正式 Memory 的 `content` 被替换为脱敏标记，候选的
AES-GCM 密文被清空，类型、稳定键、状态、哈希和生命周期审计仍保留。Worker 使用 `FOR UPDATE SKIP LOCKED`、
有界批次和 `REDACTED` 审计事件，既支持多实例并行，也不会读取或解密正文。`content_redacted=true` 的 Memory
不能再进入训练计划 Prompt，脱敏候选只能查询生命周期事件，不能继续批准。

候选生命周期事件保存在 `agent_memory_candidate_events` 不可变审计表中，候选创建、批准、拒绝和过期与状态变更
使用同一事务写入。用户可通过 `GET /api/v1/agent/memory-candidates/{candidate_id}/events` 查看自己的事件摘要；
响应不会返回候选密文、正文摘要或内部请求标识。候选提取、过滤、持久化失败和状态流转使用固定枚举指标，避免
把用户 ID、候选 ID 等高基数信息放进 Prometheus 标签。

正式 Memory 的生命周期事件保存在 `agent_memory_events`，保存、撤销和自动过期与状态变更在同一事务内写入。
用户可以通过 `GET /api/v1/agent/memories?organization_id=...` 查看本人有效 Memory，通过
`PUT /api/v1/agent/memories/{memory_id}` 纠正值、单位和过期策略，或通过
`POST /api/v1/agent/memories/{memory_id}/revocations` 明确撤销，并通过同路径的 `/events` 查看状态摘要。
纠正只能修改已有 Memory 的内容，不能改变稳定键和类型。
撤销请求必须携带 `expected_version` 和稳定 `decision_request_id`；接口从签名主体反查机构，不接受调用方注入
任意用户或机构。管理页面的点击撤销本身就是确认动作，不再重复触发 `interrupt()`；对话工具仍保留原有确认单。

候选进入 `PENDING` 时会在同一 PostgreSQL 事务中写入 `agent_notification_outbox`，通知任务使用
`memory-candidate-pending:{candidate_id}` 去重。Outbox 只保存候选 ID，不保存候选正文；下游发布器可以通过
`claim_batch`、`mark_published` 和 `mark_retry` 接入 RabbitMQ、站内信或其他通知渠道。当前仓库完成的是可靠出站基础，
尚未把任何供应商发送结果伪装成已送达。

当前已落地无需外部供应商的站内通知渠道：`make agent-notification-worker` 启动独立 Outbox Worker，
将任务发布到 `agent_in_app_notifications` 收件箱。用户可以通过 `GET /api/v1/agent/notifications` 查询本人通知，
通过 `POST /api/v1/agent/notifications/{notification_id}/read` 标记已读；已读只改变界面状态，不需要
`interrupt()`。通知接口仍按签名 AgentContext 校验用户和机构范围。

通知策略已通过 `0022` 补齐：用户可以按机构和通知类型通过
`GET/PUT /api/v1/agent/notifications/preferences` 查询或保存授权开关、IANA 时区下的安静时间和同类通知最小间隔。
偏好不存在时默认允许站内通知；Outbox Worker 在收件箱写入同一事务中重新判断策略，安静时间会进入
`DEFERRED` 并在窗口结束后重试，用户关闭或频率限制进入 `SUPPRESSED`，不会伪装成系统失败。设置页面的
显式保存是用户操作，不是模型隐式改变业务事实，因此不触发 `interrupt()`；策略只保存用户/机构/通知类型配置，
不保存 Memory 候选正文。

通知模板已通过 `0025` 接入版本化控制面：模板按“模板键 + 渠道 + 版本”保存，生命周期为 `DRAFT`（草稿）、
`APPROVED`（已审核）、`PUBLISHED`（已发布）和 `RETIRED`（已退役）。Outbox Worker 只读取已发布的 `IN_APP`
模板，并把标题、正文和模板版本作为渲染快照写入收件箱；模板后续升级不会改写历史通知。模板变量必须显式声明，
不允许通过对象属性访问或未声明变量拼接正文；模板创建人与审核人不能是同一主体。当前 Memory 业务不接入短信（包括模拟短信）、
Push 或 RabbitMQ。
模板管理动作还通过 `0026` 写入不可变生命周期审计事件；创建、审核和发布都必须携带唯一 `operation_id`，网络重试
会复用原结果，不重复生成版本或状态事件。审计只保存模板键、版本、操作者和状态，不保存模板正文。

通知 Worker 已通过渠道适配器投递：当前 `IN_APP` 适配器将渲染快照写入站内收件箱，投递开始、成功和失败会写入
`agent_notification_delivery_attempts`（`0027`）。失败会按照 Outbox 的有限重试进入 `RETRYABLE_FAILED` 或
`FINAL_FAILED`；未来确有主动提醒或跨服务事件需求时，再新增 RabbitMQ、短信或 Push 适配器，不需要改动候选和 Outbox 业务逻辑。
当前已增加管理员运维查询 `GET /api/v1/admin/notifications/delivery-attempts`，支持按机构、通知类型、渠道和投递状态
过滤，并且只返回投递摘要、错误码和渠道消息 ID，不返回用户主体 ID、业务聚合 ID、标题或正文；同时提供低基数
Prometheus 指标 `fitness_agent_notification_delivery_attempts_total`，用于观察各渠道成功、可重试失败和最终失败数量。

检索引用接口为 `POST /api/v1/agent/knowledge/search`，只返回已完成权限过滤的来源引用，
包括来源 URI、版本、章节、PDF 页码、Excel 工作表、表格序号/行范围和命中片段。离线评测
样例位于 `evals/rag_smoke.json`，阈值位于 `evals/rag_thresholds.json`。本地执行
`make agent-eval` 会校验 Recall@K、MRR 和禁止 ID 命中数；CI 会把同一命令作为质量门禁。
当前是稳定的黄金结果回归集，不使用线上用户数据；后续接入真实评测数据库时复用相同指标和阈值模型。

Operations 趋势摘要的离线评测样例位于 `evals/operations_trend_smoke.json`，阈值位于
`evals/operations_trend_thresholds.json`。执行 `make agent-operations-eval` 会调用真实的
`build_operations_report`，验证 DAY/WEEK 时间桶补零、首桶为 0 时不伪造变化百分比、空结果不判断趋势、
单个原始桶不宣称趋势，以及课程/教练预约量和完课量的固定指标过滤结果。该门禁不访问数据库、LLM 或在线数据，
只验证聚合结果进入解释层后的确定性安全边界；CI 通过率低于阈值或出现失败用例时直接失败。

Operations 环比摘要的离线评测样例位于 `evals/operations_comparison_smoke.json`，阈值位于
`evals/operations_comparison_thresholds.json`。执行 `make agent-operations-comparison-eval` 会验证当前周期与上一等长
周期的总量、差值、方向、百分比和跨月/跨年的日期边界，并覆盖预约总量、课程预约量和教练预约量，以及上一周期为 0、
两周期都为 0 和当前周期为 0 的除零场景。
当前同比已固定为上一自然年同一月日区间，2 月 29 日在非闰年映射到 2 月 28 日；本门禁覆盖该边界及对比周期为 0 的除零处理。

Operations 查询前策略评测样例位于 `evals/operations_policy_smoke.json`，阈值位于
`evals/operations_policy_thresholds.json`。执行 `make agent-operations-policy-eval` 会验证用户问题与模型工具参数的
指标、日期、时间桶、环比/同比口径和组织范围一致；歧义问题、指标漂移、日期扩大和组织越权会在访问 Java Gateway
之前被拦截。策略校验只提供 fail-closed 的前置保护，最终权限仍由 Java Gateway 根据签名 AgentContext 决定。

固定指标目录由 `OPERATIONS_METRIC_CATALOG` 集中维护。每个指标定义都包含稳定 ID、中文名称、业务口径、维度含义、
支持的时间桶、是否支持上一等长周期环比和是否支持上一自然年同期同比；报表的 `metric_definition` 会返回这些非敏感元数据，帮助模型和管理员正确
解释结果。新增经营指标时必须先补齐目录定义、Gateway 固定 SQL、权限/审计和评测，不能只增加一个自然语言关键词。

Operations 查询还受服务端资源策略保护：单次时间范围最多 92 天、结果最多 100 行；当前周期加环比/同比最多触发两次
Gateway 查询，每次调用受 `AGENT_OPERATIONS_QUERY_TIMEOUT_SECONDS` 超时约束。生产 API 按机构使用 Redis 固定窗口限制请求量，
由 `AGENT_OPERATIONS_RATE_LIMIT_REQUESTS` 和 `AGENT_OPERATIONS_RATE_LIMIT_WINDOW_SECONDS` 配置；Redis 限流不可用时查询 fail-closed，
避免把缓存故障转化为 MySQL 查询洪峰。Prometheus 会通过低基数 `operations_query_events_total{event=...}` 记录成功、限流、
超时、调用预算超限、Gateway 失败和审计失败等固定事件，不记录机构 ID、请求 ID、SQL 或业务参数。上述限制和指标只用于控制和观测资源消耗，
不替代 Java Gateway 的签名权限校验。

短期会话摘要的确定性安全评测样例位于 `fitness-agent-service/evals/session_summary_samples.json`，阈值位于
`fitness-agent-service/evals/session_summary_thresholds.json`。执行 `make agent-session-summary-eval` 会检查摘要 JSON、
非空、长度、必需上下文、凭证脱敏、禁止越权表达和通过率；禁止表达用于拦截动态事实、权限、医疗诊断和训练计划状态
被固化进摘要的明显风险。该命令不调用 DeepSeek，不使用真实用户会话，只作为摘要后处理逻辑的 CI 门禁，不能替代人工语义评审。

训练计划生成使用只读工具 `fitness.training.plan.generate_draft.v1`：工具先按已验证身份检索已发布
健身知识，再通过统一模型网关要求 DeepSeek 返回 JSON Object，最后复用训练计划 Schema 和业务规则
校验训练日、动作数量、编号连续性及目标一致性。返回值是带引用的 `DRAFT_PREVIEW`，不会直接写入训练
业务库；若要创建草案，必须继续调用 `fitness.training.plan.create_draft.v1`，由 LangGraph `interrupt()`
展示确认卡并签发窄范围凭证，之后仍要经过教练审核和发布。模型不能生成或决定组织权限、计划状态和审核结论。
当前已通过 Supervisor 集成测试验证“生成预览 → 调用创建工具 → `interrupt()` 拦截 → 用户批准后恢复执行”
链路；确认前不会调用 Java Gateway 的创建接口。训练计划生成只读取已确认且未过期的结构化 Memory，不能把
模型候选或当前对话临时文本描述为已完成个性化记忆。

本地连接默认使用 Docker Compose 创建的 `fitness-agent-postgres`：宿主机
`127.0.0.1:5433` 映射到容器 `5432`，数据库 `fitness_agent`，用户 `fitness_agent`。
连接配置位于 `AGENT_DATABASE_URL`；PostgreSQL 镜像为 `pgvector/pgvector:pg16`。

## 生产镜像

```bash
make agent-image
```

镜像使用多阶段构建和 `uv.lock`，最终容器以固定 UID/GID `10001:10001` 运行；CI 会在
Python 检查通过后重新构建镜像，防止只在开发机缓存中可用。
