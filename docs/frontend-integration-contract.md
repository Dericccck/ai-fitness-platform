# AI 健身 Agent 前端接入契约

本文是当前后端能力与 `fitness-web` 前端交付范围的接入说明。它只覆盖健身业务，不包含赛事、作品、活动运营、短信、Push、人工转接、图片动作框选和复杂训练扩展。

## 1. 当前交付边界

当前仓库已新增独立的 `fitness-web` React/Vite/TypeScript 第一阶段工作台，但它不是小程序、管理端或教练端的最终独立产品。后端已经提供稳定的 Agent API、确认 API、Memory API、通知 API、知识审核 API 和管理员运维 API；后续正式端仍应按本文接入。

前端不应直接调用 Java Gateway 的 `/internal/**` 接口。Gateway 是 Agent 到业务事实库之间的内部边界，浏览器只能调用认证服务允许暴露的 BFF/Agent API。

## 2. 认证和请求约定

前端登录后由认证服务或 BFF 转发短时签名 `X-Agent-Context`。前端不能自己生成或修改其中的用户 ID、机构 ID、角色、能力和资质。

Agent API 的公共请求头：

| Header | 是否必需 | 用途 |
| --- | --- | --- |
| `X-Agent-Context` | 是 | 认证服务签发的短时身份和机构范围 |
| `X-Request-ID` | 建议 | 一次用户请求的幂等/排障标识 |
| `X-Trace-ID` | 建议 | 跨 Agent、Gateway 和业务服务的链路标识 |
| `X-Confirmation-Token` | 否 | 仅保留给受控内部联调；正常前端流程不提交 |

前端至少要做到：

1. 每次用户点击提交生成稳定的请求 ID，网络重试复用同一个 ID；不要因为重试而生成第二个业务动作。
2. 不在前端保存确认 Token、原始工具参数、参数密文、JTI 或 LangGraph thread_id。
3. 收到 `401` 时刷新登录/身份上下文；收到 `403` 时提示权限范围，不替换成另一个用户或机构 ID 重试。
4. 收到 `409` 时重新读取资源或确认单，不能盲目重复提交；收到 `503` 时使用有限退避并显示“服务暂时不可用”。
5. 所有错误展示 `request_id` 或响应头中的请求标识，便于从结构化日志定位，但不把内部异常和 SQL 展示给用户。

## 3. 三类角色的页面和业务

### 3.1 学员端

| 页面/组件 | 使用接口 | 业务说明 |
| --- | --- | --- |
| Agent 对话页 | `POST /api/v1/agent/chat` | 询问训练、动作、课程、合同、预约和已发布训练计划；创建预约或训练计划时只生成待确认动作 |
| 确认卡片 | `GET /api/v1/agent/confirmations/{id}`、`POST /api/v1/agent/confirmations/{id}/decisions` | 展示脱敏摘要；用户批准/拒绝后由服务端恢复 `interrupt()` 并执行，前端不接触原始参数 |
| 确认单撤销 | `POST /api/v1/agent/confirmations/{id}/revocations` | 仅撤销尚未开始执行的确认单 |
| 训练计划 | 通过 Agent 对话读取已发布计划 | 学员只能看到 `PUBLISHED`，不能修改原始计划 |
| 训练日执行 | 通过 Agent 对话记录执行结果 | 当前仅支持训练日 `COMPLETED`/`SKIPPED` 和简短备注，不扩展逐组数据、疼痛/疲劳量表或自动调参 |
| Memory 收件箱 | `GET /api/v1/agent/memory-candidates/inbox`、`POST /api/v1/agent/memory-candidates/{id}/decisions` | 模型提出候选，学员明确批准后才成为正式 Memory；打开收件箱不会自动批准 |
| 已确认 Memory | `GET /api/v1/agent/memories`、`PUT /api/v1/agent/memories/{id}`、`POST /api/v1/agent/memories/{id}/revocations` | 查看、纠正或撤销本人有效 Memory；纠正和撤销使用版本号防止旧页面覆盖新数据 |
| 通知中心 | `GET /api/v1/agent/notifications`、`POST /api/v1/agent/notifications/{id}/read` | 查看站内通知和标记已读；已读不是业务事实写入，不需要 `interrupt()` |
| 通知设置 | `GET/PUT /api/v1/agent/notifications/preferences` | 配置站内通知开关、安静时间和同类通知最小间隔 |

### 3.2 教练端

| 页面/组件 | 使用接口 | 业务说明 |
| --- | --- | --- |
| Agent 对话页 | `POST /api/v1/agent/chat` | 查询授权学员、课程、合同、预约和训练资料；生成训练计划只能形成草案 |
| 训练计划审核 | 通过 Agent 对话触发审核/发布确认流程 | 训练计划必须经过教练审核；学员不能执行未发布计划 |
| 健身知识审核 | `GET /api/v1/knowledge-review/jobs/{job_id}`、`GET /source`、`POST /decisions` | 仅具备签名审核能力的教练可审核对应健身内容；普通 `COACH` 角色不自动获得专业/临床审核资质 |
| 能力初始化 | `GET /api/v1/agent/capabilities` | 根据签名角色动态生成菜单和按钮，不硬编码权限 |

### 3.3 管理员端

| 页面/组件 | 使用接口 | 业务说明 |
| --- | --- | --- |
| 能力目录 | `GET /api/v1/agent/capabilities` | 初始化角色可见能力；使用 `ETag` 和 `catalog_version` 缓存 |
| 经营指标配置 | `GET /api/v1/admin/operations/metric-catalog` | 获取固定指标、口径、支持的时间桶和环比/同比能力，不允许前端提交 SQL |
| 经营审计 | `GET /api/v1/admin/operations/query-audits` | 分页查看查询审计摘要，不返回 SQL、Prompt 或预约明细 |
| 知识上传 | `POST /api/v1/admin/knowledge/documents` | multipart 上传 PDF、DOCX、XLSX、Markdown/TXT 等资料，进入待审核任务 |
| 知识任务 | `GET /api/v1/admin/knowledge/jobs`、`GET /jobs/{id}` | 展示解析、扫描、审核和索引任务状态 |
| 质量报告 | `GET /api/v1/admin/knowledge/jobs/{id}/review-report` | 展示 `PASS`、`REVIEW_REQUIRED`、`BLOCKED`、页级画像和质量门禁结果 |
| 知识审批/重试 | `POST /jobs/{id}/approve`、`/reject`、`/retry` | `BLOCKED` 不能强制发布；审批后才允许进入索引流程 |
| 索引重建 | `POST /reindex/jobs`、`GET /reindex/jobs`、`GET /reindex/jobs/{id}`、`POST /reindex/jobs/{id}/retry` | 用于模型、切片或索引版本升级，不直接修改 Java 健身业务事实 |
| 通知模板 | `/api/v1/admin/notifications/templates...` | 当前只支持 `IN_APP` 模板的草稿、审核、发布和审计 |
| 投递运维 | `GET /api/v1/admin/notifications/delivery-attempts` | 查看站内通知投递成功、可重试失败和最终失败摘要 |

## 4. Agent 对话和确认流程

前端只需要实现下面的状态流程，不需要理解 LangGraph 内部对象：

```text
发送消息
  ↓
普通只读回答 ───────────────→ 展示回答和引用
  ↓ 写操作
返回 confirmation_id + 脱敏摘要
  ↓
展示确认卡片
  ├─ 拒绝 → POST decisions(REJECT) → 展示已拒绝
  ├─ 撤销 → POST revocations      → 展示已撤销
  └─ 批准 → POST decisions(APPROVE)
             ↓
          服务端恢复 interrupt、验签并调用 Gateway
             ↓
          刷新确认详情，展示最终执行状态
```

`POST /chat` 返回写操作草案时，前端应根据 `confirmation_id` 和 `confirmation_summary` 渲染确认卡片；如果当前响应没有确认单，则按普通回答处理。批准接口返回的是脱敏确认单状态，不保证同步返回完整业务对象，页面应以确认单状态和后续业务查询为准。

## 5. 状态展示规则

### 5.1 确认单

授权状态 `authorization_status`：

| 英文字段 | 页面文案 | 前端动作 |
| --- | --- | --- |
| `PENDING` | 待确认 | 显示批准、拒绝；过期后刷新 |
| `APPROVED` | 已批准 | 若执行中则轮询详情；不能再次批准 |
| `REJECTED` | 已拒绝 | 只读展示 |
| `EXPIRED` | 已过期 | 提示重新发起业务请求 |
| `CANCELLED` | 已撤销 | 只读展示 |

执行状态 `execution_status`：

| 英文字段 | 页面文案 | 前端动作 |
| --- | --- | --- |
| `NOT_STARTED` | 未执行 | 已批准但尚未领取执行权 |
| `RUNNING` | 执行中 | 禁止重复提交，按确认 ID 刷新 |
| `SUCCEEDED` | 执行成功 | 展示完成并刷新业务数据 |
| `FAILED_RETRYABLE` | 可重试失败 | 使用原决定请求 ID 重试或提示稍后再试 |
| `FAILED_FINAL` | 最终失败 | 停止自动重试，展示 request ID 并引导重新发起 |

### 5.2 训练计划

`DRAFT` 草案、`PENDING_REVIEW` 待审核、`APPROVED` 已审核、`REJECTED` 已驳回、`PUBLISHED` 已发布。学员端只渲染 `PUBLISHED`，教练端重点处理 `PENDING_REVIEW`，管理员端可以查看审计但不能把未审核计划直接伪装成已发布。

### 5.3 知识任务和通知

- 知识任务：`PENDING_REVIEW` 待审核、`QUEUED` 已批准待处理、`INDEXING` 索引中、`SUCCEEDED` 成功、`FAILED` 失败、`REJECTED` 已拒绝。
- 质量报告：`PASS` 可进入审批、`REVIEW_REQUIRED` 需要指定审核能力、`BLOCKED` 必须修复或补充资料。
- 通知投递：`STARTED` 已开始、`SUCCEEDED` 成功、`RETRYABLE_FAILED` 可重试失败、`FINAL_FAILED` 最终失败；`DEFERRED` 和 `SUPPRESSED` 是通知策略结果，不应显示为系统故障。

## 6. RAG 和引用展示

Agent 的回答引用来自：

```text
POST /api/v1/agent/knowledge/search
```

前端只展示后端返回的已完成权限过滤的引用信息，例如文档标题、页码、章节和来源摘要。不要根据返回文本自行拼接机构权限，也不要把原始文档暂存路径、对象存储密钥或内部 SQL 展示给用户。图片密集页在真实 OCR 不可用时可能处于 `REVIEW_REQUIRED`/`BLOCKED`，这是质量门禁的正常结果，不应由前端强行标记为可用。

## 7. 当前明确不做的前端页面

以下内容不属于本阶段前端交付，也不应因为后端存在遗留代码就新增菜单：

- 短信、Push 和 Memory 模拟短信；
- 自动人工转接、自动改派或自动关闭客服工单；
- 图片区域框选、动作图片结构化标注；
- 身体测量、疼痛、疲劳、训练反馈和教练阶段性自动调整；
- 赛事、作品、活动运营及其报表；
- Linux/GPU 环境下的真实 OCR 验收。

## 8. 前端联调验收清单

1. 三种签名角色分别请求 `/api/v1/agent/capabilities`，确认目录和缓存不会串角色。
2. 学员发起一次训练计划创建请求，确认只返回草案/确认单，未批准前数据库没有训练计划写入。
3. 使用同一个 `decision_request_id` 重试批准，确认不会重复创建计划或重复扣课时。
4. 学员确认 Memory 候选，刷新收件箱和正式 Memory，确认 `PENDING → APPROVED → ACTIVE` 的可见结果。
5. 教练审核并发布训练计划，确认学员在发布前不可见、发布后可执行。
6. 管理员读取经营指标目录和审计，确认教练、学员收到 `403`，前端不会把权限错误当成空数据。
7. 管理员上传知识资料，分别验证质量报告 `PASS`、`REVIEW_REQUIRED`、`BLOCKED` 的按钮状态。
8. 断网后恢复页面，使用原确认 ID 和请求 ID刷新，不新建业务动作；批准后的非终态确认单应自动或手动刷新到最终执行状态。

当前 `fitness-web` 已完成能力目录、Agent 对话、业务工作台快捷入口、确认卡片和统一错误处理；训练计划、预约、客服和管理员运维的结构化页面，以及正式认证/BFF 接入仍按路线逐步补齐。前端构建通过不等于上述端到端验收全部完成。
