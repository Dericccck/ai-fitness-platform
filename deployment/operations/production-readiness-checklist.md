# AI 健身多 Agent 生产就绪检查清单

本文用于预发布和生产上线前的变更评审。它描述执行顺序、通过标准和证据留存要求，
不负责自动发布，也不替代审批、值班和数据库变更流程。

当前项目明确不纳入本清单的功能：Linux/GPU OCR、短信、Push、人工转接、图片动作框选、
人工动作结构化标注，以及身体测量、疼痛、疲劳、训练反馈和教练阶段性调整等复杂训练扩展。

## 1. 版本和配置

- [ ] 代码提交已合并到目标发布分支，记录 40 位 Git SHA。
- [ ] Agent 镜像使用不可变 `@sha256` digest，`service_version == source_commit`。
- [ ] 执行 `make agent-release-manifest-check`，保存发布清单和配置契约摘要。
- [ ] 执行 `make production-config-check`，确认五个服务的生产模板通过。
- [ ] Secret Manager 已注入 DeepSeek Key、数据库密码、Redis/RabbitMQ 凭证、服务间 Token、
      Agent 确认密钥和认证 JWKS 地址；仓库、镜像和发布清单中没有 Secret 值。
- [ ] 当前约定范围关闭 OCR：`AGENT_RAG_OCR_BACKEND=disabled`。如果未来启用 OCR，必须单独走
      OCR 环境评审，不能只修改一个环境变量直接上线。

证据至少包括：发布清单、配置键名摘要、镜像扫描结果、Secret 注入记录和变更单编号。

## 2. 数据库权限和迁移

- [ ] DBA 已执行 `deployment/mysql/gateway-readonly-grants.sql.example` 的等价生产脚本，
      Gateway 账号只拥有所需健身表的 `SELECT`。
- [ ] DBA 已执行 `deployment/mysql/gateway-readonly-verify.sql.example`，保存脱敏的
      `SHOW GRANTS`、information_schema 和只读抽样结果；写入权限拒绝只在隔离克隆库验证。
- [ ] Booking、Training、Customer Service 使用不同的写账号，不能复用 Gateway 只读账号或彼此账号。
- [ ] 使用 `SHOW GRANTS` 保存脱敏权限证据，确认没有 `ALL PRIVILEGES`、`DROP`、`ALTER` 或跨库权限。
- [ ] 使用独立预发布库执行 `make gateway-it`，确认真实机构、课程和全部固定经营指标查询通过。
- [ ] 使用预发布快照执行 Booking、Training、Customer Service 迁移，确认数据库结构版本和应用版本匹配。
- [ ] 迁移前后执行表结构、关键索引、行数和字符集校验；长事务期间观察锁等待和连接池耗时。
- [ ] 生产关闭应用启动自动迁移：`*_SCHEMA_INIT_ENABLED=false`；迁移由独立 Job 执行并可审计。

Gateway 的真实只读联调和 Booking 的本地受控写入已完成；正式环境仍必须使用独立预发布库，
不能把本地 `fitness-mysql` 当成生产证据。

## 3. 备份、恢复和数据保护

### PostgreSQL

- [ ] 已配置加密备份、对象存储保留策略、跨可用区副本和访问审计。
- [ ] 在隔离数据库执行：

  ```bash
  make agent-postgres-backup-restore-check ARGS="--execute --rto-target-seconds 60"
  ```

- [ ] 记录备份开始/结束时间、备份大小、SHA-256、恢复开始/结束时间、恢复 RTO 和一致性校验结果。
- [ ] 说明逻辑备份只能证明一致性快照恢复；WAL/PITR 必须通过数据库平台能力另行演练。

本地验收基线（不能替代以上生产项）：2026-08-30 使用当前 `fitness-agent-postgres` 完成临时库备份恢复，
29 张 public 表、1733 行、备份 2712457 字节，恢复加逐表校验 RTO 为 3.71 秒，行数全部一致；临时库和容器内临时备份已清理。

### MySQL

- [ ] 使用 Secret Manager 注入备份账号，不把密码写进 shell 历史、脚本或日志。
- [ ] 使用 `--single-transaction`、`--routines`、`--events`、`--triggers` 和明确字符集生成一致性备份。
- [ ] 将备份加密后上传对象存储，并验证对象版本、生命周期、恢复权限和删除保护。
- [ ] 在隔离 MySQL 实例恢复备份，核对核心健身表、Agent 扩展表、中文数据和关键索引。
- [ ] 记录 MySQL 备份 RPO、恢复 RTO、数据校验结果和失败回滚步骤。

本地验收入口：`make gateway-mysql-backup-restore-check ARGS="--execute --rto-target-seconds 60"`；该命令只使用本地
`fitness-mysql` 的测试账号，临时恢复库命名空间固定为 `fitness_restore_`，完成后自动删除，不能替代生产备份账号和隔离实例。

具体执行命令、字符集核对、隔离目标保护和证据模板见
`deployment/operations/mysql-backup-restore-runbook.md`。

任何恢复演练都不得直接对生产源库执行 `DROP DATABASE` 或 `docker compose down -v`；
恢复目标必须是唯一的隔离实例或临时数据库。

## 4. 服务和消息可靠性

- [ ] Agent、Gateway、Booking、Training、Customer Service 的 `/health/live` 和 `/health/ready` 均正常。
- [ ] 就绪检查失败时，流量平台不会将实例加入服务发现；存活检查不能代替就绪检查。
- [ ] RabbitMQ 已配置持久化队列、账号最小权限、连接加密策略、死信和队列堆积告警。
- [ ] 在隔离窗口执行 Agent/Redis/PostgreSQL/RabbitMQ 单组件重启，并按
      `deployment/operations/recovery-drill.md` 记录恢复时间和遗留任务数量。
- [ ] 验证 Outbox、Inbox、确认 JTI 和通知幂等：重复投递不能重复创建预约、训练计划或通知。
- [ ] 验证网络中断、依赖 5xx、超时和重启后的重试上限，不允许无限重试或静默丢消息。

## 5. 监控、告警和值班

- [ ] Prometheus 已加载 Agent/Worker 告警规则，Metrics 端点只允许监控网络访问。
- [ ] OTel Trace 已发送到受控 Collector，HTTP 标签不包含用户 ID、机构 ID、Prompt 或原始 URL。
- [ ] Alertmanager 已接入企业内网 HTTPS 值班系统，并配置认证、收件人、升级、抑制和恢复通知。
- [ ] 隔离窗口验证 Agent 宕机、数据库不可用、RabbitMQ 堆积、Outbox 增长和审计失败告警。
- [ ] 每条 critical 告警都有负责人、响应时间目标、处置手册和恢复确认记录。

当前仓库已完成本地 Prometheus→Alertmanager 触发、恢复和抑制验收；企业值班系统接入仍需在目标环境执行。

## 6. 压测、灰度和回滚

- [ ] 在独立预发布环境使用脱敏数据执行 Agent、LLM、RAG、Gateway、MySQL、PostgreSQL 和 Redis 联合压测。
- [ ] 按并发阶梯记录 P50/P95/P99、错误率、Token 消耗、数据库连接池、Redis 命中率和 RabbitMQ 堆积。
- [ ] 确认限流阈值、模型超时、Tool 超时、重试上限和降级响应满足目标 SLO。
- [ ] 灰度期间先放少量机构流量，观察至少一个完整业务周期，再逐步扩大范围。
- [ ] 回滚时切换到上一份不可变镜像和配置清单，不在生产重新构建镜像。
- [ ] 回滚后执行：

  ```bash
  make agent-release-rollback-check \
    AGENT_RELEASE_URL="https://agent-staging.internal" \
    AGENT_EXPECTED_VERSION="<previous-git-sha>" \
    AGENT_EXPECTED_ENVIRONMENT="staging"
  ```

- [ ] 数据库迁移遵守向前/向后兼容原则；不可逆迁移必须先完成数据备份、灰度验证和人工审批，
      不允许把应用镜像回滚误当作数据库自动回滚。

## 7. 上线决策

只有以下条件同时满足，才可以将状态标记为“生产就绪”：

1. 版本、配置、权限、迁移、备份、恢复、监控、压测和回滚证据齐全；
2. 所有阻断项有明确负责人和截止时间；
3. 业务负责人、平台负责人、DBA 和值班负责人完成审批；
4. 上线窗口内有可执行的停止、回滚和故障升级路径。

当前仓库状态仍应记录为“本地企业化验收完成，具备进入预发布准备条件”。真实认证服务、生产集群、
对象存储、企业值班系统和正式 RTO/RPO 证据完成前，不得在简历或发布说明中写成“已生产上线”。
