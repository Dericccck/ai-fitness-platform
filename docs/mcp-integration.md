# 健身项目 MCP 实施与运行指南

更新：2026-10-28。Python MCP SDK 固定 `2.3.0`，协议固定 `2026-07-28`，共享契约版本 `1`。

## 调用边界

```text
FastAPI / LangGraph
  → Tool Registry（身份绑定、Schema、角色、确认、审计）
  → 原业务 handler
  → RoutedBusinessToolsClient
  → MCP Client → Streamable HTTP /mcp → fitness-mcp-service
  → 原 Java Gateway → 预约 / 训练 / 客服及业务事实查询
```

现有 24 个工具中，11 个 Java 读能力与 9 个 Java 写能力接入 MCP。训练草案生成及
Memory list/save/revoke 四个工具保留本地。经营指标 `fitness.operations.metric.query.v1`
仍在 Agent 执行问题一致性、限流、比较、预算和审计；它调用远端
`fitness.operations.metric.aggregate.v1` 完成每期查询。

授权前置检查、确认前资源快照、用户结果查询与离线对账继续走受限内部 HTTP。
Registry 的本地批准目录仍是模型能力来源；远端目录只用于核对，不自动引入新工具。
服务不调用 LLM、业务数据库，不签发用户身份或确认 Token。

## 代码入口

| 位置 | 用途 |
|---|---|
| `packages/fitness-tool-contracts` | 20 个远端契约、输入输出 DTO、固定路由、确定性 JSON 与请求构造 |
| `fitness-mcp-service` | 独立轻量服务、每请求鉴权、SDK、Gateway Adapter、指标与 Docker |
| Agent `app/infrastructure/mcp_client.py` | 固定目录校验、逐调用头隔离、连接池、错误与 UNKNOWN |
| Agent `app/infrastructure/business_tools_client.py` | 固定方法映射、灰度及已确认动作的固定通道 |
| Agent `app/confirmation` | 精确确认、版本持久化、结果未知、租约及退避对账 |
| Java `ToolOperationController/Client/QueryService` | 三个业务域的统一只读操作结果查询 |

共享 DTO 不依赖整个 Agent。Agent SDK Client 按调用隔离用户 headers，复用安全的底层
连接池；单个取消不会关闭其他请求的连接池。目录分页有上限，未知/重复名称及不匹配
Schema 不执行。缺少远端能力时仍可使用本地能力，健康接口报告降级。

## 配置与服务凭证

从 `deployment/environments/*.production.env.example` 准备仓库外的六份配置：
`agent.env`、`mcp.env`、`gateway.env`、`booking.env`、`training.env`、`customer-service.env`。
真实值通过 Secret Manager 注入，不提交 `.env`、Token 或私钥。

| 身份 | 明文所在进程 | 验证配置 |
|---|---|---|
| Agent → MCP | `AGENT_MCP_SERVICE_TOKEN` | `MCP_SERVICE_CREDENTIALS_JSON` 中的 SHA-256、principal、scopes、expiry、resource |
| MCP → Gateway | `MCP_GATEWAY_SERVICE_TOKEN` | `GATEWAY_MCP_EXECUTION_TOKEN_SHA256` / `...EXPIRES_AT` |
| Agent HTTP 兼容执行 | `AGENT_GATEWAY_HTTP_EXECUTION_SERVICE_TOKEN` | `GATEWAY_AGENT_EXECUTION_TOKEN_SHA256` / `...EXPIRES_AT` |
| Agent 内部查询 | `AGENT_GATEWAY_QUERY_SERVICE_TOKEN` | `GATEWAY_AGENT_QUERY_TOKEN_SHA256` / `...EXPIRES_AT` |
| 后台对账 | `AGENT_GATEWAY_RECONCILIATION_SERVICE_TOKEN` | `GATEWAY_RECONCILIATION_TOKEN_SHA256` / `...EXPIRES_AT` |

五个凭证必须不同且至少 32 字符。有效期为 UNIX 秒。MCP 凭证 JSON 数组每项格式为：

```json
{"principal":"agent-api","sha256":"<64位摘要>","scopes":["fitness:read","fitness:write"],"expires_at":<到期UNIX秒>,"resource":"https://fitness-mcp.internal/mcp"}
```

这是字段示意，需用合法 JSON 和实际值替换占位符。`resource`、`MCP_RESOURCE_URL` 和
`AGENT_MCP_URL` 在当前配置门禁中精确一致。配置精确 Host allowlist；可信代理终止 TLS 时
限制 forwarded headers 来源、关闭 SSE 缓冲，代理超时应覆盖受控调用期限。
Compose 使用私有 `http://fitness-mcp:8001/mcp`，需要配套 resource、Host 和显式
`AGENT_MCP_ALLOW_PRIVATE_HTTP=true`；生产私有 HTTP 需要平台网络加密及访问控制。

MCP access token 不转发给 Java。用户身份另用认证端签发的 AgentContext，并由 Agent、
MCP、Gateway 分别验证；生产使用 RS256 公钥/JWKS，Adapter 不得安装签名私钥或 HMAC
签发密钥。仅本地测试可显式启用 HS256。现有生产配置已要求 RS256；历史 HS256 环境
须先完成认证切换并排空旧 Token，再启用 MCP，禁止自由算法回退。

旧共享 Token 默认不接受。临时兼容必须同时设置
`GATEWAY_ALLOW_LEGACY_SERVICE_TOKEN=true` 和有限截止时间；最终生产配置门禁要求关闭。
轮换时先部署同时信任新旧独立凭证的验证配置，再换调用方，排空后撤销旧凭证。

## 写操作及异常恢复

确认单固定 `transport` 与 `contract_version`。恢复时使用原动作、原业务 request ID、
原机构/主体/资源与稳定 JTI；模型不得提供身份、角色、Token 或审计 ID。
`payload_hash` 绑定确认动作，`request_body_sha256` 绑定实际 Gateway HTTP body 字节。
MCP JSON-RPC 包装不参与后者；训练记录 `day_id → dayId` 使用共享构造器。

写请求不自动网络重试。明确拒绝为 FAILED_FINAL；响应丢失、取消、5xx、损坏输出等
无法证明结果时为 UNKNOWN。不能因超时更换 JTI、换业务 ID 或改走 HTTP 再写一次。
MCP `isError` 和业务错误对象不会记为成功。取消等待不表示 Java 事务回滚。

九个 Java 写工具均可查询原操作：

```text
GET  /internal/agent-tools/v1/operations/{operationId}
     ?toolId=...&confirmationId=...&organizationId=...&payloadHash=...
POST /internal/agent-reconciliation/v1/operations/{operationId}
     {toolId,confirmationId,organizationId,actorId,payloadHash}
```

用户入口的 actor 来自签名身份，后台入口只接受对账服务身份。结果逐项绑定
operation/tool/confirmation/org/actor/payload hash。只有同事务业务事实、消费与审计证据
匹配才能返回 SUCCEEDED；缺记录继续 UNKNOWN。后台查询不触发业务执行。
本地 Memory 的未知结果保留证据交人工核实，不占用 Java 自动对账队列。

对账记录持久化领取租约和下次尝试时间，采用指数退避，防止长期 UNKNOWN 挤占后续动作。
证据与业务幂等键的保留时间必须覆盖对账、重放和人工处理窗口，不能跟随短 Token 到期清理。

## 安装、迁移与验证

从仓库根执行：

```bash
make agent-sync
make mcp-sync
make mcp-check
make production-config-check
PRODUCTION_ENV_DIR=/secure/release-config make production-runtime-config-check
make app-config-check
make mcp-image
make agent-image
```

Agent Docker 已改用仓库根作为 build context，以打包共享契约。MCP 使用独立镜像与
SAE 应用；独立 Worker 不连接 MCP。`make mcp-run` 以本地 8001 端口启动 Adapter，
需要先通过环境变量配置 `MCP_*`；Agent 开启 `AGENT_MCP_ENABLED=true` 后接入。
Compose 的 Worker 覆盖项清空 MCP 和 HTTP 执行凭证；生产平台也应按进程角色注入 Secret，
仅为需要内部查询或对账的进程提供对应只读凭证，不把 Agent API 的完整 Secret 集复制给所有 Worker。

先备份并执行兼容迁移，再发布业务服务、Gateway、MCP、Agent 和相关 Worker：

- PostgreSQL：`alembic upgrade head`。0048 保存执行通道/版本、修复 UNKNOWN 审计约束、
  将历史 FAILED_RETRYABLE 隔离为 UNKNOWN；0049 增加公平对账调度及租约元数据。
- MySQL 训练：执行 `V20261027_010__unique_confirmation_operation.sql`。部署前检查
  `SELECT request_id, COUNT(*) FROM training_confirmation_consumption GROUP BY request_id HAVING COUNT(*)>1;`。
  若有重复应先核实，不能删除审计或绕开约束强行迁移。

可重复真实 MySQL 测试入口：`fitness-core-gateway/scripts/isolated-mysql-acceptance.sh`。
它只创建并销毁自己的容器/临时库，不连接开发 `fitness-mysql`。PG 验收通过
`scripts/runtime_delivery_live_check.py --execute` 创建指定前缀临时库；需先配置独立测试 PG/Redis。
普通测试跳过外部验收不等于已经验证这些依赖。

## 灰度、回退与监控

`AGENT_MCP_TOOL_IDS=[]` 表示全部 20 个批准远端能力；非空时按固定工具 ID 灰度。
经营能力在此使用远端 `fitness.operations.metric.aggregate.v1`。
只读灰度 Token 可以仅有 `fitness:read`，但清单也须仅列 11 个读工具；全量需 read/write。

`AGENT_MCP_ENABLED=false` 只影响新动作。回退期间保留 MCP 地址、凭证、服务及旧版本
构造器，直到旧确认单完成、明确取消/过期且在途 UNKNOWN 已核实。仍保持原 transport 的
已批准动作不受新开关影响。未知写操作禁止自动改走另一通道。不得回退到重新签 JTI 重发
不确定写操作的旧镜像；迁移采用增量兼容方式，禁止丢失确认状态与证据。

`/health/live` 为存活；MCP `/health/ready` 反映配置和生命周期，业务可用性由受控调用观察。
Agent readiness 中 MCP 状态来自缓存，不因探针触发远端工具发现。
Prometheus 已配置 MCP scrape；观察工具成功/拒绝/UNKNOWN/取消、耗时、在途调用，及
Agent 的目录可用性、Java 对账积压/最老年龄、本地人工处理积压。所有标签均无用户/机构/请求 ID。
日志不保存 Token、签名上下文、完整工具参数或返回。长期未知告警后按业务事实人工核实，
不能把查不到结果标为失败后重放。

性能需在目标预发容量下与旧 HTTP 链路比较；测试环境吞吐不能作为生产容量承诺。
本次代码与隔离环境验收的精确结果见 `mcp-implementation-verification.md`。
