# 健身 Agent 状态与枚举字典

本文档是代码中英文状态值的业务解释。数据库和接口继续使用稳定的英文值，代码注释、接口文档和
开发沟通必须同时说明中文含义。赛事、作品和活动运营遗留模块不纳入本字典。

## 1. 写操作确认状态

确认状态和业务执行状态必须分开，不能使用一个 `status` 同时表达“用户是否允许”和“Java 业务是否成功”。

### 1.1 授权状态 `authorization_status`

| 英文值 | 中文含义 | 说明 |
| --- | --- | --- |
| `PENDING` | 待确认 | Agent 已生成不可变动作摘要，等待用户明确批准或拒绝；不能执行。 |
| `APPROVED` | 已批准 | 用户已批准该摘要、资源和参数哈希；还必须通过凭证签发、资源版本和 Gateway 权限校验。 |
| `REJECTED` | 已拒绝 | 用户拒绝本次动作；原确认单不能重新改成批准，修改参数必须创建新确认单。 |
| `EXPIRED` | 已过期 | 超过确认单 TTL；不能批准、签发凭证或执行。审计事件必须保留。 |
| `CANCELLED` | 已撤销 | 用户或服务在执行领取前主动撤销；不能恢复为批准。 |

授权流转：

```text
PENDING ──批准──> APPROVED ──执行条件满足──> 保持 APPROVED
   │                  │
   ├─拒绝────────────> REJECTED
   ├─超时────────────> EXPIRED
   └─撤销────────────> CANCELLED
```

`APPROVED` 不是“执行成功”，它只表示授权事实成立。可重试失败不会修改授权状态。

### 1.2 执行状态 `execution_status`

| 英文值 | 中文含义 | 说明 |
| --- | --- | --- |
| `NOT_STARTED` | 未执行 | 尚未领取执行权；批准、凭证和资源版本仍需在真正执行前重新校验。 |
| `RUNNING` | 执行中 | 某个恢复请求已原子领取执行权；其他并发恢复请求不能再次领取。 |
| `SUCCEEDED` | 成功 | Java Gateway/训练服务返回真实成功，不能由模型自行声明。 |
| `FAILED_RETRYABLE` | 可重试失败 | 网络超时、临时服务不可用等可恢复错误；重试只重置执行状态，并必须签发新 JTI。 |
| `FAILED_FINAL` | 最终失败 | 参数、权限、资源版本或业务状态等确定性错误；不能自动重试。 |

执行流转：

```text
NOT_STARTED ──领取执行权──> RUNNING ──成功──> SUCCEEDED
                                  ├─临时错误──> FAILED_RETRYABLE ──重排──> NOT_STARTED
                                  └─确定性错误> FAILED_FINAL
```

### 1.3 确认事件 `event_type`

`CREATED` 创建、`APPROVED` 批准、`REJECTED` 拒绝、`EXPIRED` 过期、`CANCELLED` 撤销、
`ISSUED` 签发 JTI、`CLAIMED` 领取执行权、`CONSUMED` 消费一次性凭证、`REQUEUED` 可重试重排、
`EXECUTION_SUCCEEDED` 执行成功、`EXECUTION_FAILED` 执行失败。事件不可更新或删除来“修正”历史。

## 2. 训练计划状态

训练计划状态属于 Java 训练业务事实，不等于 Agent 确认状态：

| 英文值 | 中文含义 | 学员能否执行 |
| --- | --- | --- |
| `DRAFT` | 草案 | 否 |
| `PENDING_REVIEW` | 待教练审核 | 否 |
| `APPROVED` | 教练审核通过、待发布 | 否 |
| `REJECTED` | 教练驳回 | 否 |
| `PUBLISHED` | 已发布正式计划 | 是，仅限本人且仍需 Java 权限校验 |

允许的业务流转是：`DRAFT/REJECTED -> PENDING_REVIEW -> APPROVED/REJECTED -> PUBLISHED`。
`PUBLISHED` 不原地回退或静默修改，内容变化应产生新版本或新计划。

## 3. 知识库任务与审核状态

### 3.1 上传/索引任务 `knowledge_ingestion_jobs.status`

`PENDING_REVIEW` 待管理员或专业审核；`QUEUED` 已通过门禁、等待 Worker；`INDEXING` 正在解析、
切片、Embedding 和事务发布；`SUCCEEDED` 已完成；`FAILED` 失败，可在预算内重试；`REJECTED` 被拒绝，
不能进入检索。

### 3.2 知识文档版本 `knowledge_documents.status`

`DRAFT` 草稿、`PUBLISHED` 已发布且可被召回、`ARCHIVED` 历史归档且不参与线上召回。文档状态必须
与权限范围、有效期和审核凭证一起过滤。

### 3.3 索引重建

- 批次：`QUEUED` 排队、`INDEXING` 执行、`SUCCEEDED` 完成、`FAILED` 批次失败。
- 明细：`PENDING` 待领取、`INDEXING` 处理中、`SUCCEEDED` 成功、`SKIPPED` 内容未变化而跳过、
  `FAILED` 该文档失败。批次统计不能掩盖失败明细。

### 3.4 解析质量报告

`PASS` 质量达到门禁，可进入管理员审批；`REVIEW_REQUIRED` 需要指定领域的教练/专业人员审核；
`BLOCKED` 存在缺页、OCR、严重碎片或安全问题，必须重新解析或修复，不能由普通管理员备注绕过。

结论严重级别：`WARNING` 提醒；`REVIEW_REQUIRED` 需要人工处理；`BLOCKING` 直接阻断发布。

### 3.5 PDF 页面路由

`NORMAL` 原生文字足够、可正常解析；`OCR_REQUIRED` 缺少文字层、必须 OCR；
`VISUAL_REVIEW_REQUIRED` 图片承载动作或姿态信息、必须人工视觉审核；
`OCR_AND_VISUAL_REVIEW_REQUIRED` 同时需要 OCR 和视觉审核，人工审核不能替代 OCR。

## 4. 其他核心枚举

- 文档块：`TEXT` 普通文本，`TABLE` 保留表头和行范围的表格文本。
- 可见范围：`GLOBAL` 全局，`ORGANIZATION` 机构范围，`PRIVATE` 所有者私有；这是 ACL 维度，不是生命周期。
- 工具审计：`started` 已开始，`succeeded` 真实成功，`failed` 真实失败；不能替代业务状态。
- Agent 路由：`FITNESS_COACHING` 健身指导，`BOOKING` 预约，`OPERATIONS` 经营分析，
  `UNSUPPORTED_LEGACY` 明确拒绝赛事/作品/活动遗留业务。
- 文件结构安全：`STRUCTURAL_VALIDATED` 结构检查通过；恶意软件状态 `NOT_CONFIGURED` 表示未接入外部
  杀毒服务、`CLEAN` 表示外部扫描通过。`NOT_CONFIGURED` 绝不能解释为安全。

## 5. 新增状态的强制要求

新增或修改状态时，必须同时提交：

1. 英文值、中文含义、进入条件、终态定义和允许流转图。
2. 代码中的 `Literal`/`enum` 注释，以及状态转换方法的失败边界说明。
3. 数据库 `CHECK` 约束、表/字段注释和接口契约说明。
4. 正常、非法、并发、幂等和重试测试；尤其要证明失败不会被伪装成成功。
5. 本文档和 `docs/ai-fitness-agent-development-roadmap.md` 的实施状态同步。
