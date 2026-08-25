# fitness-customer-service

健身客服工单业务服务。它只负责客服工单这一类业务事实，不负责 RAG 检索、LLM 推理或
旧赛事/作品/活动模块。

## 当前范围

- 使用本地 `fitness` MySQL 数据库中的 `agent_customer_service_ticket` 表；表和字段均有中文注释。
- 提供经过内部 Token、角色、机构范围和主体身份校验的工单查询接口，以及必须经过确认凭证的工单创建接口。
- 学员/教练只能查询自己的工单；组织管理员可以查询本机构工单；系统管理员可以查询授权机构范围内的工单。
- 工单创建只允许分类、标题、描述和可选关联资源；来源固定为 `AGENT`，状态固定从 `OPEN` 开始。
- 创建使用 `X-Request-ID` 幂等，确认 JTI 消费、工单创建和 `CREATED` 审计必须在同一事务内完成；本服务当前不提供 Agent 自动改派、关闭或解决工单。
- 客服服务不会只相信完整的确认请求头，还会再次核对工具 ID、动作、机构和资源标识，形成 Gateway 之外的纵深防御。
- 真实写入验收使用 Agent 项目的受控脚本；测试工单携带随机 `request_id`，验收后只删除本轮
  `request_id` 对应的工单、确认消费和审计，不允许按机构、状态或时间范围批量清理。

## 本地启动

```bash
export CUSTOMER_SERVICE_DB_USERNAME=fitness
export CUSTOMER_SERVICE_DB_PASSWORD=fitness_dev_2026
export CUSTOMER_SERVICE_INTERNAL_SERVICE_TOKEN='与 Gateway 客服服务配置相同的内部 Token'
make customer-service-check
make customer-service-run

# 真实客服工单验收前的只读环境检查，不会创建工单
make agent-customer-service-preflight

# 默认只做客服确认单无写入联调
make agent-customer-service-live-check

# 只有确认使用本机测试数据且允许自动清理时，才执行受控写入验收
export CUSTOMER_SERVICE_LIVE_ALLOW_WRITE=1
export CUSTOMER_SERVICE_LIVE_CLEANUP=1
make agent-customer-service-write-live-check

# 只读验证管理员、教练、学员的工单权限边界，不会写入客服数据
export CUSTOMER_SERVICE_LIVE_ORGANIZATION_ID='本地测试机构 ID'
export CUSTOMER_SERVICE_LIVE_STUDENT_ID='本地测试学员 ID'
export CUSTOMER_SERVICE_LIVE_COACH_ID='本地测试教练 ID'
export CUSTOMER_SERVICE_LIVE_ADMIN_ID='本地测试管理员 ID'
export FITNESS_DEV_CONTEXT_ISSUER=1
make gateway-customer-service-role-live-check
```

受控写入验收还要求 `GATEWAY_DB_USERNAME`、`GATEWAY_DB_PASSWORD` 和
`AGENT_LIVE_AGENT_CONTEXT`。脚本只接受回环 Agent 地址，并且 Makefile 和脚本都要求显式写入开关；
不应在生产或共享数据库中运行。脚本会验证确认批准、`AGENT/OPEN` 工单、一次性确认消费、
`CREATED` 审计和中文内容编码，然后在 `finally` 中精确清理。

角色只读联调只调用 Gateway 的 `GET /internal/agent-tools/v1/customer-service/tickets`，验证学员、
教练和管理员的主体范围及机构范围；它不会调用 LLM，也不会创建客服工单。

默认端口为 `8084`。生产环境应关闭 `CUSTOMER_SERVICE_SCHEMA_INIT_ENABLED`，由独立迁移任务执行
`src/main/resources/db/migration/V20260824_001__create_customer_service_ticket.sql`。

`GET /health/live` 是进程存活探针，只返回 `{"status":"ok"}`，不访问 MySQL。`GET /health/ready` 会只读检查
MySQL 连接、最新客服表结构版本和内部 Token 配置，返回脱敏的 `ready/not_ready` 状态，不返回密钥或异常正文。
真实验收前应先运行 `agent-customer-service-preflight`，再按受控写入脚本的开关和授权要求执行。
该 preflight 对本地健康检查会绕过系统 HTTP 代理；如果客服服务未启动，会直接报告无法连接 `8084`，不会把
代理返回的 `502` 当成客服服务业务响应。

## 内部接口

- `GET /internal/customer-service/v1/tickets?organizationId=...`
- `GET /internal/customer-service/v1/tickets/{ticketId}?organizationId=...`
- `POST /internal/customer-service/v1/tickets`（必须带完整确认声明，仅由 Gateway 调用）

接口只能由 Java Tool Gateway 调用，Agent 不得直连本服务或数据库。

仓储自动化测试覆盖四类写入边界：新 JTI 的安全幂等复用、参数摘要冲突、并发唯一键竞争和已消费
JTI 重放；这些测试只使用 Mockito，不会连接本地 MySQL。真实数据库写入仍必须通过受控验收脚本执行。

Gateway 的 `CustomerServiceClientTest` 另外覆盖下游 `403`、`404`、`5xx`、网络异常、空响应和缺少
内部 Token 的统一错误语义，并验证内部身份与机构请求头会被透传；这些测试同样不写入客服数据库。

受控写入脚本的离线测试还验证：成功流程和事实校验失败流程都会执行 `finally` 精确清理，且清理顺序为
客服审计、确认消费、工单。该测试不会调用 Agent、DeepSeek 或 MySQL。
