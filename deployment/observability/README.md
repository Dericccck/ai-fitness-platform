# 健身 Agent 可观测性与告警

## 本地启动

先启动 Agent API（默认 `8090`），再启动观测组件：

```bash
docker compose -f deployment/docker-compose.agent-infra.yml --profile observability up -d agent-prometheus agent-otel-collector
```

Prometheus 地址为 <http://127.0.0.1:9090>，Agent Metrics 地址为
<http://127.0.0.1:8090/metrics>。OpenTelemetry Collector 继续负责 Trace 接收和本地调试输出，
Prometheus 负责抓取 Metrics；两条链路职责不同，不用 Trace 代替指标告警。

## 告警范围

`fitness-agent-alerts.yml` 当前覆盖：

- Agent 进程不可用、HTTP 5xx 比例、P95 延迟和请求堆积；
- Operations 审计失败；
- Memory、会话摘要、通知等后台维护批次失败；
- 站内通知投递失败。

提交前可运行 `make observability-check`，它会检查告警名称集合、PromQL 中引用的指标是否在 Agent 中定义、告警是否具备
固定低基数标签和说明，以及 Prometheus 是否配置 Agent/Worker 抓取任务。启动本地 Prometheus 后，可运行
`make observability-live-check`，只读调用 Prometheus API 验证规则已经加载。

Agent 开启 TruLens 在线 OTEL 导出后，导出批次成功/失败会进入
`fitness_agent_trulens_export_batches_total`，失败会触发 `FitnessAgentTruLensExportFailed`；该指标只包含固定
状态，不包含 Trace ID、用户 ID 或机构 ID。在线导出必须同时打开 OTEL、TruLens 和 metadata/evaluation 采集，且
评测库必须与业务库、Checkpoint 库分离。

告警表达式的触发和恢复可运行 `make observability-rule-test`。该命令使用 Prometheus `promtool` 的合成时间序列，验证
Agent 宕机告警在持续异常后触发、恢复后解除，以及 Operations 审计失败告警的计数器窗口逻辑；不会停止本地服务、写入业务
数据库或发送外部通知。

Alertmanager 路由和恢复通知可运行 `make observability-alertmanager-check`。该命令启动唯一临时 Alertmanager 容器和宿主机临时
webhook，向 Alertmanager 注入一条合成 critical 告警，验证 `firing` 和 `resolved` 两次回调后清理容器。通知只在本机验收端口
流转，不发送短信、Push、邮件或外部值班通知；生产环境必须替换为带认证的企业内网 HTTPS 接收器。

告警标签只使用 `severity`、`service` 等固定值，不包含机构 ID、用户 ID、工单 ID、确认单 ID 或
`request_id`。具体请求必须通过告警时间窗口、结构化日志和 Trace 关联，避免把业务标识直接放进
Prometheus 时间序列。

## 生产接入要求

本地配置只用于验证规则加载和 Metrics 抓取。生产环境还必须：

1. 使用服务发现或内网地址替换 `host.docker.internal`；
2. 给 Prometheus 配置持久化存储、容量保留策略和访问控制；
3. 将 Prometheus 告警转发到企业 Alertmanager/值班系统，并为 `critical` 和 `warning` 配置不同升级策略；
4. 将告警与既有 OTLP Trace、结构化日志、数据库和 RabbitMQ 监控关联；
5. 对告警规则做值班演练，确认 Agent 宕机、审计失败和通知失败都能在预期时间内发现。

当前运行时验收只验证 Prometheus 规则加载，不自动制造故障、不停止服务，也不验证外部 Alertmanager 的收件人路由。
生产接入 Alertmanager 后，还需要在隔离窗口演练告警触发、抑制、恢复和升级策略。

## TruLens 评测报告

离线确定性评测和 Judge 评测都输出运行级报告，包含 `run_id`、输入文件 SHA-256、案例/领域分布、指标平均分、
指标覆盖率和代码/Prompt/模型/知识库/图版本覆盖率。可通过 `ARGS` 保存报告：

```bash
make agent-trulens-eval ARGS="--no-persist --report var/evaluations/trulens-latest.json"
```

使用 `--traces` 时，输入必须是已脱敏且包含根 Span 输入/输出、`record_id`、`trace_id` 及五类版本关联字段的真实
Trace；metadata 模式只用于排障，不能伪装成可评测 Record。缺字段会直接失败，不会被静默跳过。

## 本地独立 TruLens PostgreSQL

本地需要验证在线导出时，可以启动独立评测库，不要复用 `fitness-agent-postgres`：

```bash
make trulens-infra-up
TRULENS_DATABASE_URL=postgresql+psycopg://fitness_eval:fitness_eval_local@127.0.0.1:5434/fitness_agent_eval \
  make trulens-postgres-check ARGS="--execute"
```

该容器使用独立卷 `fitness-agent-trulens-postgres-data`、5434 端口和 `fitness_eval` 本地验收账号。示例密码只适用于本地，
生产必须由 Secret Manager 注入并改用独立托管数据库、专用权限账号、加密、备份和访问控制。

本配置不会自动发送短信、Push 或外部通知，也不会改变健身业务的写入确认流程。
