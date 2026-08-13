# Fitness Training Service v1 契约

该服务是训练计划的业务事实源，和只读 `fitness-core-gateway` 分离。赛事、作品和活动运营不在
契约范围内。

## 角色规则

| 角色 | 允许能力 |
| --- | --- |
| 系统/机构管理员 | 在签名机构范围内创建草案、提交审核、审核和发布 |
| 负责教练 | 为负责学员创建草案、提交审核、审核和发布 |
| 学员 | 只能参与本人草案内容，不能提交审核、审核、发布；只能查看 `PUBLISHED` 计划 |
| Agent | 只能通过业务写工具创建结构化 `DRAFT`，不能把状态写入请求体 |

训练服务再次校验 `organizationId + studentId + coachId` 的现有 `user_and_coach` 关系，不能
只相信 Agent 传入的 ID。

## 状态机

```text
DRAFT -> PENDING_REVIEW -> APPROVED -> PUBLISHED
                  └------> REJECTED -> PENDING_REVIEW
```

已发布计划不能原地修改；后续修改必须产生新的计划版本。每次状态转换都要求新的
`X-Request-ID`，数据库使用版本号和唯一请求 ID 防止并发覆盖和重复提交。

## 内部 Header

所有 `/internal/training/v1/**` 请求需要：

```text
X-Internal-Service-Token
X-Actor-User-Id
X-Actor-Roles
X-Actor-Organization-Ids
X-Request-ID
```

这些字段只能由已经验证 `AgentContext` 的 Gateway 注入，Python Agent 不得直接构造或调用训练
服务。下一步将由 Gateway 固定映射这些字段，并在 Python Tool Registry 中增加版本化 Schema。

## 当前 HTTP API

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| POST | `/internal/training/v1/plans/drafts` | 创建结构化 Agent 草案 |
| POST | `/internal/training/v1/plans/{id}/submit-review` | 负责教练/机构管理员提交审核 |
| POST | `/internal/training/v1/plans/{id}/review` | 负责教练/机构管理员审核或驳回 |
| POST | `/internal/training/v1/plans/{id}/publish` | 发布已审核通过的计划 |
| GET | `/internal/training/v1/plans/{id}` | 按角色读取计划 |

训练执行记录、组次反馈、测量和阶段调整暂未开放；不能用计划备注字段替代。
