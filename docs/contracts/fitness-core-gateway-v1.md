# Fitness Core Gateway v1 契约

该契约描述 Python Agent 与 Java `fitness-core-gateway` 之间的内部调用边界。赛事、作品和活动运营不属于契约范围。

## 认证 Header

每个 `/internal/agent-tools/v1/**` 请求必须同时携带：

```text
X-Internal-Service-Token: <Agent 服务凭证>
X-Agent-Context: <base64url(payload)>.<base64url(HMAC-SHA256(payload))>
X-Request-ID: <跨服务请求 ID>
X-Trace-ID: <可选链路 ID>
X-Confirmation-Token: <写工具必需；由上游确认服务签发>
```

`X-Internal-Service-Token` 只证明请求来自受信任的 Agent 服务，不包含用户身份。`X-Agent-Context` 由认证服务签发，payload 至少包含：

```json
{
  "sub": "user-id",
  "orgs": ["organization-id"],
  "roles": ["STUDENT"],
  "capabilities": [],
  "qualifications": [],
  "iat": 1786492800,
  "exp": 1786492980,
  "nonce": "single-context-id"
}
```

Gateway 默认只接受 5 分钟以内的上下文。Agent 不得根据用户自然语言、模型输出、URL 参数或
审核表单自行生成 `sub`、`orgs`、`roles`、`capabilities` 或 `qualifications`。

`capabilities` 和 `qualifications` 是可选的签名数组，缺省等同空集合，只用于窄范围高风险能力。
当前健身知识审核定义：

- `KNOWLEDGE_REVIEW_FITNESS`：被指定为健身知识审核员；仅有 `COACH` 角色不自动获得。
- `KNOWLEDGE_REVIEW_CLINICAL`：被指定为临床运动知识审核员。
- `KNOWLEDGE_REVIEW_GLOBAL`：允许审核平台全局知识；组织审核能力不能扩大到全局资料。
- `VERIFIED_HEALTH_PROFESSIONAL`：认证事实源已核验的健康专业人员资质。

Java 认证事实源尚未完成审核员配置和资质核验适配前，不得给测试账号或管理员默认签发这些
claim。Agent 服务会 fail-closed，因此这一外部依赖未落地时专业审核接口会返回 403。

## 只读工具

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| GET | `/internal/agent-tools/v1/me` | 获取当前用户安全视图 |
| GET | `/internal/agent-tools/v1/organizations/{organizationId}` | 获取当前授权机构 |
| GET | `/internal/agent-tools/v1/courses?organizationId=...&limit=...` | 查询机构课程 |
| GET | `/internal/agent-tools/v1/contracts?organizationId=...&userId=...&limit=...` | 查询合同和剩余课时 |
| GET | `/internal/agent-tools/v1/appointments?organizationId=...&userId=...&from=...&to=...&limit=...` | 查询时间范围内预约 |
| GET | `/internal/agent-tools/v1/booking/availability?organizationId=...&studentId=...&coachId=...&courseId=...&start=...&end=...` | 预约写入前的只读可用性预检 |

学生只能读取本人数据；教练读取其他学员时必须存在有效的机构教练关系；机构管理员只能读取签名上下文授权的机构范围。所有列表默认最多 20 条，单次最多 100 条；预约时间范围最多 92 天。

## 错误语义

```json
{"code":"UNAUTHORIZED","message":"authentication required"}
```

| HTTP | code | Agent 处理 |
| --- | --- | --- |
| 400 | `INVALID_ARGUMENT` | 修正参数后再调用，不自动重试 |
| 401 | `UNAUTHORIZED` | 终止当前工具调用，刷新认证上下文 |
| 403 | `FORBIDDEN` | 不得换 ID 重试，向用户说明权限范围 |
| 404 | `NOT_FOUND` | 说明资源不存在，不泄露 SQL 细节 |
| 408/429/5xx | 无固定 code 要求 | 有限指数退避，耗尽后转为不可用 |

训练计划写工具和创建预约写工具都必须同时具备确认凭证、幂等请求 ID、事务和审计；改约、取消预约仍未开放。
创建预约已经通过独立预约业务服务执行，不能因为 Agent 已完成确认就跳过业务服务的最终校验。可用性预检也不等于预约成功：它只检查
组织、学员、教练、时间范围、教练已有预约冲突、教练请假和已接入的非营业日规则，结果返回 `available`、
`reasonCodes` 和冲突预约；真正写入时必须在同一业务事务内再次校验。

预约创建路径：

```text
POST /internal/agent-tools/v1/appointments
X-Confirmation-Token: <已批准确认凭证>
```

预约写服务会锁定相同请求和教练业务日期，重新校验合同状态、有效期、剩余课时、课程状态、组织关系、
请假、非营业日和时间冲突；成功后在同一事务中扣减课时、创建 `appointment`、记录审计、消费 JTI 并写入 Outbox。

训练计划工具路径：

```text
GET  /internal/agent-tools/v1/training/plans/{planId}
POST /internal/agent-tools/v1/training/plans/drafts
POST /internal/agent-tools/v1/training/plans/{planId}/submit-review
POST /internal/agent-tools/v1/training/plans/{planId}/review
POST /internal/agent-tools/v1/training/plans/{planId}/publish
GET  /internal/agent-tools/v1/training/plans/{planId}/executions
POST /internal/agent-tools/v1/training/plans/{planId}/days/{dayId}/execution
```

写工具的 `X-Confirmation-Token` 当前由 Agent 服务端使用共享 HMAC 密钥签发，Gateway 会绑定并校验
签名主体、工具 ID、动作、机构、资源、请求 ID、参数哈希、JTI 和过期时间。Gateway 不把原始 Token
继续传给训练服务，而是只转发已验签的声明 Header：`X-Confirmation-Id`、`X-Confirmation-JTI`、
`X-Confirmation-Tool-ID`、`X-Confirmation-Action`、`X-Confirmation-Organization-ID`、
`X-Confirmation-Resource` 和 `X-Confirmation-Payload-Hash`。训练服务必须在自己的业务事务中
消费 JTI；模型不能在工具参数中传入或伪造它，浏览器也不应持有它。
Agent Registry 会在发起 HTTP 请求前拦截缺少确认凭证的写调用。

## Python Tool Registry v1

Python Agent 不直接把模型输出转换成 HTTP 请求，而是先经过进程级 `ToolRegistry`。当前
注册的工具 ID 为：

```text
fitness.user.get_current.v1
fitness.organization.get.v1
fitness.course.list.v1
fitness.contract.list.v1
fitness.appointment.list.v1
fitness.booking.availability.check.v1
fitness.booking.create.v1
```

每个工具必须固定定义输入 Pydantic Schema、版本、描述、允许角色、是否只读和是否需要
确认；调用时先拒绝未知工具和额外字段，再由固定适配器调用 `GatewayClient`。组织 ID、
用户 ID 等参数即使通过 Schema，也只代表查询意图，最终资源权限仍由签名 `AgentContext`
和 Java Gateway 重新校验。

Agent 侧审计只记录工具 ID、成功/失败、请求 ID、Trace ID、耗时和稳定错误码，不记录原始
参数、Tool View、Prompt 或签名上下文。后续 Supervisor 只能使用 Registry 暴露的工具
Schema；写工具还必须在注册阶段声明确认要求，并在 Java Gateway 侧实现幂等和事务审计。
