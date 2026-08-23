# Fitness Training Service v1 契约

该服务是训练计划的业务事实源，和只读 `fitness-core-gateway` 分离。赛事、作品和活动运营不在
契约范围内。

## 内部确认声明

Gateway 验证 Agent 的 `X-Confirmation-Token` 后，不转发原始 Token，而是转发以下内部声明：

```text
X-Confirmation-Id
X-Confirmation-JTI
X-Confirmation-Tool-ID
X-Confirmation-Action
X-Confirmation-Organization-ID
X-Confirmation-Resource
X-Confirmation-Payload-Hash
```

训练服务只接受由正确的 `X-Internal-Service-Token` 保护的完整声明，并再次把工具、动作、机构和
资源绑定到当前业务请求。JTI 在训练服务的 MySQL 事务中消费，和训练计划写入、状态审计使用同一
事务边界；同一 JTI 的重复消费返回冲突。

## 角色规则

| 角色 | 允许能力 |
| --- | --- |
| 系统/机构管理员 | 在签名机构范围内创建草案、提交审核、审核和发布 |
| 负责教练 | 为负责学员创建草案、提交审核、审核和发布 |
| 学员 | 不能创建、修改、提交审核、审核或发布训练计划；只能查看本人 `PUBLISHED` 计划，并记录本人训练日完成或跳过 |
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
| GET | `/internal/training/v1/plans/{id}/executions` | 查询已提交的训练日执行记录 |
| POST | `/internal/training/v1/plans/{id}/days/{dayId}/execution` | 学员记录训练日完成或跳过 |

执行接口的请求体包含 `dayId`、`status=COMPLETED/SKIPPED` 和可选 `note`，其中 `dayId` 必须与路径一致；训练日期由服务端生成，
并绑定 `planId:dayId` 资源范围。训练执行记录、组次反馈、测量和阶段调整暂未开放；不能用计划备注字段替代。
