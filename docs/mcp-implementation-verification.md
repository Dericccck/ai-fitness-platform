# MCP 实施验收记录

完成日期：2026-10-28。代码目录：`/Users/a1-6/Desktop/fitness-backend`。
这是工作区改造及隔离测试结果；现有业务数据库未执行发布迁移，未部署生产。

## 已交付

- 官方 SDK `mcp==2.3.0`、协议 `2026-07-28` 的独立 Python MCP 服务，以及共享契约包。
- Agent 的固定目录发现与校验、20 个 Java 业务能力路由、4 个本地能力保留；经营比较保留本地编排。
- 单请求身份与确认凭证隔离、固定 HTTP 路径、服务 scope/有效期/资源绑定、独立凭证及 RS256 验证。
- 九个写工具的精确确认、实际 body 字节哈希、稳定 ID、未知结果处理、取消与全域对账。
- HTTP 写入不重试；明确 MCP 错误不计成功；SDK 异常组保持领域错误分类；不自动换通道重写。
- 确认的 transport/contract_version 持久化、旧确认兼容及历史可重试记录隔离。
- 公平对账领取、持久化租约、fencing、指数退避、查询与清理时限；Memory 未知动作进入人工核实汇总。
- Compose/SAE、独立镜像、生产配置门禁、Worker MCP/执行凭证隔离、监控指标与告警、启动与回退文档。

## 实际验证

| 验证 | 结果与范围 |
|---|---|
| Agent 全量 pytest | **902 passed / 31 skipped**，26.84 秒；1 条既有 LangChain 待弃用警告 |
| Agent → MCP 贯通 | **27 passed**，已包含于 Agent 全量；20 工具、9 写、None/date/冒号 ID、伪造身份 |
| MCP 服务及共享契约 | **50 passed**，9.99 秒；真实 TCP/SSE、100 用户并发、取消、scope、deadline、20 工具矩阵、dot 段拒绝 |
| Java 四模块全量 Maven | **148 执行通过 / 5 条原外部集成跳过**；Gateway 59、Training 42、Booking 26、Customer 26 个方法共 153 |
| 隔离真实 MySQL | **27/27 passed，0 skipped**；Gateway 2、Booking 9、Training 9、Customer 7 |
| 隔离真实 PostgreSQL | **2 项新确认测试通过**，另验证升级/降级/历史隔离与回滚保护 |
| 静态类型 | 根集成 9 个源文件、确认相关 12 个源文件 mypy 通过 |
| 格式及静态检查 | 相关 Python Ruff 通过、`git diff --check` 通过 |
| 依赖锁 | Agent 275 包、MCP 46 包的 lock 检查通过 |
| 部署配置 | 用不含真实 Secret 的六份模板校验 Compose 通过；生产模板及独立凭证契约测试通过 |
| 告警 | 静态契约通过；真实 `promtool check rules` **16 条规则通过** |

Agent→MCP 贯通测试使用真实 Registry、RoutedBusinessToolsClient、McpBusinessToolsClient、
TCP MCP 服务及 RS256 鉴权。九个写工具使用真实确认归一化与 ConfirmationTokenIssuer；
受控 Gateway HTTP stub 验证签名、body SHA、subject、tool、request ID、JTI 和实际字节。
这是跨 Python 组件的真实协议测试；Java/数据库另外由 Maven、H2 与隔离 MySQL 验证，
不把 stub 测试描述成完整生产 Java 链路。

MySQL 使用任务专用随机端口容器与随机库，执行训练 10 条及客服 2 条真实迁移。
验证九工具业务证据、绑定冲突、事务回滚、预约并发、训练唯一键及前导标点 ID；
缺可靠事实保持 UNKNOWN。使用最小列 fixture，不代表用户旧库全部表结构已经核验。
容器与测试库均已清理，未连接开发 `fitness-mysql`。

PostgreSQL 验证 `0047→0048→0049`，历史 UNKNOWN 保留原 ID/JTI，8 并发执行领取仅 1 成功，
多 Worker `SKIP LOCKED`、长期未知退避后的后续操作可被领取、租约过期恢复和旧租约 token
无法释放新租约。`0049→0048→0049`、未完结 MCP 确认阻止降级、清理后
`head→0047→head→base` 均通过。任务专用容器及临时库已清理。

## 容器结果

- 最终 MCP 验证镜像：`fitness-mcp-service:mcp-validation`，
  `sha256:c2c8e4020375492e3f83ff160ca7c71b3d5f5ab785d5640c794928fa36f475ae`。
  生产 RS256 公钥配置烟测：live=200、ready=200、缺 Bearer=401（带 challenge），UID=10001。
- Agent 原生 Linux ARM64 打包验证：`fitness-agent-service:mcp-native-validation`，
  `sha256:58f7b5f41c1312f45dc88e46ec7db700f211411ce6e2406421b0804e4cd22e15`。
  根 context、共享包 wheel、冻结依赖和非 editable 安装成功；断网导入 Client、GatewayClient、
  共享包成功，UID=10001，运行镜像不依赖 `/packages` 源目录。
- Agent **Linux AMD64 尚未构建验收通过**。四次受控尝试均在 Python 基础镜像的 FROM
  拉取阶段发生 blob 大小校验错误（`28289 != 5502`，另一 digest 为 `28167 != 5497`），
  未进入项目 COPY 或依赖安装。未清缓存、重启 Docker 或改变生产 Dockerfile绕过校验。

这些镜像是验收 tag；正式发布应在最终提交上重新生成不可变制品，并在目标架构完成验收。
所有临时烟测容器已删除；未改变现有业务服务。

## 发布前仍需提供的环境条件

生产 Secret、认证/JWKS、实际目标域名和精确 Host allowlist 需由部署平台提供。
执行 PG head 与训练唯一键迁移前须备份并检查历史重复 request_id；不能删除证据强行通过。
若发布到 AMD64，必须先解决镜像仓库拉取错误并完成该架构镜像构建/烟测。
真实 LLM、RabbitMQ、用户真实旧库以及目标预发容量/延迟没有在本轮冒充验收。

启动、凭证、迁移、灰度与回退步骤见 [MCP 实施与运行指南](mcp-integration.md)。
可重复 MySQL 测试见 [隔离验收说明](../fitness-core-gateway/scripts/README-mysql-acceptance.md)。

本地证据日志：`/private/tmp/fitness-mcp-final-agent-tests.log`、
`/private/tmp/fitness-mcp-final-types.log`。日志为临时文件，长期证据应由 CI 保存。
