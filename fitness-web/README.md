# fitness-web

AI 健身平台的第一版 Web 工作台，按 `docs/frontend-integration-contract.md` 接入现有 Agent API。

当前已实现：

- 由后端能力目录动态生成角色能力展示，不在前端硬编码权限；
- Agent 对话、会话 ID 保留和统一请求/Trace ID；
- 写操作确认卡片、批准/拒绝和确认状态展示；
- 401、403、409、503 及服务不可达的统一错误提示；
- Vite 开发代理：`/api`、`/health` 转发到本地 Agent `8090`。

## 本地启动

```bash
cp .env.example .env.local
# 将认证服务签发的短时 AgentContext 填入 .env.local 的 VITE_AGENT_CONTEXT
npm install
npm run dev
```

浏览器访问 `http://127.0.0.1:5173`。生产环境不能把 AgentContext 固定编译进前端，
应由认证服务/BFF 根据登录会话注入短时上下文。
