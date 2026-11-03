# 健身图片与视频分析：移动端完整实施设计

版本：2026-11-02｜状态：设计完成，尚未实施或进行真实模型验收。

目标客户端：**微信原生小程序 + 独立 App**。App 的具体 UI 框架未指定；本设计定义跨端契约及 iOS/Android 适配要求，不假定使用 React、uni-app 或 WebView。

模型选型范围：截至 **2026-06-30 已上线可用**的模型；模型能力与公开价格于 2026-11-02 查询，不能把当前价格当作上半年历史价格。

本次交付是一套完整能力，实施工作按依赖顺序拆解，不拆成多个功能不完整的发布版本。对应执行清单见 [实施工作包与验收](./multimodal-fitness-mobile-execution-20261102.md)。

## 1. 已确定的方案

用户在健身助手中选择图片或视频，输入问题，例如“我这个坐姿划船动作标准吗”。后端将**原素材、用户问题和按权限检索出的相关健身知识**交给多模态模型，返回动作观察、调整建议和无法判断的部分。支持继续追问、后台任务恢复、历史记录与删除。

采用 **阿里云百炼 qwen3.7-plus-2026-05-26，华北2（北京），按量付费 API**。固定快照，不使用可能漂移的 latest 别名。本模型在本方案中负责图片和视频画面分析；**不理解视频音轨**。用户补充说明来自输入文字，或现有独立语音输入转写成的文字。界面说明“分析视频画面，补充说明请填写文字或使用语音输入”。

项目不建设视频抽帧、切片、转码、独立画质检测或人体骨架推理流水线。厂商内部的视频采样由厂商完成；我们只配置接口允许的采样参数。文件头、实际大小、尺寸和时长的技术校验用于执行上传限制，不承担画面质量或动作正确性判断。

接入结构为：**移动客户端 → Agent API → 私有 OSS / Agent PostgreSQL → 持久运行任务 → 只读动作分析用例 → 授权 RAG → 多模态模型 → 结构化结果**。Java Gateway 继续负责业务查询和业务写入权限，不接收媒体二进制，不承担视觉推理。

动作分析结果是辅助观察，不自动变成伤病诊断、已确认身体事实、长期记忆或已发布训练计划。用户随后提出修改计划等业务请求，仍经过现有 Tool Registry、Java Gateway 和确认流程。

## 2. 模型选型及证据

### 2.1 候选比较

| 候选 | 上半年可用性证据 | 与本项目相关的能力与取舍 | 决定 |
| --- | --- | --- | --- |
| Qwen3.7-Plus，2026-05-26 快照 | 百炼官方更新表列明 **2026-06-01 上线** | 图片、视频、文字输入；结构化输出；北京接入；与现有百炼语音服务属于同一供应商，便于运维 | **唯一默认模型** |
| Qwen3.7-Max，2026-06-08 快照 | 官方更新表列明 **2026-06-09 上线视觉版本** | 可做离线效果对照，费用更高；5月20日快照是纯文本，不能只看 Max 名称选型 | 不自动切换，仅作为评测对照 |
| Qwen3.5-Omni-Plus，2026-03-15 快照 | 官方记录国内 **2026-03-30 上线** | 适合同时理解视频画面与声音的需求；当前动作画面分析不需要增加音频处理路径 | 当前不接入 |
| Gemini 3.1 Pro Preview | Google 于 **2026-02-19** 公告 | 音视频与图片理解候选；Preview 状态以及 Developer API 地区可用性需考虑，增加大陆项目接入条件 | 当前不作为默认 |

来源：[百炼模型更新记录](https://help.aliyun.com/zh/model-studio/newly-released-models)、[Plus 模型信息](https://help.aliyun.com/zh/model-studio/qwen3-7-plus)、[Max 模型信息](https://help.aliyun.com/zh/model-studio/qwen3-7-max)、[Omni 模型信息](https://help.aliyun.com/zh/model-studio/qwen3-5-omni-plus)、[Google 发布公告](https://blog.google/innovation-and-ai/models-and-research/gemini-models/gemini-3-1-pro/)、[Gemini 可用地区](https://ai.google.dev/gemini-api/docs/available-regions)。

选 Plus 是针对本项目的**工程适配与成本判断**，不是声称它在健身动作判断上已经证明最准确。所选精确快照必须通过本项目的授权素材和教练验收，失败时不应通过改宣传口径来跳过质量门槛。

### 2.2 接口使用决策

- 服务端使用厂商 OpenAI-compatible Chat Completions 接口；专用 MultimodalGateway 组装 image_url / video_url 内容块。
- 图片和视频均引用 OSS 的短期签名 HTTPS 地址。地址在实际调用前签发，不在进入队列时签发；模型服务必须可访问，不能使用本机路径、Docker 地址或 OSS 内网地址。取件响应必须有正确的 Content-Length、Content-Type，并保留模型需要的 Range/完整文件读取能力。
- 使用用户文字、授权的知识片段、必要的会话文字和原媒体共同生成最终报告。只把“用户上传了视频”交给文本模型不属于视觉分析。
- 视频采样以 2 fps 作为首次验证配置，同时用相同样本对比 4 fps。生产值由验收记录锁定；这是厂商接口参数，不是项目自行抽帧。
- 固定 JSON schema 与最大输出；优先验证非思考模式，若精确快照/目标区域拒绝关闭思考，则验证默认思考与结构化返回的组合并冻结配置。官方思考指南与错误码页对此存在描述差异，不能把未经调用验证的参数组合当作事实。不能直接复用 DeepSeek 的专有参数。finish_reason=length、拒绝响应、截断JSON不发布报告，格式修复不得补造缺失视觉结论。
- 模型不拥有业务写工具、任意网络访问或存储凭据。本分支关闭联网搜索、代码执行等不需要的工具。
- 接口参数支持情况以该快照、该区域的真实小规模调用为准。部署配置填写已验证的 base_url，不把文档中的 WorkspaceId 占位符当作真实地址。

厂商目前说明：支持原视频文件输入，内部会采样；视觉接口不理解音轨，URL 与 Base64 的限制也不同。[图像与视频理解](https://help.aliyun.com/zh/model-studio/vision)。正式发布前重新检查精确快照可用性、文件限制和账号配额，保留契约验证记录。

最小请求形状如下，签名地址和提示词由服务端填入。此示例使用JSON mode加本地schema验证；若目标快照已验证JSON Schema模式，则由adapter统一替换。enable_thinking参数按契约测试结果加入，不在此把未验证模式写死。图片请求把video_url内容块替换为image_url即可。

~~~json
{
  "model": "qwen3.7-plus-2026-05-26",
  "messages": [
    {"role": "system", "content": "按movement-analysis.v1要求输出JSON；只依据可见画面和提供的知识，无法判断时明确说明。"},
    {"role": "user", "content": [
      {"type": "video_url", "video_url": {"url": "SIGNED_HTTPS_URL_CREATED_BY_SERVER", "fps": 2}},
      {"type": "text", "text": "用户问题：这是坐姿划船，我有哪些地方需要调整？\n授权知识片段及citation_id：由本次检索填入。"}
    ]}
  ],
  "response_format": {"type": "json_object"},
  "stream": false,
  "max_tokens": 4096
}
~~~

接口细节核对入口：[结构化输出](https://help.aliyun.com/zh/model-studio/qwen-structured-output)、[深度思考](https://help.aliyun.com/zh/model-studio/deep-thinking)、[错误码](https://help.aliyun.com/zh/model-studio/error-code)。

### 2.3 费用与版本管理

2026-11-02 查询的北京 Plus 公开原价：输入不超过256K档位，输入每百万 tokens 2元、输出8元；更高档位为6元/24元。以上不是每段视频的固定价格，也不包括 OSS 请求、存储和流量。[模型价格](https://help.aliyun.com/zh/model-studio/qwen3-7-plus)

费用计算采用实际 usage：**费用（元）= Σ（各计费类别 tokens ÷ 1,000,000 × 对应的每百万tokens单价）**。区分未缓存输入、缓存命中输入和计费输出，reasoning tokens不得与已经包含它的输出总数重复计费。模型识别预调用、重试和追问分别入账。未知 usage 标记 UNKNOWN，不能记为零。举例：假设20K未缓存输入、2K计费输出，在上述低档位约0.056元，仅用于解释算式。

保留 model_id、provider、region、prompt_version、schema_version、sampling_config、pricing_version。更换其中任何影响行为的配置都需要固定测试集回归。不得在模型不可用时静默切换文本模型并声称看过素材。

## 3. 当前代码事实与新增边界

以本次工作区源码为准；仓库已有大量其他未提交改动，本次设计只新增文档。

| 边界 | 当前事实 | 本次设计 |
| --- | --- | --- |
| 客户端 | 仓库可见 fitness-web；未发现已实现的原生小程序/App 工程 | 新客户端按统一 OpenAPI 契约实现；现有网页仅参考协议，不是目标客户端 |
| 认证 | app/infrastructure/browser_auth.py、app/api/routes/auth.py 已实现 Cookie BFF；服务端保存签名身份 | 新增移动端会话适配，归一为 AgentIdentity；不要求小程序模拟浏览器 Cookie/CSRF |
| 对话输入 | app/api/routes/agent.py 的 AgentChatRequest 仅有文本等字段 | 扩展媒体 ID、引用和模式；继续禁止客户端提交用户、角色作为权限依据 |
| 任务 | app/streaming/service.py 是进程内 asyncio 执行；PostgreSQL 保存终态 | 在同一 chat run 模型上新增持久投递与 worker；不能把现状描述成已支持服务重启续跑 |
| 完成提交 | streaming/repository.py、completion.py 已有运行终态/历史提交版本/摘要事务 | 报告、消息及附件依赖加入同一提交事务；统一终态来源 |
| RAG | rag/service.py、repository.py 已有来源、发布状态与权限过滤 | 复用 RetrievalScope 与实际来源引用，增加训练内容类型过滤 |
| 文件 | rag/storage.py 是知识文件 store/read，整体 bytes 读写 | 新建 MediaStorage，支持直传、不可变版本、预览授权和删除，不把知识存储视为现成视频服务 |
| 模型 | infrastructure/model_gateway.py 为文本/embedding 网关，存在全文 JSON 长度限制 | 新建 MultimodalGateway，复用可靠性与用量机制，媒体与文字预算分别计算 |
| 会话记忆 | session_summary.py 忽略非字符串消息；memory/source.py 记录用户文字来源 | 独立保存媒体关联，摘要只存受控文字投影；模型视觉推断不进入长期记忆 |
| Java业务 | core gateway、training/booking/customer service 管业务事实与写事务 | 保持业务所有权；图片分析不直接写 MySQL 或修改计划 |

### 3.1 组件职责

~~~mermaid
flowchart LR
  MP[微信原生小程序] --> API[Agent API 与移动认证适配]
  APP[独立 App] --> API
  MP -->|受限 POST 直传| OSS[私有 OSS]
  APP -->|受限 POST 直传| OSS
  API --> PG[(Agent PostgreSQL)]
  API --> AUTH[受信任业务认证端]
  API --> REDIS[(Redis 会话与限流)]
  PG --> W[多模态运行 Worker]
  W --> CASE[MovementAnalysisService]
  CASE --> RAG[授权 RAG 检索]
  CASE --> MM[MultimodalGateway]
  MM --> QW[Qwen3.7-Plus 固定快照]
  QW -->|短期授权取原文件| OSS
  CASE -->|报告与终态原子提交| PG
  API -->|用户另行提出业务操作| SUP[现有 Supervisor]
  SUP --> GW[Java Gateway 与业务服务]
~~~

不额外建立一个拥有独立终态的 analysis_jobs 再让 chat run 等待它。调度表只保存投递/租约信息，用户看到的运行状态始终来自 agent_chat_runs。

## 4. 产品范围与移动交互

### 4.1 页面

在“智能健身助手”现有入口提供：会话列表、聊天页、素材全屏预览、分析报告详情和素材删除。默认不要求用户填写动作名称、拍摄角度或完整身体资料，发送文字与文件即可。

聊天输入区包含“＋”附件按钮、文字输入、现有语音输入、发送按钮。附件来源是“从相册选择”和“拍摄”。只在用户选中相应功能时请求平台所需权限；拒绝权限后保留文字输入、重新选择或设置入口。

独立 App 可用原生或跨平台 UI，但以下网络、状态和身份契约必须相同。微信使用 wx.chooseMedia / wx.uploadFile 等原生接口；不使用浏览器 DOM、File、EventSource 作为客户端正确性的前提。

### 4.2 一次完整操作

1. 用户选择素材，立即本地预览；此时不上传、不调用模型、不产生模型费用。
2. 输入问题，可使用“帮我看看这个动作”“有哪些地方需要调整”等快捷文案。纯附件可发送，客户端显示并提交明确默认问题“请根据我上传的图片或视频分析训练动作，并说明可改进的地方”；后端记录这是默认问题。
3. 点击发送，固定本轮问题与附件快照，创建稳定 client_message_id 和 run_id，并在发出网络请求前落盘。重复点击不会创建第二次发送；获得upload_id/asset_id后立即更新本地记录，不等到页面关闭时才保存。
4. 申请受限上传表单，按真实已上传字节显示进度。上传失败保留草稿；默认两文件并发，限制总请求量。
5. 上传完成通知后端，状态显示“文件已上传，正在准备”。后端校验并将文件固化为 READY；不把 OSS 返回200当作分析已提交。
6. 创建运行任务并显示“排队中 / 正在查看图片或视频 / 正在整理建议”。分析阶段不显示虚构的百分比或确定的剩余时间。
7. 用户可离开页面。任务已受理后由服务端继续；上传尚未完成时，小程序切后台不保证继续上传。
8. 返回页面或重新打开小程序/App，重新认证后查询同一个 run_id。状态和已完成结果恢复，不能重新发起模型任务。
9. 完成后展示结果卡：结论、最多3条优先观察与建议、无法判断的部分、可展开知识引用。
10. 输入框上方显示“继续分析：本次视频”标签；下一轮带明确媒体 ID。用户可移除标签，变成纯文字解释；上传新素材默认替换活动素材，不把旧视频悄悄带入。

### 4.3 结果表达

允许的判断：NEEDS_ADJUSTMENT（有值得调整的观察）、NO_OBVIOUS_ISSUE_OBSERVED（可见部分未发现明显问题）、INSUFFICIENT_EVIDENCE（信息不足）、NEEDS_CLARIFICATION（动作/目标不明确）。界面不把第二项翻译成“完全标准”或“绝对安全”。

每条建议关联至少一条画面观察，或明确标记为一般训练说明。图片仅评价可见瞬间；视频可描述过程。精确关节角度、人体骨架、百分制标准分不属于输出。时间点可空；仅在有实际依据且位于真实时长内时提供跳转，不能强制模型编造时间。

用户报告疼痛等不适时，回答优先建议暂停引发不适的动作并寻求相应专业帮助；不能根据画面诊断伤病，也不能凭模型推测将用户标记为有伤病。

### 4.4 微信小程序细节

- wx.uploadFile 使用 multipart/form-data POST。后端必须返回 OSS PostObject 所需 host、formData 和文件字段名，不能返回只支持 PUT 的签名地址。
- UploadTask 的进度事件驱动进度条；success回调仍须检查statusCode与OSS约定成功结果，不能把任意HTTP响应当作成功。abort仅取消上传。已经受理的分析必须调用运行取消接口。网络timeout不代表OSS一定未收到文件，先查询原upload状态。
- 临时文件路径不作为永久数据。发送前及每次阶段推进均持久化草稿文字、素材 ID、upload_id、run_id 和幂等键；onHide补充保存并停轮询，冷启动与onShow执行同一个恢复状态机。临时文件已失效时明确要求重选。
- 默认使用普通 JSON 任务查询，不依赖流式连接：前台先每2秒，再退避到5秒查询，加入抖动并遵守 poll_after_ms；后台停止轮询，回前台立即查询。配置可调。
- 可选分块流式输出属于体验增强，不能改变最终一致性；没有收到增量也能通过查询得到完整结果。
- 正式环境配置 API request、OSS uploadFile、媒体预览 downloadFile 等对应合法域名、HTTPS证书和平台隐私接口要求，必须真机发布态测试。
- 同一账号多个会话、切账号或切机构时，状态按身份范围+conversation_id隔离。退出登录清理本地预览与缓存引用，旧异步响应不能写入新用户页面。

微信接口依据：[官方 API 类型定义](https://github.com/wechat-miniprogram/api-typings/blob/master/types/wx/lib.wx.api.d.ts)、[官方生命周期定义](https://github.com/wechat-miniprogram/api-typings/blob/master/types/wx/lib.wx.app.d.ts)。微信开发文档正文在此次查询中未成功抓取，基础库最低版本与合法域名细则列为实施时真机核验项，不把未核实版本号写成事实。

### 4.5 独立 App 细节

- 系统媒体选择器、相机、视频播放与文件上传分别封装平台适配；业务层只依赖 MediaPicker、Uploader、RunTracker、SessionStore 四个接口。
- access token 放在平台适当的安全存储，刷新凭据使用 Keychain/Keystore 等平台能力；不要写普通日志、分享链接或明文分析埋点。
- iOS 可采用文件型 background URLSession；Android 根据系统版本与用户触发场景选择受支持的数据传输机制。不能承诺用户强制结束 App 后上传必定继续。
- 任务被服务端受理后不依赖 App 后台常驻。手机锁屏、接电话、切网络或重启后，都用 run_id 查询。
- 基线实现失败重传，不承诺字节级断点续传。已上传成功的素材重试分析时复用，不重新上传。是否使用 App 原生后台上传不改变服务端接口。
- 本次完成通知以会话内状态为主，不新增 Push 或微信订阅消息依赖；页面回来后一定能获取结果。

参考：[Apple 后台 URLSession](https://developer.apple.com/documentation/foundation/urlsessionconfiguration/background(withidentifier:))、[Android 数据传输任务](https://developer.android.com/develop/background-work/background-tasks/data-transfer-options)。

## 5. 认证与权限

### 5.1 移动身份接入

新增 MobileAuthService 和移动认证路由，复用现有服务端加密会话模式，但使用 Authorization: Bearer 的不透明移动会话令牌，不依赖 Cookie 和 Origin/CSRF。

用户先通过原有业务登录完成身份验证：微信登录码必须在受信任业务服务端兑换，并绑定平台内部用户；App 使用原有业务登录。之后移动端用有效业务登录证明调用 exchange，Agent 服务通过受信任认证端验证并换取短时签名 AgentContext 和受限刷新凭证。**当前仓库的浏览器用户名密码适配不等于已经具备这个移动交换能力；认证桥接契约是新增工作。**

认证私钥属于认证端；Agent 只验证签名。前端不可自行生成 AgentContext，也不把 user_id、openid、organization_id、role 当作可信身份。机构选择是请求，实际授权机构由认证端校验。

建议：移动 access 会话15分钟、轮换 refresh 最长7天；这是产品默认配置而非平台规定。会话数据在 Redis 加密保存，token 只保存哈希索引，刷新原子轮换并检测复用。若沿用已有业务 App 的 token 体系，适配器必须达到同等校验与撤销语义，不另外建立账号库。

auth_session_ref绑定稳定的服务端auth_session_id / family_id，而不是某一代access或refresh token。客户端刷新与worker刷新AgentContext共享CAS/single-flight，避免相互误判为凭据重放。令牌轮换不改变任务引用；logout撤销整个family并增加auth_epoch。

HTTP 中间件在服务端还原/刷新 AgentContext，归一为 AgentIdentity。拒绝移动 Bearer、浏览器 Cookie、客户端显式 X-Agent-Context 混用。已有网页调用可保留原通道，不能因为支持小程序而关闭网页 CSRF。

### 5.2 后台任务授权

投递表保存加密请求和服务端 auth_session_ref，不保存明文 bearer、长期凭据或过期 AgentContext。worker 使用服务端会话引用通过认证适配器重新验证/刷新当前身份，提交时身份快照仅供审计，不能无限授权。

执行前、检索前、调用模型前及最终提交前验证会话/机构仍有效。执行权限不得超过提交时范围；当前scope与提交scope变化时以ACCESS_REVOKED结束，不能换owner_hash继续旧run。登出、撤权、刷新失败或会话失效时停止新调用并以 AUTH_CONTEXT_EXPIRED / ACCESS_REVOKED 收尾；用户重新登录后，按当前资源权限查询已完成结果，不要求仍持有原执行会话。身份已变化时不能只使用提交时的旧角色权限。

现有 AgentContext 默认最长约300秒，不能把它塞进持久任务后无限重放。移动认证桥接若尚未具备后台刷新/撤销能力，持久 worker 不得先上线再靠延长令牌绕过。

### 5.3 资源权限

每次读写 asset、upload、run、message、conversation、report 都检查真实 subject、机构范围、资源状态及会话归属。素材默认本人私有，同机构教练和管理员不自动获得访问权。当前方案不实现教练共享；以后需要时增加显式授权关系。

报告/消息/会话保存authorization_scope_hash（沿用run_owner包含角色、capabilities等权限范围的语义），读取不能只检查subject和机构。角色或能力发生变化时，旧权限范围的报告默认不可读，不能通过新历史API绕过现有run权限限制；需要重新建立符合当前范围的会话。普通令牌刷新且权限不变不影响读取。模型引用的知识只暴露本次允许范围，不把内部文档路径直接当公开下载地址。

用户只能提交 asset_id，不能提交模型要抓取的任意 URL、存储 key 或其他用户的 file_id。实际模型地址由服务端对已授权不可变对象签发。

## 6. 文件与 OSS 生命周期

### 6.1 建议产品限制

以下均为待真实兼容性测试确认的产品默认值，远低于厂商最大限制，可通过 capabilities 下发：

| 项目 | 默认目标 |
| --- | --- |
| 单轮素材 | 1～3张图片，或1个视频；图片与视频混合/跨视频比较暂不纳入本次接口契约 |
| 图片 | JPEG/PNG/WebP，单张≤10MiB，最长边/总像素按验证配置限制，建议≤24MP |
| 视频 | MP4/MOV，2～120秒，≤100MiB；H.264/HEVC支持矩阵由真实手机样本和模型验证后锁定 |
| 上传并发 | 每用户2个，整体限流配置化 |
| 处理并发 | 每会话1个活动 run，每用户最多2个；模型全局并发以账号配额为上限 |
| 文件保留 | 已提交素材和关联分析默认30天；未提交上传24小时清理 |
| 访问签名 | 上传表单15分钟；模型读地址10分钟；用户预览读地址2分钟，均按需重签 |
| 任务时限 | 排队≤60秒，执行≤240秒，总截止≤300秒；达到上限明确终态 |

超过限制在选择后/发送前给就地提示；后端仍校验真实数据。手机常见 HEIC、HEVC、旋转元数据、横竖屏是必须验证的兼容场景。未验证格式不能在 capabilities 中宣称支持；不静默转码，不擅自压缩改变动作细节。

### 6.2 上传与固化

1. create upload：验证登录、会话和配额；创建 upload_id、asset_id、随机暂存 key，预留上传字节额度，返回限定对象和大小的短期 POST 表单。
2. 客户端直传私有 OSS 暂存对象，不向 OSS 发送业务 bearer、AgentContext 或模型密钥。
3. complete：客户端报告 upload_id；后端从 OSS 验证真实对象。客户端提供的 ETag/大小只能辅助，不能作为可信结论。
4. 暂存桶开启版本控制；服务端确定一个 version_id 后，所有读取、校验和复制均使用该版本，避免有效上传签名被重复使用而覆盖已校验文件。
5. 校验真实大小、文件头和容器元数据；图片使用有界解码读取尺寸，视频使用受限 ffprobe 或同等只读元数据解析。限制CPU/内存/超时、禁止任意网络输入；不生成帧、不做动作质量算法。
6. 从固定源版本复制到客户端无写权限的 final 随机 key；保存 final_version_id 和可信内容摘要。READY 后内容不可变。
7. 只有 READY 素材可以绑定 run。用户提交问题时绑定 asset_id + revision + final_version，模型调用时再签发访问地址。

上传状态：ISSUED → UPLOADED → VERIFYING → READY；任何非最终状态可转 REJECTED / EXPIRED。complete 重复调用返回同一状态，不重复复制或占用配额。校验可异步执行，客户端查询素材状态。

GET upload状态时返回asset_id、状态、是否已发现完整暂存对象以及next_action。init回包丢失时使用同一幂等键重放，返回原upload；模型任务的run_id也在发送前已生成并持久化，提交回包丢失后可直接查询。只要upload尚未完成且未取消/过期，允许为同一upload重新签发一次受限表单；旧签名只能写暂存区，最终固定版本不受覆盖影响。上传会话已过期时以显式新upload替换草稿引用，并释放旧配额。

预览地址过期后，播放器取得新的授权地址，保留播放位置并重新加载；拖动、暂停后继续、切前台都可能触发新的Range请求。已删除返回MEDIA_GONE而非继续重签，预览重试有上限。短期URL不得作为消息永久内容。

ETag（特别是 multipart ETag）不能当作文件 SHA-256。使用服务端校验和/可信存储校验字段并记录算法；请求摘要至少绑定不可变版本，不依赖客户端声称的 hash。

版本化对象与固定源版本复制的依据：[OSS CopyObject](https://help.aliyun.com/zh/oss/developer-reference/copyobject)。受限表单直传依据：[服务端签名直传](https://help.aliyun.com/zh/oss/user-guide/obtain-signature-information-from-the-server-and-upload-data-to-oss)。

### 6.3 删除

界面“删除素材及相关分析”明确作用于使用该素材的分析及追问链；普通无关会话消息保留。单纯移除输入框引用标签只影响下一轮，不删除素材。

删除事务：写 tombstone、增加 media revision / conversation content_epoch、撤销活动引用、请求取消相关 run、登记删除任务并立即停止新的预览签名。run查询、消息历史、报告读取和缓存同时检查媒体依赖/失效标记，立即隐藏已经完成的相关结果；不能继续从agent_chat_runs.result_ciphertext返回旧报告。后续报告提交必须检查素材 revision 和 content_epoch，因此删除前发出的旧模型结果不能重新发布。

清理任务删除原文件所有对应版本、暂存版本、分析报告、agent_chat_runs结果密文、受影响消息内容、派生摘要、缓存和投递正文。受影响会话的 LangGraph checkpoint 按当前 store 能力失效/重建：先取得会话锁，保留已授权未删除消息，从这些消息重建安全上下文；不能只删原视频而留下旧推断。若线程有待确认业务操作，先等待/按既有撤销规则结束，不能直接抹掉正在执行的业务状态。

删除 lineage 需要包含报告解释、后续追问、摘要的媒体依赖，防止只删首轮报告而保留衍生内容。审计保留无内容的操作标识和删除时间。对象版本清理失败可重试并报警。

业务访问立即撤销，物理删除目标≤24小时。已签发外部 URL 的短暂剩余有效期、供应商处理和备份保留不应宣称瞬间归零。恢复备份时重放删除账本并验证 tombstone 后再开放服务，避免被删内容复活。

## 7. 对话运行、持久调度与一致性

### 7.1 统一运行模型

保留 agent_chat_runs 为唯一公开状态和加密终态结果来源，增加 run_kind、queued_at、started_at、retry_of、stage、stage_version、content_epoch 等必要字段。为多模态运行新增投递记录 agent_chat_run_dispatch（run_id唯一），保存加密输入快照、schema版本、auth_session_ref、available_at、lease_until、lease_token、attempt、provider_dispatch_started。

旧纯文本路径继续走原 manager，新增媒体路径走 durable executor；通过 executor_kind 明确派发，不让同一个 run 同时被两个执行器领取。原有会话互斥索引和查询必须把 QUEUED 加入活动状态集合。

调度仅描述“谁在处理哪个 run”，不再建立第二个业务状态机。API/worker/清理进程使用同一个 RunRepository 和原子完成接口。

### 7.2 状态与提交

~~~mermaid
stateDiagram-v2
  [*] --> QUEUED: 请求与素材绑定事务成功
  QUEUED --> RUNNING: worker领取并取得租约
  QUEUED --> CANCELLED: 用户取消且未调用模型
  QUEUED --> FAILED: 排队超时或授权失效
  RUNNING --> COMPLETED: 有效报告原子提交
  RUNNING --> FAILED: 已知失败
  RUNNING --> CANCEL_REQUESTED: 用户停止或删除
  CANCEL_REQUESTED --> CANCELLED: 停止确认
  RUNNING --> UNKNOWN: 上游调用结果不确定或执行丢失
  CANCEL_REQUESTED --> UNKNOWN: 停止结果无法确认
~~~

COMPLETED / FAILED / CANCELLED / UNKNOWN 都是终态，不把旧 run 改回 RUNNING。重新分析生成新 run，并记录 retry_of。INSUFFICIENT_EVIDENCE 是成功完成的报告内容，不属于基础设施失败。

提交请求的数据库事务同时写入：run、投递、用户消息、assistant占位、素材依赖和会话版本。不在持有事务期间调用模型或上传对象。worker通过 SKIP LOCKED 领取短租约，周期心跳；所有推进和完成写入校验 lease_token、run状态、会话版本及媒体revision。

完成事务原子提交：结构化报告 + 最终回答 + 消息可见状态 + run终态 + 历史commit版本 + 可选安全摘要。扩展现有 streaming/completion.py / repository.py 的完成事实，而不是各组件分别写成功。

**消息与LangGraph的对齐是必做改造。** 新agent_chat_messages是完成对话的内容事实来源，媒体、报告解释及该客户端普通文本回合都写相同消息投影；旧入口也在完成路径同步投影。Supervisor在同一会话锁/fencing下读取已提交消息构建新的普通对话上下文，并明确消费content_epoch与history版本。待确认业务操作仍使用原LangGraph checkpoint与Command(resume)，不能用普通消息重建替代业务恢复。旧代码会比较checkpoint.turn_run_id与history_commit.run_id，不可只让媒体worker更新commit而遗漏这处适配，否则后续文字会话会回退并丢失分析上下文。重建同时保留既有memory_revision撤销栅栏，不重新引入已撤销记忆生成的旧上下文。

### 7.3 重启与重试

- API重启不影响已经进入worker的任务，客户端重新查询。
- worker在调用模型前退出：租约到期可重新领取，执行幂等的授权/素材检查。
- worker已标记 provider_dispatch_started 后退出或网络结果不明：标记 UNKNOWN，不自动再扣一次模型调用；用户明确重新分析才创建新run。
- 已收到429等明确拒绝且尚无有效输出时，可在同一截止时间与预算内按 Retry-After 有限重试，建议至多一次。
- 发送后超时不能默认“厂商没收到”；SDK关闭隐藏重试，由网关显式管理。
- 若厂商提供可查询的请求状态，可以记录 request_id并按其契约对账，但在未确认能力前不承诺 exactly-once 推理或服务端断点续算。

任务持久化保证可恢复状态，不等于供应商推理可无损续算。必须真实展示 UNKNOWN 的含义并允许复用文件重试。

### 7.4 幂等和取消

run_id / client_message_id 稳定且全局随机；request_digest覆盖标准化问题、locale、媒体ID及顺序、不可变版本、模式、引用来源。模型/提示词配置在首次创建时固化，重试查询不重新读取配置生成另一个任务。

同key同请求返回原run；同key不同内容409；同会话已有活动run返回CONVERSATION_BUSY和原run引用。查询和客户端重连不触发推理。

新增持久幂等映射：(subject, org_scope, conversation_id, client_message_id) → run_id、request_digest、message_id。client_message_id是客户端发送标识，message_id是服务端消息主键，返回中均有，禁止两端混用。映射至少保留90天，且不短于历史保留期与明确的客户端重放窗口；客户端仅自动恢复7天内未确认提交。run控制记录24小时清理后，查询原run仍可通过映射返回历史结果或RESULT_EXPIRED/CONTENT_DELETED，不重新dispatch。用户删除后幂等映射仅保留不可逆请求摘要和无内容tombstone。超过客户端重放窗口的草稿须用户明确作为新消息发送。

停止分析只影响run，不自动删素材。取消与完成由状态CAS决定，先成功提交的终态胜出。删除还会增加媒体revision，能阻止迟到结果；普通取消后旧结果也不得覆盖CANCELLED。取消不保证撤销厂商已发生的计费。

## 8. 多模态分析与 RAG

### 8.1 明确的编排路径

MovementAnalysisService 是 FITNESS_COACHING 的只读分析用例。提交包含媒体或显式 media_context 时，确定性进入此用例；不依赖文本模型碰巧选择看图工具。

执行步骤：

1. 验证当前身份、会话锁、素材 READY 和版本；读取本轮必要用户文字。
2. 若用户明确说“坐姿划船”，使用动作名/器械/问题检索已发布训练知识。
3. 若只有“这个动作标准吗”，先以同一个视觉模型进行小额动作识别，返回动作候选、可见器械、是否不确定；不生成最终评价。动作不确定则返回澄清问题，不强行套用标准。
4. 根据识别结果调用现有 RagService.search(query, RetrievalScope)，限定已发布、未失效、用户有权限的训练知识。限制片段数量和长度，例如top4、总6000中文字符，实际随模型预算调整。
5. 将原媒体、用户问题、有限会话上下文、实际检索片段和分析约束一起交给同一个视觉模型生成最终JSON报告。无动作名时最多两次视觉调用，两次共享总时限和费用预算。
6. 若用户动作名与画面明显冲突，报告NEEDS_CLARIFICATION，不能按错误动作名给纠正。更正动作之后由用户继续提问。
7. 校验结果结构、引用和素材依赖，通过后渲染文字/卡片并原子完成。最终观察不再交给纯文本模型随意改写，避免新增无视觉依据的细节。

知识无召回时可返回画面观察和明确标注的一般建议，citations为空；RAG服务故障则展示“本次未结合平台知识库”，不伪造引用。模型看不到素材时属于失败，不能退化为一份假装看过画面的通用回答。

### 8.2 提示词与可信边界

提示词固定版本，要求：基于输入的实际画面；单图不推断完整动作过程；辨认动作变式；观察、解释和建议分开；不能判断时明确说明；建议不超过3条优先项；不生成精确角度/伤病诊断/标准率；知识引用只使用本次提供的citation_id。

图片文字、视频字幕、用户文件与RAG片段均为内容数据，其中“忽略上文”“调用工具”等指令不得改变系统权限。模型输出不用于授予访问权，也不直接调用业务写工具。JSON schema和程序权限控制是必要约束，提示词本身不保证识别正确。

### 8.3 连续追问

请求必须带 media_ids 或 reference_message_id；服务端展开为已授权不可变媒体集合。引用报告后追问需要重新观察画面时，重新传原媒体，不假设厂商自动记住视频。只问“躯干稳定是什么意思”可选择显式 REPORT_EXPLANATION，以原报告与文字回答，并说明没有重新分析视频。

历史素材已删除或过期，返回 MEDIA_GONE 并引导重传；可以保留未被删除且有权限的普通文字，但不能声称当前重新看到了素材。

长期记忆仅接收用户本人文字声明，并沿用现有候选/批准/提取开关。报告中的模型推断不自动写入Memory；会话摘要保存带“不确定/来源”的文字投影和独立媒体依赖，不保存临时URL/Base64/供应商原始响应。

## 9. 数据设计

所有新表进入 Agent PostgreSQL，经 Alembic 管理；最终迁移号在实施时从真实 heads 分配，不预占现有编号。

| 表/扩展 | 核心字段与约束 |
| --- | --- |
| agent_media_uploads | upload_id、asset_id、subject、org_scope、staging_key、expires_at、reserved_bytes、status、idempotency_key；身份+key唯一 |
| agent_media_assets | asset_id、subject、org_scope、conversation_id、kind、mime、size、dimensions、duration、final_key/version、checksum_algorithm/value、revision、status、expires_at、deleted_at |
| agent_chat_conversations | conversation_id、subject、org_scope、authorization_scope_hash、title_ciphertext、content_epoch、created/updated/deleted_at；接口只暴露业务会话ID |
| agent_chat_messages | message_id、conversation_id、run_id、authorization_scope_hash、role、status、content_ciphertext、schema_version、created_at；run+role对应受控唯一记录 |
| agent_chat_message_media | message_id、asset_id、asset_revision、ordinal、dependency_type；记录用户附件及分析派生依赖 |
| agent_movement_reports | report_id、run_id唯一、authorization_scope_hash、schema_version、report_ciphertext、model/prompt版本、sampling配置、media_manifest_hash、content_epoch |
| agent_chat_run_dispatch | run_id唯一外键、encrypted_request、auth_session_ref、available_at、lease_token/until、attempt、provider_dispatch_started；无第二套公开运行终态 |
| agent_chat_request_keys | subject、org_scope、conversation_id、client_message_id唯一映射至run_id、message_id、request_digest、expires_at、content_deleted；独立于run控制保留期 |
| agent_chat_runs 扩展 | run_kind/executor_kind、stage/version、queued/started/deadline、retry_of、content_epoch；QUEUED加入活动会话唯一约束 |
| agent_media_deletions | deletion_id、scope、target_refs、request_epoch、progress、retry_at、completed_at；删除账本无媒体原文 |
| 原有model usage扩展 | run/attempt/provider/model、request_id、usage、计费状态、pricing_version；不记录签名URL或原媒体 |

文本和报告沿用现有加密基础；AAD包含subject、资源ID与schema版本，跨用户复制密文不能解密成功。对象存储启用服务端加密与私有权限。消息历史不是把LangGraph checkpoint原样返回给客户端，必须独立投影，过滤工具参数和内部上下文。

按subject+会话+创建时间建分页索引；素材按subject+status+expires_at索引；dispatch按available_at/lease_until索引；删除按retry_at索引。不同表的一致性通过事务与唯一约束实现，Redis不作为最终结果的唯一存储。

run默认24小时清理不能连带丢失30天可见历史：报告和消息拥有独立保留期，run外键/清理顺序应调整；运行控制记录到期后历史仍可读。删除必须覆盖投递正文和衍生消息，不能仅清理run结果。

## 10. API 契约草案

所有业务API使用HTTPS。移动Authorization仅发送到业务API；JSON请求携带 X-Request-ID。错误体统一包含 code、message、request_id、retryable，必要时包含 existing_run_id、poll_after_ms。

| 方法与路径 | 用途与关键约束 |
| --- | --- |
| POST /api/v1/mobile-auth/exchange | 用可信业务登录证明换移动会话；具体上游证明格式在认证工作包固定 |
| POST /api/v1/mobile-auth/refresh、/logout | 原子刷新/撤销；不复用网页CSRF协议 |
| GET /api/v1/agent/media/capabilities | 下发格式、大小、数量、时长、模型是否处理音轨、保留期、schema版本 |
| POST /api/v1/agent/conversations | 创建服务端会话；禁止客户端绑定其他subject |
| GET /api/v1/agent/conversations | 当前身份范围的会话分页列表 |
| GET /api/v1/agent/conversations/{id}/messages | 游标分页消息、报告投影、活动run；不返回内部checkpoint |
| DELETE /api/v1/agent/conversations/{id} | 按删除契约撤销会话、素材/派生内容与活动run |
| POST /api/v1/agent/media/uploads | 创建上传会话，返回asset_id、upload_id、POST表单与expires_at |
| GET /api/v1/agent/media/uploads/{id} | 查询上传与校验进度、asset_id和next_action；不泄漏对象内部路径 |
| POST /api/v1/agent/media/uploads/{id}/renew | 有效未完成upload重新签发表单；不改变素材身份，不能续签READY/已取消对象 |
| POST /api/v1/agent/media/uploads/{id}/complete | 幂等确认，后端校验；可返回202 VERIFYING |
| DELETE /api/v1/agent/media/uploads/{id} | 取消未提交上传并释放配额/登记清理 |
| GET /api/v1/agent/media/{id} | 查状态、元数据、保留期；未授权统一404 |
| POST /api/v1/agent/media/{id}/preview | 鉴权后签发短期播放/查看地址；不作为模型持久引用 |
| DELETE /api/v1/agent/media/{id} | tombstone与关联删除任务，返回202 deletion_id |
| POST /api/v1/agent/chat/runs | 新的移动异步提交入口，请求带客户端预生成的run_id，202返回相同run_id；媒体走durable executor |
| GET /api/v1/agent/runs/{id} | 复用现有run查询，扩展stage、完整结果、清理后的历史解析与poll_after_ms |
| POST /api/v1/agent/runs/{id}/cancel | 复用实际现有路径和cancel_request_id语义，增加QUEUED处理，不并行维护两套 |
| GET /api/v1/agent/deletions/{id} | 查看删除进度；不包含已删内容 |

现有 /chat 与 /chat/stream 保持兼容。若客户端向不支持媒体的旧同步入口传媒体，明确400并指向新契约，不能静默忽略附件。移动纯文本可由新提交入口转现有executor，但必须复用原有幂等和完成逻辑。

新任务请求示例：

~~~json
{
  "conversation_id": "c_8a7f",
  "run_id": "00000000-0000-4000-8000-000000000001",
  "client_message_id": "uuid",
  "message": "这是坐姿划船，帮我看看有什么需要调整",
  "locale": "zh-CN",
  "mode": "MOVEMENT_ANALYSIS",
  "media_ids": ["asset_uuid"],
  "reference_message_id": null,
  "retry_of": null
}
~~~

media_ids 与 reference_message_id 的组合规则：可以二选一；同时存在时必须展开为同一明确集合，否则422。服务端对每个ID重新鉴权、绑定版本并生成请求摘要。前端传入revision只能做乐观检查，不能决定服务端真实版本。

报告草案示例（下列为字段示范，不是对真实用户视频的分析）：

~~~json
{
  "schema_version": "movement-analysis.v1",
  "exercise": {"name": "坐姿划船", "identification": "IDENTIFIED"},
  "assessment": "INSUFFICIENT_EVIDENCE",
  "summary": "当前画面不足以评价完整动作过程。",
  "observations": [],
  "adjustments": [],
  "limitations": ["画面中有关键部位被遮挡"],
  "follow_up_question": "可以补充一段能够看清完整动作的视频吗？",
  "knowledge_citations": [],
  "source_assets": [{"asset_id": "asset_uuid", "revision": 1}],
  "audio_used": false
}
~~~

实际schema中每条observation应有id、asset_id、description、evidence_type、可空time_range；adjustment包含related_observation_ids、advice、basis_type、可空citation_ids。模型不得填写服务端所有者、有效权限或实际模型版本；这些字段由程序封装。

校验器确认字段长度/枚举、source资产匹配、引用ID属于本次检索、时间范围在真实时长内、建议关联存在。模型输出JSON有效不代表动作判断真实；语义可靠性通过评测和抽检验证。

## 11. 异常处理与用户反馈

| 场景 | 用户体验 | 服务端约束 |
| --- | --- | --- |
| 网络断开、切Wi-Fi/蜂窝 | 保留文字和预览；提示重试上传或恢复任务 | 上传未完成不调用模型，已受理只查原run |
| 上传响应丢失 | 先查询upload状态 | 不盲目创建第二个素材；同key幂等 |
| 临时路径失效 | 保留问题并要求重选 | 不拿本地路径当可访问URL |
| 格式/大小不兼容 | 文件卡显示具体范围 | MEDIA_UNSUPPORTED / MEDIA_TOO_LARGE，供应商错误脱敏 |
| READY后模型暂不可用 | 文件保留，允许重新分析 | 不要求重复上传，不自动改用文本模型 |
| 供应商429 | 显示排队/稍后再试 | 有界退避、账号及用户预算，超时终态 |
| 模型拿不到签名素材 | 提示分析未完成 | 重新签名只在安全重试条件内；不返回伪视觉报告 |
| RAG无结果/暂不可用 | 明确本次依据范围 | 没有引用就不生成引用；必要时只提供观察 |
| 返回不是合法schema | “分析结果生成失败，可重试” | 不发布半张报告；可做一次仅格式修复，不能让文本修复器补视觉事实 |
| 用户停止 | “正在停止”后显示实际终态 | 断开HTTP不等于取消；取消/完成CAS |
| worker崩溃且模型已调用 | “本次状态未确认，可重新分析” | UNKNOWN，旧attempt禁止迟到发布，不自动再次推理 |
| 登录过期/撤权 | 登录后恢复查询或提示权限变化 | 停止新模型调用，不扩大旧身份范围 |
| 素材删除与结果同时到达 | 删除占位，无旧报告复活 | revision/content_epoch和同一完成事务 |
| 用户换会话/换账号 | 当前页面只展示当前范围 | 回调校验scope、conversation_id、run_id |

## 12. 配置、部署与可观测性

新增配置分组：MULTIMODAL_ENABLED、PROVIDER、MODEL_ID、BASE_URL、API_KEY（服务端密钥管理）、REGION、SCHEMA_VERSION、PROMPT_VERSION、VIDEO_FPS、MAX_OUTPUT_TOKENS、TIMEOUT、MAX_ATTEMPTS、MAX_CONCURRENCY；MEDIA_STORAGE、UPLOAD_POLICY_TTL、PREVIEW_TTL、MAX_BYTES、MAX_DURATION、RETENTION_DAYS；MOBILE_AUTH_*；RUN_DISPATCH_*。

配置名实施时采用当前Settings的AGENT_前缀规范。模型/API Key不能复用VITE配置；生产不允许mock自动回退。使用独立最小权限OSS角色，临时凭据只允许限定暂存对象操作，final对象仅后端可写。

新增独立 worker 进程但复用 fitness-agent-service 镜像；不因一个分析功能增加 Java 微服务。任务表升级向后兼容，部署顺序：加表/字段与索引 → API兼容代码 → worker/清理进程 → 客户端 → 功能开关。回滚关闭新提交，允许已有任务收尾或终态失败，不回滚删除账本和数据库内容。

生产多副本使用共享PostgreSQL、Redis、OSS；本地MinIO若厂商不可访问不能作为真实模型取件路径。开发测试用fake provider，真实契约测试用专用私有测试桶和经授权样本。

指标：上传成功率/耗时、校验拒绝原因、排队时长、任务完成/取消/UNKNOWN、各阶段p50/p95、每次模型tokens与费用、429/5xx、删除滞留、媒体过期拒绝、RAG使用/无召回。指标标签用低基数枚举，不放用户ID、文件名、prompt或签名URL。

日志和trace只记录可排障ID、阶段、状态、模型版本、用量和脱敏错误。关闭SDK HTTP正文日志；不把原文件、Base64、用户照片链接和认证凭据送进普通日志/评测服务。后台查询报告的访问也应审计并按权限控制。

用户流量和预算先预留再结算，unknown调用保留待核对额度；绝不能以“断网了”直接退还全部费用并无界重试。业务预算硬限额必须基于已验证的多模态token上界估算和max输出预留；无法得到可信上界时不宣称严格人民币硬封顶，先执行文件/调用次数/并发限制并把单次预算验证列为发布条件。

## 13. 发布验收

### 13.1 工程验收

必须全部通过：原图/原视频确实进入指定快照；同素材追问重新关联真实素材；权限隔离；OSS不可变版本；幂等；租约fencing；停止与完成竞争；删除与迟到结果竞争；服务重启；客户端弱网、后台和杀进程恢复；模型异常；普通文本、原语音、RAG和业务确认流程回归。

移动端真机覆盖：微信 iOS/Android 正式环境；App iOS/Android 支持版本；相册权限拒绝/仅选中项、拍摄、横竖屏、HEIC/HEVC兼容、锁屏、接电话、切微信聊天、切网络、临时路径失效、重复发送、账号/机构切换。上传和轮询故障均不得导致重复模型调用。

### 13.2 动作分析质量

建立经授权且与调提示词样本分开的测试集，建议起步≥120份图片/视频，包含坐姿划船及拟支持动作、不同器械变式、体型、视角、遮挡、单图、视频、非健身素材。该数量是项目初始目标，不是足够覆盖所有动作的证明。

两名教练独立标注可见事实、问题、建议和无法判断项，分歧仲裁。模型不得把每个样本都判为“需要调整”或一律“标准”。按动作/模态统计观察precision、问题recall、适当拒答、建议可执行性，并报告样本量和置信区间。

建议验收目标（非现成模型承诺）：可见事实precision≥90%、已标注可观察问题recall≥80%、不可判定样本适当拒答≥95%；样本内编造伤病诊断、明显危险建议、拿不到视频仍声称看过等阻断案例为0。门槛须在基线评测后由产品/教练确认，失败就收敛公开支持范围或改模型并重新评测，不能仅调低宣传标准后上线。

厂商视觉能力不能替代专项验证。研究已指出多模态模型在动作质量和错误定位方面仍有局限；该研究不直接证明所选快照对坐姿划船的准确率。[FitAQA研究](https://arxiv.org/abs/2608.08736)

### 13.3 性能与成本

对3种文件大小/3种视频时长做并发测试，记录排队、供应商与端到端p50/p95。先设队列60秒、执行240秒硬截止；服务SLO与每次成本预算根据测试冻结，不承诺未经实测的“几秒完成”或“每个视频几分钱”。取消本地新工作目标≤2秒，模型上游停止另计且不保证退费；物理删除目标≤24小时。

## 14. 实施顺序与完成定义

详细工作包见 [执行清单](./multimodal-fitness-mobile-execution-20261102.md)。必须依次完成：基线/契约 → 模型验证 → 移动认证 → 数据迁移 → OSS媒体生命周期 → 持久运行与取消 → 分析/RAG/schema → 会话追问/删除 → 微信端 → App端 → 集成与教练评测 → 部署演练与受控发布。

最终完成意味着：两类客户端都能上传、分析、追问、离开再回来查看结果、删除；身份与机构隔离可靠；断网/重试不重复创建任务；模型/任务失败透明；业务写入边界不被视觉结果突破；上线有真实模型和真机验收记录。

本设计固定的是功能和边界，不是已经验证的效果。实施前仍需在目标账号确认精确快照、私有OSS签名访问、移动业务认证桥接、App具体框架/仓库位置和发布环境域名。这些是执行工作包的输入，当前不影响完整方案成立。
