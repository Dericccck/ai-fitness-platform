# Fitness Core Tool Gateway

这是健身平台的独立 Java Tool Gateway，当前提供健身核心查询、训练计划写工具、预约创建、预约改约和预约取消写工具，以及管理员专用的固定经营指标查询：用户/学员、教练、机构、课程、合同、课时和预约。

赛事、作品、活动运营及其历史代码不属于本服务范围。本服务不依赖根目录旧 Java 项目的 Entity、Service 或组件扫描，避免遗留模块污染新的 Agent 业务边界。

## 设计边界

- Agent 服务只负责意图识别、编排和模型调用，不能直接连接业务 MySQL。
- Gateway 使用数据库只读账号和显式 SQL，返回稳定的 Tool View，不暴露旧 Entity 图、密码或无关字段。
- 每个请求同时需要 `X-Internal-Service-Token` 和签名的 `X-Agent-Context`。
- `AgentContext` 包含用户主体、机构范围、角色、签发时间、过期时间和 nonce；Gateway 每次调用都会再次校验资源权限。
- 当前已增加结构化训练计划 Tool、预约创建、预约改约和预约取消 Tool；预约可用性预检为只读工具。
  三种预约写操作都由独立 `fitness-booking-service` 持有业务 MySQL 写权限，必须具备确认凭证、幂等键、事务和审计。

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

生产环境的密钥必须由 Secret Manager 注入，不能写入 `application.yml` 或提交到 Git。
`GATEWAY_CONFIRMATION_SIGNING_SECRET` 必须与 Agent 的 `AGENT_CONFIRMATION_SIGNING_SECRET`
逐字节一致；当前是兼容 Gateway v1 的 HMAC 过渡密钥，后续 v2 会切换为可轮换的非对称验签。

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

## 当前只读工具

```text
GET /internal/agent-tools/v1/me
GET /internal/agent-tools/v1/organizations/{organizationId}
GET /internal/agent-tools/v1/courses?organizationId=...
GET /internal/agent-tools/v1/contracts?organizationId=...&userId=...
GET /internal/agent-tools/v1/appointments?organizationId=...&userId=...&from=...&to=...
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
COURSE_APPOINTMENT_COUNT
COACH_APPOINTMENT_COUNT
REMAINING_CLASS_HOURS
```

经营指标只允许 `SYSTEM_ADMIN` 和 `ORGANIZATION_ADMIN` 访问，查询会绑定签名上下文中的机构范围，
时间范围最多 92 天，单次最多返回 100 行。当前阶段不接受 SQL、表名或任意字段名；后续 Text-to-SQL
也必须先映射到这个指标目录，并继续由 Java Gateway 执行固定 SQL。

`bucket=DAY` 或 `bucket=WEEK` 当前开放给预约总量、课程预约量和教练预约量；课程指标只统计有课程 ID 的预约，
教练指标只统计有教练 ID 的预约。状态分布和剩余课时仍只能查询整个时间范围汇总。
