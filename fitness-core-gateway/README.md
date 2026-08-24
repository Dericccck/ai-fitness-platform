# Fitness Core Tool Gateway

这是健身平台的独立 Java Tool Gateway，当前提供健身核心查询、客服工单查询与确认创建、训练计划写工具、预约创建、预约改约和预约取消写工具，以及管理员专用的固定经营指标查询：用户/学员、教练、机构、课程、合同、课时和预约。

赛事、作品、活动运营及其历史代码不属于本服务范围。本服务不依赖根目录旧 Java 项目的 Entity、Service 或组件扫描，避免遗留模块污染新的 Agent 业务边界。

## 设计边界

- Agent 服务只负责意图识别、编排和模型调用，不能直接连接业务 MySQL。
- Gateway 使用数据库只读账号和显式 SQL，返回稳定的 Tool View，不暴露旧 Entity 图、密码或无关字段。
- 每个请求同时需要 `X-Internal-Service-Token` 和签名的 `X-Agent-Context`。
- `AgentContext` 包含用户主体、机构范围、角色、签发时间、过期时间和 nonce；Gateway 每次调用都会再次校验资源权限。
- 当前已增加结构化训练计划 Tool、预约创建、预约改约和预约取消 Tool；预约可用性预检为只读工具。
  三种预约写操作都由独立 `fitness-booking-service` 持有业务 MySQL 写权限，必须具备确认凭证、幂等键、事务和审计。
- 客服工单由独立 `fitness-customer-service` 持有表结构和写权限；Gateway 只代理版本化查询和确认创建，
  不允许 Agent 直接访问客服数据库，也不提供 Agent 自动修改/关闭工单。

## 本地配置

数据库账号必须具备最小权限，建议只授予以下核心表的 `SELECT`：

```text
login_user
login_user_authority
organization
user_and_coach
course
contract
appointment
system_settings
vacation_record
```

配置 `GATEWAY_DB_URL`、`GATEWAY_DB_USERNAME`、`GATEWAY_DB_PASSWORD`、
`GATEWAY_INTERNAL_SERVICE_TOKEN`、`GATEWAY_CONTEXT_SIGNING_SECRET` 和与 Agent 相同的
`GATEWAY_CONFIRMATION_SIGNING_SECRET` 后启动：

```bash
cd /Users/a1-6/Desktop/fitness-backend
./mvnw --batch-mode -f fitness-core-gateway/pom.xml test
./mvnw --batch-mode -f fitness-core-gateway/pom.xml spring-boot:run
```

如需启用客服工单查询/创建，还需要配置 `GATEWAY_CUSTOMER_SERVICE_BASE_URL`（默认
`http://127.0.0.1:8084`）和 `GATEWAY_CUSTOMER_SERVICE_TOKEN`；该 Token 必须与
`CUSTOMER_SERVICE_INTERNAL_SERVICE_TOKEN` 完全一致。客服服务的数据库账号通过
`CUSTOMER_SERVICE_DB_USERNAME`、`CUSTOMER_SERVICE_DB_PASSWORD` 注入，不能复用 Gateway 的只读账号配置。

生产环境的密钥必须由 Secret Manager 注入，不能写入 `application.yml` 或提交到 Git。
AgentContext 当前支持 `HS256` 和配置公钥环的 `RS256`；`GATEWAY_CONTEXT_SIGNING_ALGORITHM`
必须与认证服务签发的 `alg` 一致，`GATEWAY_CONTEXT_SIGNING_KEY_ID` 表示当前主密钥的
`kid`。密钥轮换时，新 Token 使用新的 `kid` 和主密钥，旧
`kid` 的短时 Token 只从 `gateway.security.context-signing-key-ring` 读取旧密钥；使用环境变量
时可按 `GATEWAY_SECURITY_CONTEXT_SIGNING_KEY_RING_<KID>` 注入，例如 `..._V1`；未知 `kid` 不会
回退到主密钥。使用 `RS256` 时只配置
`gateway.security.context-verification-public-key-ring` 公钥环，例如按
`GATEWAY_SECURITY_CONTEXT_VERIFICATION_PUBLIC_KEY_RING_<KID>` 注入；私钥只保留在认证服务。
默认不配置外部 JWKS，未配置时公钥更新仍由部署配置完成。
配置 `GATEWAY_CONTEXT_VERIFICATION_JWKS_URL` 后，Gateway 会按 `kid` 从标准 JWKS 文档读取
RSA 公钥并在短期缓存内复用；缓存过期且认证服务不可用时，RS256 请求会 fail-closed。
如果收到缓存中不存在的 `kid`，Gateway 会触发一次受控刷新以支持提前发生的密钥轮换；
未知 `kid` 刷新带有 30 秒冷却窗口，防止恶意请求反复打认证服务。
`GATEWAY_CONFIRMATION_SIGNING_SECRET` 必须与 Agent 的 `AGENT_CONFIRMATION_SIGNING_SECRET`
逐字节一致；确认凭证当前支持 HMAC v1 和 RS256。使用 RS256 时配置
`GATEWAY_CONFIRMATION_SIGNING_ALGORITHM=RS256`、按 `kid` 配置
`confirmation-verification-public-key-ring` 或 `GATEWAY_CONFIRMATION_VERIFICATION_JWKS_URL`，
Gateway 只持有公钥，Agent/确认服务持有私钥。

真实数据库集成测试默认关闭。连接到专门的测试库并提供有效机构 ID 后显式执行；本地开发也可以
临时复用 Docker `fitness-mysql`（宿主机 `3307`、数据库 `fitness`），但生产和 CI 仍必须使用
独立测试库与最小权限账号：

```bash
GATEWAY_IT_ENABLED=true \
GATEWAY_IT_DB_URL='jdbc:mysql://127.0.0.1:3307/fitness' \
GATEWAY_IT_DB_USERNAME='fitness_readonly' \
GATEWAY_IT_DB_PASSWORD='通过 Secret Manager 注入' \
GATEWAY_IT_ORGANIZATION_ID='真实测试机构 ID' \
./mvnw --batch-mode -f fitness-core-gateway/pom.xml test
```

测试库必须使用最小权限账号，禁止把生产数据库凭证用于本地或 CI 集成测试。

根目录统一质量门禁也会执行本模块：

```bash
make gateway-check
```

## 当前工具接口

```text
GET /internal/agent-tools/v1/me
GET /internal/agent-tools/v1/organizations/{organizationId}
GET /internal/agent-tools/v1/courses?organizationId=...
GET /internal/agent-tools/v1/contracts?organizationId=...&userId=...
GET /internal/agent-tools/v1/appointments?organizationId=...&userId=...&from=...&to=...
GET /internal/agent-tools/v1/customer-service/tickets?organizationId=...&subjectUserId=...&status=...&limit=...
GET /internal/agent-tools/v1/customer-service/tickets/{ticketId}?organizationId=...
POST /internal/agent-tools/v1/customer-service/tickets
GET /internal/agent-tools/v1/booking/availability?organizationId=...&studentId=...&coachId=...&courseId=...&start=...&end=...
GET /internal/agent-tools/v1/operations/metrics?organizationId=...&metric=APPOINTMENT_COUNT&from=2026-08-01&to=2026-08-15&bucket=DAY
POST /internal/agent-tools/v1/appointments
POST /internal/agent-tools/v1/appointments/{appointmentId}/reschedule
POST /internal/agent-tools/v1/appointments/{appointmentId}/cancel
GET /internal/agent-tools/v1/training/plans/{planId}
POST /internal/agent-tools/v1/training/plans/drafts
POST /internal/agent-tools/v1/training/plans/{planId}/submit-review
POST /internal/agent-tools/v1/training/plans/{planId}/review
POST /internal/agent-tools/v1/training/plans/{planId}/publish
GET /internal/agent-tools/v1/training/plans/{planId}/executions
POST /internal/agent-tools/v1/training/plans/{planId}/days/{dayId}/execution
```

Operations 指标第一阶段只允许以下目录项：

```text
APPOINTMENT_COUNT
APPOINTMENT_STATUS_BREAKDOWN
COMPLETED_CLASS_COUNT
NEW_CUSTOMER_COUNT
REVENUE_AMOUNT
COURSE_APPOINTMENT_COUNT
COACH_APPOINTMENT_COUNT
REMAINING_CLASS_HOURS
```

`COMPLETED_CLASS_COUNT` 统计 `deleted = 0` 且 `appointment.status = 6`（已完成/核销成功）的课程次数，按课程开始时间归属统计。
`NEW_CUSTOMER_COUNT` 统计有效合同中 `new_customer = 1` 的去重学员数，按合同创建时间归属统计；账号注册但未形成客户合同的用户不计入。
`REVENUE_AMOUNT` 统计有效合同的 `total_amount - refund_amount` 净营收，按合同创建时间归属统计；空金额按 0 处理，金额单位沿用合同字段。
经营指标只允许 `SYSTEM_ADMIN` 和 `ORGANIZATION_ADMIN` 访问，查询会绑定签名上下文中的机构范围，
时间范围最多 92 天，单次最多返回 100 行。当前阶段不接受 SQL、表名或任意字段名；后续 Text-to-SQL
也必须先映射到这个指标目录，并继续由 Java Gateway 执行固定 SQL。

`bucket=DAY` 或 `bucket=WEEK` 当前开放给预约总量、完课量、新客量、营收金额、课程预约量和教练预约量；新客量和营收金额按合同创建时间分组，课程指标只统计有课程 ID 的预约，
教练指标只统计有教练 ID 的预约。状态分布和剩余课时仍只能查询整个时间范围汇总。
