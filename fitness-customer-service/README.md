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

## 本地启动

```bash
export CUSTOMER_SERVICE_DB_USERNAME=fitness
export CUSTOMER_SERVICE_DB_PASSWORD=fitness_dev_2026
export CUSTOMER_SERVICE_INTERNAL_SERVICE_TOKEN='与 Gateway 客服服务配置相同的内部 Token'
make customer-service-check
make customer-service-run
```

默认端口为 `8084`。生产环境应关闭 `CUSTOMER_SERVICE_SCHEMA_INIT_ENABLED`，由独立迁移任务执行
`src/main/resources/db/migration/V20260824_001__create_customer_service_ticket.sql`。

## 内部接口

- `GET /internal/customer-service/v1/tickets?organizationId=...`
- `GET /internal/customer-service/v1/tickets/{ticketId}?organizationId=...`
- `POST /internal/customer-service/v1/tickets`（必须带完整确认声明，仅由 Gateway 调用）

接口只能由 Java Tool Gateway 调用，Agent 不得直连本服务或数据库。
