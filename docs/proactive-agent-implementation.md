# 主动式健身 Agent 最终实现说明

## 设计结论

本项目采用局部 Agent，而不是让 LLM 接管整个通知系统：定时器和领域事件负责发现事实，Agent 只负责需要语义判断的“是否打扰、怎样表达、建议下一步”。预约状态、权限、收件人、机构、幂等键、通知策略和业务写入始终由确定性代码控制。

## 运行链路

```text
Booking / Training 业务事务
  -> MySQL Outbox
  -> RabbitMQ fitness.domain.events
  -> Agent PostgreSQL Inbox（event_id 去重）
  -> 确定性事实通知，或主动候选
  -> 上下文快照（近期事件、历史反馈、七日通知量）
  -> 结构化 LLM 决策 + 安全规则 + 保守回退
  -> 决策审计 + 不可变消息快照
  -> Notification Outbox
  -> 用户授权、安静时间、频率限制、最终过期检查
  -> IN_APP 收件箱
  -> 用户反馈 / 打开对话 / 既有确认单执行
```

时间调度器只创建幂等候选，不直接调用模型或发送消息。RabbitMQ 只负责传输，Inbox 才是消费幂等边界；候选和 Notification Outbox 分别承担智能决策与最终投递的重试边界。

## 已实现能力

- 学员：训练中断关怀、连续跳过后的计划调整建议、周/周期复盘、取消预约后的重新安排帮助。
- 教练：单学员关注提醒、每日待办摘要。
- 运营：按显式订阅和本地时区生成机构日报。
- 客服：通过受控管理入口接入已有工单或外部来源，形成服务跟进候选。
- 互动闭环：接受、拒绝、稍后提醒、静音、有用/无用、打开 Agent 对话。
- 安全边界：禁止医疗诊断/治疗承诺、羞辱施压和模型自选主体；写业务只能进入现有确认流程。
- 可靠性：Outbox/Inbox、幂等键、租约恢复、有限退避、DEAD 查询与带操作者/原因的重放、投递尝试审计。
- 可观测性：候选决策、Worker 处理结果和通知投递均使用低基数指标；审计保存模型、Prompt、策略、输入输出摘要和证据引用。

## 进程与开关

```bash
make agent-proactive-worker
make agent-proactive-scheduler
make agent-proactive-decision-worker
make agent-notification-worker
```

生产环境需要显式启用以下变量，并运行 Alembic `20260917_0042`/`20260917_0043`、Training `V20260917_008` 和 `V20260917_009`：

```text
AGENT_PROACTIVE_WORKER_ENABLED=true
AGENT_PROACTIVE_SCHEDULER_ENABLED=true
AGENT_PROACTIVE_DECISION_WORKER_ENABLED=true
BOOKING_OUTBOX_PUBLISHER_ENABLED=true
TRAINING_OUTBOX_PUBLISHER_ENABLED=true
```

如果必须依赖 LLM 才允许发送，可设置 `AGENT_PROACTIVE_DECISION_MODEL_REQUIRED=true`；默认会在供应商故障时使用经过安全测试的保守模板，保证服务可用但不扩大权限。

## 运维接口

- `GET /api/v1/admin/proactive/inbox/dead`
- `POST /api/v1/admin/proactive/inbox/dead/{event_id}/replay`
- `GET /api/v1/admin/proactive/candidates/dead`
- `POST /api/v1/admin/proactive/candidates/dead/{candidate_id}/replay`
- `POST /api/v1/admin/proactive/candidates`
- `PUT /api/v1/admin/proactive/subscriptions`

所有接口都要求签名 AgentContext 和管理员角色；机构管理员只能操作自身机构，平台管理员可跨机构。重放不绕过原有幂等、偏好和安全检查。

## 外部渠道边界

当前真正完成并可验证的是 IN_APP。短信、Push、微信不能在没有供应商账号、回执协议和用户渠道授权时伪装为“已送达”。代码中的渠道适配器与投递尝试表已将该边界隔离；接入真实供应商时需提供稳定幂等键、供应商 message ID、超时/重试策略和退订同步，然后才能加入生产渠道集合。
