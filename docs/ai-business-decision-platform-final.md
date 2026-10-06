# AI 自动经营决策平台（最终交付版）

本文描述仓库内已经实现的完整经营决策闭环。这里的“最终版”表示本次约定范围不再拆成后续开发阶段：从受控指标、异常检测、策略决策、人工审批、可靠执行到实验归因均有可运行实现。它不表示产品永远不再演进，也不表示已经替代生产上线审批。

## 1. 完整闭环

```text
受控经营查询
  → 可追溯指标快照
  → 稳健异常检测（median/MAD）
  → 决策案例与候选动作
  → 策略/风险/预算/影响范围门禁
  → SHADOW | 人工审批 | 低风险自治
  → 幂等动作 Outbox Worker
  → OBSERVING
  → 实验/结果观察与增量效果
  → EVALUATED → CLOSED
```

经营查询仍由 Java Gateway 的固定指标目录、签名身份、机构权限和审计约束。Python Agent 只消费已经受控并成功审计的日/周指标结果；快照写入失败不会伪造经营查询失败，且会留下结构化日志供补偿。

## 2. 数据和状态边界

- 目标与护栏：`agent_business_objectives`。
- 策略与自治边界：`agent_strategy_policies`，支持 `DISABLED / REPLAY / SHADOW / ADVISORY / AUTONOMOUS`。
- 可复算输入：`agent_metric_snapshots`，用目录版本和来源摘要保证幂等。
- 异常证据：`agent_anomaly_events`，保存检测器版本、期望值、偏差和证据。
- 决策账本：`agent_decision_cases`、`agent_decision_case_events`、`agent_decision_options`、`agent_decision_approvals`。
- 执行账本：`agent_action_runs`；业务副作用使用动作级幂等键，Worker 带租约、指数退避和最终失败状态。
- 当前内建安全动作：创建运营任务、创建教练跟进任务；站内提醒适配器只接受带明确用户与通知类型的受信任载荷。聚合指标不会生成缺少具体对象的提醒或业务写入。
- 实验和归因：`agent_experiments`、`agent_experiment_assignments`、`agent_outcome_observations`；分流稳定且不保存原始对象键。

案例状态由服务端状态机控制。诊断保存实际值、稳健基线、偏差与检测证据，并明确标记“未建立因果关系”。只有 `WAITING_APPROVAL` 可以被审批；审批窗口到期由动作 Worker 自动转为 `EXPIRED`。只有已授权动作可以入队；动作开始后进入 `EXECUTING`，成功进入 `OBSERVING`，耗尽重试进入 `EXECUTION_FAILED`。完整观察结果进入 `EVALUATED`，管理员复核后关闭。

## 3. 决策安全模型

策略引擎在执行前同时校验：模式、风险等级、允许动作、最大影响人数、预算、数据质量和最低置信度。`L4` 永不自动执行；`L2-L4` 和 `ADVISORY` 要求人工批准；`REPLAY/SHADOW` 只留决策记录，不创建动作。

自动候选只针对组织级、可幂等且能补偿性重放的动作。模型输出不是授权；审批请求带独立幂等 ID，同一 ID 不能改用另一方案或决定。所有管理员接口都验证签名 AgentContext、角色与机构范围。

## 4. 管理接口

统一前缀为 `/api/v1/admin/decisions`：

- `POST/GET /objectives`：创建和读取经营目标。
- `POST /objectives/{id}/policies`、`GET /policies`、`POST /policies/{id}/suspend`：管理策略。
- `POST /metric-snapshots`：接收治理后的指标快照并触发决策。
- `GET /cases`、`GET /cases/{id}`、`POST /cases/{id}/decision`：审查与批准/拒绝。
- `GET /actions`、`POST /actions/{id}/cancel|retry`：查看和恢复动作。
- `POST/GET /experiments`、`POST /experiments/{id}/status|assign`：实验生命周期和稳定分流；只有运行中的实验允许新增分组。
- `POST/GET /cases/{id}/outcomes`、`GET /cases/{id}/effects`、`POST /cases/{id}/close`：结果、增量效果和结案。

前端“经营决策”页面汇总目标、策略、案例、候选方案和动作执行状态，并提供审批入口。接口不会把审批能力交给普通会员或教练角色。

## 5. 运行

先执行迁移，再分别运行 API 和动作 Worker：

```bash
make agent-migrate
make agent-run
make agent-decision-action-worker
```

Worker 配置项为 `AGENT_DECISION_ACTION_WORKER_ENABLED`、`AGENT_DECISION_ACTION_BATCH_SIZE`、`AGENT_DECISION_ACTION_POLL_SECONDS`、`AGENT_DECISION_ACTION_MAX_ATTEMPTS` 和 `AGENT_DECISION_ACTION_WORKER_METRICS_PORT`。生产必须以独立进程部署，且同一环境可水平扩展；数据库的 `FOR UPDATE SKIP LOCKED` 和动作锁负责竞争控制。

Prometheus 采集 `business_decision_events_total` 以及通用 Worker 批次/条目指标。动作最终失败有 critical 告警；标签不包含机构、用户、案例或动作 ID。具体对象通过结构化日志、案例事件和动作账本关联。

## 6. 发布门禁

```bash
make agent-check
make web-check
make agent-migration-check
make production-config-check
```

预发布还必须在已迁移 PostgreSQL 上完成：策略四种执行模式、并发审批幂等、Worker 崩溃恢复、最终失败重放、实验分流稳定性、效果观察、机构越权拒绝和 Prometheus 告警演练。真实业务自治应先运行 `SHADOW`，比较至少一个完整经营周期后再由负责人批准提升模式；提升模式属于业务授权，不是代码默认行为。

## 7. “最终版”的含义

本版本已补齐从“回答发生了什么”到“发现问题、形成受控建议、执行、验证效果”的产品闭环，因此不是只有查询和追踪的演示骨架。未来仍可扩展更多指标、因果模型和业务动作适配器，但这些属于新增业务能力；当前架构不依赖一个未实现的 V2 才能完成基本决策闭环。
