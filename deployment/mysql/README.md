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

Booking、Training、Customer Service 的运行账号模板位于
`deployment/mysql/service-runtime-grants.sql.example`，对应的只读验收查询位于
`deployment/mysql/service-runtime-grants-verify.sql.example`。三类账号分别只拥有当前代码所需
的表级 DML 权限，不包含 `CREATE`、`ALTER`、`DROP` 或 `GRANT OPTION`。生产迁移必须使用独立的
短时迁移账号，应用启动时关闭 `*_SCHEMA_INIT_ENABLED`，不能为了让应用自动建表而给运行账号
追加 DDL 权限。

权限验收查询位于 `deployment/mysql/gateway-readonly-verify.sql.example`。它只读取
`information_schema` 和三张业务表的数量，检查当前身份、全局权限、九张允许读取的表以及
是否出现非 `SELECT` 权限；写入、修改、删除、建表和删表的拒绝测试只允许在隔离克隆库执行，
不会在生产业务表上执行危险语句。

账号创建后，应分别执行以下验证：

1. 使用 Gateway 账号运行 `make gateway-it`，确认机构、课程和固定经营指标查询通过；
2. 执行 `gateway-readonly-verify.sql.example` 保存脱敏结果，并在隔离克隆库验证 Gateway 账号无法
   `INSERT`、`UPDATE`、`DELETE`、`ALTER` 和 `DROP`；
3. 使用 Booking/Training/Customer Service 各自账号执行对应真实受控验收，确认写权限没有错误复用；
4. 将账号、密码、连接串和轮换记录纳入 Secret Manager 与变更审计。

本地 `fitness-mysql` 的 `fitness@%` 当前是开发账号并拥有 `fitness.*` 的 `ALL PRIVILEGES`，
这只说明本地联调方便，不是最小权限验收通过。不要在本地直接把这个账号改造成生产账号；应在
隔离克隆库创建模板中的独立账号，先执行 `SHOW GRANTS` 和对应服务验收，再由 DBA 在预发布环境
按同样流程落地。

本地现有 Docker `fitness-mysql` 中的 `fitness` 账号目前是开发账号，拥有较宽权限，只能用于
本地联调，不应作为生产 Gateway 账号。
