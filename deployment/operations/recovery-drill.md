# 健身 Agent 重启与恢复演练手册

本文只用于本地或隔离测试环境。执行前确认没有正在进行的预约、训练计划审核发布、客服工单或知识库写入。
生产环境必须由值班人员按变更流程执行，禁止直接执行 `docker compose down -v`。

## 1. 演练前基线

```bash
make agent-recovery-check
```

检查通过后记录 Agent `/health/ready`、Prometheus、RabbitMQ 管理台和 PostgreSQL 当前状态。
`stale_inbox` 与 `stale_notification_outbox` 应为 `0`；如果不是 `0`，先处理遗留任务，不要把旧故障混入本轮结果。
数据库刚重启时 Agent 的 Checkpoint 连接池可能在短暂窗口内返回 503，检查脚本默认会在 30 秒内重试；超过窗口
仍未就绪才视为故障。

## 2. 服务恢复演练

每次只重启一个组件，观察 10～30 秒后再执行下一项：

```bash
docker restart fitness-agent-redis
make agent-recovery-check

docker restart fitness-agent-postgres
make agent-recovery-check

docker restart fitness-agent-rabbitmq
make agent-recovery-check
```

Agent API 进程和 Java Gateway 不由本手册自动停止。需要验证应用进程重启时，在 IDEA 中停止并重新启动对应
启动项，等待 `/health/ready` 恢复为 `ready` 后再次执行 `make agent-recovery-check`。

## 3. 重点观察结果

- Agent 存活探针可以快速恢复；依赖未恢复时就绪探针必须保持非就绪，不能接收业务流量。
- Redis 恢复后，临时会话锁和限流键可以正常读写；长期业务事实不能依赖 Redis 单独保存。
- PostgreSQL 恢复后，LangGraph Checkpoint 表仍可访问，已有会话不会因为 Agent 进程重启而丢失。
- RabbitMQ 恢复后，消息应重新连接；Inbox 中的 `PENDING`、`RETRYABLE_FAILED` 或租约超时的 `PROCESSING`
  事件应由 Worker 继续领取，不能出现重复通知。
- RabbitMQ 网络短暂不可达时，Worker 日志应出现带 `attempt` 和 `delay_seconds` 的
  `proactive_rabbitmq_reconnect_scheduled`，重连间隔按上限指数退避；网络恢复后不应持续忙循环。
- Prometheus/结构化日志中不应出现机构 ID、用户 ID、确认凭证或数据库密码；失败应能按时间窗口定位。

## 4. 通过标准和收尾

```bash
make agent-recovery-check ARGS=--strict-stale
```

通过标准是 Agent/Gateway 存活、Agent 就绪、PostgreSQL/Checkpoint/Redis/RabbitMQ 均正常，且没有超时锁定的
Inbox 或通知 Outbox。演练结束后再次执行该命令，并确认 RabbitMQ 队列无异常堆积、Prometheus 告警恢复、日志中
没有未处理的 `PROACTIVE_EVENT_PROCESSING_FAILED`。

本手册不替代生产灾备方案；生产还需要备份加密、跨可用区副本、WAL/PITR、明确 RTO/RPO、Alertmanager 和定期
恢复演练。

## 5. RabbitMQ 网络中断演练（需人工确认）

该步骤会影响共享 RabbitMQ 容器及其所有连接，不能在有真实业务消息时执行。先完成第 1 节基线，并确认当前只
运行本地测试 Worker；演练期间不要执行预约、训练计划发布或客服工单真实写入。

```bash
# 只在本地隔离环境执行；该参数会暂停共享 RabbitMQ 容器约 8 秒
make agent-proactive-reliability-live-check \
  ARGS="--execute --network-recovery --network-pause-seconds 8"

# 恢复后重新检查基础设施与遗留任务
make agent-recovery-check ARGS=--strict-stale
```

通过标准：Worker 没有永久退出；RabbitMQ 恢复后临时事件能够被消费；Inbox 的同一 `event_id` 仍只有一条；
每个收件人的通知 Outbox 仍最多一条；恢复检查通过且没有异常积压。`--network-recovery` 是显式的共享容器级
故障注入，默认命令不会执行；不要在有真实业务消息或其他消费者时运行。
