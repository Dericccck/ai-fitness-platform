# 正式登录、密钥轮换、确认请求体绑定与 SAE 接入交接

日期：2026-10-06。与 `enterprise-runtime-upgrade-20261006.md` 配套阅读。

本轮完成的是代码、配置契约、回归测试和本地镜像验收。没有发布云资源、推送镜像、迁移现有业务库，也没有把测试认证端部署成正式服务。

## 一、现有环境检查结论

仓库旧后端的 Spring Security/Frog 登录使用手机号、密码/验证码和服务端会话。`FrogUserDetailsService` 的账号分支仍存在 MD5 密码匹配，不能视为新的安全认证中心，也不能由 AI 服务根据手机号自行推定机构和角色。

没有找到可直接使用的“签发短期 AgentContext + 刷新 + 撤销”认证接口配置。当前机器也没有可直接执行发布的阿里云 CLI/SAE 应用清单。项目中的阿里云媒体相关依赖不等于 SAE 部署权限。这不表示你没有云账号，只表示本轮没有得到可核验的目标环境。

因此采用可配置、失败关闭的 BFF 登录适配，不创建虚构生产账号，不默认授予管理员，不从模型或浏览器读取角色作为授权依据。

## 二、登录改造前后

| 环节 | 之前 | 现在 |
| --- | --- | --- |
| 浏览器身份 | 联调粘贴短期签名上下文 | 正式模式只持有随机 HttpOnly Cookie；上下文和刷新凭证留在服务端 |
| 权限来源 | 已有签名验证和 Java RBAC | 保留原验证/RBAC；额外验证签发端返回的用户、单一机构范围 |
| 刷新 | 需要人工重新签发 | 到期前刷新；单窗口 single-flight，跨窗口 Redis 互斥，旋转写回使用 CAS |
| 退出 | 移除联调身份 | 先撤销本地 Redis 会话，再尽力通知认证端撤销刷新凭证 |
| 跨站请求 | 显式 Header 联调 | Cookie 写请求需要精确 Origin、CSRF Token；禁止 Cookie 与显式身份混用 |
| 生产未配置 | 容易继续使用演示入口 | 生产配置契约要求正式登录；前端生产构建不读 VITE_AGENT_CONTEXT，未配置不降级为演示身份 |

### 2.1 服务端会话

`app/infrastructure/browser_auth.py` 使用 256 位随机会话 ID，Redis 键只保存该 ID 的 SHA-256 摘要。签名上下文、刷新凭证、CSRF Token 等会话内容采用 AES-GCM 加密，AAD 绑定 Redis 会话键。

默认绝对寿命 8 小时、空闲寿命 30 分钟；访问只延长空闲时间，不能突破绝对寿命。`EXPIRE` 不会重新创建被退出流程删除的键。Redis 不可用时返回认证依赖不可用，不放行匿名请求。

生产 Cookie 为 `__Host-fitness-session`，使用 Secure、HttpOnly、SameSite=Strict、Path=/，不设置 Domain。浏览器拿到的会话响应只有 subject、organization_id、roles、到期时间和 CSRF Token，绝不含 AgentContext/refresh_token。响应设置 `Cache-Control: no-store`。

接口为 `/api/v1/auth/config`、`/login`、`/session`、`/refresh`、`/logout`。业务中间件解密 Cookie 会话后，在服务端注入原有 `X-Agent-Context`，因此原来的会话隔离、Tool Registry 和 Java Gateway RBAC 仍生效。

`/session` 可以返回短期签名已到期但绝对会话仍有效的元数据，供前端发起刷新；业务接口会再次验证短期签名，到期即拒绝。不能用长会话寿命替代短期身份校验。

### 2.2 刷新与业务 POST

前端每次动作先读取当前会话，发现其他窗口切换用户、机构或角色时，清除旧会话关联存储并阻止当前动作，要求重新载入工作台。到期不足 60 秒时刷新，多个并发动作共用一次刷新。

服务端使用 15 秒 Redis owner 锁，锁内重新读取会话。刷新响应不得换用户或机构，角色变化则更新后由前端识别授权范围变化。写回比较原密文：并发退出已删会话时，刷新不能把它复活。

刷新调用失败或取消时撤销本地会话，不重复使用结果未知的刷新凭证。认证 API 有有界超时。业务 POST 返回 401 时不会先刷新再重发业务操作，避免重复预约、重复写计划或重复经营动作。

### 2.3 Origin、限流与反向代理

允许来源必须是精确 scheme/host/port，不接受通配符、路径、查询、userinfo。生产上游、Origin 都必须为 HTTPS，Cookie 必须 Secure。登录每账号 10 次/分钟、每可信客户端地址 30 次/分钟，Redis 原子计数，不在错误中回显密码或上游响应正文。

Web Nginx 覆盖 `X-Forwarded-For` 为实际连接方地址，不透传任意客户端自报地址。部署时配置 Uvicorn 的 `FORWARDED_ALLOW_IPS`，只信任 Web 代理的固定地址或受控网段，不使用 `*`。如果前面还有负载均衡，必须由受控 `real_ip`/代理链解析真实来源；未配置时所有请求可能按代理地址合并限流，不能认为 IP 配额已经精确区分用户。

同源部署是当前方案的前提，前端使用 `credentials: same-origin`。不要只把 API 地址改成跨域并打开宽泛 CORS 来绕过 Cookie/CSRF 约束。

会话、CSRF 的设计采用认证技能的服务端会话原则，并参考 [OWASP 会话管理](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html) 和 [CSRF 防护指南](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html)。这不是完整安全审计证明；HttpOnly 也不能消除同源 XSS 风险。

## 三、你之后需要提供的认证端契约

这不仅是补一个凭证。需要已有认证服务实现以下接口，或在选定的认证中心前增加可信桥接服务：

```text
AGENT_BROWSER_AUTH_UPSTREAM_URL = https://受控认证服务/internal/agent-sessions

POST {base}/login
  Header: X-Internal-Service-Token（BFF 的机器凭证）
  Body: {"username":"...","password":"...","organization_id":"..."}

POST {base}/refresh
  Header: 同上
  Body: {"refresh_token":"..."}

login/refresh 成功返回 200:
  {"agent_context":"...","refresh_token":"旋转后的不透明刷新凭证"}

POST {base}/logout
  Header: 同上
  Body: {"refresh_token":"..."}
  成功返回 200 的 JSON 对象，例如 {"revoked":true}
```

认证失败使用 401/403，依赖失败使用非 200；BFF 不把上游原始错误转给浏览器。上游地址固定、禁止跳转，不允许请求参数指定上游 URL。机器凭证不是用户权限，也不能写进 VITE_* 或源码。

AgentContext 是本项目既有的两段格式 `base64url(claims_bytes).base64url(signature)`，不是标准三段 OIDC JWT。认证端需要按既有验证协议签名：claims 至少包含 sub、orgs、roles、iat、exp、nonce；版本化配置使用 alg、kid，生产采用 RS256/JWKS，私钥留在认证端。签名有效期不能超过 Agent/Gateway 的共同最大 TTL（默认 300 秒）。

`orgs` 必须恰好是本次选定且真实有权访问的一个机构，不能把跨机构角色合并后返回。roles、capabilities、qualifications 由认证端从可信账号/权限数据读取。刷新端必须支持单次旋转、重放检测、撤销及有效期控制，并对重新授权后的范围重新核验。

普通 OIDC 提供方不会直接返回本项目 AgentContext，不能把 access_token 改名就当适配成功。若选择只支持授权码/PKCE 的提供方，需要按其协议建设回调/桥接，再接这个内部契约；本轮没有声称通用 OIDC 登录已经落地。

最少需要补充：认证服务选型及接口、组织/角色数据来源、上游机器凭证、公钥/JWKS、测试账号、正式 HTTPS Origin。不要直接复用旧 MD5 登录作为新的生产认证中心；密码迁移、哈希升级和账号归属需要专项处理。

## 四、AES-GCM 密钥轮换

### 4.1 之前与现在

之前只持有一个解密密钥，部分读取逻辑直接拒绝非当前版本。切换主密钥后，旧确认单、流任务结果、Memory 候选和会话摘要可能无法读取。

现在写入始终使用当前 key/version；读取按记录中的版本选取当前密钥或只读旧密钥环，未知版本直接失败，不尝试所有密钥、不回退当前密钥。浏览器会话使用同一版本化能力。AES-GCM 的原有 AAD 校验保留，因此旧密文换用户/资源/记录也不能解密。

配置项：

```text
AGENT_CONFIRMATION_ENCRYPTION_KEY_VERSION=enc-v2
AGENT_CONFIRMATION_ENCRYPTION_KEY_BASE64=由 Secret 管理的新密钥
AGENT_CONFIRMATION_ENCRYPTION_DECRYPTION_KEY_RING={"enc-v1":"由 Secret 管理的旧密钥"}
```

这里只展示格式，不包含实际密钥。旧环不能重复当前版本，Base64 严格校验，AES 密钥仅允许 16/24/32 字节。不同环境分别管理，禁止把生产密钥带到测试。

### 4.2 可执行的轮换顺序

1. 盘点旧版本的确认单、chat_runs、Memory 候选、摘要、Redis 会话和备份；备份密钥与数据的恢复路径分别受控。
2. 先让所有 API/Worker 加载旧+新解密能力，再分批切换当前写入版本。不能让新写入与旧读取实例不兼容。
3. 验证旧记录可读、新记录标记新版本、AAD 篡改失败；检查解密错误和用户操作失败。
4. 回滚写入版本时也保留已写入的新密钥为只读密钥，否则回滚后又无法读新密文。
5. 等待保留窗口和会话到期，或通过经过验收的重新加密任务完成迁移后，才能考虑退役旧键；备份恢复仍可能需要旧键。

本轮实现了双读单写和旧记录恢复，不是“所有历史数据已重新加密”。没有自动批量重加密/销毁密钥，不能未经盘点删除旧键。AgentContext/确认签名密钥的 kid/JWKS 轮换与此 AES 数据密钥轮换是不同流程。

## 五、确认参数与实际 HTTP 请求体绑定

之前的 `payload_hash` 绑定动作、资源、版本与精确参数的规范化封装，不等于实际 HTTP body 的逐字 SHA-256。这两个概念不能混写。

现在签发确认 Token 时额外加入：

```text
body_binding_version = 1
request_body_sha256 = SHA256(实际发送给 Gateway 的 UTF-8 字节)
```

Python 在 HTTP 发送前使用同一个规范化序列化器生成 bytes，并用 `content=bytes` 发送，不让签名和 httpx 各自做一次不同 JSON 序列化。训练日 day_id→dayId 的字段映射共享同一个构造函数。

Gateway 在 MVC 反序列化前读取有界原始 body（上限 2 MiB），保存 SHA-256 并提供可重复读取包装。确认验签后要求绑定版本/摘要存在且与实际 body 恒定时间匹配。修改数字、字符串、空值或实际字节会拒绝；没有过滤器属性时也失败关闭。原 payload_hash、主体/机构/工具/动作/资源/请求范围、TTL 和事务 JTI 幂等校验仍保留。

Gateway 转发到训练/预约/客服时，RestTemplate 拦截器在实际 DTO 序列化后计算新的 body 摘要，内部服务在 MVC 前验证该 Header 与收到的字节。此处是可信内部链路的纵深保护，不是把原 Agent 签名原样端到端贯穿不同 DTO：知道内部服务 Token 且能改动链路的攻击者仍可能同时伪造 body 和摘要，因此内网权限/TLS、内部 Token 与 Gateway 信任边界不可省略。

已存在的加密确认单没有新增必填数据库列；再次准备执行时会生成带新绑定声明的 Token。但旧 Token 不含新声明，旧 Gateway 不转发新 Header；本次需要成套协议发布：暂停写入口、排空旧请求、依次更新内部业务服务/Gateway/Agent，再恢复确认写入。不能不做兼容评估就把新旧二进制放在同一写流量池做任意灰度；回滚也必须匹配协议。

本轮 Python/Java 原始字节、UTF-8/空值、篡改、缺失摘要、超限和重复读取单元测试已通过。真实生产请求、不同代理配置和并发写入仍需预发布验证，不把单元测试说成线上全链路安全审计。

## 六、SAE 接入与发布计划

`deployment/sae/release.example.json` 是无 Secret 的应用清单模板，当前包含占位符，正常会被拒绝。先在专用测试命名空间创建独立应用并配置 VPC/Secret/服务 DNS/镜像权限/探针/资源/终止宽限期，再填应用 ID 和已推送镜像的 sha256 摘要地址。

核心六应用：训练、预约、客服、Gateway、Agent API、Web。最多 11 个 Worker 使用同一个 Agent 镜像的不同 `python -m app.*` 入口，各自是独立 SAE 应用；模板只列一个示例，其他角色按发布范围增加，不自动部署未启用业务。

```bash
# 在 fitness-agent-service 目录执行，仅生成计划，不调用阿里云 API
.venv/bin/python scripts/sae_release_plan.py \
  --config /受控目录/sae-release.json --report ../tmp/sae-release-plan.json
```

生成的阶段为：业务服务并行 → Gateway → Agent API → 启用 Worker → Web。PG/MySQL 备份、兼容迁移和确认写流量暂停是人工前置门禁，不是自动跳过的步骤。每阶段等待变更单成功及 readiness；HTTP 200 或 ChangeOrderId 只表示受理，不能视为服务已可用。参数形式参考 [SAE DeployApplication 官方文档](https://www.alibabacloud.com/help/zh/sae/api-sae-2019-05-06-deployapplication)。

计划生成器不创建应用、不注入 Secret、不推镜像、不执行任何发布；也不会核验清单中的云资源实际存在。需要提供阿里云测试地域/命名空间、应用 ID、ACR 仓库和最小权限发布凭证，才能进行真实部署验收。无需把这些凭证发进聊天正文，后续通过受控配置/Secret 注入。

本机六个镜像构建成功，使用 ARM64 Docker 环境。云运行架构必须单独匹配，多架构/AMD64 镜像不能凭本机成功就默认通过。Java 默认镜像源下载异常时，本轮使用官方 ECR Public Maven/Temurin 源，Web 使用官方 ECR Node 24 Alpine 与 Nginx 官方 GHCR 源；Dockerfile 的可配置 ARG 保留默认值，不改全局代理或清空缓存。

共享 MySQL 表意味着数据库兼容性仍是发布约束。0045 生产回滚优先切兼容代码并保留新增表；不默认执行会删除数据的 downgrade。Worker 至少保留所需常驻副本，本地 BGE 权重采用只读共享挂载并单独测量冷启动。

## 七、完整 Agent SSE 基准

`scripts/agent_end_to_end_benchmark.py` 请求实际 `/chat/stream`，而不是只调用裸模型。它观测端到端首有效文本、最终时长、请求用量；可在最后一个样本主动断开流，仅通过 GET 同一 run_id 恢复，不重复 POST、不批准确认单。

默认只输出计划，不联网。样本 1..20、并发最多 4，单次总超时 5..180 秒。实际运行需要 `--execute --synthetic-test-account` 以及通过环境注入 `AGENT_LIVE_AGENT_CONTEXT`；远端必须 HTTPS。固定知识问题避免发起写操作，但模型仍可能提出确认，出现确认单就判验收失败，不自动执行。

```bash
make agent-end-to-end-benchmark ARGS='--samples 3 --concurrency 1 --disconnect-sample --report ../tmp/agent-e2e.json'
# 先配置真实的专用测试环境/身份，再追加 --execute --synthetic-test-account。
```

真实执行会计费，并留下测试会话、Checkpoint 和台账，需按保留策略管理。报告不保存问题、答案、Token 凭证或错误响应正文。知识问题不保证每次一定调用所有 RAG/Gateway 工具，实际调用应结合 request_usage/Trace 核验。它不是写入吞吐压测或 P95 SLA 证明。

交接初轮完成了脚本的完整流、主动断开恢复和错误不重发的合成测试。当时没有执行真实业务端到端压测。

后续已用独立临时PG、真实Redis/本地检索模型/LLM和临时合成签名身份完成12次实际HTTP知识问答（1/2/4并发各4次）。这不等于正式登录、Java动态业务或生产容量验收；实际带工具回合没有answer.delta。客户端初始/终态sequence=0兼容也已修正并回归。详细条件与指标见 [性能实测说明](性能召回并发与续费效果实测_20261006.md)。

## 八、本轮验证与后续清单

完成：Python 650 通过/20 跳过；四个 Java 服务 113 通过/5 跳过；前端 8 通过；独立 PG/Redis 6 项真实验收；六个应用镜像构建；Agent 非 root 导入烟测和 Nginx 配置检查。跳过项需要外部依赖，没有被改成虚假通过。

浏览器使用真实 BFF 路由/中间件、合成身份签发/Redis/业务回答，已验证登录→自动刷新→能力目录→SSE 展示→退出→恢复登录表单。Cookie 不可由 JavaScript 读取，敏感身份键没有写入 Web Storage，页面没有脚本错误/错误浮层；测试替身不等于真实认证端联调。浏览器验证技能还帮助发现并修复登录表单低对比度与未登录状态文案。

前端构建链的 source-map-js 固定到修复版 1.2.2，当前 npm audit 为 0；修复依据 [GHSA-68fv-2mgg-jv7q](https://github.com/advisories/GHSA-68fv-2mgg-jv7q)。这不代表所有 Java/Python/系统镜像依赖已经完成完整漏洞审计。

仍需外部条件：

- 选定真实认证端、账号/组织/角色来源并实现上述内部契约，提供测试账号与受控凭证。
- 阿里云测试资源、镜像仓库、Secret/VPC/TLS/代理信任配置与发布权限。
- 历史 Prompt 模型对比的人工复核、必要重复采样；本轮没有自动设置 `--reviewed`。
- 真实 RAG/Gateway 业务链路、并发负载、断线/重启/排空/回滚演练、预算价格配置与账单核验。
- Java 8/Spring Boot 2.1/旧 MD5 登录的后续升级专项；本轮不做高风险整栈替换。

这些是“接入、运行与证据待完成”，不是已经部署上线。不要在简历里写虚构用户量、固定降时比例或云端秒级回滚。
