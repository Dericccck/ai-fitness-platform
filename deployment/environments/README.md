# Agent 环境配置约定

Agent 服务使用同一份镜像在不同环境运行，环境差异只能通过环境变量、Secret Manager 和
部署清单注入，禁止为测试、预发布或生产环境维护不同代码分支。

## 环境定义

| 环境 | `AGENT_ENVIRONMENT` | API 文档 | Trace 采样建议 | 配置来源 |
| --- | --- | --- | --- | --- |
| 本地 | `local` | 开启 | 100%（启用时） | 开发者未提交的 `.env` |
| 自动测试 | `test` | 关闭 | 关闭 | CI 环境变量 |
| 预发布 | `staging` | 按内网策略 | 100% | 部署平台 ConfigMap/Secret |
| 生产 | `production` | 关闭 | 初始 10%，按容量调整 | Secret Manager + 部署平台 |

## 配置分级

- 普通配置：服务名、端口、日志级别、模型名称、采样率，可放入部署清单。
- 敏感配置：数据库密码、Redis 密码、LLM/Embedding/Reranker Key、OTLP 鉴权 Header、
  Gateway 服务间 Token、Gateway 数据库密码，只能由 Secret Manager 注入。
- 动态业务配置：提醒频率、Agent 策略、组织开关等，后续进入配置中心并保留版本与审计，
  不通过环境变量频繁修改。

## 发布规则

1. CI 只构建一次不可变镜像，并使用 Git Commit SHA 标记版本。
2. 同一镜像依次提升到测试、预发布和生产，不在环境间重新构建。
3. 生产默认关闭 Swagger/ReDoc，Metrics 只允许监控网络访问。
4. Trace 必须发送到受控 OpenTelemetry Collector，不允许业务服务直接写入多个厂商后端。
5. 环境模板只能包含非敏感默认值或占位符，真实凭证不得进入 Git。
6. Agent 发布必须生成不可变清单：`service_version` 绑定 Git Commit SHA，staging/production 镜像使用 `@sha256` digest，
   配置契约只保存键名摘要，不保存 Secret；由 `make agent-release-manifest-check` 校验后再交给部署平台。

各环境的非敏感覆盖示例位于当前目录。完整变量说明以
`fitness-agent-service/.env.example` 为准。
