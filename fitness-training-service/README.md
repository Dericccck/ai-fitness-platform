# Fitness Training Service

这是健身项目的结构化训练业务服务，不是 Demo Agent，也不是旧赛事/作品/活动模块的恢复。
它负责保存训练计划事实、执行状态机和操作审计；`fitness-core-gateway` 负责后续把这些能力
以受权限保护的 Tool 暴露给 Agent。

## 当前已实现

- Agent 通过管理员或负责教练上下文创建训练计划草案；请求体不能伪造状态、审核人或发布人，学员不能创建草案。
- 计划、训练日、动作明细三级结构，动作包含组数、次数、休息、目标重量、RPE 和备注。
- `DRAFT -> PENDING_REVIEW -> APPROVED -> PUBLISHED` 状态机，驳回后可以重新提交审核。
- 只有负责教练或机构管理员可以审核和发布；学员只能查看已发布计划。
- MySQL 版本化 SQL、计划版本号并发控制、请求 ID 幂等、不可变状态审计。
- Gateway 已验签的 `confirmation_id/tool_id/action/organization/resource/payload_hash/jti` 会通过
  内部 Header 传入；本服务在训练计划业务事务中写入确认消费记录，JTI 重放会被唯一键拒绝。
- 确认消费和训练计划写入处于同一个 MySQL 事务：业务失败会一起回滚，网络重试仍由原始请求 ID
  幂等收敛，不能产生第二份训练计划或第二次状态审计。
- 最小训练执行闭环支持学员对已发布计划的训练日标记 `COMPLETED`（已完成）或 `SKIPPED`（已跳过），
  保存执行日期、简短备注、当前版本和不可变审计；暂不保存逐组实绩、身体测量、疼痛或疲劳量表。
- 独立 Maven 构建，不依赖不完整的旧 Java 赛事代码。

## 启动与配置

```bash
mvn -q -s ../.mvn/settings.xml -Dmaven.repo.local=../.mvn/repository test
mvn -q -s ../.mvn/settings.xml -Dmaven.repo.local=../.mvn/repository spring-boot:run
```

服务默认连接现有本地 MySQL `fitness` 库（端口 `3307`），账号和密码必须通过环境变量注入：

```text
TRAINING_DB_URL
TRAINING_DB_USERNAME
TRAINING_DB_PASSWORD
TRAINING_INTERNAL_SERVICE_TOKEN
```

本地首次启动会按顺序执行 `db/migration/` 中的版本化 SQL，并通过版本表避免重复执行；
生产环境应由独立迁移 Job 执行同一份 SQL 后设置 `TRAINING_SCHEMA_INIT_ENABLED=false`。
当前环境无法下载 Flyway 依赖，因此暂时使用版本化 SQL 加版本表；后续统一迁移平台接入时不得
修改业务表结构和迁移语义。

## 内部 API

存活检查：`GET /health`，成功返回 `{"status":"UP"}`。该接口不需要业务主体 Header；内部
训练接口才需要以下安全 Header。

`/internal/training/v1/**` 需要同时携带：

- `X-Internal-Service-Token`
- `X-Actor-User-Id`
- `X-Actor-Roles`
- `X-Actor-Organization-Ids`
- `X-Request-ID`
- `X-Confirmation-Id`
- `X-Confirmation-JTI`
- `X-Confirmation-Tool-ID`
- `X-Confirmation-Action`
- `X-Confirmation-Organization-ID`
- `X-Confirmation-Resource`
- `X-Confirmation-Payload-Hash`

这些 Header 只能由已验证的 Gateway 注入，Python Agent 不得直接调用训练服务，也不得自行构造
用户身份、角色或确认声明。JTI 消费与训练计划写入使用同一个事务边界。

训练日执行接口：

- `GET /internal/training/v1/plans/{planId}/executions`
- `POST /internal/training/v1/plans/{planId}/days/{dayId}/execution`
