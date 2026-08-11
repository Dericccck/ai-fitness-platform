# AI 健身多 Agent 平台

本仓库基于原有健身管理后端建设企业级多 Agent 平台。开发范围、架构边界、实施顺序和
协作规则统一以
[AI 健身多 Agent 平台开发路线与项目规则](docs/ai-fitness-agent-development-roadmap.md) 为准。

赛事与活动运营属于历史遗留代码，默认不纳入当前健身项目的分析、开发和简历描述。

## 项目组成

- 历史 Java 8 + Spring Boot 源码：作为用户、机构、教练、课程、合同和预约规则的业务参考。
- `fitness-core-gateway`：独立、可复现构建的健身核心 Tool Gateway，提供受权限保护的只读业务查询。
- `fitness-agent-service`：承载 Agent 编排、模型网关、RAG、Memory 和 Tool 调用。
- PostgreSQL/pgvector：承载 Agent 状态、长期记忆、知识索引和审计扩展数据。
- Redis：承载缓存、短期状态、幂等与分布式协作数据。

## 统一开发命令

```bash
make infra-up
make agent-sync
make agent-check
make gateway-check
make agent-image
```

查看全部命令：

```bash
make help
```

历史 Java 源码来自不完整的旧项目快照，缺失赛事、作品、活动等类型，并曾因本地
`target/classes` 残留产物表现为“可以编译”。干净环境执行 `clean compile` 会真实暴露这些缺失，
因此它不属于当前 CI 质量门禁，也不得在项目材料中描述为可完整构建。阶段 2 已建立不依赖遗留
赛事代码的首版健身核心适配层，后续继续补充真实数据库集成测试和写工具的业务安全门禁。

如需复现并审计旧项目的编译问题，可显式执行 `make legacy-java-diagnostic`；该命令当前预期失败，
不属于日常开发检查。Maven Wrapper 3.8.8 仅用于固定诊断和后续 Java 适配层的构建工具版本。

`fitness-core-gateway` 是当前阶段新增的 Java 构建边界，可使用 `make gateway-check` 验证。
它只访问健身核心表，不恢复赛事、作品和活动代码。

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
`WorksVideoUploadService`，无法形成可复现构建。历史 POM 保留了这组失效视频代码的编译隔离
配置供审计；它们不会进入新的健身核心适配层。

如果后续需要训练视频能力，应建设独立媒体模块，并通过受控的私有制品仓库、权限校验、
文件扫描、回调验签和自动化测试重新接入，不直接恢复来源不可追踪的本地 JAR。

## 开发约束

- 核心业务代码、Agent 编排、权限、幂等、审计、RAG 和 Memory 逻辑需要详细中文注释。
- 每次提交信息应具体说明完成的功能、关键设计和验证结果。
- 所有业务事实必须来自 Java Tool Gateway、业务数据库或经过治理的知识库。
- Agent 不得绕过 Java 权限层直接修改现有健身业务数据。
