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
  检索顺序固定为服务端权限过滤 → 向量/关键词混合候选召回 → RRF 融合 → 真实 Reranker
  → 带来源证据的 Agent 上下文。
- 混合召回：PostgreSQL `tsvector` 全文索引和 `pg_trgm` GIN 索引与 pgvector 并行工作；两条
  路径都独立执行租户、角色、所有者、生效时间和发布状态过滤，随后按 Reciprocal Rank Fusion
  融合，避免向量分数和关键词分数直接比较。
- 文档索引：`DocumentIngestionService` 负责统一解析 Markdown/TXT、PDF、DOCX、XLSX，执行文本
  清洗、标题/段落语义切片、checksum 去重、稳定文档/切片 ID，以及新版本发布时的旧版本归档；
  Embedding 和切片写入受批次和事务边界控制。PDF 页码、DOCX 标题层级、XLSX 工作表和表格
  行范围会随子节点保存，解析失败不会静默按纯文本入库。
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

## 本地启动

以下命令均在仓库根目录执行；本地环境模板会启用 ClamAV，模型密钥仍需按个人环境补充。

```bash
cp deployment/environments/agent.local.env.example fitness-agent-service/.env
make infra-up-security
make agent-sync
make agent-migrate
make agent-run
```

本地若要实际处理扫描型 PDF，还需在 Linux/GPU 或 amd64 推理节点启动独立 OCR 服务：
`make infra-up-ocr`。macOS 开发机可以只运行 Agent，并把 `AGENT_RAG_OCR_ENDPOINT_URL` 指向
远程 OCR 服务。

`/health/live` 只检查进程，`/health/ready` 检查 PostgreSQL、Redis 和三个模型能力是否均已配置并可用。
没有配置真实模型凭证时服务不会伪装成 ready。

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

当前本地 RAG 验证默认使用学习项目已下载的 BGE-M3 和 BGE-Reranker Base：
`AGENT_EMBEDDING_BACKEND=local`、`AGENT_EMBEDDING_MODEL_PATH` 指向 BGE-M3 快照，
`AGENT_EMBEDDING_DIMENSIONS=1024`，以及 `AGENT_RERANKER_BACKEND=local`、
`AGENT_RERANKER_MODEL_PATH` 指向 BGE-Reranker 快照。生产环境可切换为 `openai` Embedding
和 `http` Reranker，但必须同时执行全量索引重建；向量维度变更不能只修改环境变量。

Supervisor 必须通过 `app.state.tool_registry` 获取工具定义和调用入口；不能在 Prompt、
Agent 节点或模型回调中自行拼接 Gateway URL。当前 Registry
只包含只读工具，预约写操作要等确认凭证、幂等键和 Java 事务审计完成后再加入。

当前对话接口为 `POST /api/v1/agent/chat`。调用方必须传入认证服务签发的
`X-Agent-Context`，以及 `conversation_id`、`message` 和可选 `locale`；写工具还需要由上游
确认流程签发的 `X-Confirmation-Token`，接口拒绝额外的
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

检索引用接口为 `POST /api/v1/agent/knowledge/search`，只返回已完成权限过滤的来源引用，
包括来源 URI、版本、章节、PDF 页码、Excel 工作表、表格序号/行范围和命中片段。离线评测
样例位于 `evals/rag_smoke.json`，阈值位于 `evals/rag_thresholds.json`。本地执行
`make agent-eval` 会校验 Recall@K、MRR 和禁止 ID 命中数；CI 会把同一命令作为质量门禁。
当前是稳定的黄金结果回归集，不使用线上用户数据；后续接入真实评测数据库时复用相同指标和阈值模型。

本地连接默认使用 Docker Compose 创建的 `fitness-agent-postgres`：宿主机
`127.0.0.1:5433` 映射到容器 `5432`，数据库 `fitness_agent`，用户 `fitness_agent`。
连接配置位于 `AGENT_DATABASE_URL`；PostgreSQL 镜像为 `pgvector/pgvector:pg16`。

## 生产镜像

```bash
make agent-image
```

镜像使用多阶段构建和 `uv.lock`，最终容器以固定 UID/GID `10001:10001` 运行；CI 会在
Python 检查通过后重新构建镜像，防止只在开发机缓存中可用。
