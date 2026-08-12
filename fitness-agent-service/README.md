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
- Supervisor Runtime：基于 LangGraph 执行模型 Tool Calling、工具预算、真实结果回填和业务范围护栏。
- 会话持久化：PostgreSQL 保存 LangGraph Checkpoint，Redis 负责会话互斥锁和短期状态。
- RAG 基础：Alembic 管理版本化知识文档、切片、租户/角色权限字段和 pgvector HNSW 索引；
  检索顺序固定为服务端权限过滤 → 向量候选召回 → 真实 Reranker → 带来源证据的 Agent 上下文。
- 文档索引：`DocumentIngestionService` 负责统一解析 Markdown/TXT、PDF、DOCX、XLSX，执行文本
  清洗、标题/段落语义切片、checksum 去重、稳定文档/切片 ID，以及新版本发布时的旧版本归档；
  Embedding 和切片写入受批次和事务边界控制。PDF 页码、DOCX 标题层级、XLSX 工作表和表格
  行范围会随子节点保存，解析失败不会静默按纯文本入库。
- 父子节点：子节点参与向量召回，父节点保存章节或表格完整上下文；命中后按 `parent_id`
  扩展且同一父节点只注入一次，所有格式的表格子节点都会转为带表头的 Markdown 表示，
  并记录表格序号、页码/工作表和行范围。扫描型 PDF 当前会明确要求后续 OCR 流程，不会把
  空内容写入知识库。
- 知识库管理：管理员上传先进入私有暂存区和 `PENDING_REVIEW`，审核通过后进入 `QUEUED`；
  Worker 以数据库原子 Claim 进入 `INDEXING`，只有父子节点、Embedding 和发布事务完成后
  才标记 `SUCCEEDED`。失败任务保留错误类型、尝试次数和审核记录，支持有限次数人工重试；
  组织管理员只能看到签名组织范围内的组织知识，平台管理员才可管理全局知识。
- 文件安全与存储：上传会在解析和存储前执行 UTF-8、文件签名、Office ZIP 路径、加密包、
  符号链接、宏文件和解压大小检查，并记录 SHA-256 与扫描器版本。存储支持本地适配器和
  S3-compatible/MinIO 适配器；当前结构扫描不是杀毒引擎，生产环境必须接入 ClamAV 或云端
  文件安全服务。
- 索引 Worker：`KnowledgeIngestionWorker` 提供有界轮询入口，任务通过 PostgreSQL 原子 Claim
  防止重复执行；超过重试上限的 `FAILED` 任务即为死信状态，后续可由独立 Worker Deployment
  或队列消费者接管，不依赖单个 API 进程内存。
- Prometheus：低基数 HTTP 请求量、耗时、并发和构建信息指标。
- OpenTelemetry：可选 OTLP/HTTP Trace 导出，默认关闭且不发送 Prompt 或用户档案。

## 本地启动

```bash
cp .env.example .env
cd ..
make infra-up
make agent-sync
make agent-migrate
make agent-run
```

`/health/live` 只检查进程，`/health/ready` 检查 PostgreSQL、Redis 和三个模型能力是否均已配置并可用。
没有配置真实模型凭证时服务不会伪装成 ready。

常用质量检查：

```bash
make agent-format
make agent-check
```

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

DeepSeek 配置使用 `DEEPSEEK_API_KEY`、`DEEPSEEK_MODEL` 和 `DEEPSEEK_BASE_URL`；默认模型为
`deepseek-v4-flash`，默认关闭 thinking，以保持工具调用参数和结构化响应稳定。旧的
`AGENT_LLM_*` 变量仍可兼容读取，但新环境统一使用 `DEEPSEEK_*`。

Supervisor 必须通过 `app.state.tool_registry` 获取工具定义和调用入口；不能在 Prompt、
Agent 节点或模型回调中自行拼接 Gateway URL。当前 Registry
只包含只读工具，预约写操作要等确认凭证、幂等键和 Java 事务审计完成后再加入。

当前对话接口为 `POST /api/v1/agent/chat`。调用方必须传入认证服务签发的
`X-Agent-Context`，以及 `conversation_id`、`message` 和可选 `locale`；接口拒绝额外的
用户/组织/角色字段。当前先提供非流式稳定协议，SSE、Checkpoint 和断线恢复将在会话持久化
边界进一步验证后接入。

当前版本已接入 PostgreSQL LangGraph Checkpoint 和 Redis 会话锁：同一用户/组织/角色范围
内的 `conversation_id` 会生成稳定的匿名 `thread_id`，不同身份即使使用相同会话 ID 也
无法读取同一份状态；后续请求会读取最新 Checkpoint 并恢复历史消息；同一会话的并发请求会返回 409。PostgreSQL 是会话状态事实源，Redis
只承担短租约互斥和短期状态，不作为长期会话数据的唯一存储。

RAG 表结构通过 `make agent-migrate` 显式升级，不在服务启动时自动创建业务表。知识切片的
`organization_id`、`visibility`、`owner_user_id`、`allowed_roles` 和生效时间由数据库查询
过滤，模型不能传入或改变这些权限条件；如果候选存在但 Reranker 未配置，检索会失败，不会
静默退回向量分数排序。

管理员知识库接口位于 `/api/v1/admin/knowledge`：上传使用 multipart 表单，审核接口为
`/jobs/{job_id}/approve`、`/reject` 和 `/retry`，任务状态可通过 `/jobs` 或具体任务 ID
查询。接口仍只接受认证服务签发的 `X-Agent-Context`，不会信任表单中的用户或角色字段。
本地暂存目录默认是 `./var/rag-staging`，已加入 Git 忽略；生产环境应替换为带生命周期、
加密、恶意文件扫描和访问审计的对象存储适配器。

本地可通过 `make infra-up-storage` 启动 MinIO；将 `AGENT_RAG_STORAGE_BACKEND` 改为 `s3`
并填写 S3 配置后，上传对象会写入 `knowledge/` 前缀。MinIO 账号只用于本地开发，生产环境
必须使用 Secret Manager 注入独立凭证。

本地连接默认使用 Docker Compose 创建的 `fitness-agent-postgres`：宿主机
`127.0.0.1:5433` 映射到容器 `5432`，数据库 `fitness_agent`，用户 `fitness_agent`。
连接配置位于 `AGENT_DATABASE_URL`；PostgreSQL 镜像为 `pgvector/pgvector:pg16`。

## 生产镜像

```bash
make agent-image
```

镜像使用多阶段构建和 `uv.lock`，最终容器以固定 UID/GID `10001:10001` 运行；CI 会在
Python 检查通过后重新构建镜像，防止只在开发机缓存中可用。
