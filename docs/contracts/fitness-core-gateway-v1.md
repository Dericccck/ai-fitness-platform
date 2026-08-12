# Fitness Core Gateway v1 契约

该契约描述 Python Agent 与 Java `fitness-core-gateway` 之间的内部调用边界。赛事、作品和活动运营不属于契约范围。

## 认证 Header

每个 `/internal/agent-tools/v1/**` 请求必须同时携带：

```text
X-Internal-Service-Token: <Agent 服务凭证>
X-Agent-Context: <base64url(payload)>.<base64url(HMAC-SHA256(payload))>
X-Request-ID: <跨服务请求 ID>
X-Trace-ID: <可选链路 ID>
```

`X-Internal-Service-Token` 只证明请求来自受信任的 Agent 服务，不包含用户身份。`X-Agent-Context` 由认证服务签发，payload 至少包含：

```json
{
  "sub": "user-id",
  "orgs": ["organization-id"],
  "roles": ["STUDENT"],
  "iat": 1786492800,
  "exp": 1786492980,
  "nonce": "single-context-id"
}
```

Gateway 默认只接受 5 分钟以内的上下文。Agent 不得根据用户自然语言、模型输出或 URL 参数自行生成 `sub`、`orgs` 或 `roles`。

## 只读工具

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| GET | `/internal/agent-tools/v1/me` | 获取当前用户安全视图 |
| GET | `/internal/agent-tools/v1/organizations/{organizationId}` | 获取当前授权机构 |
| GET | `/internal/agent-tools/v1/courses?organizationId=...&limit=...` | 查询机构课程 |
| GET | `/internal/agent-tools/v1/contracts?organizationId=...&userId=...&limit=...` | 查询合同和剩余课时 |
| GET | `/internal/agent-tools/v1/appointments?organizationId=...&userId=...&from=...&to=...&limit=...` | 查询时间范围内预约 |

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

当前契约只开放读操作。创建预约、改约、取消预约必须新增确认凭证、幂等键、事务和审计字段后才允许加入。
