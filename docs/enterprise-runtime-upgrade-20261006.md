# AI 健身平台：企业级运行时升级与验收说明

更新日期：2026-10-06。

本文对应本次六组改造，区分“已经实现”“本次实际验证”“仍需上线环境验证”。它不是生产上线证明，也不把小样本模型测试包装成真实用户规模或性能提升。

后续补齐的正式登录适配、旧密文密钥轮换、确认实际请求体绑定、历史 Prompt 文本对比和 SAE 交接，详见 [接入交接与补齐说明](auth-key-rotation-and-sae-handoff.md)。下面的验收状态已同步到本轮最终结果。

2026-10-06 后续实际性能验收已补充到 [性能、召回、并发与续费效果实测](性能召回并发与续费效果实测_20261006.md)：包含真实检索、历史父上下文对比、12次实际HTTP问答及3组首次检索预热对比。续费提升仍缺真实实验数据；这里不把测试工具完成等同于业务效果已证明。

## 1. 改造前后总览

| 范围 | 原有基础 | 本次补齐 |
| --- | --- | --- |
| Prompt 治理 | 运行时提示词、部分发布摘要和领域评测 | 导出 Router 与全部领域意图的真实模板、同集模型版本对比、工具参数 Schema 评分、可选 Judge、报告摘要与人工复核发布门禁 |
| 性能与缓存 | 已有 RAG 上下文预算、本地 Embedding/Reranker 和混合召回 | 稳定工具序列化、合并相邻 System 消息、完整旧轮次裁剪、RAG 分段计时、真实流式性能基准与缓存命中观测 |
| 用量与成本 | OTEL/TruLens、部分 Token 信息；部分响应字段为会话累计值 | 每次模型尝试的持久化台账、请求级汇总、重试用量、未知值语义、版本化 Decimal 价格估算 |
| 流式交互 | `/chat` 返回完整 JSON | POST SSE、领域子图自定义事件、真实模型增量、确认中断共用原图、任务去重、断线结果恢复、前端流解析 |
| 运行保护 | Redis、会话锁、经营查询配额和业务幂等 | 对话用户/机构配额、瞬时突发保护、共享模型并发租约、有界等待与重试、实例熔断、Worker SIGTERM drain |
| 部署与运维 | Agent 镜像、基础设施 Compose、部分 CI/监控 | 四个独立 Java 镜像与 Web 镜像、API/Worker 独立容器、拓扑发布、就绪探针、完整应用 Compose、CI 矩阵、运行时告警与 Grafana 导入模板 |

现有 Supervisor 仍路由到四个 LangGraph 领域子图；它们是同一 Agent 运行时的逻辑子图，不是四个独立远程服务。主动通知和自动经营决策的原有业务实现保留，没有被本次改造替换。

## 2. Prompt 版本与评测：没有强行引入 Prompt Pilot

### 2.1 真实模板来源

`app/evaluation/prompt_regression.py` 从运行时代码导出：

- `router_system_prompt()` 的意图路由模板。
- 四个领域、所有已注册意图的 `_system_prompt()`。
- 该意图实际可见的 Tool Schema，而不是人工抄写的工具目录。
- Prompt 版本、模型、输出预算和输入契约。

导出的快照不包含 API Key、数据库连接串、签名上下文或用户资料。发布组件生成器也同步纳入 Router 和每个意图，避免旧清单只记录四个基础模板而遗漏意图层变化。

### 2.2 两种检查不能混淆

离线契约检查验证案例结构、唯一 ID、实际意图、工具是否存在、Schema 是否有效。它不会调用模型，报告明确标记 `model_evaluated=false`。

真实同集对比固定模型、temperature、各回合输出预算、Tool Schema 和案例集合，分别调用 baseline/candidate。评测工具只检查模型提出的调用，不执行真实业务工具。评分覆盖路由、工具白名单、工具参数合法性、禁止的成功声明、健康风险和提示注入等。

可选 `--judge` 使用固定版本评审模板产生 relevance/groundedness/safety 分数，并单独记录 Judge 的用量。Judge 不是事实来源，没有参考证据时不能推定事实正确；健康风险、权限和写入确认仍需要人工复核。

规则评分器也有版本。本次真实检查发现“我不能直接声称预约成功”被旧关键词规则误判；v2 仅豁免紧邻命题的明确否定，混合句后续的肯定声明仍失败。原始模型回答和旧报告保留，新报告可以通过 `--rescore` 离线生成并记录来源摘要，不能修改回答来制造通过。

发布门禁 `scripts/prompt_release_gate.py` 要求：真实模型报告通过、候选与评测集摘要匹配、基线证据存在、全部案例和重复采样完整、候选没有失败项、人工复核已完成。离线通过报告不能替代这个门禁。

门禁也拒绝 baseline/candidate 没有实际 Prompt 差异的同版本自比较。新增历史导出脚本从真实 Git 提交提取受限纯 Prompt 函数，不执行旧应用启动代码。本轮完成 10 个领域案例 × 新旧两版共 20 次真实调用，没有候选规则失败；固定当前模型/工具/输入协议，未比较 Router、未回放旧系统、不等于质量提升或完整人工批准。证据在 `tmp/prompt-historical-20261006/`，历史来源为 `6c1569c9227ab40a730af9e284a5d61faa7e4cd9`。

### 2.3 使用方式

在仓库根目录：

```bash
make prompt-snapshot
make prompt-regression-contract
```

将已经验收的历史快照作为 baseline；修改候选代码和 `AGENT_PROMPT_VERSION` 后另行导出 candidate。不能把同一份快照改个文件名，就宣称完成了历史版本优化。

在 `fitness-agent-service` 目录真实对比：

```bash
uv run --extra eval python -m app.evaluation.prompt_regression \
  --dataset evals/prompt_regression.json \
  --baseline ../tmp/baseline-prompts.json \
  --candidate ../tmp/candidate-prompts.json \
  --execute --repetitions 2 --judge \
  --report ../tmp/prompt-comparison.json

uv run --extra eval python scripts/prompt_release_gate.py \
  --snapshot ../tmp/candidate-prompts.json \
  --dataset evals/prompt_regression.json \
  --report ../tmp/prompt-comparison.json --reviewed
```

真实调用会计费；`--reviewed` 表示人工审核已经实际完成，不是为了让命令通过而添加的开关。CI 默认只做离线契约，不在每个 PR 上调用收费模型或自动代替人工批准。

## 3. 性能与缓存：实现可测量优化，不写虚构的“15 秒到 2 秒”

### 3.1 已经落地

`ModelGateway` 稳定排序工具定义、规范化 Schema 键顺序，并合并相邻纯 System 消息。不会重排用户对话、改变工具调用/结果对应关系，也不会为了缓存命中重排 RAG 的相关性证据。

`agent_context_max_chars` 是保守的字符级请求预算，不冒充某个模型的精确 Tokenizer。超出预算时删除完整的旧用户轮次，连同该轮工具调用和结果一起删除；当前用户问题和 System 规则保留。仍超限则拒绝调用，不截断当前工具参数或降低安全规则。

RAG 新增 embedding、vector recall、keyword recall、rerank 的独立 Span 和延迟 Histogram。向量/关键词召回依旧并行，排序逻辑不变。关键词召回也补上与向量召回一致的文档/分块可见性一致性条件。

### 3.2 Prefix Caching 与 KV Cache 的边界

应用可以稳定输入前缀、减小上下文并观察供应商返回的缓存命中 Token；不能凭应用代码强制托管模型启用某种内部 KV Cache，也不能保证每次命中。

当前记录支持 DeepSeek 的 cache hit/miss 字段与标准 `prompt_tokens_details.cached_tokens`。供应商没有返回缓存信息时记录未知，不假定“已经命中”。本项目没有自建大模型推理集群或自己管理显存中的 KV Cache。

### 3.3 真实性能基准

```bash
make llm-performance-benchmark ARGS='--execute --samples 3 --report ../tmp/llm-performance.json'
```

脚本仅使用合成健身资料，通过实际模型流和 LangGraph custom 事件测量有效文本 TTFT、总时间、Token 和缓存命中。稳定前缀/变动前缀交替采样，避免固定执行次序造成明显偏差。不清除供应商缓存，不把第一轮强称为冷缓存。

此前一轮两次真实模型调用的首有效文本约为 **0.85 秒、0.67 秒**，报告位于 `tmp/llm-performance-20261006.json`；两次缓存命中均为 0，价格未配置，因此成本为 null。

这两次是直接模型链路的小样本，不包括完整 Supervisor 路由、真实 RAG 数据查询或 Java 业务查询。不能据此声称端到端 P95、真实并发规模、缓存加速比例或“延迟降低 60%”。需要在固定数据、并发和模型配置下补压测、质量回归及足够样本。

### 3.4 后续真实证据与限制

新增固定34份合成文档/26条标注查询的实际模型检索，Recall@5单向量98.08%、当前混合+重排100%，增加1.92个百分点，禁止文档命中0；不能外推为线上准确率。3组全新模型首请求比较中，预热将就绪后的首次RAG等待均值2.66秒降到0.39秒，加载成本转移到启动准备阶段。

长父章节上下文预算将固定真实模型配对输入从13,298降到5,300 Token，少60.14%；TTFT只观察到约0.019秒均值差，区间跨0，未证明明显速度提升。完整Agent知识问答1/2/4并发各4次均完成，但带工具Schema的回合先缓冲，本次没有用户可见answer.delta，不能把裸模型首字数据当成这条链路性能。

RAG服务在8并发、16请求批次全部成功，不过4→8并发吞吐基本不增、延迟上升，CPU推理竞争需要后续profile。完整条件、原始报告、失败测试记录、续费实验方案和简历可用表述均见上述实测文档。

## 4. 用量、成本与 Trace

### 4.1 不再拿会话累计 Token 作为单次请求成本

兼容保留旧响应的 `input_tokens/output_tokens`，但新增 `request_usage` 只汇总当前请求的实际模型尝试。路由、领域生成、摘要等经过 ModelGateway 的聊天/JSON/工具调用都进入该请求上下文；后台 Worker 每个批次获得独立关联 ID。

新增 `agent_model_usage` 保存 call_id、request_id、可选 run_id、模型、供应商、调用类型、重试序号、Prompt 版本、状态、Token、缓存信息、耗时与估算成本。它不保存 Prompt、答案或签名身份正文，也不依赖 Trace 采样率。

每次网络尝试前先提交 STARTED；成功后更新 SUCCEEDED，失败更新 FAILED，取消/中断保留 UNKNOWN。相同 call_id 的更新幂等，迟到 STARTED 不会覆盖终态。台账不可用时不会静默绕过计量调用模型。失败后无法确定供应商消耗的 Token，不假设为零。

### 4.2 估算价格配置

```dotenv
AGENT_LLM_PRICING_MODEL=与你实际调用一致的模型名称
AGENT_LLM_PRICING_VERSION=实际价格生效版本
AGENT_LLM_PRICING_CURRENCY=CNY
AGENT_LLM_INPUT_PRICE_PER_MILLION=实际未命中输入单价
AGENT_LLM_CACHED_INPUT_PRICE_PER_MILLION=实际缓存输入单价
AGENT_LLM_OUTPUT_PRICE_PER_MILLION=实际输出单价
```

价格用 Decimal 运算并以 NUMERIC 入库。只有模型名称、价格版本、必要用量和价格均明确时才估算。缓存折扣已配置却没有命中信息时也不能伪造精确费用。

成本是 API 用量估算，不是供应商最终账单；不包含服务器、SAE、数据库、存储、网络或本地 Embedding/Reranker 的计算费用。本次新增的账本主要覆盖 LLM completion，远程 Embedding 账单没有被假装合并进来。

### 4.3 可观测性

已有 OTEL/TruLens 继续使用，新增真实尝试耗时、供应商首内容延迟、用户首有效答案延迟、Token、估算成本、台账错误、流事件及 RAG 分段指标。Metrics 不用 user_id、run_id、request_id 等高基数标签；详细关联通过台账和 Trace 完成。

## 5. SSE 与 LangGraph：一个执行内核，两种返回方式

### 5.1 接口与实际事件

旧 `POST /api/v1/agent/chat` 保留完整 JSON 兼容接口。

新 `POST /api/v1/agent/chat/stream` 增加客户端生成的 UUID `run_id`，继续验证 `X-Agent-Context`。请求体不能自行添加组织、角色或能力授权字段。

公开事件包括 run.started、stage.changed、answer.delta、confirmation.required、run.completed、run.failed、stream.reset；事件携带 run_id。心跳是 SSE comment，不计入答案 TTFT。

项目锁定的 LangGraph 0.6.x 使用：

```python
graph.astream(..., stream_mode=["custom", "values"], subgraphs=True)
# 输出为 (namespace, mode, chunk)
```

这里没有直接照搬新版本 `version="v2"`，也没有同时消费 `messages` 和 custom 而重复输出 Token。领域子图事件转发通过已有图执行完成。确认中断、会话 Redis 锁、PG fencing 和 Checkpointer 与非流式接口共用。

### 5.2 为什么不是所有模型回合都逐字展示

工具选择回合可能包含未完成的参数、内部协议和中间说明，先缓冲并聚合；完整工具调用交给原 Registry 和确认机制。只有工具已关闭的最终回答回合允许增量展示。

经营领域完成确定性指标查询后切换为只解释结果；这项策略同时用于 JSON/SSE，避免两种接口改变业务执行语义。其他领域保留自主工具循环，因此部分请求先看到阶段变化，最后一次性出现最终答复，这是有意的安全取舍。

分片过滤器跨 chunk 识别内部工具/think 标记；完整 JSON 工具参数聚合后才能解析，不在参数未闭合时执行。供应商流没有正常结束、被长度截断或出现不完整协议时失败，不把半截输出当成功。

### 5.3 断线、重连与多实例

任务登记在 `agent_chat_runs`，run_id 唯一并绑定权限范围摘要及请求摘要。两个实例同时领取，只能一个创建执行任务；换请求或换权限范围复用 ID 返回冲突。

任务执行与 SSE 连接分离。用户断线不取消正在执行的图；同实例可以继续订阅增量，另一实例通过 PG 获取最终结果。前端断流后查询 `GET /api/v1/agent/runs/{run_id}`，不自动重新 POST 原请求。

最终结果先以 AES-GCM 加密持久化，再发布完成事件。终态更新只接受 RUNNING，迟到执行者不能覆盖 UNKNOWN。加密结果 AAD 绑定 run_id 与所有者范围；权限变化后的身份不能读取旧范围结果。

终态写入与退出时的未知状态记录都有有限超时，避免数据库故障让 drain 永久等待；前端单次任务查询也有超时，并在错误通知后优先核对数据库终态。

如果进程失联或执行超时，任务标记 UNKNOWN，不自动重跑可能产生副作用的原图。恢复依赖确认单/业务幂等和对账，不靠重新生成同一条模型回复猜测是否成功。

慢客户端有有界队列，满时收到 stream.reset，随后查询最终结果；不阻塞模型执行。本实现不持久化每个 Token、不承诺 Last-Event-ID 精确逐片重放。

任务结果默认保留 24 小时。这也是流任务去重/恢复窗口，而不是永久去重保证。更长期业务写幂等仍属于确认/Java 业务层。当前结果复用确认加密密钥；轮换时需保留旧版本解密能力或等待保留窗口结束，不能假定已有多版本 keyring。

### 5.4 前端与身份

前端用 ReadableStream、TextDecoder 和有界 SSE parser 处理 UTF-8 字节拆分、CRLF、心跳、多行 data。增量显示后以服务端最终结果替换，失败则不保留半截回答为成功。sessionStorage 仅用任务 ID/会话 ID 恢复任务，不保存用户问题来自动重发。

Web 镜像不复制 `.env*`，也不把签名身份打包给所有用户。新增“接入身份”入口供演示/联调粘贴可信端签发的短期 AgentContext；授权仍由后端验签决定，机构输入只用于当前页面选择，不扩大权限。

生产仍应接入可信登录/刷新流程，通过 HTTPS 交付身份。该入口不是自行签发 JWT 的登录系统，也没有把开发签名密钥放到浏览器。

## 6. 限流、模型保护与退出

### 6.1 为什么继续用 Redis 而不是替换成 SlowAPI

当前已有 Redis Lua 原子配额能力，直接复用，避免再引入一套默认内存计数或两套冲突的限流。新昂贵对话共享同一配额：用户默认 20 次/分钟、3 次/秒，组织默认 120 次/分钟。所属多个组织的身份逐个消费组织配额。

超额返回 429 和 Retry-After。Redis 异常返回 503，不能静默失去保护。结果恢复、健康检查和人工确认不消耗新生成配额，它们继续使用自己的验证/业务边界。

这是固定窗口和突发窗口组合，不是精确的滑动窗口；边界附近可能产生突发，阈值需按实际用户行为调节。多范围配额采取保守顺序扣减，后续范围拒绝不会返还前面范围。

### 6.2 模型并发、重试和熔断

进程内默认 8 个并发槽、等待最多 2 秒；共享 Redis 默认 16 个槽，在 API 与模型调用 Worker 之间约束总并发。共享租约覆盖逻辑调用及有界重试，使用 Redis 服务端时钟，到期后可回收进程崩溃留下的槽。

SDK 隐藏重试被关闭，统一网关最多尝试两次，只重试连接/超时/429/5xx 类瞬时错误；鉴权或参数错误不重试。Retry-After 不在请求内无限等待，流式回合不会自动重试拼接两次生成。

此外设置覆盖账本写入、所有网络尝试和退避的逻辑总截止时间，早于共享并发租约到期；不能只限制单次 HTTP 请求而允许数据库等待无界延长整次调用。

熔断器是实例级连续故障计数，默认连续 5 次故障熔断 30 秒，半开只放行一次探测；它不是跨实例全局熔断。全局保护由 Redis 并发槽承担。

降级是稳定的不可用/澄清反馈，不凭模板编造收入、预约成功或训练业务结果。输出预算、配额和并发限制可控制风险，但不是月度账单预算管理系统。

### 6.3 SIGTERM 的真实保证

全部常驻 Worker 使用统一生命周期：收到信号先停止领取新批次，让已领取批次完成；超过默认 45 秒则取消并依靠事务回滚、租约、Outbox/Inbox 幂等恢复。

API 使用 Uvicorn 的停止接流量和有界退出；lifespan 在关闭依赖前 drain 持久化流任务。Java 四个服务显式暂停 Tomcat Connector、等待工作线程，readiness 在 drain 后失效，再销毁依赖。Compose 提供 120 秒 stop_grace_period。

不能保证任何请求在 SIGKILL、节点故障或外部平台提前终止时都完成；有界退出与持久化恢复结合才是正确描述。这里不声称 exactly-once 或“所有在途请求永不丢失”。

## 7. 独立部署与阿里云 SAE

### 7.1 可以独立部署的边界

```text
Web / HTTPS 入口
  └─ Agent API ── Gateway ── Training / Booking / Customer Service
        ├─ PostgreSQL / Redis / 模型 API
        └─ 独立 Worker ── PostgreSQL / Redis / RabbitMQ / Gateway
```

Java 四个服务分别有独立多阶段 Dockerfile、非 root 运行用户和可单独替换的镜像。Agent API 与 Worker 复用同一代码镜像，但入口、进程、指标端口、资源与副本数独立。四个 Agent 领域子图暂不拆成远程微服务。

共享部分 MySQL 表意味着发布兼容性仍有关联，独立镜像不等于数据库完全解耦。Java 业务 schema 初始化在应用 Compose 中关闭；先按现有迁移方案准备数据库，再发布服务。

### 7.2 完整应用 Compose

`deployment/docker-compose.app.yml` 连接外部已准备的 PostgreSQL、Redis、MySQL、RabbitMQ 等依赖，不额外重建你的开发库或删除其卷。

为每个环境准备受控目录，包含 agent.env、gateway.env、training.env、booking.env、customer-service.env。从对应环境模板生成真实配置，不把 production 空白模板当可用的凭证。Docker 中的数据库地址不能用容器自己的 `127.0.0.1`；本机联调用 host.docker.internal，生产用受控内网地址。

```bash
export FITNESS_CONFIG_DIR=/绝对路径/受控配置目录
export FITNESS_MODELS_DIR=/绝对路径/已验收模型目录
export FITNESS_RELEASE_TAG=不可变发布版本

make app-config-check
make app-images
docker compose -f deployment/docker-compose.app.yml \
  --profile proactive --profile maintenance --profile observability up -d
```

Agent 模型路径应与 `/models` 只读挂载一致。应用按 Agent 迁移成功、Java 业务服务就绪、Gateway 就绪、Agent API 就绪、Web 启动的顺序发布。缺少新运行时表时 Agent readiness 返回 503；liveness 不依赖数据库。

Worker profile 按需启用。监控 worker-targets.json 的 required 标签应与实际启用角色匹配，可选角色未启用不应触发必需 Worker 宕机告警。Prometheus/Alertmanager 端口默认只绑定本机回环，不公开到互联网。

API 和 Worker 的数据库池分别设置，不能把 12 个进程都照搬默认大连接池。本 Compose 的单 API、全部 11 个 Worker 在 SQLAlchemy/Checkpoint/fence 池全部达到上限时约需 70 个 Agent PG 连接，不含其他客户端。增加副本前应按数据库总连接预算重新分配。

### 7.3 上 SAE 的方案与不能代做的事项

容器镜像可映射为多个 SAE 应用：Web、Agent API、四个 Java 服务以及独立 Worker。配置镜像仓库、Secret/VPC、探针、终止宽限期和资源限制后按依赖拓扑发布。

常驻 RabbitMQ 消费者和轮询 Worker 不适合按短 HTTP 函数直接搬到函数计算；优先用常驻容器部署，并保留必要最小副本。API 可按 HTTP/CPU 等指标扩缩容，后台角色需要按队列积压、任务年龄和处理能力制定不同策略。

Agent 本地模型存在权重挂载、内存和冷启动成本，不能未经实测承诺秒级启动或无限弹性。灰度/回滚要提升同一不可变镜像摘要，同时满足数据库向前兼容。0045 downgrade 会删除新增台账/任务结果表，不能拿生产 downgrade 当默认回滚手段；通常回滚兼容代码、保留新增表。

本次没有你的云资源 ID、仓库权限、SAE 应用/VPC/Secret 配置，因此没有推镜像或实际云部署，也没有验证云端灰度、自动扩容和回滚时间。

新增 `deployment/sae/release.example.json` 与 `scripts/sae_release_plan.py`，填真实应用 ID 和不可变镜像摘要后可离线生成拓扑更新计划；占位符、重复应用 ID、标签镜像和混入 Secret 会被拒绝。计划不会调用阿里云。认证提供方与云资源的具体接入要求见配套交接文档。

## 8. CI、告警和可视化

CI 增加：四个 Java 独立服务测试/镜像构建矩阵；Web parser 测试、类型/构建与镜像；独立 pgvector/Redis 服务上的唯一临时库验收；运行时 Prompt 快照和离线契约制品。工作流只构建，不自动发布或部署。

新增告警：用量台账持久化失败、首有效答案持续变慢、模型实际尝试失败比例、已启用必需 Worker 未就绪。HTTP 延迟规则排除长连接 SSE 路由，避免把连接持续时间误判为普通接口延迟。

`deployment/observability/grafana-runtime-delivery.json` 可导入 Grafana，覆盖答案 TTFT、供应商 TTFT、缓存命中、模型耗时、Token、估算成本、账本失败、RAG 分段耗时和 Worker 就绪。它是导入模板，本次没有安装新的 Grafana 服务。

监控阈值目前是配置初值，不是已验证的 SLA，需按上线流量校准。可选 Worker 的抓取 DOWN 与已启用 Worker 故障应明确区分。

## 9. 迁移、回归与本次验收证据

新增迁移 `20261006_0045`，接在 `20260918_0044` 后，只增加模型用量和流任务表及索引。保留窗口清理使用小批次和 FOR UPDATE SKIP LOCKED，避免多个进程全表清理或长时间互相等待。建模和清理采用了 PostgreSQL 最佳实践技能中的索引、数据类型和锁边界指导。

本次实际验收：

- Python lint、格式和 mypy 已通过；全量测试 650 项通过、20 项跳过。需要真实外部依赖的既有集成测试未被伪造为通过；明确开启的临时库验收单独统计。
- 独立临时 PG 库完成 head → 0044 → head → base；真实 PG/Redis 六项用量幂等、密文结果及旧密钥恢复、并发领取、未知状态、共享配额和并发认证刷新测试通过。脚本最后只删除本轮生成的临时库与测试专用 Redis 键。
- Java 四个服务总计 118 项测试，113 项通过、5 项需要显式外部集成配置而跳过，失败/错误为 0；新增就绪/drain 与请求体绑定单元测试已运行。
- Web 八项认证与字节级/边界解析测试通过，TypeScript 检查与生产构建通过。浏览器在合成测试端完成登录、自动刷新、目录加载、SSE 展示与退出；真实认证提供方尚待接入。
- 13 条告警静态契约、旧/新两组 promtool 规则测试通过；应用 Compose 含全部 profile 的配置解析通过。
- 真实模型流式性能测试两次成功，原始测量在 tmp 报告；不是业务端到端压测。
- Prompt 16 案例离线契约通过；完成 16 案例×两轮共 32 次真实模型检查。原始报告 `tmp/prompt-live-self-comparison-20261006.json` 保留一次否定句误判，v2 原样重新评分后的 `tmp/prompt-live-rescored-20261006.json` 无规则失败项。两轮都是同一快照，是管线和规则验收，不伪造历史基线、不代表候选优于旧版本，也未启用 Judge 或完成生产人工批准。
- 补做历史领域 Prompt 文本对比 20 次真实调用，候选规则失败为 0；有真实历史来源、不是同版本自比较，但只重复一次、未作人工批准，不宣称统计性质量提升。
- 完整 Agent SSE/断线恢复基准脚本已经实现且合成协议测试通过；真实专用账号端到端运行仍待接入，不用裸模型或浏览器替身充当业务压测证据。

六个应用镜像均已构建成功。默认 node/Temurin 源先遇到 `failed size validation`，本轮通过 Dockerfile ARG 使用官方 ECR/GHCR 替代源，未清空 Docker 数据或修改全局代理。Agent 镜像非 root 运行时导入烟测、Web Nginx 配置检查通过。镜像是本机 ARM64 制品，不等于云端 AMD64/全 Compose 业务联调已验证；没有推送或上线。

日常检查：

```bash
make runtime-delivery-check
make runtime-delivery-live-check
make observability-check
make observability-rule-test
make production-config-check
```

启动最新 API 前，在目标环境备份和确认连接配置后执行 `make agent-migrate`。本次验收没有替你迁移开发/生产数据库、重启现有服务或部署云资源。

## 10. 面试怎么讲，以及仍不能夸大的地方

可以据实说：统一模型网关关闭隐式重试、记录每次尝试用量；请求级聚合避免会话累计值重复计费；Redis 多维配额和共享并发租约保护模型；SSE 与 LangGraph 子图使用同一个确认和 Checkpoint 执行内核；run_id 持久化去重和加密终态实现断线恢复；独立 API/Worker 镜像入口支持拓扑发布与有界 drain；Prompt 发布绑定实际模板和同集评测报告。

还不能说：已支持数万真实用户、延迟降低固定百分比、每次必然触发 KV Cache、完成生产灰度/秒级回滚、跨云高可用、所有 Worker exactly-once、所有费用已准确对账、身份登录集成已经上线或完整安全审计已经完成。

正式登录的 BFF 适配、版本化旧密文读取和确认实际请求体绑定已补齐并回归，不再列为“完全没实现”。其中认证端签发/刷新/撤销服务仍需选型接入；内部服务转发摘要依赖可信 Gateway/服务 Token，不冒称原始签名端到端穿透所有 DTO。

仍需生产验证：真实认证端联调、密钥轮换操作与备份恢复、组织级预算策略、完整业务链路负载与恢复演练、Java 8/Spring Boot 2.1 依赖升级、共享数据库迁移兼容性及成套确认协议灰度/回滚。只有本地代码和测试通过，不能确认达到生产规模或完整安全审计要求。

这些边界应保留在项目材料里。企业级的重点是可测量、可审计、可恢复和清楚说明取舍，不是把所有平台品牌和性能数字写进简历。
