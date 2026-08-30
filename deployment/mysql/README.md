# MySQL 权限边界

健身多 Agent 平台的数据库账号按服务职责拆分：

| 服务 | 权限 | 用途 |
| --- | --- | --- |
| Gateway | `fitness_gateway_ro`，核心健身表 `SELECT` | 查询业务事实和固定经营指标 |
| Booking | 独立写账号 | 预约、改约、取消及预约 Outbox |
| Training | 独立写账号 | 训练计划、审核发布、执行记录及训练 Outbox |
| Customer Service | 独立写账号 | 客服工单及客服审计 |

Gateway 的只读权限脚本位于
`deployment/mysql/gateway-readonly-grants.sql.example`。该脚本只能由 DBA 使用管理员账号
在目标环境执行；真实密码必须来自 Secret Manager，不能写入仓库或发送给 Agent。

账号创建后，应分别执行以下验证：

1. 使用 Gateway 账号运行 `make gateway-it`，确认机构、课程和固定经营指标查询通过；
2. 在隔离测试表或回滚事务中验证 Gateway 账号无法 `INSERT`、`UPDATE`、`DELETE`、`ALTER` 和 `DROP`；
3. 使用 Booking/Training/Customer Service 各自账号执行对应真实受控验收，确认写权限没有错误复用；
4. 将账号、密码、连接串和轮换记录纳入 Secret Manager 与变更审计。

本地现有 Docker `fitness-mysql` 中的 `fitness` 账号目前是开发账号，拥有较宽权限，只能用于
本地联调，不应作为生产 Gateway 账号。
