# AI 健身多 Agent 平台

本仓库基于原有健身管理后端建设企业级多 Agent 平台。开发范围、架构边界、实施顺序和
协作规则统一以
[AI 健身多 Agent 平台开发路线与项目规则](docs/ai-fitness-agent-development-roadmap.md) 为准。

赛事与活动运营属于历史遗留代码，默认不纳入当前健身项目的分析、开发和简历描述。

## 项目组成

- Java 8 + Spring Boot：承载现有用户、机构、教练、课程、合同、预约和权限等业务。
- `fitness-agent-service`：承载 Agent 编排、模型网关、RAG、Memory 和 Tool 调用。
- PostgreSQL/pgvector：承载 Agent 状态、长期记忆、知识索引和审计扩展数据。
- Redis：承载缓存、短期状态、幂等与分布式协作数据。

## 统一开发命令

```bash
make infra-up
make agent-sync
make agent-check
make java-check
```

查看全部命令：

```bash
make help
```

`make java-check` 当前验证历史 Java 后端能够完成默认编译。旧测试套件依赖外部数据库、
Redis 和过时业务数据，原 POM 默认关闭了测试；在权限与 Tool Gateway 阶段会为新增核心
能力建立可隔离、可在 CI 中稳定执行的测试。

## Java 本地启动

开发环境可使用以下 Spring 参数：

```text
--spring.profiles.active=dev
--spring.cloud.config.server.bootstrap=false
--spring.cloud.config.server.git.cloneOnStart=false
```

环境配置中的数据库、Redis、短信、微信和云服务凭证必须通过环境变量或密钥管理系统注入，
不得提交真实值。示例配置仅允许保留空值或明确的本地占位值。

## 历史阿里云视频代码

旧视频上传代码依赖阿里云单独分发的本地 JAR，同时引用了本仓库缺失的
`WorksVideoUploadService`，无法形成可复现构建。默认 Maven 构建仅隔离这组失效的视频上传、
回调和测试控制器，源码仍保留供业务审计；图片上传及其他可正常解析的依赖不受影响。

如果后续需要训练视频能力，应建设独立媒体模块，并通过受控的私有制品仓库、权限校验、
文件扫描、回调验签和自动化测试重新接入，不直接恢复来源不可追踪的本地 JAR。

## 开发约束

- 核心业务代码、Agent 编排、权限、幂等、审计、RAG 和 Memory 逻辑需要详细中文注释。
- 每次提交信息应具体说明完成的功能、关键设计和验证结果。
- 所有业务事实必须来自 Java Tool Gateway、业务数据库或经过治理的知识库。
- Agent 不得绕过 Java 权限层直接修改现有健身业务数据。
