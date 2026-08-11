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
make java-check
```

Python 版本固定为 3.11，`uv.lock` 是依赖事实源；CI 和本地均使用 `uv sync --locked`，
禁止在未更新锁文件的情况下隐式升级依赖。HTTP 请求会返回 `X-Request-ID` 和
`X-Trace-ID`，结构化日志使用相同字段关联后续 Agent、模型和 Tool 调用。
