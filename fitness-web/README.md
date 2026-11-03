# fitness-web

AI 健身平台的第一版 Web 工作台，按 `docs/frontend-integration-contract.md` 接入现有 Agent API。

当前已实现：

- 由后端能力目录动态生成角色能力展示，不在前端硬编码权限；
- Agent 对话、会话 ID 保留和统一请求/Trace ID；
- 写操作确认卡片、批准/拒绝和确认状态展示；
- Memory 候选确认、正式 Memory 纠正/撤销、站内通知和通知偏好；
- 管理员知识资料上传、质量报告查看、审批/重试，以及通知投递运维摘要；
- 业务领域快捷入口，训练计划、预约、客服和经营分析继续复用 Agent 的服务端路由；
- 401、403、409、503 及服务不可达的统一错误提示；
- Vite 开发代理：`/api`、`/health` 转发到本地 Agent `8090`。

## 本地启动

```bash
cp .env.example .env.local
# 将认证服务签发的短时 AgentContext 填入 .env.local 的 VITE_AGENT_CONTEXT
# 将本地真实机构 ID 填入 .env.local 的 VITE_AGENT_ORGANIZATION_ID，用于 Memory/通知数据范围查询
npm install
npm run dev
```

浏览器访问 `http://127.0.0.1:5173`。生产环境不能把 AgentContext 固定编译进前端，
应由认证服务/BFF 根据登录会话注入短时上下文。

也可以从仓库根目录执行 `make web-check` 检查类型和生产构建，或执行 `make web-run` 启动开发服务器。

## 录音与语音回复

连接 Agent 后点击输入区「录音」，结束后识别文字会追加到原草稿；检查后点击发送。
「语音回复」开关控制新回复的自动朗读，默认关闭；也可点击某条完整回复下方的「朗读」。
播放器支持暂停、继续、停止和失败段重试，自动播放被拦截时点击播放即可。

录音需要 HTTPS 或 localhost，麦克风权限只在主动点击时请求。录音最长 60 秒，
账号/机构切换、离开聊天页或页面隐藏时清理录音和播放。百炼 Key 只配置在 Agent 服务，
前端无需任何语音 Key。重启后端加载 `.env`，重新构建或启动前端即可使用。

`npm test` 包含音频编码与播放器竞态测试。可选浏览器验收
`npm run test:voice-browser` 使用已有 Playwright、隔离 Chrome 和测试 API 夹具；需要
`PLAYWRIGHT_MODULE` 指向 Playwright 的 ESM 入口，`VOICE_CHROME_PATH` 可覆盖 Chrome 路径。
先启动 Vite 15173 端口，并运行服务端 `voice_live_check.py` 生成固定测试音频。
此浏览器测试不访问真实账号或业务库，移动视口不代表 iOS Safari 实机验收。
