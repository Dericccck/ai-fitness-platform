**健身项目业务流程逐场景详解**

核对日期：2026 年 10 月 16 日；第 12 节知识库入库流程补充核对于 2026 年 10 月 18 日。项目的业务客户端是小程序，本文统一以小程序的学员端、教练端、AI 助手、确认卡和消息中心描述交互。依据当前工作区的 Python Agent、Java Gateway、预约服务、训练服务和客户端接入接口编写，包含当前未提交的实现。

小程序页面名称用于说明后端接口的接入位置；本文没有验证实际小程序页面联调，也不把页面接入方案当成已完成验收。登录和身份依据可信业务认证，用户身份、机构和角色由服务端确定；不预设特定的客户端登录 SDK。本文没有启动业务服务验证运行环境，也没有修改业务实现。

每个场景都按“谁触发 → 识别什么 → 查询什么 → 是否确认 → 怎样写入 → 怎样通知 → 各种分支”展开。文中的“当前缺口”表示代码尚未完成的步骤；它与已有代码的正常流程分别说明。

**场景索引**

| 场景 | 入口 | 核心结果 | 后续主动候选 |
| --- | --- | --- | --- |
| 取消预约 | 用户对话 | 取消原预约，退回一个课时 | 学员 `APPOINTMENT_FOLLOW_UP` |
| 预约成功 | 用户创建预约 | 新预约成功，扣减一个课时 | 当前不由创建事件生成跟进候选 |
| 预约改约 | 用户对话 | 原预约换教练或时间，课时不变 | 当前不由改约事件生成跟进候选 |
| 跳过训练 | 用户记录一个训练日 | 执行结果为 `SKIPPED` | 学员关怀、教练关注 |
| 完成训练 | 用户记录一个训练日；系统判断周期记录情况 | 日结果为 `COMPLETED`；满足条件发周期事件 | 周期复盘候选 |
| 个性化记忆提取并待确认 | 健身指导对话 | 生成待批准的 Memory 候选 | 使用记忆候选审批链，不走主动决策候选链 |
| 主动训练关怀 | 跳过事件或定时扫描 | 询问训练安排是否需要帮助 | `TRAINING_ADHERENCE` |
| 计划调整建议 | 定时扫描跳过事件 | 建议调整；正式方案需授权草案、审核、发布 | `TRAINING_PLAN_MISMATCH` |
| 运营日报 | 定时扫描已启用的运营订阅 | 机构事件摘要提醒 | `OPERATIONS_DAILY_DIGEST` |
| RAG 与工具查询 | 用户提问 | 已授权知识与实时业务事实组合回答 | 普通查询本身不生成主动候选 |
| 教练为学员生成训练草案 | 小程序教练端对话 | 师生关系授权、目标学员训练记忆、RAG、草案预览/确认创建 | 提交审核和发布分别产生后续业务通知 |
| RAG 知识库文档入库 | 管理员上传文件、审核和批准 | 格式解析/OCR、质量门禁、父子分块、子块向量化、版本发布 | 不由上传自动生成主动候选 |

**先把所有场景共用的三个流程讲清楚**

**A. 用户对话如何进入领域子图**

1. 用户在小程序 AI 助手输入框输入并点击发送。小程序携带业务登录凭证请求可信后端入口；认证/接入层根据当前账号和机构权限提供短期签名 `X-Agent-Context`，再请求 Agent 对话接口。小程序不能自行签发身份，也不能通过修改用户 ID、机构或角色取得权限。
2. Agent 验证 AgentContext 的签名、算法、密钥标识、用户 ID、机构集合、角色、签发时间、过期时间和最大有效期等。缺失、篡改、过期或字段不合法，入口拒绝请求。
3. 验证成功后执行对话配额检查，并根据“用户 + 机构范围 + 角色 + conversation_id”生成隔离的 Thread ID。服务端据此恢复 Checkpoint；不同主体不能仅凭相同 conversation_id 读取彼此历史。
4. 如果该线程有有效会话摘要，按线程和用户读取、解密并校验摘要。摘要与近期消息组成对话上下文，再追加本次输入。默认达到消息数量阈值后才生成摘要，摘要不能替代最新业务查询。
5. 默认 `hybrid` 路由先执行规则。明确写意图、明确高置信度请求等可以规则直达；其他情况调用 JSON 分类模型。
6. 分类模型得到本次消息、上一轮 `route`、最近默认 6 条 user/assistant 消息，以及 system prompt 里的合法领域—意图目录。没有单独传 `previous_intent`；这里是 6 条消息，不是 6 轮。
7. 后端校验模型返回的领域和意图组合、置信度等。置信度不足则澄清；模型超时或结果不合法，按规则、上下文继承或澄清策略处理。一次最多处理两个意图；两个写操作需要拆开。查询与一个写操作并存时，先查询再准备写确认。
8. 根据 route 调度同一个 Python 服务内的四个 LangGraph 子图：健身指导、预约、客服、经营分析。
9. 子图构造统一 system prompt，其中有通用规则、领域规则、当前 route/intent；模型可见工具是“领域白名单 ∩ 意图白名单 ∩ 已注册工具”。工具 Schema 告诉模型工具名称、描述、参数结构。
10. 模型返回文本或结构化 `tool_calls`。只读调用经过参数、角色、机构及资源权限校验后执行，结果追加为 tool 消息，再交模型解释或继续查询。写调用进入下面的确认流程。

当前创建、改约、取消预约和训练日记录等意图会指定对应工具。这个约束保证明确写意图进入确认路径，但没有完成“先把自然语言定位成真实业务 ID”的确定性查询编排。格式合法的 ID 也不等于已经查证的真实业务事实。

代码依据：[对话入口](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/api/routes/agent.py:102)、[身份验证](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/infrastructure/agent_context.py:73)、[分类输入](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/agent/routing.py:481)、[多意图处理](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/agent/routing.py:831)、[工具交集](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/agent/supervisor.py:1666)。

**B. 写操作如何确认和执行**

1. 模型返回写工具请求后，服务端再次检查领域、意图和注册工具边界。一轮写调用必须单独形成确认动作。
2. 机构参数绑定到已验证身份中的唯一机构；纯学员上下文的主体字段绑定为本人。多机构上下文无法唯一绑定时停止，不让模型自行挑机构。
3. 根据工具 Schema 校验必填字段、类型、范围及格式。缺少形成精确动作所需的参数时，返回澄清，不生成含糊确认单。
4. 对需要资源快照的训练动作，确认准备阶段通过 Gateway 读真实计划。当前预约创建、改约、取消的确认策略没有查询预约资源快照，主要进行参数规范化。
5. 规范化动作确定工具、操作、机构、资源、执行参数和展示摘要，计算参数哈希。执行参数 AES-GCM 加密后保存到确认表，连同身份授权快照、状态、期限等形成同一张确认单；默认有效期 600 秒。
6. 小程序端拿到确认 ID、摘要、期限等并展示确认卡。LangGraph 在确认节点 interrupt 暂停。当前取消确认摘要主要是机构 ID、预约 ID、原开始时间；完整教练和课程名称的查询与展示尚需接通。小程序应清楚区分等待确认、执行中和执行结果。
7. 用户在小程序批准或拒绝时，提交确认 ID、决定和独立的 `decision_request_id`。后端重新验证 AgentContext、确认归属、角色和机构范围快照、状态及期限；批准决定先落库。
8. 批准后，服务端恢复原线程，读取已批准单，生成或复用并持久化 JTI，解密原执行参数，签发绑定用户、工具、操作、资源、请求 ID、参数哈希、请求体哈希及有效期的确认 Token。
9. Python Tool Registry 调注册 handler。预约、训练等跨 Java 服务的工具通过 handler 请求 Gateway 对应 HTTP 接口；Gateway 验证内部服务凭证、AgentContext、确认 Token 和机构范围，再转发已验证的操作者和确认声明。Memory 保存/撤销工具则直接调用 Python MemoryService，在 Agent 自有仓储内执行。
10. 对预约、训练这类业务，Java 做最终数据检查、锁定、幂等和事务写入，JTI 消费与业务修改同事务提交。Memory 工具使用 Python 服务的稳定请求 ID 等机制保证幂等。确认 Token、JTI 和参数密文留在服务端，不交给小程序；恢复执行使用已经批准的参数，不重新让模型生成另一套。
11. 成功后记录确认执行成功；失败记录执行状态。拒绝或过期不会执行业务。已成功的重复批准返回已有结果；正在执行的重复批准提示处理中。
12. 网络或进程异常可能使业务结果暂时未知。对账 Worker 可以把长期运行单标记为 UNKNOWN，并查询下游预约 operation；查到成功才补写成功，查不到不会凭空宣布已完成。重试仍依赖稳定业务请求 ID，不能再次退课时或扣课时。

代码依据：[确认准备与恢复](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/confirmation/service.py:60)、[批准 API](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/api/routes/confirmations.py:106)、[Token 字段](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/confirmation/token.py:51)、[执行节点](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/agent/supervisor.py:1133)、[对账](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/confirmation/reconciliation_worker.py:43)。

**C. 业务事件如何变成事实通知或主动建议**

```text
Java 业务事务：业务写入 + JTI 消费 + 幂等/审计 + 业务 Outbox
  → 发布 Worker → RabbitMQ 队列
  → Agent 消费者验证事件，写 PostgreSQL Inbox，提交后 ACK
  → Inbox 事件 Worker
      → 确定性事实通知 Outbox
      → 或/同时：主动候选 → 主动决策 Worker → 消息快照 + 通知 Outbox
  → 通知 Worker 检查偏好、免打扰、频率
  → IN_APP 站内收件箱 → 小程序消息中心读取 → 用户阅读或反馈
```

发布成功表示 broker 接收并可以路由，不表示下游已处理。RabbitMQ 允许重投；Inbox 按 event_id 去重，通知和候选还各有去重键。事件 Worker 生成通知/候选与标记 Inbox 已处理在同一事务中完成，失败可以重试。

主动候选初始 PENDING；决策 Worker 领取后 PROCESSING。它读取 Agent 自有的近期事件、用户反馈计数和近 7 天已发布通知数，再调用专用 JSON 决策模型。当前后台没有加载原对话线程，也没有查询实时空闲课表。主动决策输入中的 `facts.agent_context` 是判断上下文，不是登录用的签名 AgentContext。

| 决策 | 当前处理 |
| --- | --- |
| IGNORE | 候选改 IGNORED，不发送 |
| DEFER | 改 DEFERRED，更新下次处理时间，到时重新判断；原有效期不延长 |
| NOTIFY / ASK_USER / PROPOSE_ACTION / ESCALATE_TO_COACH | 保存消息快照，写通知 Outbox，改 NOTIFICATION_QUEUED |

后端校验 JSON、证据引用、联系类置信度和消息内容。默认模型失败可使用固定安全文案；配置模型必须成功时进入重试，默认最多 5 次，超限 DEAD。`ESCALATE_TO_COACH` 当前仍使用候选绑定的收件人，不会自动换成另一个教练 ID。

通知 Worker 再检查通知开关、安静时间和最小发送间隔。允许时使用事实模板或已存消息快照，事务内写站内通知并完成 Outbox；主动候选随后标记 COMPLETED。COMPLETED 表示这条候选的处理结束，用户阅读另记。未配置偏好时默认开启，没有默认免打扰或最小间隔；当前实际投递渠道是 IN_APP。

用户在小程序消息中心点击主动通知的“打开助手”，先标记阅读，再记录 OPENED，然后进入 AI 助手并预填建议消息。用户发送时带 proactive_candidate_id，后端校验本人和机构归属，记录 OPENED_CHAT，加入候选背景后走对话流程。需要修改业务时仍要新的确认单。当前 IN_APP 是服务端站内消息，小程序通过通知接口读取；微信订阅消息等外部送达渠道需要另行接入。

代码依据：[Inbox 消费](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/proactive/rabbit_consumer.py:138)、[事件分支](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/proactive/worker.py:106)、[主动决策](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/proactive/decision_worker.py:99)、[通知投递](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/notifications/worker.py:142)、[小程序可接的反馈接口](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/api/routes/notifications.py:242)、[候选进入对话](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/api/routes/agent.py:123)。

**1. 取消预约：从输入到退课时，再到重新预约关怀**

用户示例：“帮我取消明天下午的游泳课。”

正常流程可以这样描述：

1. 用户发送后走公共入口，识别为 `BOOKING / CANCEL_BOOKING`，进入预约子图。
2. 取消意图白名单包括预约列表与取消预约。当前代码指定取消工具，工具需要 `organizationId、appointmentId、expectedStartTime`。最后一个字段是原预约精确开始时刻。
3. 如果先前对话已有真实查询结果，可以据此形成取消参数；如果只有课程名和模糊时间，当前没有保证自动查到对应预约，缺参则要求补充具体预约。
4. 参数有效后绑定机构、校验并保存加密确认单，小程序显示确认卡。当前这一步没有读预约数据库做取消业务预检。
5. 用户批准后恢复执行，签发确认 Token，经 Registry 和 Gateway 进入 Java 取消服务。
6. Java 开事务，取得 requestId 命名锁，先找相同请求已有取消结果；有成功结果就返回，防止重复退课时。
7. 没有已有成功结果时，读取本机构的原预约，检查存在、未删除、可取消状态、原课程未开始、原开始时间与确认参数一致、操作者有权处理该学员、学员仍属于机构。
8. 依次取得教练日期资源行锁、学员日期资源行锁，锁原预约行重新核对，再锁合同。这里教练/学员日期锁是资源表行锁，只有请求锁使用命名锁。
9. 在同一事务里恢复合同一个课时、增加合同版本；原预约设 deleted=1；更新同合同后续预约的兼容 amount 快照；消费 JTI；写取消幂等记录、审计和 `APPOINTMENT_CANCELLED` Outbox。
10. 提交成功，Agent 根据真实结果记录执行成功并返回。任何中途失败都整体回滚，不会留下“课时已退但预约没取消”的部分成功事务。
11. 取消事件经过公共异步链，给学员和教练生成固定取消事实通知。取消还会抑制同一预约尚未投递的旧创建/改约提醒。
12. 同时给学员创建 `APPOINTMENT_FOLLOW_UP`，有效 24 小时，默认立即可处理。主动决策可以忽略、延期，或询问是否需要重新安排；默认建议动作 `BOOK_APPOINTMENT`。用户进入助手并发送后，才开始新的预约查询和确认流程。

各种情况：

| 情况 | 当前行为 |
| --- | --- |
| 没有真实预约 ID、原开始时刻 | 参数无法形成确认动作时澄清；没有完整自动定位编排 |
| 同日下午多节游泳课、同名教练 | 当前没有完善的候选列表选择流程，不能假定取第一条正确 |
| 预约不存在或已删除 | Java 拒绝；不退课时 |
| 已开始或状态不允许取消 | 拒绝；可取消状态目前是旧编码 0/1/3 |
| 用户批准期间预约被修改 | expectedStartTime 或锁内重查不一致，拒绝并要求重新查询 |
| 用户没有资源权限、学员已不属于机构 | 拒绝，即使参数格式合法或确认单曾存在 |
| 重复批准、重复业务请求 | 确认决定 ID、业务 requestId 和 JTI 各自约束对应阶段 |
| 拒绝、确认过期、执行前撤销 | 不进入业务写入 |
| 请求锁争抢、数据库或网络失败 | 返回失败/处理中，按状态重试或对账，不宣布取消成功 |
| 取消成功但 MQ/通知暂时不可用 | 取消事务已提交，Outbox 后续重试；业务成功与通知投递分别记录 |
| 用户关闭重新预约提醒 | 跟进通知可以被抑制，已完成的取消业务不回滚 |

当前缺口需要单独记住：名称、模糊日期到真实预约 ID 的查询定位，以及确认卡前的真实取消预检，尚未形成完整链。已有预约列表可按机构、用户、时间查候选，返回课程名、教练 ID、预约 ID 等，但不支持教练姓名过滤，也没有教练姓名返回。补齐时应由只读查询工具接收名称/时间条件并返回真实候选，唯一候选继续、多候选让用户选择，不能让模型猜 ID。

三个预约场景还共用时间处理边界：无时区的 datetime 在参数层按 Asia/Shanghai 解释，再转成 UTC；带偏移量的时间按其对应时刻转 UTC。这个转换不负责解析“明天”“后天”。当前预约领域 prompt 没有注入当前日期，也没有确定性的相对日期解析流程；模型生成的精确日期仍需真实查询和清晰确认来约束。代码依据：[时间规范化](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/agent/fitness_tools.py:38)、[领域 prompt](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/agent/supervisor.py:1475)。

代码依据：[取消参数](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/agent/fitness_tools.py:286)、[Java 取消校验与锁](/Users/a1-6/Desktop/fitness-backend/fitness-booking-service/src/main/java/com/shuyiwa/fitness/booking/service/BookingService.java:253)、[取消事务](/Users/a1-6/Desktop/fitness-backend/fitness-booking-service/src/main/java/com/shuyiwa/fitness/booking/repository/BookingRepository.java:374)、[取消双分支](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/proactive/worker.py:148)。

**2. 预约成功：创建预约、扣课时和事实通知**

用户示例：“帮我预约明天下午三点的游泳课。”这里“预约成功”指创建业务提交成功，不是模型说了一句已经预约。

1. 用户发送后识别为 `BOOKING / CREATE_BOOKING`，代码指定创建预约工具。
2. 创建参数需要真实机构、学员、合同、教练、课程 ID，以及精确开始和结束时间。此时没有新预约 ID，业务成功时由服务端生成。
3. 创建意图允许课程、合同、预约列表和可用性查询，但当前没有保证先完成这些查询再调用写工具。参数缺失或无法规范化时澄清；格式合法但未经查询证实的 ID 仍可能进入确认单，最终由 Java 业务检查拒绝。
4. 已有 `booking.availability.check` 是只读预检：在已知教练 ID 和精确时间后，可以检查过去时间、教练冲突、非营业日、请假等，返回 available、reason_codes 和 conflicts。它不锁住时间、不扣课时、不保证后续创建成功，也未覆盖全部合同和学员冲突规则。
5. 参数规范化后生成创建确认单。用户批准再执行 Java 创建，不因“available=true”提前创建。
6. Java 检查字段、开始结束顺序、开始不早于当前、时长不超过 8 小时、机构及确认范围、学员访问权限、机构成员关系、教练属于机构。
7. requestId 命名锁下查已有结果，再取得教练日期行锁、学员日期行锁和合同行锁。
8. 锁内检查合同正常、日期在有效期内、至少一个剩余课时、课程与合同一致、课程存在且启用、机构营业、教练未请假、教练和学员均无重叠预约。
9. 同一事务生成新 appointmentId；合同课时 -1、版本 +1；插入旧编码 status=1 的预约成功记录；消费 JTI；写幂等、审计和 `APPOINTMENT_CREATED` Outbox。
10. 真实成功结果返回 Agent 和小程序。异步事件生成学员和教练的固定预约成功通知，当前创建事件不生成重新预约跟进候选。

各种情况：

| 情况 | 当前行为 |
| --- | --- |
| 只有“游泳课”“张老师”，没有真实合同/课程/教练 ID | 没有完整自动名称解析；不能保证一步创建 |
| 预检通过后时间被其他人占用 | 最终锁内校验冲突，创建回滚 |
| 合同失效、课时不足、课程不匹配或停用 | 拒绝；不扣课时、不插入成功预约 |
| 非营业日、教练请假、学员/教练时间冲突 | 拒绝；需另选时间或教练 |
| 合同版本/扣减条件变化 | 条件更新冲突，整体回滚 |
| 重复确认或成功请求重试 | 返回同一成功事实，不再次扣课时或新建预约 |
| 创建成功后很快改约或取消 | 更高版本事件会抑制尚未投递的旧成功提醒 |
| 通知未读或投递延迟 | 不影响已创建预约；阅读另记 |

代码依据：[创建参数](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/agent/fitness_tools.py:190)、[可用性检查](/Users/a1-6/Desktop/fitness-backend/fitness-core-gateway/src/main/java/com/shuyiwa/fitness/gateway/service/FitnessToolService.java:124)、[创建业务](/Users/a1-6/Desktop/fitness-backend/fitness-booking-service/src/main/java/com/shuyiwa/fitness/booking/service/BookingService.java:88)、[创建事务](/Users/a1-6/Desktop/fitness-backend/fitness-booking-service/src/main/java/com/shuyiwa/fitness/booking/repository/BookingRepository.java:298)。

**3. 预约改约：保留预约 ID，调整教练和时间**

用户示例：“把明天下午的游泳预约改到后天下午四点。”

1. 输入识别为 `BOOKING / RESCHEDULE_BOOKING`，代码指定改约工具。
2. 参数需要原 appointmentId、原 expectedStartTime、新 coachId、新 startTime/endTime。v1 沿用原课程和合同，只调整教练与时间。
3. 模糊描述没有真实原预约引用时，当前不能保证自动定位。已有可用性工具支持 excludeAppointmentId，可以查询冲突时排除原预约；查询结果仍不能替代最终业务校验。
4. 服务端绑定机构，保存含原开始时间与新教练/时间的加密确认动作，展示确认卡。批准后用这套参数执行。
5. Java 验证新时间有效、开始非过去、时长不超过 8 小时、机构和确认范围；requestId 命名锁下查重复结果，锁原预约行。
6. 检查原预约存在、状态允许、原开始时间未变、学员访问权限、成员关系、新教练机构归属。
7. 取得新教练日期资源行锁、学员新日期行锁、合同行锁，核对原合同正常且新日期有效、原课程启用、营业/请假、学员和新教练无冲突；冲突查询排除当前预约。
8. 同一事务更新原预约的教练和起止时间、操作者；消费 JTI；写改约幂等、审计和 `APPOINTMENT_RESCHEDULED` Outbox。
9. 原 appointmentId 保留，课时不再次扣减，也不退还。异步事实通知发给学员与新教练，当前没有由改约事件生成 APPOINTMENT_FOLLOW_UP。

各种情况：

| 情况 | 当前行为 |
| --- | --- |
| 想把游泳直接换成另一课程/合同 | v1 改约不支持，需要另行设计业务动作 |
| 原时间已经变化 | expectedStartTime 不匹配，拒绝并重新查询 |
| 新教练不属于机构或无权限 | 拒绝 |
| 新时间冲突、请假、非营业日、合同新日期失效 | 整笔回滚，保留原预约事实 |
| 查询冲突时把自己也算进去 | 已有 excludeAppointmentId 支持排除当前预约 |
| 改约给另一教练 | 当前通知目标只有学员与新教练，原教练的独立通知尚未实现 |
| 原课程已经开始 | 当前 reschedule 没有单独校验原开始时刻，只校验可改状态和新时间；不能承诺和取消具有同样未开始限制 |
| 改约通知排队期间再次改约/取消 | 旧待投递提醒按更高聚合版本抑制 |

代码依据：[改约参数](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/agent/fitness_tools.py:243)、[改约业务](/Users/a1-6/Desktop/fitness-backend/fitness-booking-service/src/main/java/com/shuyiwa/fitness/booking/service/BookingService.java:171)、[改约事务](/Users/a1-6/Desktop/fitness-backend/fitness-booking-service/src/main/java/com/shuyiwa/fitness/booking/repository/BookingRepository.java:337)。

**4. 跳过训练：记录一个训练日为 SKIPPED**

用户示例：“今天这次训练不做了，帮我记为跳过。”当前工具操作的是某个训练日，整个计划没有一键“跳过”动作。

1. 用户发送后识别为 `FITNESS_COACHING / RECORD_EXECUTION`，进入健身指导子图。
2. 意图允许计划查询、日执行记录查询和记录执行工具，当前指定记录写工具。参数必须有真实 plan_id、day_id、status=SKIPPED，可带最多 1000 字备注。
3. 只有“今天的训练”而没有计划和训练日 ID 时，当前没有保证自动定位；参数不足走澄清。跳过计划、暂停周期和记录一个训练日跳过也不能视为相同动作。
4. 这个训练动作配置了 resource_required。确认前先按已经提供的 plan_id 经 Gateway 读取真实计划，取得机构、版本和资源快照；再把 day_id、目标 SKIPPED、备注与快照绑定到确认哈希，加密保存执行参数。
5. 展示确认卡并暂停。用户拒绝或过期不写；批准后恢复，签发确认 Token，调用 Java 训练服务。
6. Java 检查计划在授权范围且已发布、操作者是该计划本人学员、day_id 属于计划、路径与参数一致、状态合法、备注长度和确认范围。教练、管理员不能代替学员提交其日执行结果。
7. 执行日期由服务端取当天，当前参数不允许用户任意回填历史执行日期；实际时区取决于训练服务运行配置。
8. Java 锁训练日行，没有执行记录则插入，已有记录则更新并增加记录版本。同 requestId 重试返回原事实；用一个新的合法确认请求可以更新原结果。
9. 执行记录、确认 JTI 消费、审计和 `TRAINING_DAY_SKIPPED` Outbox 在同一事务提交。
10. 事件经过 Outbox → RabbitMQ → Agent Inbox，事件 Worker 进入候选引擎，给学员生成 TRAINING_ADHERENCE；存在 coachId 时给教练生成 COACH_STUDENT_ATTENTION。
11. 学员候选有效 72 小时，教练候选 48 小时；跳过关怀按学员/计划/当天等去重。后续经主动决策、消息快照、通知策略和 IN_APP 投递，询问训练节奏是否需要帮助，或请教练关注学员。

各种情况：

| 情况 | 当前行为 |
| --- | --- |
| plan_id/day_id 缺失或 day 不属于计划 | 澄清或后端拒绝，不写入随意训练日 |
| 计划尚未发布、无权读取 | 训练确认预读或最终业务校验拒绝 |
| 管理员/教练代学员提交 | 训练服务拒绝；计划审核权限不等于代提交执行权限 |
| 用户希望补记历史日期 | 当前执行日期由服务端当天确定，接口没有任意日期参数 |
| 已记录 COMPLETED 后新请求改 SKIPPED | 当前允许更新已有日执行结果，产生新事件 |
| 同 requestId 重试 | 幂等返回，不重复执行 |
| 某天跳过后所有训练日都已有记录 | 同次执行可能还生成周期记录完成事件，见下一场景 |
| 业务记录成功但主动模型选择 IGNORE | 跳过事实仍成立，只是不发送关怀消息 |
| 消息/决策失败 | 事件、候选或投递按各自状态重试，不重做训练写入 |

该场景当前没有独立的“跳过成功”固定模板通知分支；业务结果由对话返回，事件后续进入学员/教练的主动候选流程。

代码依据：[日执行参数](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/agent/fitness_tools.py:372)、[训练确认策略](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/agent/fitness_tools.py:762)、[训练服务校验](/Users/a1-6/Desktop/fitness-backend/fitness-training-service/src/main/java/com/shuyiwa/fitness/training/service/TrainingPlanService.java:136)、[执行事务](/Users/a1-6/Desktop/fitness-backend/fitness-training-service/src/main/java/com/shuyiwa/fitness/training/repository/TrainingPlanRepository.java:246)、[跳过候选](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/proactive/candidate_engine.py:73)。

**5. 完成训练：完成一个训练日与整个周期结果记录完**

用户示例：“今天这一节训练完成了，帮我记录。”

1. 入口、路由、工具及确认链与跳过训练相同，status 改为 COMPLETED。真实 plan_id/day_id 仍是必填；不能凭一句“整个计划做完了”直接把所有训练日标记完成。
2. 确认前读取真实计划快照，批准后 Java 检查本人、已发布计划、训练日归属、确认范围，再插入或更新日执行记录。
3. 同一事务发 `TRAINING_DAY_COMPLETED`。该事件进入 Agent Inbox 后，当前候选引擎没有为单日完成创建即时祝贺/关怀候选；它留作后续事件上下文和调度统计。
4. Java 每次记录日结果后，比较计划总训练日数与已有 execution 记录数。所有训练日都已有一条记录时，额外发出 `TRAINING_PLAN_COMPLETED`。
5. 当前这项判断把 SKIPPED 也算作已有记录，没有要求所有日状态都是 COMPLETED；训练计划自身仍保持 PUBLISHED。准确说法是“整个周期的日结果记录齐了”。
6. 周期事件进入候选引擎，为学员创建 TRAINING_CYCLE_REVIEW，按计划 ID 和计划版本等去重，有效 7 天。
7. 主动决策模型结合有限事件上下文决定忽略、延后或发送复盘提醒；默认建议查看训练计划，CTA 为 OPEN_TRAINING_PLAN。消息走快照和通知 Outbox，投递前检查用户偏好。
8. 学员点击“打开助手”，输入框预填查看当前训练计划的请求。发送后重新验证身份、候选归属并查询真实计划/记录；下一周期安排需要新的建议、草案及授权流程。

各种情况：

| 情况 | 当前行为 |
| --- | --- |
| 只完成一个训练日，其余未记录 | 只有日完成事件，不触发周期记录完成 |
| 部分完成、部分跳过，但每一天都有记录 | 当前也会发 TRAINING_PLAN_COMPLETED；不能宣传为全部训练完成 |
| 全部有记录后，新请求修改某日结果 | 可能再次产生周期事件，周期候选按计划/版本去重 |
| 记录或 JTI 事务失败 | 不产生成功周期 Outbox |
| 模型选择不打扰用户 | 候选 IGNORED，训练执行事实不变 |
| 用户想查看具体完成率或详细动作报告 | 需要实际执行记录查询；通知本身不是已生成的完整复盘报告 |

此外 Scheduler 在周一可以聚合过去 7 天完成/跳过事件，生成同类型 TRAINING_CYCLE_REVIEW，原因是 WEEKLY_TRAINING_REVIEW。它统计事件数量，不是在当前执行表里去重重算独立训练日。定时入口还受后文说明的调度 SQL 缺陷影响。

代码依据：[周期事件条件](/Users/a1-6/Desktop/fitness-backend/fitness-training-service/src/main/java/com/shuyiwa/fitness/training/repository/TrainingPlanRepository.java:323)、[周期候选](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/proactive/candidate_engine.py:97)、[周复盘入口](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/proactive/scheduler.py:189)。

**6. 个性化记忆提取并待确认：从用户原话到 ACTIVE Memory**

用户示例：“我通常只有晚上能训练，我喜欢游泳，以后安排时参考一下。”

1. 用户发送后走公共入口。当前只有进入健身指导子图时，领域准备节点才尝试提取 Memory 候选；预约、经营、客服子图没有这项自动准备。本次请求最多准备一次。
2. 提取器先检查当前原话有无“记住、我通常、我习惯、我喜欢、我不喜欢、我只有、以后都”等信号；没有就返回空候选。
3. 有信号时调用 JSON 提取模型，输入只有提取 system prompt 和当前原话。没有提供最近六条、线程摘要、已有 Memory、已有版本或模型置信度字段。
4. 模型最多返回五条低敏候选，字段是 memory_type、memory_key、value、unit。允许类型为训练目标、训练偏好、器械、时间偏好、沟通偏好。
5. 提取 prompt 要求模型不提取疾病、身体指标、支付、合同、预约、课时或未经用户提供的推断。返回后，程序校验 JSON、类型、稳定键、控制字符、健康敏感词及重复项。当前没有对上述全部业务词或“是否完全受原话支持”做硬校验，用户批准仍是候选生效的必要步骤。
6. 只有身份恰好一个机构且有 requestId 才持久化。主体和机构来自验签身份；候选正文 AES-GCM 加密，计算内容哈希，写 agent_memory_candidates，初始 PENDING，默认有效 24 小时。
7. 同事务写候选 CREATED 审计和 MEMORY_CANDIDATE_PENDING 通知 Outbox。通知只引用候选 ID，不把偏好原文复制到通用 Outbox。
8. 同一待确认内容按本人、机构、类型、键和内容哈希去重。主对话模型得到“尚未确认候选”上下文，不能据此声称已经保存记忆。
9. 通知 Worker 可生成站内待确认提醒；小程序“待确认记忆”页通过候选 Inbox 接口直接读取本人机构内的有效 PENDING 候选，所以通知投递延迟不会阻挡候选审批。
10. 用户点击确认或拒绝，提交 candidate_id、decision、decision_request_id。后端重验身份、归属、状态、期限并解密候选。
11. 拒绝时行锁更新 REJECTED 并写审计，不保存正式 Memory。批准时先调用 Python MemoryService 保存 ACTIVE Memory，再把候选改 APPROVED、写批准审计，并在 API 响应中返回 memory_id。
12. 这个页面点击已经是显式确认，批准不再执行 LangGraph interrupt，也不经过 Java Gateway。批准使用稳定请求键 memory-candidate:{id}:approve；进程在保存 Memory 后中断，重试仍不会重复加版本。
13. 正式 Memory 按用户、机构、类型、稳定键保存。第一次版本 1，同一键的新确认值会更新并增加版本。以后 ACTIVE、未过期、未脱敏记忆可以由 memory.list 工具读出；结构化训练草案生成会自动读取本人已确认 Memory。

还有一条对话入口需要区分：用户明确要求“记住这件事”时，主模型可以调用 memory.save 写工具，走公共确认单、interrupt、批准后保存流程。它与上述候选页面批准并存，记忆实际由 Python MemoryService 管理。

各种情况：

| 情况 | 当前行为 |
| --- | --- |
| 没有提取触发词 | 不调用提取模型；普通对话继续 |
| 用户说偏好但路由到预约/客服 | 当前不自动提取这项 Memory 候选 |
| 模型失败、JSON 不合法或候选内容被拒绝 | 普通对话可继续，不能声称已记住 |
| 多机构身份 | 不猜所属机构、不持久化候选，交主模型澄清 |
| 候选保存失败 | 可能仍有本轮临时候选提示，不能当成已落库或生效 |
| 24 小时未决定 | 列表排除，过期 Worker 设 EXPIRED，不能再批准 |
| 用户拒绝 | 候选 REJECTED，不进入 ACTIVE Memory |
| 已终态后再做不同决定 | 冲突；相同决定重试按幂等处理 |
| 想修改待确认内容再批准 | 当前没有候选编辑 API/按钮，只有确认和拒绝 |
| 已生效内容需要纠正/撤销 | ACTIVE Memory 管理支持 expected_version，防止旧页面覆盖新值；清理相关派生上下文 |
| 提取内容与已生效记忆相同或冲突 | 当前提取器没有先读取 ACTIVE Memory 做新旧比较；候选去重主要针对 PENDING 内容 |
| 教练为学员生成训练草案读取学员 Memory | 先走受控学员训练上下文授权入口，不能伪造学员身份；完整过程见场景 11 |

代码依据：[候选触发和提取](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/memory/candidate.py:151)、[持久化与批准](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/memory/candidate_service.py:68)、[候选审批 API](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/api/routes/memory_candidates.py:171)、[Memory 管理](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/memory/service.py:200)、[草案读取 Memory](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/agent/training_plan_generation.py:190)。

**7. 主动训练关怀：即时跳过事件和定时无记录扫描**

这类流程由后台事件或 Scheduler 触发，不需要用户先在输入框发一条消息。

1. 即时入口：某训练日写入 SKIPPED 后，事件 Worker 创建学员 TRAINING_ADHERENCE 候选，必要时同时创建教练关注候选，具体写业务过程见场景 4。
2. 定时入口：Scheduler 从 Inbox 找每个机构/学员最近一次 TRAINING_DAY_COMPLETED 或 TRAINING_DAY_SKIPPED 事件。
3. 默认最近记录距当前至少 3 天，而且该关联计划没有 TRAINING_PLAN_COMPLETED 事件时，创建 TRAINING_ADHERENCE，原因 TRAINING_INACTIVE，事实包含最近记录时间和 3 天阈值。
4. 同学员、机构、周的这类候选去重，有效 72 小时。Scheduler 只创建候选；独立决策 Worker 才领取并调用模型。
5. 决策前补充相关近期事件、反馈次数、近 7 天提醒量，结合专用 prompt 判断是否需要关怀。没有后台实时计划/空闲课表查询，也没有在此自动加载长期 Memory。
6. IGNORE 结束、DEFER 延后重新判断，联系类决策保存消息快照和通知 Outbox。默认回退询问“最近的训练安排还合适吗？”，建议动作 RESUME_TRAINING。
7. 通知 Worker 检查用户开关、免打扰、频率后投站内信。用户可以打开助手，也可以反馈有帮助/没帮助、明天提醒或不再提醒此类。
8. 打开助手预填“请结合这条主动建议，帮我安排恢复训练的下一步”。用户发送后才进入交互 Agent，依据当前意愿查询或提出训练安排。

各种情况：

| 情况 | 当前行为 |
| --- | --- |
| 学员从未产生完成/跳过事件 | 当前定时查询找不到此人，不能覆盖所有从未开始训练者 |
| 3 天没有新记录，但学员线下确实训练了 | 系统只知道没有记录，不能认定其没训练 |
| 已有周期记录完成事件 | 该关联计划被定时关怀查询排除 |
| 同周多次 Scheduler 扫描 | 候选去重，不每次插入相同关怀 |
| 模型不应联系、用户关闭提醒 | 忽略或通知抑制 |
| 用户说时间不合适 | 在对话里提供可行建议；需要业务写入再走确认 |
| 同一候选延后超过有效期 | 过期后不再领取，不因为 DEFER 自动延长有效期 |
| Scheduler SQL 失败 | 本轮定时候选事务可能整体回滚，见场景 9 的实现问题 |

代码依据：[无记录扫描](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/proactive/scheduler.py:62)、[主动上下文](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/proactive/context_builder.py:23)、[关怀默认结果](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/proactive/decision_agent.py:142)。

**8. 计划调整建议：重复跳过信号到有权限的新草案**

1. Scheduler 聚合 Inbox 中同计划过去 14 天的 TRAINING_DAY_SKIPPED 事件。默认至少两条，并排除已经有 TRAINING_PLAN_COMPLETED 事件的计划。
2. 给学员创建 TRAINING_PLAN_MISMATCH，原因 REPEATED_TRAINING_SKIPS，事实包括 skip_count 和 lookback_days。优先级 HIGH，有效 96 小时，按计划/周去重。有 coachId 时也为教练创建关注候选。
3. 主动决策 Worker 读取候选和有限事件上下文，判断应忽略、延期还是建议调整。默认结果是 PROPOSE_ACTION，建议 ADJUST_TRAINING_PLAN。
4. 决定联系时写消息快照与 TRAINING_PLAN_ADJUSTMENT_SUGGESTED 通知 Outbox，候选改 NOTIFICATION_QUEUED。候选决策本身不调用计划写工具。
5. 用户看到通知后点击“打开助手”，输入框预填“请结合这条主动建议，帮我制定训练计划调整方案”。发送后验证身份和候选归属，询问或查询当前目标、时间、器械及约束，形成可讨论的方案。
6. 当前没有直接原地修改已发布训练计划的工具。正式落地通常由有 author 权限的教练或管理员生成新草案，再经过创建、提交审核、审核、发布。
7. 生成结构化草案预览时按当前身份检索已授权 RAG 知识，读取已确认 Memory 或受控的学员上下文，JSON 模型输出多日训练明细，再做 Schema、语义、证据引用校验。
8. 只读预览本身不写业务。用户明确要求创建且有权限时，对同一份已校验草案参数准备确认单；批准后才创建 DRAFT。
9. 后续每个正式写动作分别确认：DRAFT 提交到 PENDING_REVIEW；审核通过/驳回；通过后发布。普通学员没有 create_draft、submit、review、publish 的 author 权限。

各种情况：

| 情况 | 当前行为 |
| --- | --- |
| 两次跳过来自同一训练日的新请求更新 | Scheduler 数的是事件，没有 distinct day_id，因此不等于两个不同训练日 |
| 两次跳过并非连续发生 | 没有连续性判断，不能称为连续两次没坚持 |
| 模型认为无需调整 | 候选 IGNORE；原计划不变 |
| 学员只想听建议 | 可以获得普通训练建议；当前结构化草案预览工具也只对教练或管理员开放 |
| 学员希望直接改正式计划 | 当前需要有权限的人创建/审核/发布新草案 |
| 没有授权知识或 Memory/学员上下文读取失败 | 结构化草案生成明确失败，不继续创建 |
| 草案 JSON、动作结构或证据引用不合法 | 有限修复后仍失败则停止，不保存不合格草案 |
| 教练驳回审核 | 停留在未发布状态，不能供学员当作已发布处方执行 |
| 没有部署/启用 Scheduler 或其事务失败 | 不会自动产生这类定时候选 |

代码依据：[重复跳过扫描](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/proactive/scheduler.py:119)、[调整默认结果](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/proactive/decision_agent.py:149)、[草案生成](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/agent/training_plan_generation.py:167)、[训练业务审核发布](/Users/a1-6/Desktop/fitness-backend/fitness-training-service/src/main/java/com/shuyiwa/fitness/training/service/TrainingPlanService.java:43)。

**9. 运营日报：机构事件摘要、决策提醒和后续指标查询**

当前这里的运营日报是“过去 24 小时机构事件的摘要提醒”。营收金额、新客量、实际课时余额等需要后续经营工具查询，后台日报候选本身没有调用这些工具。

先说明这次核对发现的实现问题：当前 Scheduler 运营聚合 SQL 选取 subscription.timezone，却只 GROUP BY subject_user_id、organization_id；迁移定义的主键还包含 audience。按这份 PostgreSQL 表结构，这个分组没有满足未聚合 timezone 的要求，会导致查询报错。WHERE audience='OPERATIONS' 不能替代 GROUP BY 包含完整主键的识别条件。

该 SQL 在每次 run_once 内、判断当地是否 8 点之前执行；整个轮次使用同一个事务，所以这里失败会回滚这轮先前创建的定时训练关怀、计划调整、周复盘等候选。即时事件 Worker 的跳过/周期候选走另一条事务链，不因这段 Scheduler SQL 直接回滚。本次未连接业务 PostgreSQL 实测，也未修复这段代码，不能把定时机制当作已经验证运行成功。

代码依据：[运营 SQL](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/proactive/scheduler.py:278)、[同轮事务](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/proactive/scheduler.py:61)、[订阅复合主键](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/migrations/versions/20260917_0042_proactive_agent_platform.py:267)。分组规则依据：[PostgreSQL SELECT 文档的函数依赖说明](https://www.postgresql.org/docs/current/sql-select.html#SQL-SELECT-COMPATIBILITY)。

按现有代码编排意图，后续流程是：

1. 管理员通过主动订阅接口配置本人在某机构的订阅，audience=OPERATIONS，enabled=true，设置 IANA 时区。接口重验签名身份和管理员权限，机构管理员在授权机构内配置，接收人固定为当前管理员本人。
2. 独立 Scheduler 启用后默认每小时轮询。运营查询按启用订阅关联该机构过去 24 小时 Inbox created_at 范围的事件。
3. 聚合 event_count、APPOINTMENT_CANCELLED 次数、TRAINING_DAY_SKIPPED 次数，以及触发事件 ID 列表。这里使用事件入库时间，不是严格按业务发生时间或昨天自然日统计。
4. 没有事件就跳过；当前时间换算为订阅时区，只有当地小时等于默认 8 时才生成日报候选，以机构、管理员、当地日期去重。
5. 创建 OPERATIONS_DAILY_DIGEST，原因 OPERATIONS_DAILY_SUMMARY，绑定机构和收件管理员，保存汇总事实，有效 24 小时。
6. 主动决策 Worker 领取，补充本人相关近期事件、反馈统计和提醒量，调用 JSON 决策模型。模型可以忽略、延期或生成摘要提醒；默认回退为 NOTIFY / OPERATIONS_DIGEST / REVIEW_OPERATIONS。
7. 决定发送后保存决策审计、消息快照、通知 Outbox；经通知开关、免打扰和频率策略写入站内收件箱。
8. 管理员在小程序消息中心点击“打开助手”，小程序预填“请结合这条主动建议，帮我查看运营待办”。发送后验证本人候选及机构，再进入正常路由。
9. 管理员明确询问“本月营收”“本周预约量”等时，经营子图才调用固定经营指标工具。服务端根据原问题和唯一签名机构构造指标、日期、时间桶、对比参数；模型侧不自由填写组织和查询条件。
10. Tool Registry 和 Gateway 再校验管理员角色、机构、指标白名单、时间范围等，通过固定 SQL 查询聚合数据，不返回学员或合同明细，也不执行模型生成的任意 SQL。
11. 查询结果经审计、整理后交模型解释。支持范围内的同期/上一周期对比最多调用两个 Gateway 查询；这个指标工具不直接修改预约、合同等原业务事实，不走这些业务的写确认单。成功且满足时间桶条件时，还会尝试写指标快照并进入另一条经营决策派生链，详见下面的完整后续。

**从打开小程序日报到查询回答，继续展开**

管理员看到运营摘要后，点击“打开助手”，再发送：“查询 2026-10-01 至 2026-10-16 的每日营收趋势，并与上期比较。”这里的日期只是说明查询语义的示例，不代表已经查得真实营收。

1. **小程序先记录交互。** 通过通知已读接口标记阅读，再向主动反馈接口提交 OPENED。反馈关联 notification_id、candidate_id 和当前管理员；服务端重验签名身份、候选主体/机构，以及通知是否属于此人和此候选。重复点击按候选、用户、反馈类型去重。
2. **用户发送，才请求 Agent。** “打开助手”只是进入对话并预填文本。用户点击发送后，请求带 conversation_id、message、locale 和 proactive_candidate_id。小程序不提交 SQL、权限或确认 Token。
3. **对话入口再次校验并记录 OPENED_CHAT。** 后端验证身份和配额，按本人查询候选并校验机构，记录候选与 conversation_id 的关联。该记录在调用 Supervisor 前保存，后续即使路由或查询失败，也不回滚为“未打开”。
4. **只加入已有的候选引用。** 当前后端实际注入 candidate_type、reason_code、aggregate_type、aggregate_id 四项。没有自动加载原日报的 event_count/cancellations/skipped、通知标题正文、原决策或消息快照。因此本轮模型知道“从运营日报进入”，但不保证拿到原日报全部数字。当前这一步也没有候选状态或期限校验，不能承诺过期候选一定在此被拒绝。
5. **恢复会话并重新识别问题。** 根据管理员身份和 conversation_id 恢复线程、有效摘要和近期对话，再规则/JSON 分类。明确的经营指标问题进入 OPERATIONS，子图只提供固定指标查询工具，不执行 RAG，也不提供预约、合同写工具。
6. **服务端解析指标口径。** 领域准备阶段按原话识别唯一指标、日期、NONE/DAY/WEEK 和对比方式，加入经营 system prompt。示例映射 REVENUE_AMOUNT、DAY、PREVIOUS_PERIOD。若只是预填“查看运营待办”，没有具体指标，prompt 会要求先澄清；若模型仍发起查询，安全参数准备可能失败，不能保证自动查询全套日报数据。
7. **首次模型决定是否调用工具。** 经营模型看见固定指标工具 `fitness_operations_metric_query_v1`，内部稳定 ID 为 `fitness.operations.metric.query.v1`，模型侧参数 Schema 是空对象。模型仍可能选择直接澄清；只有返回工具调用才进入实际指标查询。
8. **后端重建执行参数。** Supervisor 使用本次原话和验签身份的唯一机构，生成 metric、from、to、bucket、comparison 及当前固定 limit=20。模型即使输出其他参数，也不能自行扩大查询机构或时间。Registry 再检查角色和 Schema，当前经营工具仅允许 SYSTEM_ADMIN、ORGANIZATION_ADMIN。
9. **再次核对原问题并限流。** handler 比较机构、指标、日期、时间桶和比较方式是否与原话一致，再按机构使用 Redis 固定窗口限流，默认每 60 秒 60 次。Redis 不可用或超限，查询被拒绝。
10. **Gateway 用固定 SQL 查询。** Java 复验管理员和机构权限、日期、limit 和指标时间桶能力。日期按 Asia/Shanghai 转为“开始日零点至结束日次日零点”的半开区间，执行固定指标 SQL。数据只返回维度、标签、聚合值、查询日期和生成时间等，不返回合同/学员明细。
11. **按周期保存查询审计。** Gateway 返回后，Python 保存当前周期的主体、角色、机构、指标、时间桶、实际日期、行数、状态、请求与 Trace ID。成功审计保存失败则不返回这份经营结果；Gateway 失败也尝试记录失败审计。审计不复制 SQL、Prompt 和原始业务明细。
12. **有对比时再查询第二周期。** 无比较只查一次；PREVIOUS_PERIOD 查询紧接本期之前的等长自然日区间；SAME_PERIOD_LAST_YEAR 对应上一自然年同月日，闰日映射为 2 月 28 日。最多两次 Gateway 调用，每个周期默认 5 秒超时。第二周期失败时，不输出完整对比；本期已经保存的审计和指标派生不会整体回滚。
13. **程序计算报表摘要。** 工具基于返回聚合行计算 total_value、最大维度、占比、状态构成、时间序列、趋势提示和比较差值/百分比。比较基数为 0 时百分比是 null，只报告差值。不能将“没有有效记录”直接解释为业务异常，也不能把集中度提示当作因果结论。统计完整性仍受后面列出的截断和口径问题约束。
14. **第二次模型调用解释已有数据。** 工具结果作为 tool 消息追加；经营子图在成功完成工具节点后清空工具列表，后续模型主要根据 data/report 生成中文说明，不再在这个回合自由查其他指标。管理员下一条追问会重新经过意图和参数准备。
15. **小程序显示结果与范围。** Agent 返回 answer、route、tool_steps、status、用量等对话字段。当前聊天响应没有独立返回完整 report、图表数据或经营 case_id；专用经营图表与案例详情需要对接相应接口，不能把内部 report 对象等同为小程序已拿到结构化报表。原日报通知和原主动候选也不会因为查询完成而自动变成“经营整改完成”。

当前可用指标与统计口径需要一起说明：

| 指标 | 当前固定 SQL 主要口径 | 时间桶/对比能力 |
| --- | --- | --- |
| APPOINTMENT_COUNT | 按课程开始时间统计未删除预约 | 支持 NONE/DAY/WEEK、环比/同比 |
| COMPLETED_CLASS_COUNT | 按课程开始时间统计 status=6 的未删除预约 | 支持 NONE/DAY/WEEK、环比/同比 |
| NEW_CUSTOMER_COUNT | 按合同创建时间统计未删除且标记 new_customer=1 的合同中的去重学员，不另筛合同状态或当前有效期 | 支持 NONE/DAY/WEEK、环比/同比；有桶时每桶独立去重 |
| REVENUE_AMOUNT | 按合同创建时间汇总 total_amount-refund_amount | 支持 NONE/DAY/WEEK、环比/同比；不是现金流水日报 |
| COURSE_APPOINTMENT_COUNT | 无桶时按课程分组；有桶时统计带课程 ID 的预约时间序列 | 支持 NONE/DAY/WEEK、环比/同比 |
| COACH_APPOINTMENT_COUNT | 无桶时按教练分组；有桶时统计带教练 ID 的预约时间序列 | 支持 NONE/DAY/WEEK、环比/同比 |
| APPOINTMENT_STATUS_BREAKDOWN | 按状态分组；当前状态标签和实际预约编码不一致 | 仅 NONE，不支持当前对比能力 |
| REMAINING_CLASS_HOURS | 查询当前合同余额，日期用于筛合同覆盖范围 | 仅 NONE；不是历史余额快照 |

日期解析当前支持显式完整日期区间、近 1–92 天、本周、本月、上月；未识别时默认近 30 天。单独说“今天”“昨天”“上周”没有专门解析，可能落到默认范围，不能声称已准确理解。没有桶的总量查询，以及只有同比/环比但仍为 NONE 的查询，不进入下面的指标快照派生分支。

代码依据：[候选进入对话](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/api/routes/agent.py:123)、[指标和日期解析](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/agent/operations_tools.py:462)、[经营提示](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/agent/operations_tools.py:510)、[模型侧空参数](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/agent/supervisor.py:1720)、[后端重建参数](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/agent/supervisor.py:1245)、[策略与限流](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/agent/operations_tools.py:770)、[Java 查询权限](/Users/a1-6/Desktop/fitness-backend/fitness-core-gateway/src/main/java/com/shuyiwa/fitness/gateway/service/OperationsToolService.java:51)、[固定统计 SQL](/Users/a1-6/Desktop/fitness-backend/fitness-core-gateway/src/main/java/com/shuyiwa/fitness/gateway/repository/JdbcOperationsReadRepository.java:63)、[程序报表](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/agent/operations_tools.py:272)。

**查询成功后，满足条件时继续进入经营决策**

这条派生分支已有实现，但带前提；它不是每张日报、每次点击都会自动走到动作执行。

```text
本期 DAY/WEEK 指标查询成功 + 成功审计
  → 尝试保存一条指标快照
  → 无匹配策略 / 历史不足 / 质量不通过 / 无异常：结束该次派生
  → 有匹配策略且统计异常：异常记录 + 经营案例 + 固定诊断 + 候选方案
  → 策略不允许 / SHADOW、REPLAY / 等待人工批准 / 低风险自治
  → 获准动作 → 独立 Action Worker → 创建跟进任务 → OBSERVING
  → 另行接入真实效果观测 → EVALUATED → 管理员复核关闭
```

1. **满足快照条件才派生。** 正式应用已装配 decision_snapshot_sink。本期查询 role=CURRENT，且 bucket 不是 NONE，并且 Gateway 查询和成功审计完成后，handler 尝试调用 DecisionPlatformService.ingest_snapshot。派生失败只记录错误，不隐藏已经取得且审计成功的查询结果。
2. **形成机构级指标快照。** 当前把整个查询窗口返回的 rows 求和成一条机构级快照，保存 metric、维度、DAY/WEEK、周期边界、value、sample_size、口径版本和来源摘要。sample_size 是返回行数，不是会员人数；不会自动把每一天都拆成一条历史快照。
3. **匹配已配置的经营目标和策略。** 系统查本机构、对应指标的 ACTIVE 目标和生效策略。没有匹配策略则只留快照，返回 NO_ACTIVE_POLICY；不能靠一次聊天自动替管理员创建经营目标或授予自治权限。
4. **读取已有历史基线。** 按同机构、指标、维度、bucket 读取更早且质量 PASSED 的已存快照，默认最多 28 条、至少 7 条才判断。当前没有自动从 Java 补采历史；一次返回 16 个日桶也不等于已经有 16 条历史快照。
5. **程序做统计异常检测。** 使用中位数与 MAD 等固定算法，默认相对偏离阈值 15%、稳健阈值 3.5。当前质量不通过、基线不足或没有满足异常条件，就不生成异常案例。这里的判定由程序完成，不调用主动通知 JSON 模型。
6. **有异常才建案例。** 事务内写异常记录和经营案例，保存指标事实、预期值、偏离、检测版本及证据，重复异常按仓储幂等处理。随后写固定统计诊断，明确 causality_status=NOT_ESTABLISHED，不把相关性当成经营原因。
7. **程序生成受支持的方案。** 当前自动方案仅包括 CREATE_OPERATIONS_TASK 和 CREATE_COACH_FOLLOW_UP，根据策略允许动作生成并排序。收益评分是同案例内的排序依据，不是真实预计营收；此处也没有另一个 LLM 自动规划整改方案。
8. **评估策略门槛。** 检查模式、风险级别、允许动作、影响范围、预算、数据质量、置信度等。拒绝则 POLICY_REJECTED；REPLAY/SHADOW 留记录不创建动作；ADVISORY 或需要审批的风险进入 WAITING_APPROVAL；满足 AUTONOMOUS 及低风险条件才自动授权动作。L4 在这套策略评估中被拒绝自动化。
9. **小程序通过经营决策接口处理待审批案例。** 管理员需读取本机构案例和方案，选择 option_id 后提交 APPROVE/REJECT、decision_request_id、备注等。后端重新验证管理员和机构范围、案例状态、方案归属与审批幂等；批准后生成 action_run。该链使用经营案例审批接口，不是预约确认单的 confirmation_id/JTI 流程；在聊天里说“同意”、点击 HELPFUL 或 OPENED 都不能替代审批。
10. **Action Worker 执行获准动作。** 独立进程启用后领取已授权、可执行动作，核对 Payload 摘要和执行锁，推进案例到 EXECUTING。当前这两个自动方案都只在 agent_operations_tasks 写 OPEN 跟进任务，可能没有 assignee；不会因此自动取消预约、改合同、扣款或向教练发送一条消息。
11. **记录动作完成并进入观察。** 创建任务与完成动作、更新案例 OBSERVING 在同一事务内处理；同 action_run 重试不创建第二个任务。失败按退避重试，默认第 5 次执行尝试仍失败时进入 FINAL_FAILED，案例可以进入 EXECUTION_FAILED，再由授权运维重试或处理。
12. **业务整改由任务承接。** 当前创建任务是机器动作执行成功，不能推导为有人已经跟进学员或业务指标已经改善。小程序任务列表、分派、处理和业务结果回填需要相应接口或现有业务系统衔接；当前这条 Worker 没有自动完成这些人工业务。
13. **效果观测需要另行提交。** 当前没有定时自动拉取效果数据的 Worker。管理员或受控数据接入方需提交 case_id 下的 metric、value、sample_size、观察窗口、来源摘要，可关联 action_run 或 experiment。记录 ALL 观察，或同指标同窗口有 TREATMENT/CONTROL 两组时，处于 OBSERVING 的案例可进入 EVALUATED。
14. **需要实验时另行建实验和分组。** 已有实验配置、稳定分流和效果查询 API，但点击日报不会自动创建实验；动作入队也不保证自动完成实验对象关联。效果查询对同窗口组值计算绝对/相对差值，不代替自动因果验证。
15. **管理员复核后关闭。** 授权管理员可通过 close 接口将符合状态要求的案例改 CLOSED，保留案例、方案、审批、动作和观察账本。当前不会因为用户把日报标记已读、查询成功或点“有帮助”，就自动关闭经营案例。

对应的小程序接入接口如下。它们都是后端已有接口，实际小程序界面接通情况尚未验证。

| 环节 | 主要接口 |
| --- | --- |
| 阅读/打开日报 | `POST /api/v1/agent/notifications/{id}/read`；`POST /api/v1/agent/notifications/proactive/{candidate_id}/responses` |
| 进入助手 | `POST /api/v1/agent/chat` 或 `/chat/stream`，携带 proactive_candidate_id |
| 配置经营目标/策略 | `POST /api/v1/admin/decisions/objectives`；`POST /objectives/{id}/policies` |
| 查看案例与方案 | `GET /api/v1/admin/decisions/cases`；`GET /cases/{case_id}` |
| 批准/拒绝方案 | `POST /api/v1/admin/decisions/cases/{case_id}/decisions` |
| 查看/重试动作 | `GET /api/v1/admin/decisions/actions`；`POST /actions/{action_id}/retry` |
| 提交与读取效果 | `POST/GET /api/v1/admin/decisions/cases/{case_id}/outcomes`；`GET /cases/{case_id}/effects` |
| 关闭案例 | `POST /api/v1/admin/decisions/cases/{case_id}/close` |

表内省略共同前缀的路径均属于 `/api/v1/admin/decisions`；小程序不能直接调用内部 Java 工具接口。

代码依据：[查询后快照派生](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/agent/operations_tools.py:965)、[单快照构造](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/agent/operations_tools.py:1022)、[正式装配](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/main.py:284)、[策略/异常/案例编排](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/decision/service.py:43)、[固定方案](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/decision/service.py:260)、[异常与策略算法](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/decision/engine.py:28)、[案例审批](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/api/routes/admin_decisions.py:274)、[动作执行](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/decision/action_worker.py:132)、[效果记录](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/api/routes/admin_decisions.py:476)、[效果计算与关闭](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/api/routes/admin_decisions.py:553)。

**反馈、统计限制与当前尚未连通的地方**

| 情况或记录 | 当前实际含义/限制 |
| --- | --- |
| 通知已读 | 已标记阅读，不等于发起查询 |
| OPENED | 点击主动通知入口，重复反馈去重 |
| OPENED_CHAT | 已带候选发起对话，可能随后查询失败 |
| HELPFUL / NOT_HELPFUL | 用户主观反馈，用于后续主动判断上下文；不直接训练模型或批准经营动作 |
| ACCEPTED / DECLINED | 反馈接口允许记录的交互值，不是案例审批或业务写授权 |
| “明天提醒” | 新建延期候选，只放父候选引用/分钟数，当前不复制原日报事实或重算原统计 |
| “不再提醒此类” | 关闭通知类型偏好，不等于关闭 OPERATIONS 订阅本身 |
| 原候选 COMPLETED | 通知投递阶段已完成，不能作为经营整改完成标志 |
| 本期总量查询或环比但 bucket=NONE | 不自动进入指标快照派生 |
| DAY/WEEK 查询超过返回条数 | 当前 limit=20，时间桶 SQL 会截断；程序补 0 可能把未返回数据误当无记录，影响趋势和派生快照 |
| 课程/教练维度超过 20 个 | report.total_value 是返回行之和，不能保证全机构总量 |
| 新客按日/周分桶后求总量 | 每桶独立去重，同一学员可能跨桶重复计数；各桶求和不保证等于整个查询区间的去重人数 |
| 询问取消率/完成率 | 当前状态分布标签 4/5 与业务编码不一致，取消实际为 deleted=1 而统计排除 deleted，不能承诺正确计算 |
| 看“本月和上月比” | PREVIOUS_PERIOD 实际取前一个等长周期，不一定是上个完整自然月 |
| 查询当前剩余课时 | 是当前余额，不是对应日期的历史余额 |
| 查到营收总额 | 按合同创建时间归属，金额单位沿用业务字段，不能擅自解释为实际到账现金流 |
| 查询后写快照失败 | 记日志，已审计查询仍可返回；不能把快照派生当成查询成功的强事务保障 |
| 本期快照已派生，但比较第二周期失败 | 查询失败不代表本期审计/快照/可能案例一起回滚 |
| 指标快照数据被截断或口径不一致 | 当前没有完整数据质量硬检测；PASSED 字段不能替代实际质量校验 |
| 策略/历史未配置、没有基线 | 只留快照；不会自动补齐历史并保证出案例 |
| 创建跟进任务成功 | 仅任务已写入，不等于人员已跟进或指标改善 |
| 想证明“这张日报促成了哪次营收改善” | 当前候选 ID 没有传入快照派生，未自动建立日报→查询→案例→动作→改善的业务归因关系 |

上述扩展按当前实现描述已有分支，没有因此修复 Scheduler、日期解析、指标截断或状态口径问题，也没有替小程序实现新的界面或后台采集任务。

各种情况：

| 情况 | 当前行为 |
| --- | --- |
| 当前 Scheduler SQL 未修复 | 按迁移表结构会阻断本轮定时事务；不能宣称日报正常生成 |
| Scheduler/决策/通知进程未启用 | 对应阶段不会自动执行 |
| 没有启用 OPERATIONS 订阅 | 不给该管理员生成这个定时日报候选 |
| 过去 24 小时没有收到事件 | 不生成日报候选 |
| Worker 没在当地 8 点小时内执行 | 不保证补发错过的这一天日报 |
| 事件延迟到 Inbox | 可能进入次轮 24 小时入库窗口，不能称严格昨日业务报表 |
| 想在日报里看到完整营收、收入和新客表 | 当前候选没有这些数据，需明确进入经营指标查询 |
| 只说“查看运营待办”，无法解析具体指标 | 不能保证生成唯一合法查询；若模型发起该查询，参数准备可能失败 |
| 学员查询经营指标 | 角色权限拒绝 |
| 多机构身份没有唯一机构 | 安全参数准备停止，先由上游切换明确机构 |
| 任意 SQL、任意指标或不支持的时间桶组合 | 白名单/能力校验拒绝 |
| 限流、Gateway 超时或审计落库失败 | 查询失败，不把无审计保护或未知结果当作成功返回 |

手动创建同类型候选可以独立进入决策和通知下游，但不能证明 Scheduler 的 SQL 已正常工作。

代码依据：[运营订阅](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/api/routes/admin_proactive.py:99)、[日报默认决策](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/proactive/decision_agent.py:177)、[固定指标目录](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/agent/operations_tools.py:93)、[服务端构造查询条件](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/agent/operations_tools.py:641)、[查询策略、限流和调用](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/agent/operations_tools.py:770)。

**10. 需要 RAG 检索和工具调用的问题：知识证据与实时业务事实组合**

用户示例：“游泳课取消有哪些规则？再帮我查一下合同还剩多少课时。”规则来自已发布且有权访问的知识资料，当前课时来自真实合同工具。

1. 用户发送，身份验证、恢复线程历史和有效摘要、规则/JSON 分类按公共入口完成。这个例子可能拆成规则问答和合同查询两个意图，按序处理并合并回答；一条超过两个需求会要求先选重点。
2. 进入领域准备阶段。健身指导开启 RAG；客服只有 BUSINESS_RULE_QA 开启 RAG；预约和经营子图当前没有这项自动检索。RAG 由服务端先调用，不是模型先返回一个 RAG 工具调用。
3. 检索问题使用本次输入，范围由签名身份的 subject、organization_ids、roles 构造。不能从自然语言中的角色/机构描述扩大知识范围。
4. 生成问题 Embedding，然后并行做向量召回与词法召回，默认各 20 条。数据库查询先过滤文档发布状态、生效/过期时间、GLOBAL/机构/本人 PRIVATE 可见范围以及允许角色。
5. 按加权 RRF 融合去重，默认向量权重 0.6、词法 0.4、k=60；调用真实 Reranker，默认取 Top 5。权限已在召回阶段检查，重排只负责相关性排序。
6. 对命中片段补充父章节上下文，控制重复和长度，默认总预算 10000 字符、每条最多 1800 字符。证据附标题、来源、版本及章节/页码/表格位置等标签。
7. 将证据加进本轮统一领域 system prompt，再结合摘要、历史、本次消息和意图工具交集调用 Tool Calling 模型。知识文本作为参考证据，不能作为新的系统指令。
8. 若还缺动态合同/课时/预约事实，模型返回合法查询工具和参数。Registry 校验角色和 Schema、绑定机构及纯学员主体，再由 handler 请求 Gateway，Gateway 校验身份、机构与资源后查 Java/数据库。
9. 工具结果作为 tool 消息追加，模型再解释或继续查。默认每个领域意图最多 4 次工具节点循环；预算耗尽后撤下工具，生成已有结果的回答。一次节点里可能有多个只读调用，因此预算不是绝对 4 个 HTTP 请求。
10. 只问规则和查询事实就直接回答，不需要写确认单。用户另行明确要求取消/创建/修改时，必须识别为合法写意图，生成独立确认单再批准执行。

在这个例子中，最终回答应把“规则证据支持的说明”和“合同工具查到的剩余课时”说清楚；不应把知识文档中的示例数字当成学员当前课时。

各种情况：

| 情况 | 当前行为 |
| --- | --- |
| 文档未发布、过期、跨机构或不允许角色 | 召回过滤，不提供给模型 |
| 向量或词法召回都没有结果 | 普通问答可以继续无证据对话；当前没有证据为空必须拒答的硬门槛 |
| 命中相关性低 | 当前没有统一召回最低相关性硬阈值，不能把命中本身当作答案保证 |
| Embedding、数据库或 Reranker 故障 | 报检索失败；有候选时重排故障不会静默退回纯向量结果 |
| 只读工具多传了 Schema 外字段 | 返回 TOOL_INPUT_INVALID，在已有预算内允许模型纠正 |
| 工具跨领域、跨意图或角色不允许 | 拒绝，不扩大白名单 |
| Gateway/业务查询失败 | 明确失败，不能编造当前课时、预约或计划状态 |
| 用户仅问取消规则 | 规则问答不授权取消真实预约 |
| 一条消息两个写需求 | 要求拆开，每个动作单独确认 |
| 问题涉及医疗、受伤、退款/赔付或合同争议 | 当前确定性 guard 按能力边界回复，不保证进入通用 RAG+工具链 |

一般问答与结构化训练草案的证据要求也有区别：一般问答的 prompt 有证据来源标签，但 Supervisor 没有独立结构化 citations 响应和逐条引用 ID 硬校验；小程序端的完整引用卡能力需要相应接口和展示支持。训练草案生成工具会再次检索、读取已确认 Memory，校验结构和合法 evidence ID；无授权知识或非法证据引用时会明确失败。

代码依据：[领域检索入口](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/agent/supervisor.py:870)、[融合和重排](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/rag/service.py:192)、[检索权限过滤](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/rag/repository.py:426)、[证据上下文](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/rag/service.py:48)、[工具循环](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/agent/supervisor.py:1236)、[结构化草案证据检查](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/agent/training_plan_generation.py:175)。

**11. 教练为学员生成训练草案：小程序操作、师生授权、记忆辅助、确认创建和审核发布**

教练在小程序 AI 助手中输入：“为我负责的这位学员生成一份增肌训练草案，每周三次，每次 45 分钟，只使用哑铃。”

整个过程中，操作者一直是当前教练；目标学员是这次计划服务的对象。教练的 AgentContext 与学员的业务 ID 各有作用：前者证明谁在操作、有哪些权限，后者说明为谁生成。授权和读取过程中不会把教练伪装成学员。

先说明学员选择的接入边界：小程序可以把“我的学员 → 学员详情 → AI 助手”作为入口，但必须完成选中学员与 Agent 请求之间的业务上下文衔接。当前 AgentChatRequest 只有 conversation_id、message、locale 和可选 proactive_candidate_id，没有独立 student_id/coach_id 字段，额外字段会被拒绝。当前 Agent 工具也没有按学员姓名搜索名单的专门能力。因此，“小程序已经选了学员”不等于后端对话自动获得其 ID；已知真实引用如何进入生成工具参数仍是接入工作，不能让模型猜测“小王”的 ID。

生成工具需要的主要参数如下：

| 参数 | 含义与当前来源约束 |
| --- | --- |
| organization_id | Tool Registry 绑定为教练验签身份中的唯一机构；多机构上下文先明确机构 |
| student_id | 真实目标学员 ID；教练不能借修改此值读取任意学员 |
| coach_id | 草案指定负责教练 ID；当前没有自动强制等于登录教练 |
| goal_type | 本次明确训练目标，例如增肌；生成内容不能擅自换目标 |
| training_days | 本次要生成的训练日数，1–7 |
| level | 学员训练水平，必填；不能无依据推断 |
| session_minutes | 单次 20–180 分钟 |
| equipment | 可用器械列表，最多 20 项，可为空 |
| focus / constraints / title | 可选重点、限制和标题；仍受长度及内容约束 |

正常流程可以这样描述：

1. **小程序发请求，后端验证教练身份。** 教练登录小程序后发送需求，可信认证/接入层提供当前教练的签名 AgentContext。Agent 验证身份、机构、角色、期限等，恢复此教练当前会话的历史和有效摘要。
2. **识别到健身指导和草案相关意图。** 路由可以是 FITNESS_COACHING / GENERATE_TRAINING_DRAFT，或明确要求创建时的 CREATE_TRAINING_DRAFT。领域模型在合法工具集合中请求 `fitness.training.plan.generate_draft.v1`，并给出生成参数。
3. **检查生成工具权限和参数。** 生成预览本身虽是只读工具，也只允许 SYSTEM_ADMIN、ORGANIZATION_ADMIN、COACH；普通学员不能调用。Registry 校验 Schema，并绑定唯一签名机构。对教练不会把 student_id 改成本人，也不会自动把 coach_id 改成本人。
4. **生成服务先检索知识。** 它把目标、水平、天数、时长、器械、重点和限制构成检索问题，按当前教练的 subject/机构/角色范围做 RAG。检索的是教练有权使用的已发布知识；没有授权证据或检索失败就停止。
5. **识别为代学员生成，申请受控读取。** 目标 student_id 与操作者 subject 不同，生成服务通过 GatewayStudentTrainingContextReader 请求 Gateway 的 training-context-access 接口，继续使用原教练的签名 AgentContext、内部服务凭证和链路标识。
6. **Gateway 校验机构和师生关系。** 普通教练跨主体读取时，数据库必须存在其负责此学员的有效机构关系，目标学员也必须仍是本机构有效成员。管理员在其授权范围内代读，同样要校验目标成员资格。
7. **Gateway 返回授权收据。** 收据含 organizationId、actorId、studentId、accessType。Gateway 不读取 Memory 正文；Python 再核对机构、操作者和目标学员三个 ID，必须与本次请求一致。
8. **Agent 读取学员训练类 Memory。** 授权通过后，Python 在自己仓储按目标学员和机构读取 ACTIVE、未过期的训练目标、训练偏好、器械、时间偏好。不会把待确认候选当成正式偏好，也不代读沟通偏好。
9. **区别没有记忆与读取失败。** 合法查询返回空列表，可以用本次要求和知识继续生成。Gateway 拒绝、收据不一致、缺少当前签名上下文或仓储读取异常，会明确中断，不能把故障当作“学员没有偏好”。生产装配包含受控 reader；其他调用方如果未装配 reader，当前类本身允许跳过代读，因此不能假定任意实例都有完整代读授权链。
10. **调用专门的 JSON 草案生成模型。** 输入包括生成 system prompt、本次目标和限制、已授权知识、学员已确认训练偏好及受限 JSON 结构。本次明确要求优先。模型生成标题、训练日、动作、组数、次数、休息、重量/RPE 和动作证据引用，不生成机构/学员/教练归属 ID，也不决定审核或发布状态。
11. **程序校验并有限修复。** 检查 JSON、训练日数恰好符合请求、编号和动作顺序连续、每天 1–8 个动作、组数和数值范围、同日动作不重复、总组数不超过 40、估算时长容量、声明器械和限制性文本。每个动作至少引用一个本次授权证据 ID，引用必须真实存在。默认带错误信息修复一次；两次仍失败则停止，不写业务。
12. **生成只读草案结果。** 服务端把原请求归属 ID 与已校验内容组合成创建 Payload，内部返回 DRAFT_PREVIEW、requires_confirmation=true、requires_coach_review=true、知识引用、动作证据和 Memory 来源 ID/版本。context_sources 不回显 Memory 原值，但生成模型实际使用了四类训练 Memory 的值。
13. **按本轮意图区分预览和准备创建。** 生成工具自身不创建训练业务。若当前是允许创建的意图，且服务端把本次原话判定为明确要求创建/制定/生成训练计划，Supervisor 会直接取同一份已校验 Payload 准备创建确认单，不再让模型重写执行参数。仅预览且未触发创建判定时，生成结果用于回复。
14. **小程序接收确认状态并展示。** 创建确认单把多日训练参数、身份机构范围、操作摘要和哈希保存，参数加密，LangGraph 暂停。创建确认分支的 display_summary.details 已包含完整 days/items，小程序可以从聊天的 confirmation_summary 或确认单查询接口取得并展示这些参数，教练批准后才写入；实际小程序界面接入尚未验证。对于没有生成确认单的纯预览分支，当前聊天响应没有独立的完整结构化预览对象，生成工具结果进入模型消息/Checkpoint 时也会移除精确 Payload；这类专用预览卡仍需补接口衔接，不能把两种分支混为一谈。
15. **教练批准后执行创建。** 小程序提交确认 ID、批准决定和幂等决定 ID；后端重验教练身份、确认归属和状态，恢复原线程，持久化 JTI、解密已批准参数、签发确认 Token，再经 Registry handler 和 Gateway 请求 Java 训练服务。
16. **Java 再做最终创建授权。** 普通教练操作者必须负责目标学员；学员必须是机构有效成员；参数 coach_id 也必须是该学员在本机构的有效负责教练。操作者与计划指定教练不一定相同，但各自关系都必须合法。
17. **Java 校验业务动作目录后事务写入。** 除训练结构、顺序、日期、时长等检查，还使用 training_exercise_policy 检查动作是否在启用目录、器械是否满足、限制关键词是否冲突。Python 证据校验通过不代表一定符合此业务目录。通过后同事务插入计划、训练日、动作明细并消费 JTI，生成真实 plan/day/item ID；创建 requestId 幂等。计划初始 source=AGENT、status=DRAFT、version=0。
18. **返回草案创建结果。** Java 返回含真实计划 ID 和状态的草案结果，Agent 记录执行成功并向小程序返回完成答复。当前聊天响应没有独立的 plan_id 或业务结果字段，展示新计划 ID 和创建后的草案详情还需衔接查询/接口。DRAFT 尚不可给学员执行；当前单纯创建 DRAFT 没有训练业务通知事件，也没有独立 CREATE 状态审计行，不能讲成创建后立即通知学员执行。
19. **教练另外提交审核。** 计划负责教练或机构管理员发起提交审核，形成新的确认单，批准后 DRAFT/REJECTED → PENDING_REVIEW。事务更新版本、消费 JTI、写状态审计及 TRAINING_PLAN_REVIEW_REQUIRED Outbox，之后经 MQ/Inbox 给计划指定教练生成确定性待审核通知。
20. **负责教练或管理员审核。** 审核同样是新的写确认动作。APPROVE 后进入 APPROVED；REJECT 后进入 REJECTED，驳回必须说明原因。当前没有强制要求另一位教练审核，也没有给 APPROVED/REJECTED 单独生成主动事件通知。
21. **通过后另外确认发布。** APPROVED → PUBLISHED 仍需独立发布确认。发布事务写 TRAINING_PLAN_PUBLISHED Outbox，经 MQ/Inbox 给学员生成事实通知；旧待审核提醒可被抑制。小程序学员端读取已发布计划后，才能按该计划记录 COMPLETED/SKIPPED。

四类代读记忆的作用可以这样理解：

| 正式 Memory 类型 | 可以辅助什么 |
| --- | --- |
| TRAINING_GOAL | 学员已经确认的训练目标 |
| TRAINING_PREFERENCE | 已确认的动作或训练偏好 |
| EQUIPMENT_AVAILABILITY | 已确认的可用器械条件 |
| SCHEDULE_PREFERENCE | 已确认的时间安排偏好 |

类型白名单不是逐值脱敏。模型会收到上述 Memory 的真实训练偏好值，工具结果的来源字段只回传 ID/版本等元数据；当前没有因此开放“教练浏览学员完整 Memory”的通用工具。正常脱敏仅处理终态记忆，所以不会成为 ACTIVE 查询结果；异常已脱敏数据也会由 to_prompt_line 渲染为脱敏标记。

各种情况：

| 情况 | 当前行为或接入边界 |
| --- | --- |
| 小程序已选择学员，但请求没有衔接其真实业务引用 | 当前 AgentChatRequest 不会自动知道所选学员，也不能直接增加未声明的 student_id 请求字段 |
| 教练只说“给小王生成” | 当前没有按姓名可靠定位学员的工具，不能编造 student_id |
| 水平、天数、时长或 ID 缺失/类型不合法 | 生成工具参数校验失败，在工具预算内纠正或补充；不继续生成合格草案 |
| 普通学员请求调用结构化生成预览 | 工具角色校验拒绝；普通建议不等于结构化生成工具权限 |
| 教练未负责目标学员、学员离开机构、机构不在签名范围 | Gateway 拒绝受控代读；最终创建还会再次验权 |
| 多机构身份 | 当前 Registry 要求唯一机构，不能让模型挑其中一个 |
| 学员没有已确认 Memory | 空列表可继续生成；不会拿未确认候选替代 |
| Memory 读取出错 | 明确失败，不静默按无记忆生成 |
| 本次教练输入与长期偏好不同 | prompt 要求本次明确输入优先；当前没有独立 Memory 冲突审批流程 |
| 没有授权知识、生成证据 ID 伪造、结构修复仍失败 | 停止，不创建计划 |
| 新动作有 RAG 证据但未进入业务启用动作目录 | 预览可能通过，Java 创建仍拒绝，事务回滚 |
| CREATE_TRAINING_DRAFT 下模型直接调用 create_draft | 仍需确认和 Java 校验，但不能说该分支必经 RAG、代读 Memory 和生成修复；当前没有强制先调用生成工具 |
| 原话写“只预览”，但也出现“生成训练计划”等创建词 | 当前规则和创建判定较粗，不能保证只凭“预览”两个字就不生成确认单；批准之前仍不会写业务 |
| 生成时有师生关系，批准时关系已失效 | Java 最终验权拒绝，不凭旧授权收据继续创建 |
| 计划指定给另一位有效负责教练 | 当前允许，但之后按 plan.coachId 限制读取/提交/审核/发布，原操作者未必能继续处理 |
| 教练拒绝确认、确认过期 | 只保留未执行确认状态，不创建 DRAFT |
| 网络重试或重复点击批准 | 使用确认决定 ID、业务 requestId 和 JTI 防止重复写入 |
| 审核通过但还没发布 | 学员仍不能读取未发布计划或提交其日执行结果 |
| 审核驳回 | 保留 REJECTED，状态机允许再次提交审核；当前没有通用原地编辑草案工具，内容修订入口需实际接入 |
| 想直接修改已发布计划 | 当前不支持原地修改正式计划，需另建计划/版本和审核发布流程 |
| 小程序退出后希望收到微信订阅提醒 | 当前 IN_APP 仅写消息收件箱；微信订阅通知需要单独接入并获得相应用户授权 |

代码依据：[对话请求字段](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/api/routes/agent.py:40)、[生成参数](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/agent/training_plan_generation.py:32)、[工具角色](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/agent/fitness_tools.py:1071)、[Gateway 受控授权](/Users/a1-6/Desktop/fitness-backend/fitness-core-gateway/src/main/java/com/shuyiwa/fitness/gateway/service/FitnessToolService.java:101)、[授权收据绑定](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/agent/training_plan_generation.py:118)、[代读 Memory 白名单](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/memory/repository.py:369)、[生成/修复/证据](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/agent/training_plan_generation.py:167)、[语义校验](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/agent/training_plan_generation.py:411)、[生成后准备确认](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/agent/supervisor.py:1304)、[训练业务最终授权](/Users/a1-6/Desktop/fitness-backend/fitness-training-service/src/main/java/com/shuyiwa/fitness/training/service/TrainingPlanService.java:43)、[创建事务](/Users/a1-6/Desktop/fitness-backend/fitness-training-service/src/main/java/com/shuyiwa/fitness/training/repository/TrainingPlanRepository.java:54)、[状态迁移](/Users/a1-6/Desktop/fitness-backend/fitness-training-service/src/main/java/com/shuyiwa/fitness/training/domain/TrainingPlanStatus.java:21)。

**12. RAG 知识库文档入库：上传、解析与 OCR、审核、父子分块、向量化和发布**

以管理员上传一份“机构游泳课程预约与取消规则.pdf”为例。小程序管理端可以通过已有后端接口接入文件上传、审查报告和审批；本次没有验证小程序知识管理页面的实际联调。

先回答“是不是先按文档形成父节点，再从父节点拆子节点”：当前确实有父子节点，但不统一把整份文件当作一个父节点。先由格式解析器产生正文、表格及其来源信息，再由切分器生成子块和对应父上下文；发布前才把相同父上下文归并成真正的父节点记录。PDF、DOCX、XLSX 和上传的 Markdown，其父节点粒度并不相同。当前向量化的是子块正文，父节点保存较完整的上下文，供以后命中子块时补充阅读。

完整顺序如下，预分块和正式分块是两个时点，不能把第一次生成审查报告说成已经向量入库：

```text
小程序管理端提交文件 + 文档元数据
  → 验签身份、管理员/机构授权、大小与结构安全检查、上传幂等
  → 首次格式解析 → PDF 按页判断，必要且已启用时调用 OCR
  → 标准解析块 + 来源位置 + 页级状态
  → 预分块 → 解析质量报告
  → 私有暂存原件，保存 PENDING_REVIEW 任务和报告
  → 质量阻断：修复文件/OCR，重新提交；需要专业审核：先完成专业审核
  → 管理员批准 → QUEUED
  → Worker 认领 INDEXING，重读原件并核对哈希/报告/凭证
  → 再次解析/OCR → 最终页路由门禁 → 正式父子分块
  → 正文/发布属性去重和版本判断
  → 为子块批量生成 Embedding，校验数量与维度
  → 同一事务：归档旧版 + 写文档版本 + 父节点 + 子块及向量
  → 提交后记录任务 SUCCEEDED → 有效且有权限的查询才可召回
```

**从小程序提交到批准索引，逐步描述**

1. **管理员选文件并填写元数据。** 小程序接入 `POST /api/v1/admin/knowledge/documents`，以 multipart 提交 file、source_uri、title、document_type、effective_from 等；可设置风险级别、人工审核要求、可见范围、机构、允许角色和失效时间。source_uri 是 `knowledge://` 来源标识，用来管理同一资料的更新，不是要求后端去抓取的网页地址。这条链是管理接口，不经过聊天意图识别、领域子图或大模型选工具。
2. **后端先验证可信身份和文档范围。** 验证签名 AgentContext，再验证管理员角色。GLOBAL 资料须平台管理员；ORGANIZATION 资料绑定获授权机构，未填写机构时只有签名中恰好一个机构才能补足；PRIVATE 的 owner 固定为当前上传者。小程序传来的可见范围是待校验的配置，不能替代服务端授权。
3. **读取文件并做结构安全检查。** 默认源文件上限 20 MiB，按分段累计大小限制。文本检查 UTF-8 和二进制 NUL，PDF 检查文件签名，DOCX/XLSX 检查压缩包路径、加密、宏、成员数量、展开大小与损坏情况。同时计算原件 SHA-256。可选 ClamAV 扫描；只做默认结构检查时会记录 malware_status=NOT_CONFIGURED，不能说已经完成病毒检测。
4. **处理上传重试和同来源冲突。** 可选 Idempotency-Key 用于小程序超时重传；同一用户与完整资料作用域下，相同请求复用原任务，文件内容或当前比较的来源、标题、类型、权限、生效期等属性不一致时冲突。当前比较项不包含 risk_level、requires_human_review，只改这两项重传仍可能复用旧任务和旧报告，不能当作重新分析。同一来源已有不同的活跃上传任务，也不会再悄悄建立第二个并行发布任务。
5. **上传请求内先解析一次。** DocumentParserRegistry 按文件扩展名选解析器，把原始字节转换成 ParsedDocument。标准块包含 TEXT/TABLE、正文、heading_path，以及可获得的页码、工作表、表序号、行范围和 metadata。不是把 PDF 文件字节直接交给 Embedding，也不是上传返回后才第一次解析。各格式的处理见下面的格式表。
6. **PDF 先提取原生文字和表格，再按页判断。** 使用 pdfplumber 读取原生文字层和表格；有可信表格坐标时，正文尽量排除表格区域，减少同一表格重复入库。清洗页眉页脚、重复模板、目录点线、部分字形重复噪声，整理换行和保守的英文断词。目录页不作为正文证据；版面顺序只在满足条件的页面按坐标调整，不能保证任意双栏或复杂排版都准确恢复。
7. **需要文字补齐的页才考虑 OCR。** 根据原生文字字符数、表格、图片面积和文字面积等形成 NORMAL、OCR_REQUIRED、VISUAL_REVIEW_REQUIRED、OCR_AND_VISUAL_REVIEW_REQUIRED 四类。默认原生非空白文字少于 12 字符且未提取到表格时要求 OCR；图片占比较高、文字较少的页面还可能要求专业视觉审核。这是规则判断，未调用视觉大模型判断动作语义；纯扫描页也不会自动被认定为某种健身动作图。
8. **启用 OCR 后调用独立 HTTP 服务。** 默认 rag_ocr_backend=disabled；配置 HTTP provider 后，解析器将整份 PDF 和所需页码发送给 OCR 服务，同步等待结果，默认超时 60 秒。当前没有在这里内置某个 OCR 引擎、单独的 OCR 异步任务队列或轮询协议。上传和正式索引会各自重新解析，因此同一份扫描 PDF 可能调用两次 OCR。
9. **校验 OCR 结果，再合并进解析块。** OCR 必须返回约定版本的 TEXT/TABLE、非空内容、来源页码、置信度和归一化区域坐标。默认置信度门槛为 75%；有效结果按页合并，并去掉与原生文字重复的 OCR 副本。没有补齐或置信度不足的页仍然阻断；OCR_AND_VISUAL_REVIEW_REQUIRED 补字成功后只解除 OCR 部分，仍需专业视觉审核。识别出图上的文字不等于证明图中姿态或安全建议正确。
10. **预分块并生成质量报告，此时不调用 Embedding。** 报告构建器用与生产相同的子块切分规则生成草稿，检查噪声、碎片、重复、父上下文回溯、表格结构、跨页续表、缺页和未完成 OCR 等。报告记录原件哈希、解析器版本、管线版本、审核策略版本、页级状态和所需审核领域。质量检查由程序完成，不让 LLM 自行决定资料是否发布。
11. **暂存原件并创建待审核任务。** 通过 LocalDocumentStorage 或 S3 兼容存储保存原始文件，数据库保存不透明 storage_key。当前发布版不存在时 requested_version=1，否则按同来源和完整作用域取当前版本+1。任务与首版报告在一个数据库事务内保存，任务状态 PENDING_REVIEW，接口返回 202 和任务信息；此时还没有可检索的新文档向量。
12. **先看质量门禁，再看是否需要专业审核。** PASS 表示质量通过且无需额外专业审核，但仍要管理员批准；REVIEW_REQUIRED 要先完成指定领域审核；BLOCKED 需要补齐 OCR、修复来源或重新解析，普通管理员备注不能强行放行。训练方法/运动安全资料要求教练审核，医疗或体重管理等资料要求对应专业资质；声明“只供参考”的 REFERENCE_PRESENTATION 当前被阻断生产索引。
13. **专业审核通过后形成发布凭证。** 审核人读取并核对不可变原件，按报告要求覆盖全文或指定页面/区域，提交领域、结论、范围和意见。服务端验证真实角色、能力、专业资质、机构范围和职责分离，上传者不能审核自己的这一版资料。全部所需领域通过后，数据库保存绑定任务、报告、原件哈希及规则版本的发布凭证；它不是交给小程序的预约确认 Token/JTI，也不能替代缺失的 OCR。此时任务仍待管理员批准。
14. **管理员批准后才入索引队列。** approve 接口复核任务范围、当前报告、哈希和 PASS/有效专业凭证，状态转为 QUEUED。接口可用 FastAPI BackgroundTasks 启动一次处理；独立知识 Worker 也会轮询 PostgreSQL 的 QUEUED 任务，默认每批 10 个。这里没有沿用预约事实事件的 RabbitMQ/Inbox 链路。
15. **Worker 原子认领并重新校验。** 认领把 QUEUED 改 INDEXING、attempt_count 加一；并发 Worker 不能同时认领同一任务。Worker 从暂存读取原始字节，重新验证 SHA-256、报告时效和发布凭证，然后调用 ingest_file 再解析一次。最终页路由门禁在 Embedding 前执行，只有凭证覆盖的纯视觉审核页可以解除视觉限制，OCR 缺失仍停止发布。
16. **正式构建父子块并判断是否需要重建向量。** 按下面的父子规则生成 ChunkDraft，再对解析内容、结构、来源和父上下文计算规范化 checksum，另对权限、标题、生效期等计算发布属性指纹。正文和属性都没变则 SKIPPED_UNCHANGED；正文没变而发布属性改变则 UPDATED_METADATA，原子更新属性并复用向量；正文变化则校验版本，拒绝版本回退或同版本不同内容，再进入新版本索引。这类内容级去重发生在正式解析之后，并不保证省掉 OCR 或解析开销。
17. **只有子块正文批量向量化。** 为文档、父节点和子块生成稳定 ID；相同父上下文的草稿复用父 ID，子块保存 parent_id。RagService 默认每批 32 个，把各子块 content 送给 Embedding provider。标题路径前缀属于子块正文时也会进入 Embedding；不会把整份 PDF 二进制、权限字段或整段父全文另做一套父向量。
18. **校验向量后才做最终发布。** 代码支持本地 SentenceTransformer/BGE-M3 方案，或按部署配置调用兼容的 Embedding HTTP 服务；本地方案在工作线程中编码并归一化。当前配置默认维度和后续迁移目标均为 1024，具体模型仍由部署配置确定。每批检查返回向量非空、维度一致且符合配置，全部批次结束后核对向量数与子块数一致。任何一批失败都不会发布半份新文档；前面已经执行的模型调用也不会因数据库回滚而“撤销”。
19. **文档、父节点、子块与向量原子入库。** repository.replace_document 在一个 PostgreSQL 事务中复核版本冲突、把同来源作用域的旧 PUBLISHED 版本改 ARCHIVED，并写入新文档、父节点、子块和向量。向量由 pgvector 保存，另有由子块文本生成的关键词索引。Embedding 已在事务前完成；发布事务失败时回滚新版本修改，原有已发布版本不会因本次失败被提前隐藏。
20. **记录任务成功，并等待有权限的检索。** 发布提交后，另一步把任务改 SUCCEEDED 并记录 document_id。任务状态更新与知识发布不是跨两个事务的一次原子提交。此后检索仍检查 PUBLISHED、生效时间、可见范围、用户/机构和允许角色；成功入库不等于所有人立刻都能读取。后续问答如何召回、重排和引用见第 10 节。

代码依据：[上传接口](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/api/routes/admin_knowledge.py:183)、[上传/暂存/任务编排](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/rag/admin_service.py:63)、[结构安全与可选杀毒](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/rag/safety.py:64)、[PDF 提取与 OCR 接入](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/rag/formats.py:272)、[页级分流](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/rag/formats.py:481)、[OCR 响应校验](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/rag/ocr.py:95)、[质量报告](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/rag/review.py:129)、[专业审核](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/rag/review_service.py:78)、[后台重验与索引](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/rag/admin_service.py:277)。

**不同文档怎样解析，父节点究竟是什么**

父节点负责补上下文，子节点负责细粒度检索；父节点之间没有保存一棵可递归遍历的多级 parent_id 树。heading_path 保存标题层级，当前关系是“文档版本 → 父上下文记录 → 子块”。

| 来源/入口 | 解析得到什么 | 当前父上下文粒度 | 子块从哪里产生 |
| --- | --- | --- | --- |
| 上传 PDF 的原生正文 | pdfplumber 逐页提取、清洗；页内按标题/编号启发式组织正文，并预拆约 900 字符文本块 | 同一页内该章节的正文上下文；多个解析文本块可共享；标题自身也可形成独立结构块 | 对各解析块继续按子块预算切分，不跨页自动拼整章 |
| PDF 表格 | 提取表格并转带表头的 Markdown，保留页码、表序号等 | 当前页的这张表；相邻页满足条件只标记续表关联，不自动合成全篇一张父表 | 保留表头，按完整数据行组成子块 |
| PDF 的 OCR 块 | 采用 OCR 服务返回并通过校验的 TEXT/TABLE、页码和来源区域 | 当前 HTTP OCR 适配器不读取 parent_content，使用当前 OCR 块的标题前缀和正文 | kind=TABLE 且 table_index 非空才按表格切；缺表序号仍走文本切分，不保证重复表头或重建完整章节 |
| 上传 DOCX | python-docx 按正文顺序读取；Heading 样式维护标题路径，普通段落和表格各成一个解析块 | 正文是“标题路径 + 当前段落”，表格是“标题路径 + 当前整表”；当前没有自动把同标题全部段落并成一个父章节 | 长段落再切；表格按行切；没有内嵌图片 OCR |
| 上传 XLSX | openpyxl 只读读取已保存的单元格缓存值，跳过空表和空行；每个非空工作表转 Markdown 表格 | 当前工作表的整张转换表，带工作表名 | 每个子块重复表头，按数据行分组；不重新计算公式，也没有图片 OCR |
| 通过文件接口上传 MD/Markdown/TXT | UTF-8 解码与规范化后，当前 MarkdownParser 返回一个整文件 TEXT 解析块 | 通常是整份规范化文本；上传链没有再调用按 # 标题分章的 chunk_markdown | 走通用文本切分，不能声称上传 Markdown 已按标题建立多个父章节 |
| 受控代码直接调用 ingest(raw_content) | clean_markdown 后调用 chunk_markdown，按 # 至 ###### 维护标题路径 | 当前标题节的正文加标题路径 | 对该标题节正文切子块；这是另一个服务入口，不等于管理员文件上传当前所走路径 |

这里的“解析块”“父节点”“子节点”是三个不同层次。尤其 PDF 的一个章节父上下文可以覆盖多个约 900 字符解析块，而 DOCX 的多个同标题段落当前仍可能各有自己的父节点。不能仅凭 heading_path 相同，就认为它们一定被并成一个完整章节。

代码依据：[通用解析结构/格式注册](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/rag/formats.py:39)、[Markdown 文件解析](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/rag/formats.py:237)、[PDF 页内章节与父上下文](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/rag/formats.py:925)、[跨页表格关联](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/rag/formats.py:1434)、[DOCX](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/rag/formats.py:1536)、[XLSX](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/rag/formats.py:1585)、[文件解析块切分](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/rag/ingestion.py:298)、[直接文本入口的标题切分](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/rag/ingestion.py:355)。

**子节点到底怎样切，怎样关联父节点**

1. **正文按字符预算切，不按向量相似度聚类。** 当前默认 max_chunk_chars=1200、overlap_chars=120，使用 Python 字符数而不是 Token 数。构造子块时先加入标题路径前缀，再从预算中扣除前缀长度；短解析块整体保留，长块才继续切。
2. **长文本先尝试句子边界，再处理超长句。** 当前切句依赖句末标点后的空白；连续中文没有这类空白时可能作为超长段落走定长窗口。一般按句累积，超预算时输出当前块，并尝试把末尾 120 字符作为下一块开头；超长单句则按窗口和重叠切分。这是确定性文本规则，没有让 LLM 决定每个语义边界。
3. **重叠不是所有相邻块都机械复制 120 字符。** 短块、不同解析块、不同父上下文之间没有统一增加重叠；表格也不使用这一文本重叠。1200 是当前目标预算，现有句子累积/超长表行分支不能保证所有子块都严格小于等于 1200 字符。
4. **表格优先保留完整行。** 每个子块重复表头和分隔行，按能容纳的数据行分组，保存 table_index、row_start、row_end。如果单行过长，当前仍可能超预算；表头本身超预算时才退化为不重叠的文本硬切。行范围是转换表中去除表头后的数据行序号，XLSX 过滤了空行，因此不能直接当作原 Excel 行号。
5. **每个子块保留自己的来源。** 复制标题路径、页码、工作表、表格/行范围及可获得的 OCR 坐标/置信度等 metadata。父上下文优先使用解析器提供的 parent_content，否则使用当前解析块的标题前缀加正文。子块的权限、生效期、机构/所有者是受控字段，不能靠文档正文或 metadata 自行授予权限。
6. **同父草稿归并后持久化。** 发布准备阶段按“标题路径、父正文、来源页、工作表、表序号”识别相同父上下文，复用父节点 ID；每个子块保存 document_id、parent_id、chunk_index、正文和内容哈希等。相同标题但页码不同，不会仅凭标题被自动并为同一个父节点。父全文本身没有 1200 字符硬上限，检索注入时再按证据预算裁剪。

举一个只说明结构的例子，不代表本次已经处理了真实文件：

```text
文档版本 D：机构游泳课程预约与取消规则.pdf / v1
  ├─ 父 P1：第 2 页内“取消时限”章节正文
  │    ├─ 子 C1：该章节的第一个解析文本块，再按子块预算切分的片段
  │    └─ 子 C2：该章节后续片段；parent_id 仍为 P1
  ├─ 父 P2：第 2 页“取消扣费规则”表格全文
  │    ├─ 子 C3：标题/表头 + 第一组完整数据行
  │    └─ 子 C4：相同表头 + 后续完整数据行
  └─ 父 P3：第 3 页继续出现的相关正文或表格
       └─ 子 C5：保留第 3 页来源，不自动归并成第 2 页的父节点

生成向量：Embedding(C1)、Embedding(C2)、Embedding(C3)、Embedding(C4)、Embedding(C5)
父节点 P1/P2/P3：保存上下文文本，通过 parent_id 关联；不单独生成父向量
```

代码依据：[分块参数默认值](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/core/config.py:401)、[文本切分](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/rag/ingestion.py:407)、[表格切分](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/rag/ingestion.py:463)、[父节点归并和子节点关联](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/rag/ingestion.py:240)、[批量向量化](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/rag/service.py:146)、[本地模型编码](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/infrastructure/model_gateway.py:531)。

**向量最终存在哪里，以及哪些情况会停下来**

原件在暂存/对象存储，审核任务和报告有各自记录；生产检索内容主要分为下面三类，不是把全部内容塞进一条向量记录。

| 生产知识表 | 保存内容 | 作用 |
| --- | --- | --- |
| knowledge_documents | 文档 ID、来源、标题、版本、状态、正文校验和、发布属性指纹、机构/所有者、可见范围和生效期等 | 管理同一来源的版本与发布身份 |
| knowledge_parents | 父节点 ID、document_id、父全文、标题路径、来源页/表格及 metadata 等 | 子块命中后补足上下文；无独立 Embedding |
| knowledge_chunks | 子块正文、序号、内容哈希、parent_id、Embedding、权限与生效期、来源 metadata；自动生成的 search_vector | pgvector 语义召回和关键词召回的实际对象 |

以后用户提问，系统先召回子块，再按 parent_id 补父上下文；并不把整份父全文无条件塞给模型。默认上下文总预算 10000 字符、单条证据预算 1800 字符，同一父上下文避免重复加入。

| 情况 | 当前处理或限制 |
| --- | --- |
| 上传 .doc/.xls/.pptx、独立图片或任意网页地址 | 当前文件解析器不直接支持；需要预先转换或另接来源能力 |
| 扫描 PDF 需要 OCR，但未配置 provider | 保留 OCR 阻断，不能发布一份声称已完整识别的知识 |
| OCR 超时、HTTP 错误、无效 JSON/协议、来源坐标不合法 | 本次解析失败；上传阶段不会继续生成可批准任务，索引阶段记录 FAILED |
| OCR 服务正常返回，但漏页或可信度不足 | 未完成的页继续阻断发布，不能把 HTTP 成功当全文识别成功 |
| 动作图仍需专业视觉审核 | 补文字和专业审核分别完成；普通管理员备注不能替代审核 |
| 跨页表格关联歧义或列数不一致 | 保守标记需要复核，默认表格完整性门禁可能阻断；不让 LLM 猜表 |
| 报告 PASS，但管理员没有批准 | 仍为 PENDING_REVIEW，不生成生产向量 |
| 专业审核全部通过 | 形成发布凭证，仍需管理员批准排队；拒绝则任务 REJECTED，修订后重新上传 |
| 已批准但 Worker/后台处理没有执行 | QUEUED 不会自动等于已入库 |
| Worker 认领后，正式解析/Embedding/发布失败 | 任务 FAILED；管理员可手动 retry，默认最多 3 次总尝试，不是自动无限重试；首次上传解析失败时尚未创建任务 |
| Worker 在认领后进程硬退出 | 当前没有 INDEXING 租约超时回收，独立 Worker 只轮询 QUEUED，不能承诺所有中断都自动恢复 |
| 已发布成功，但任务成功状态保存失败 | 两次提交之间存在状态不一致窗口，需核对 document_id/版本，不能只凭任务状态判断库里一定没有内容 |
| 正文未改，仅修改角色或生效时间 | 通过发布属性指纹触发元数据更新，复用已有向量 |
| 正文改变并采用更高版本 | 当前重建整份文档全部子块的向量，不是只向量化被修改的几个子块 |
| 更换 Embedding 模型或维度 | 需受控重建并保持查询/文档向量一致；不能只改配置后继续混用旧模型向量 |
| 新版向量生成或发布失败 | 不提前隐藏旧已发布版本；已执行的 OCR/Embedding 开销不会回滚 |
| 文档已入库，但未到生效时间、已过期或无权限 | 检索过滤，不因知道 document_id 就能读出 |

这里描述的是当前代码链路及默认配置，没有实际调用 OCR/Embedding、连接业务数据库或验证对象存储部署。生产配置还要求相应存储、安全扫描和 OCR 能力正确配置；接口存在不能代替实际入库验收。

代码依据：[原件存储](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/rag/storage.py:34)、[增量与版本判断](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/rag/ingestion.py:170)、[最终页面门禁](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/rag/ingestion.py:612)、[三类知识记录原子发布](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/rag/repository.py:264)、[任务认领/完成/失败/重试](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/rag/admin_repository.py:436)、[1024 维迁移](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/migrations/versions/20260812_0009_bge_m3_dimensions.py:17)、[关键词索引](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/migrations/versions/20260812_0005_hybrid_retrieval.py:16)、[检索权限与有效期](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/rag/repository.py:462)、[父上下文证据预算](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/rag/service.py:48)、[默认关闭的 OCR/生产配置检查](/Users/a1-6/Desktop/fitness-backend/fitness-agent-service/app/core/config.py:505)。

**最后用于核对的状态与事件表**

| 类型 | 关键状态或事件 | 它表示什么 |
| --- | --- | --- |
| 对话结果 | CLARIFICATION_REQUIRED | 当前信息不足，需要补充；业务尚未完成 |
| 对话结果 | CONFIRMATION_REQUIRED | 精确动作已准备，等用户决定；业务尚未写入 |
| 确认授权 | PENDING / APPROVED / REJECTED | 用户是否批准，与实际执行结果分开 |
| 确认执行 | NOT_STARTED / RUNNING / SUCCEEDED / UNKNOWN 等 | 业务执行阶段；UNKNOWN 需要对账 |
| 预约创建事件 | APPOINTMENT_CREATED | 创建事务已成功，可异步生成事实通知 |
| 预约改约事件 | APPOINTMENT_RESCHEDULED | 原预约已更新，可异步生成事实通知 |
| 预约取消事件 | APPOINTMENT_CANCELLED | 取消和退课时已提交，生成事实通知及重新预约候选 |
| 训练日事件 | TRAINING_DAY_COMPLETED / TRAINING_DAY_SKIPPED | 某日执行结果已写入，不等于全计划都完成 |
| 训练周期事件 | TRAINING_PLAN_COMPLETED | 当前实现表示全部训练日已有结果，包含跳过 |
| 主动候选 | PENDING / PROCESSING / DEFERRED / IGNORED | 候选处理阶段，不是业务写执行阶段 |
| 主动候选 | NOTIFICATION_QUEUED / COMPLETED | 通知待投递/该候选处理已完成，用户阅读另记 |
| 记忆候选 | PENDING / APPROVED / REJECTED / EXPIRED | 记忆提议的确认阶段 |
| 正式记忆 | ACTIVE | 已确认记忆，仍需检查有效期、脱敏和版本 |
| 知识上传任务 | PENDING_REVIEW / QUEUED / INDEXING / SUCCEEDED / FAILED / REJECTED | 上传与索引阶段；不等同于所有用户可检索 |
| 知识审查报告 | PASS / REVIEW_REQUIRED / BLOCKED | 解析质量与专业审核门禁；PASS 仍需管理员批准 |
| 知识发布版本 | PUBLISHED / ARCHIVED | 当前发布版与历史版，召回还要检查有效期和权限 |
| 业务 Outbox | PUBLISHED | broker 已确认接受，不等于 Agent 已处理 |
| 通知 Outbox | PUBLISHED | 当前 IN_APP 已写收件箱，不等于已读 |

后台相关 Worker、业务 Outbox Publisher、模型服务和基础设施必须按环境启用。当前默认配置中，主动事件消费、主动决策和 Scheduler 等独立进程并非自动全部开启。本文确认的是代码结构；部署开启情况、消息传输和数据库实际执行结果需要单独验证。
