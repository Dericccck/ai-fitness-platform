# AI Fitness Platform 架构理解 Q&A

> 面向源码阅读和 Agent 面试准备的精简版问答。每个问题保留：核心结论、源码位置、一个理解示例。

---

## Q1：`AgentContextVerifier` 的作用是什么？

### A：验证请求中的 `X-Agent-Context`，确认身份上下文可信，并返回 `AgentIdentity`。

```text
X-Agent-Context
  → 验证签名和有效期
  → 解析 sub / orgs / roles
  → AgentIdentity
  → Supervisor / Tool Registry / Gateway
```

它不负责登录、不查询用户数据库，也不直接执行预约。

源码：`fitness-agent-service/app/infrastructure/agent_context.py`。

---

## Q2：`AgentContextVerifier` 验证哪些内容？

### A：验证 Token 的格式、签名、身份字段和时间字段。

典型 Claims：

```json
{
  "sub": "student-1001",
  "orgs": ["org-shanghai-01"],
  "roles": ["STUDENT"],
  "iat": 1788256800,
  "exp": 1788257100,
  "nonce": "request-context-abc"
}
```

当前代码要求 `sub`、`orgs`、`roles`、`iat`、`exp`、`nonce` 存在；`capabilities` 和 `qualifications` 可选。

---

## Q3：`X-Agent-Context` 的第一段和第二段是什么？

### A：当前项目使用两段式格式：

```text
Base64URL(payload).Base64URL(signature)
```

执行：

```python
parts = token.split(".")
```

得到：

```text
parts[0] = Base64URL(payload)
parts[1] = Base64URL(signature)
```

第一段解码后是身份 JSON；第二段解码后是签名字节，不是 JSON。

源码：`fitness-agent-service/app/infrastructure/agent_context.py:73-83`。

---

## Q4：`parts[0]` 解码后到底有什么？

### A：它是完整 payload，不只有 `sub`、`orgs`、`roles`。

当前实现至少需要：

```json
{
  "sub": "student-1001",
  "orgs": ["org-shanghai-01"],
  "roles": ["STUDENT"],
  "iat": 1788256800,
  "exp": 1788257100,
  "nonce": "request-context-abc"
}
```

如果只有：

```json
{
  "sub": "student-1001",
  "orgs": ["org-shanghai-01"],
  "roles": ["STUDENT"]
}
```

因为缺少 `iat`、`exp`、`nonce`，当前 `verify()` 会拒绝它。

---

## Q5：`parts[0]` 和 `parts[1]` 分别在哪一步解码？

### A：都在 `AgentContextVerifier.verify()` 中解码。

```python
payload = _decode_base64url(parts[0])
signature = _decode_base64url(parts[1])
```

随后：

```text
payload bytes
  → json.loads(payload)
  → claims dict

signature bytes
  → 与重新计算的签名比较
```

签名验证使用的是第一段解码后的原始 payload 字节，不是重新格式化后的 JSON。

---

## Q6：第二段 `signature` 是谁生成的？

### A：由上游认证服务生成，前端只负责原样转发。

HS256 示例：

```text
认证服务
  → 生成 payload
  → HMAC-SHA256(secret, payload)
  → 得到 signature
  → Base64URL 编码
  → 拼接 Token
```

前端收到完整 Token 后发送：

```http
X-Agent-Context: encoded_payload.encoded_signature
```

前端不应该持有 `secret`，否则它可以伪造管理员身份。

当前仓库主要实现验证端，没有实现上游认证服务的签发逻辑。

---

## Q7：为什么解码后还要验证 signature？

### A：Base64URL 只是编码，不保证内容没有被篡改。

攻击者可以直接把：

```json
"roles": ["STUDENT"]
```

改成：

```json
"roles": ["SYSTEM_ADMIN"]
```

但没有 secret 或私钥重新生成合法 signature 时：

```text
重新计算的 expected
  ≠ Token 中的 signature
  → 验签失败
  → HTTP 401
```

---

## Q8：`expected = hmac.new(...)` 和前端 signature 的关系是什么？

### A：两者应该来自同一个 payload 和同一个 secret。

认证服务生成：

```python
signature = hmac.new(secret, payload, hashlib.sha256).digest()
```

Agent 验证时重新生成：

```python
expected = hmac.new(secret, payload, hashlib.sha256).digest()
```

然后比较：

```python
hmac.compare_digest(expected, signature)
```

```text
相同 → payload 没被篡改，继续处理
不同 → Token 无效，拒绝请求
```

---

## Q9：为什么已经有 HS256，还需要 RS256？

### A：不是两次验证，而是两种可选算法。

```text
HS256 → shared secret 签名和验证
RS256 → private key 签名，public key 验证
```

HS256：

```text
认证服务、Agent、Gateway 都持有同一个 secret
```

RS256：

```text
只有认证服务持有私钥
Agent/Gateway 只持有公钥
```

当前平台服务较多，生产使用 RS256 可以避免某个验证服务拿到 secret 后伪造用户 Token。本地开发默认使用 HS256，生产配置要求 RS256。

---

## Q10：`signing_key_id`、`key_ring` 和 `kid` 有什么作用？

### A：用于识别密钥并支持密钥轮换。

Token 中可能有：

```json
{
  "alg": "RS256",
  "kid": "context-key-2026-09"
}
```

验证器根据 `kid` 选择密钥：

```text
当前 kid → 当前密钥
旧 kid   → key ring 中的旧密钥
未知 kid → 拒绝请求
```

---

## Q11：`verification_public_key_ring` 和 JWKS 是什么？

### A：它们是 RS256 模式下获取公钥的两种方式。

静态公钥环：

```json
{
  "context-key-2026-09": "-----BEGIN PUBLIC KEY-----..."
}
```

JWKS：

```text
https://auth.example.com/.well-known/jwks.json
```

查找顺序大致是：

```text
根据 kid 查本地公钥环
  → 找不到时查询 JWKS
  → 缓存公钥
  → 验证 RSA signature
```

---

## Q12：这些配置和 `sub`、`orgs`、`roles` 是一回事吗？

### A：不是。

验签配置来自环境变量或 Secret Manager：

```text
secret / algorithm / key_id / JWKS URL / TTL
```

它们回答：

> 应该如何验证 Token？

Token Claims 来自上游认证服务：

```text
sub / orgs / roles / iat / exp / nonce
```

它们回答：

> 当前请求是谁发起的？属于哪些机构？拥有什么角色？

---

## Q13：`AgentContextVerifier` 的实现是在 `agent.py` 里面吗？

### A：`agent.py` 里有调用点，具体实现位于 `agent_context.py`。

调用点：

```python
identity = request.app.state.context_verifier.verify(x_agent_context)
```

源码：`fitness-agent-service/app/api/routes/agent.py:78-84`。

实现：

```text
fitness-agent-service/app/infrastructure/agent_context.py
  → class AgentContextVerifier
  → def verify()
```

可以简单理解为：

```text
agent.py          = HTTP 路由和调用方
agent_context.py  = 验签基础设施和实现方
```

---

## Q14：`lifespan()` 是什么时候执行的？

### A：它属于进程级生命周期，通常在第一笔请求之前执行，不是每个请求执行一次。

```text
Uvicorn 启动
  → import app.main
  → 创建 FastAPI(lifespan=lifespan)
  → ASGI startup
  → 执行 lifespan() 的 yield 之前部分
  → yield
  → 应用 ready
  → 接收请求
```

源码：`fitness-agent-service/app/main.py:77-315`。

---

## Q15：`lifespan()` 中初始化什么？

### A：初始化进程内复用的数据库、Redis、模型、Gateway、Tool Registry 和 Supervisor。

核心过程：

```text
Database
  → Redis Cache
  → CheckpointStore
  → AgentContextVerifier
  → ModelGateway / Reranker
  → RagService / MemoryService
  → GatewayClient
  → Tool Registry
  → ConfirmationService
  → SessionLockManager
  → CheckpointStore.start()
  → Supervisor
  → yield
```

例如：

```python
app.state.context_verifier = AgentContextVerifier(...)
app.state.supervisor = Supervisor(...)
```

之后 `chat()` 才能从 `request.app.state` 取到这些对象。

---

## Q16：红框里的 `payload: AgentChatRequest` 是哪里来的？

### A：它来自 HTTP 请求 Body，由 FastAPI 自动解析成 Pydantic 对象。

前端发送：

```json
{
  "conversation_id": "conversation-9001",
  "message": "帮我约明天下午王教练的私教",
  "locale": "zh-CN"
}
```

FastAPI 自动完成：

```text
JSON Body
  → AgentChatRequest 校验
  → payload: AgentChatRequest
  → chat(payload, request, ...)
```

定义位置：`fitness-agent-service/app/api/routes/agent.py:34-44`。

`AgentChatRequest` 当前只有：

```text
conversation_id
message
locale
```

多传 `user_id` 等字段会因为 `extra="forbid"` 被拒绝。

---

## Q17：`message` 和 `conversation_id` 分别是什么？

### A：`message` 是用户输入的文本；`conversation_id` 是前端维护的聊天会话 ID。

例如：

```json
{
  "conversation_id": "conversation-9001",
  "message": "我还有多少课时？"
}
```

后端使用：

```text
payload.message
  → SupervisorRequest.user_message
  → classify_route / ModelGateway / Tool Calling

payload.conversation_id + AgentIdentity
  → conversation_thread_id()
  → SHA-256
  → fitness:<hash>
  → LangGraph Checkpoint thread_id
```

`conversation_id` 不是用户 ID，也不是 `X-Agent-Context` 中的 `sub`。当前仓库负责接收它，通常由前端在创建聊天会话时生成和保存。

---

## Q18：红框里的其他参数分别是什么？

### A：它们分为“框架对象”和“请求头”，不是都由前端以 JSON Body 传入。

| 参数 | 来源 | 作用 |
|---|---|---|
| `payload` | JSON Body，FastAPI 解析 | 用户消息、会话 ID、语言 |
| `request` | FastAPI/Starlette 自动注入 | 访问 `app.state` 中的 verifier、Supervisor 等运行时对象 |
| `x_agent_context` | `X-Agent-Context` 请求头 | 必填；验证当前用户身份和权限上下文 |
| `x_request_id` | `X-Request-ID` 请求头 | 可选；定位一次请求，缺失或非法时生成 UUID |
| `x_trace_id` | `X-Trace-ID` 请求头 | 可选；串联跨服务调用，缺失或非法时使用 `request_id` |
| `x_confirmation_token` | `X-Confirmation-Token` 请求头 | 可选；由服务端运行时上下文透传给 Gateway |

例如一次首次对话可能是：

```http
POST /api/v1/agent/chat
X-Agent-Context: <signed-context>
X-Request-ID: req-9001
X-Trace-ID: trace-abc
```

```text
payload.message
  → SupervisorRequest.user_message

request.app.state
  → context_verifier / supervisor

x_agent_context
  → 验签
  → GatewayRequestContext.signed_context

x_request_id / x_trace_id
  → 日志、Trace、跨服务定位

x_confirmation_token
  → 如果当前请求已经带有确认凭证，则透传
  → GatewayRequestContext.confirmation_token
```

源码：`fitness-agent-service/app/api/routes/agent.py:62-108`；请求标识规范化在 `fitness-agent-service/app/api/middleware/request_context.py`。

需要特别注意：当前仓库的正常确认流程中，浏览器不会自己生成或提交
`X-Confirmation-Token`。用户第二次请求调用的是确认决定接口；服务端在恢复执行时才
生成确认 Token，再传给 Java Gateway。`chat()` 中保留这个可选参数，表示接口支持透传
已有的运行时确认凭证，但它不是前端确认按钮必须提交的字段。

---

## 预约案例主线：从用户消息到 MySQL 写入

下面把前面多个问题串成一条真实流程。示例用户是：

> 帮我约明天下午王教练的私教。

示例标识：

```text
用户             = student-1001
机构             = org-shanghai-01
会话             = conversation-9001
第一次请求 ID     = req-booking-1001
确认单 ID         = confirm-001
决定请求 ID       = decision-booking-1001
一次性凭证 ID     = jti-8f31a0...
```

### 第 1 步：第一次请求进入 Agent

```http
POST /api/v1/agent/chat
X-Agent-Context: <signed-context>
X-Request-ID: req-booking-1001
X-Trace-ID: trace-booking-1001
Content-Type: application/json
```

```json
{
  "conversation_id": "conversation-9001",
  "message": "帮我约明天下午王教练的私教",
  "locale": "zh-CN"
}
```

调用链：

```text
FastAPI
  → app/api/routes/agent.py:chat()
  → AgentContextVerifier.verify(X-Agent-Context)
  → AgentIdentity(sub=student-1001, orgs=[org-shanghai-01], roles=[STUDENT])
  → conversation_thread_id()
  → fitness:<脱敏后的 SHA-256>
  → Supervisor.invoke()
  → classify_route() = BOOKING
```

这一阶段主要使用 LangGraph Checkpoint PostgreSQL：

```text
checkpoints
checkpoint_blobs
checkpoint_writes
```

这些表保存历史 messages、路由、工具调用次数和可能的 pending_confirmation_id。
签名上下文、Confirmation Token 和明文预约参数不会放进 Checkpoint。

### 第 2 步：调用预约可用性检查 Tool

```text
fitness.booking.availability.check.v1
  → ToolRegistry.invoke()
  → GatewayClient.check_booking_availability()
  → Java AgentToolController
  → Booking Service
```

Booking Service 可能读取：

| 表 | 读取内容 |
|---|---|
| `contract` | 合同是否有效、剩余课时是否大于 0 |
| `course` | 课程是否存在、是否启用 |
| `user_and_coach` | 学员和教练关系 |
| `login_user_authority` | 教练是否属于当前机构 |
| `appointment` | 教练时间段是否已有预约 |
| `system_settings` | 机构当天是否营业 |
| `vacation_record` | 教练当天是否请假 |

这一步只读预检，不会扣课时、创建 appointment 或写入 agent_booking_operation。

### 第 3 步：创建待确认单并暂停

预约创建是写操作，因此会执行：

```text
fitness.booking.create.v1
  → ConfirmationService.prepare()
  → 规范化预约参数
  → 计算 payload_hash
  → AES-GCM 加密精确参数
  → 写入确认单
  → interrupt()
```

Agent PostgreSQL 的 `agent_action_confirmations` 会写入：

```text
id                   = confirm-001
request_id           = req-booking-1001
tool_id              = fitness.booking.create.v1
action               = CREATE_APPOINTMENT
authorization_status = PENDING
execution_status     = NOT_STARTED
payload_hash         = a4f1c9d8...
payload_ciphertext   = <加密后的预约参数>
```

同时写入事件表：

```text
agent_action_confirmation_events
  → event_type = CREATED
```

Checkpoint 只需要保存：

```text
pending_confirmation_id = confirm-001
```

此时：

```text
确认单：PENDING
预约：尚未创建
课时：尚未扣减
Confirmation Token：尚未生成
```

### 第 4 步：用户批准确认单

前端展示确认卡片后，用户点击“确认”，调用确认决定接口：

```http
POST /api/v1/agent/confirmations/confirm-001/decisions
X-Agent-Context: <signed-context>
X-Trace-ID: trace-booking-1001
Content-Type: application/json
```

```json
{
  "decision": "APPROVE",
  "decision_request_id": "decision-booking-1001"
}
```

服务端用 URL 中的确认单 ID，加上签名身份范围查询：

```sql
SELECT *
FROM agent_action_confirmations
WHERE id = 'confirm-001'
  AND subject_user_id = 'student-1001'
  AND organization_id IN ('org-shanghai-01')
FOR UPDATE;
```

然后更新：

```text
authorization_status = APPROVED
decision_request_id  = decision-booking-1001
approved_at          = 当前时间
version              = version + 1
```

并追加 APPROVED 事件。重复提交相同的 decision_request_id 会返回原批准状态，
不会重复批准。

---

### 第 5 步：服务端恢复暂停的 Agent 图

批准落库后，确认接口内部调用：

```python
supervisor.resume_confirmation(
    confirmation_id="confirm-001",
    ...
)
```

Supervisor 随后执行：

```python
Command(resume={"confirmation_id": "confirm-001"})
```

Command(resume) 是服务端代码产生的，不是浏览器传入的。恢复时会读取 Checkpoint，
找到暂停的 confirmation 节点，再从 Agent PostgreSQL 读取确认单并检查：

```text
authorization_status = APPROVED
execution_status     = NOT_STARTED
```

确认通过后，服务端从 payload_ciphertext 解密出原始预约参数。

### 第 6 步：生成 jti 和 Confirmation Token

`ConfirmationService.prepare_execution()` 会生成一次性 `jti`：

```python
jti = record.credential_jti or str(uuid4())
```

例如：

```text
jti = jti-8f31a0...
```

Agent PostgreSQL 更新：

```text
agent_action_confirmations.credential_jti = jti-8f31a0...
```

然后生成短时 Confirmation Token。Token Payload 中包含：

```json
{
  "confirmation_id": "confirm-001",
  "tool_id": "fitness.booking.create.v1",
  "action": "CREATE_APPOINTMENT",
  "request_id": "req-booking-1001",
  "payload_hash": "a4f1c9d8...",
  "jti": "jti-8f31a0...",
  "exp": 1788257100
}
```

接着确认单进入：

```text
execution_status       = RUNNING
credential_consumed_at = 当前时间
```

并追加 CLAIMED、CONSUMED 事件。这里的 CONSUMED 是 Agent 侧领取执行权的记录；
Booking MySQL 中还会进行一次业务侧的确认凭证消费。

### 第 7 步：Java Gateway 验证确认凭证

Agent 把服务端运行时上下文交给 Gateway：

```text
GatewayRequestContext
  → signed_context
  → request_id=req-booking-1001
  → confirmation_token=<服务端生成的 Token>
```

Java Gateway 会验证：

```text
Token 签名
用户 sub
工具 tool_id
动作 action
机构 organization_id
资源 resource
request_id
payload_hash
过期时间 exp
jti
```

验证通过后，Gateway 将已验证声明转成内部请求头，发送给 Booking Service：

```text
X-Confirmation-Id
X-Confirmation-JTI
X-Confirmation-Tool-ID
X-Confirmation-Action
X-Confirmation-Organization-ID
X-Confirmation-Resource
X-Confirmation-Payload-Hash
X-Request-ID
```

浏览器不会看到 Confirmation Token，也不会自己生成它。

### 第 8 步：Booking Service 最终事务写库

Booking Service 的 create() 使用 @Transactional，在最终写入前再次读取业务事实：

```text
contract
  → SELECT ... FOR UPDATE，锁定合同行

course
  → 检查课程是否启用

user_and_coach
  → 检查学员和教练关系

login_user_authority
  → 检查教练机构权限

appointment
  → 检查时间冲突

system_settings
  → 检查营业日

vacation_record
  → 检查教练请假
```

然后在同一个 MySQL 事务中写入：

```text
contract
  → remaining_class_hours - 1
  → version + 1

appointment
  → 插入正式预约

agent_booking_confirmation_consumption
  → 写入 jti，表示业务服务已消费凭证

agent_booking_operation
  → 写入 request_id，记录预约操作已完成

agent_booking_audit
  → 写入预约审计

agent_booking_outbox
  → 写入 APPOINTMENT_CREATED 事件
```

这些操作在一个事务中。任何一步失败，都会整体回滚：

```text
不扣课时
不保留 appointment
不保留成功的幂等记录
```

### 第 9 步：执行结果回写 Agent

Booking 成功后，Agent 更新：

```text
agent_action_confirmations.execution_status = SUCCEEDED
agent_action_confirmations.finished_at = 当前时间
```

并写入 EXECUTION_SUCCEEDED 事件，最后返回：

```text
已完成王教练明天下午的私教预约。
```

### 这条主线涉及的表

```text
Agent PostgreSQL
  ├─ checkpoints
  ├─ checkpoint_blobs
  ├─ checkpoint_writes
  ├─ agent_action_confirmations
  └─ agent_action_confirmation_events

Booking MySQL
  ├─ contract
  ├─ course
  ├─ user_and_coach
  ├─ login_user_authority
  ├─ appointment
  ├─ system_settings
  ├─ vacation_record
  ├─ agent_booking_confirmation_consumption
  ├─ agent_booking_operation
  ├─ agent_booking_audit
  └─ agent_booking_outbox

### 关键源码入口

```text
Agent HTTP 入口
  → fitness-agent-service/app/api/routes/agent.py

确认决定接口
  → fitness-agent-service/app/api/routes/confirmations.py

Supervisor、interrupt、Command(resume)
  → fitness-agent-service/app/agent/supervisor.py

确认单创建、jti 签发准备、执行状态
  → fitness-agent-service/app/confirmation/service.py
  → fitness-agent-service/app/confirmation/repository.py

确认单表结构
  → fitness-agent-service/migrations/versions/20260813_0013_agent_action_confirmations.py

Gateway 确认 Token 验证
  → fitness-core-gateway/src/main/java/com/shuyiwa/fitness/gateway/security/ConfirmationTokenVerifier.java

Booking 事务和幂等
  → fitness-booking-service/src/main/java/com/shuyiwa/fitness/booking/service/BookingService.java
  → fitness-booking-service/src/main/java/com/shuyiwa/fitness/booking/repository/BookingRepository.java

Booking Agent 表结构
  → fitness-booking-service/src/main/resources/db/migration/V20260815_001__create_booking_agent_tables.sql
  → fitness-booking-service/src/main/resources/db/migration/V20260815_002__create_booking_operation_tables.sql
```
```

---

## Q19：只读请求的完整链路是什么？

### A：以“我还有多少课时？”为例。

```text
lifespan() 初始化完成
  → POST /api/v1/agent/chat
  → agent.py:chat()
  → AgentContextVerifier.verify()
  → AgentIdentity
  → conversation_thread_id()
  → Supervisor.invoke()
  → classify_route() = CUSTOMER_SERVICE
  → customer_service_agent 子图
  → ModelGateway.chat_with_tools()
  → fitness.contract.list.v1
  → ToolRegistry.invoke()
  → GatewayClient.list_contracts()
  → Java AgentToolController
  → FitnessToolService
  → JdbcFitnessReadRepository
  → MySQL
  → 合同和剩余课时返回
  → Supervisor 再次调用模型生成回答
  → AgentChatResponse
  → HTTP 200
```

---

## Q20：预约写请求为什么需要两次 HTTP 请求？

### A：第一次生成确认单，第二次用户确认后恢复执行。

第一次：

```text
POST /api/v1/agent/chat
  → BOOKING
  → contract.list / availability.check
  → booking.create
  → ConfirmationService.prepare()
  → AES-GCM 保存参数
  → interrupt()
  → 返回 CONFIRMATION_REQUIRED
```

此时还没有写 Booking MySQL，也没有扣课时。

第二次：

```text
POST /api/v1/agent/confirmations/{id}/decisions
  → decision=APPROVE
  → ConfirmationService.decide()
  → Command(resume)
  → 重新读取确认单
  → 签发 Confirmation Token
  → ToolRegistry
  → Java Gateway
  → BookingService.create()
  → MySQL 事务
```

---

## Q21：为什么 `availability.check` 后还要在创建预约时再次检查？

### A：因为可用性检查只是预检，不是锁。

```text
15:00  availability.check：空闲
15:01  其他用户抢先预约
15:02  当前用户确认
15:03  BookingService.create() 再次检查：冲突
```

最终 Booking Service 会回滚：

```text
不创建预约
不扣课时
不写成功 Outbox
```

预检负责用户体验，最终事务负责并发正确性。

---

## Q22：为什么 Agent 和 Java Gateway 要验签两次？

### A：因为它们是两个独立的安全边界。

```text
Agent API
  → Python AgentContextVerifier
  → 生成 AgentIdentity

Agent 调用 Gateway
  → X-Agent-Context 继续传递
  → Java AgentContextInterceptor
  → Java AgentContextVerifier
  → 最终业务权限校验
```

第一层防止无效请求进入 Agent；第二层防止错误的 Agent 调用越过 Java 业务边界。

---

## Q23：三类 Token 分别做什么？

### A：它们解决不同问题。

| 凭证 | 作用 |
|---|---|
| `X-Agent-Context` | 当前用户是谁、属于哪些机构、具有什么角色 |
| `X-Internal-Service-Token` | 请求是否来自受信任的服务 |
| `Confirmation Token` | 用户是否确认了这一次具体写操作 |

预约创建示例：

```text
X-Agent-Context
  → student-1001 属于 org-shanghai-01

X-Internal-Service-Token
  → 请求来自受信任的 Agent/Gateway

Confirmation Token
  → 用户确认创建这一个具体预约
```

---

## Q24：Worker 和 `lifespan()` 是什么关系？

### A：API 进程由 Uvicorn 自动触发 lifespan，独立 Worker 显式复用它。

```python
async with lifespan(app):
    consumer = ProactiveRabbitConsumer(...)
    worker = ProactiveEventWorker(...)
```

API 进程处理：

```text
HTTP → chat()
```

主动事件 Worker 处理：

```text
RabbitMQ → Agent Inbox → Notification Outbox
```

通知 Worker 处理：

```text
Notification Outbox → IN_APP 收件箱
```

---

## Q25：这套架构一句话怎么总结？

### A：

> `lifespan()` 负责进程启动时装配基础设施；用户请求进入 `agent.py:chat()` 后，先验证 `X-Agent-Context`，再由 Supervisor 路由到领域子图，通过 Tool Registry 调用受控工具。只读请求经 Java Gateway 查询业务事实，写请求经过确认和 `Command(resume)`，最终由业务服务在 MySQL 事务中完成写入，并通过 Outbox/RabbitMQ/Agent Inbox/Notification Worker 完成异步通知。

核心职责分工：

```text
LLM
  → 理解文本、选择工具、生成回答

Agent 程序
  → 路由、Schema、上下文绑定、确认和恢复

Java Gateway
  → 业务访问入口和最终权限边界

业务服务
  → 业务规则、锁、幂等、事务和数据库写入

Outbox / Inbox / Worker
  → 跨服务事件和通知可靠性

---

## 预约链路追问补充

## Q26：这些问题是不是都在同一个流程里？

### A：是的，基本都围绕同一条“预约写操作”链路。

    第一次 /chat
      → 读取 Checkpoint
      → BOOKING 路由
      → availability.check
      → 创建 PENDING 确认单
      → interrupt 等待用户决定

    第二次确认请求
      → APPROVE
      → Command(resume)
      → 生成 jti 和 Confirmation Token
      → Gateway 验证
      → Booking Service 最终事务写入
      → 消费确认凭证
      → 更新确认单为 SUCCEEDED

前面的问题分别观察了不同层：

    Checkpoint       → Agent 如何记住历史
    加密参数          → 如何保存待确认动作
    confirmation_id → 如何找到确认单
    jti              → 如何标识一次性凭证
    payload_hash     → 如何绑定具体参数
    FOR UPDATE       → 如何保护同一合同
    GET_LOCK         → 如何防止并发请求

## Q27：创建确认单时会操作哪些表？

### A：主要操作 Agent PostgreSQL，不会直接写 Booking 业务表。

    agent_action_confirmations
      → 插入确认单
      → 保存 request_id、payload_hash、加密参数
      → authorization_status=PENDING
      → execution_status=NOT_STARTED

    agent_action_confirmation_events
      → 插入 CREATED 事件

    checkpoints / checkpoint_blobs / checkpoint_writes
      → 保存图暂停前后的状态
      → 保存 pending_confirmation_id

此时不会写入 appointment、contract.remaining_class_hours 或
agent_booking_operation，因为用户还没有批准。

## Q28：用户确认时，服务端如何找到确认单？

### A：通过 URL 中的 confirmation_id，再结合签名身份范围查询。

    POST /api/v1/agent/confirmations/confirm-001/decisions

confirm-001 对应 agent_action_confirmations.id。服务端还会验证：

    subject_user_id = 当前 X-Agent-Context 中的 sub
    organization_id ∈ 当前 X-Agent-Context 中的 orgs

因此知道确认单 ID 还不够，必须是原用户且仍在允许的机构范围内。

## Q29：为什么保存加密后的预约参数？

### A：为了执行“用户看到并批准的原动作”，而不是相信第二次请求重新提交的参数。

第一次确认的是：

    王教练
    2026-09-02 15:00
    contract-001

第二次请求只表达：

    decision=APPROVE
    decision_request_id=decision-booking-1001

服务端从确认单解密第一次保存的参数后执行。前端不能在第二次请求中偷偷替换成李教练
或其他时间。

## Q30：jti 在哪个阶段生成、哪个阶段消费？

### A：批准后由 Agent 生成，Gateway 验证，Booking Service 在业务事务中消费。

    APPROVE
      → ConfirmationService.prepare_execution()
      → uuid4() 生成 jti
      → 保存 credential_jti
      → 写入 Confirmation Token
      → Gateway 验签并转发
      → Booking Service 插入 confirmation_consumption

例如：

    confirmation_id = confirm-001
    request_id      = req-booking-1001
    jti             = jti-8f31a0...

Agent 的 CONSUMED 事件表示已经领取执行权；Booking MySQL 的
agent_booking_confirmation_consumption 记录表示业务服务真正消费了凭证。

## Q31：payload_hash 是什么？

### A：它是确认范围的 SHA-256 摘要，不是加密后的参数。

当前代码会把以下内容纳入哈希：

    tool_id
    organization_id
    action
    resource_type
    resource_id
    expected_resource_version
    具体预约 payload
    target_status

例如确认的是“王教练 + 15:00 + contract-001”，执行时如果变成“李教练 + 20:00”，
哈希范围就不一致，可以拒绝执行。文档中的 abc123... 只是缩写，真实值是 64 位十六进制
SHA-256 字符串。

## Q32：confirmation_id 在消费表中可以重复吗？

### A：消费表没有把 confirmation_id 设为唯一，但正常流程通常不会重复。

    jti        → PRIMARY KEY
    request_id → UNIQUE
    confirmation_id → 普通字段

它重点保护：

    同一个 jti 不能消费两次
    同一个业务 request_id 不能执行两次

同一确认单理论上可以产生不同的凭证尝试，但当前项目通常会复用同一业务 request_id，
所以正常重试不会创建第二条成功消费记录。

## Q33：Agent 在批准后崩溃，能恢复吗？

### A：批准已落库但尚未恢复时可以；进入 RUNNING 后存在需要对账的窗口。

    decide() 已提交 APPROVED
      → Agent 崩溃
      → 重试相同 decision_request_id
      → 发现 APPROVED + NOT_STARTED
      → 继续 Command(resume)

如果 jti 已经保存但执行权还没有领取，恢复时会复用已有 jti。

如果状态已经是 RUNNING，但 Agent 在调用 Gateway 前崩溃，当前代码不会自动把它恢复成
NOT_STARTED。如果 Booking 已成功而 Agent 尚未回写 SUCCEEDED，Booking 的 request_id 幂等
记录可以避免重复创建预约，但确认单状态可能需要恢复任务或人工对账。

对于明确记录为 FAILED_RETRYABLE 的失败，服务端会清理旧 jti、重新入队，并在下一次尝试时
生成新的 jti。

## Q34：FOR UPDATE 会锁住整张 contract 表吗？

### A：通常是 InnoDB 行级锁，只锁定匹配到的合同记录。

    用户 A 操作 contract-001
      → 锁住 contract-001

    用户 B 也修改 contract-001
      → 等待 A 提交或回滚

    用户 C 操作 contract-002
      → 通常不受影响

锁的持有时间是当前事务的生命周期，事务提交或回滚后释放。

## Q35：GET_LOCK 使用 Redis 吗？

### A：不使用 Redis，是 MySQL 内置的会话级命名锁。

    GET_LOCK('req:req-booking-1001', 5)
      → 返回 1：获取成功
      → 返回 0：5 秒内没有获取到
      → 返回 NULL：MySQL 执行错误

执行结束后调用：

    RELEASE_LOCK('req:req-booking-1001')

它不是数据库表记录，也不是 Redis Key，锁与 MySQL 连接会话绑定，连接断开通常会释放。
当前代码还会使用 coach:机构:教练:日期 形式的教练日期锁，防止不同 request_id 同时
抢同一教练当天的时段。
```
