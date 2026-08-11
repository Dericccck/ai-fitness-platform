# AI Fitness Agent Service

这是健身平台的 Python Agent 服务，与现有 Java Spring Boot 业务后端并行部署。
赛事、活动运营及其遗留代码不属于本服务的业务范围。

## 责任边界

- Java 后端：用户认证、RBAC、组织数据权限、预约/合同/课时等业务事务、审计和对外业务接口。
- Agent 服务：意图识别、Supervisor 编排、RAG/Memory、模型调用和受控 Tool Calling。
- Agent 不直接写健身业务库；后续所有业务动作必须经过 Java Tool Gateway 的授权、幂等和审计。

## 当前基础设施

- PostgreSQL + pgvector：Agent 会话、Memory、知识库和向量索引。
- Redis：LangGraph checkpoint、短期会话状态、限流和缓存。
- LLM：OpenAI-compatible Chat Completions 接口。
- Embedding：OpenAI-compatible Embeddings 接口，可与 LLM 使用不同服务商。
- Reranker：可配置的 HTTP 服务，不提供本地 mock 或静默降级。
- Prometheus：低基数 HTTP 请求量、耗时、并发和构建信息指标。
- OpenTelemetry：可选 OTLP/HTTP Trace 导出，默认关闭且不发送 Prompt 或用户档案。

## 本地启动

```bash
cp .env.example .env
cd ..
make infra-up
make agent-sync
make agent-run
```

`/health/live` 只检查进程，`/health/ready` 检查 PostgreSQL、Redis 和三个模型能力是否均已配置并可用。
没有配置真实模型凭证时服务不会伪装成 ready。

常用质量检查：

```bash
make agent-format
make agent-check
```

历史 Java 项目是不完整的旧源码快照，不属于 Agent 服务质量门禁。阶段 2 新增的健身核心
Tool Gateway 会拥有独立、可复现的 Java 构建和自动化测试，且不会恢复赛事、作品或活动代码。

Python 版本固定为 3.11，`uv.lock` 是依赖事实源；CI 和本地均使用 `uv sync --locked`，
禁止在未更新锁文件的情况下隐式升级依赖。HTTP 请求会返回 `X-Request-ID` 和
`X-Trace-ID`，结构化日志使用相同字段关联后续 Agent、模型和 Tool 调用。

## 可观测性

- `GET /metrics` 暴露 Prometheus 文本格式指标，生产环境必须在网关或网络策略层限制访问。
- 本地启动 OpenTelemetry Collector：`make observability-up`。
- 启用 Trace 时配置 `AGENT_OTEL_ENABLED=true` 和
  `AGENT_OTEL_EXPORTER_OTLP_TRACES_ENDPOINT`。
- OTLP 鉴权信息使用标准 `OTEL_EXPORTER_OTLP_HEADERS` 注入，不得写入环境模板。
- HTTP 指标只使用路由模板，不使用用户 ID、原始 URL、Prompt 或 Tool 参数作为标签。

环境分层和 Secret 管理规则见 `deployment/environments/README.md`。

## 生产镜像

```bash
make agent-image
```

镜像使用多阶段构建和 `uv.lock`，最终容器以固定 UID/GID `10001:10001` 运行；CI 会在
Python 检查通过后重新构建镜像，防止只在开发机缓存中可用。
