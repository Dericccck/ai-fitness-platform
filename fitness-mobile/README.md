# Move Well 移动端

基于 Expo / React Native 的独立 App 工程，提供动作分析页面及移动端接口接入。微信小程序不在本工程内。

## 当前能做什么

- 账户登录、会话列表、创建及删除；可显式关联当前账户已有的文本会话，读取最近六条安全可用的文本供多模态分析参考。原文本历史不被迁移或删除，分析结果不会写回原文本会话。安全令牌在 iOS/Android 使用系统安全存储，轮换和撤销通过后端完成。
- 选择图片或视频、预览、输入问题、上传进度、停止上传、提交分析、轮询及恢复任务、查看报告和历史、删除素材。允许最多 3 张图片或 1 个视频。
- 正式模式需 `EXPO_PUBLIC_AGENT_BASE_URL` 指向后端 HTTPS 根地址。模型密钥、OSS 密钥以及身份签名密钥只能放在后端安全环境中。
- 未配置真实服务时，首页的演示页面只展示示例，不上传和评价动作；本机联调模式见下文。

原生 App 与生产服务尚未经过真机及真实外部供应商验收，不能据此宣称生产就绪。

## 本机联调模式

该模式使用真实 API、独立测试 PostgreSQL/Redis、上传验证和后台任务；认证、OSS 和模型为本机替身，文件只存于模拟服务进程内存。报告明确表明**没有调用真实模型，也不能判断动作是否标准**。浏览器会话和草稿仅在内存，刷新会清除；重启服务将丢失模拟素材。不得上传真实私人素材，不得将此模式部署或对公网监听。

依赖 Docker、Python 开发环境、`ffprobe`、Node 24 和 npm。以下命令均从仓库根目录执行，容器只用于本机联调；若相同名称或端口已有服务，先检查后再操作。

```sh
docker run --name codex-mobile-pg-test -d -e POSTGRES_PASSWORD=local-test -e POSTGRES_DB=fitness_multimodal_test -p 127.0.0.1:57323:5432 pgvector/pgvector:pg16
docker run --name codex-mobile-auth-test -d -p 127.0.0.1:57043:6379 redis:7-alpine
```

等待数据库就绪后，在后端目录的一个终端执行：

```sh
cd fitness-agent-service
export AGENT_DATABASE_URL='postgresql+asyncpg://postgres:local-test@127.0.0.1:57323/fitness_multimodal_test'
export AGENT_MOBILE_TEST_REDIS_URL='redis://127.0.0.1:57043/15'
.venv/bin/alembic upgrade head
FITNESS_DESKTOP_SIMULATOR=1 .venv/bin/uvicorn scripts.multimodal_desktop_simulator:app --host 127.0.0.1 --port 8099
```

在另一个终端：

```sh
cd fitness-mobile
npm ci
EXPO_PUBLIC_DESKTOP_SIMULATION=1 EXPO_PUBLIC_AGENT_BASE_URL=http://127.0.0.1:8099 npx expo start --web --port 8086
```

访问 `http://localhost:8086`，选择“进入本机联调”。测试登录可填写 `demo` / `demo` / `org-1`。模拟认证是替身，不连接用户账户。测试结束后可停止两个终端及测试容器；如果要重新建库，先确认容器内没有需要保留的数据。

## 真实后端连接

在后端安全环境配置模型服务、版本控制开启的私有 OSS、可信上游认证、数据库、Redis、加密及身份签名配置，完成迁移并分别启动 API 和 `python -m app.multimodal_worker_main`。三个特性开关 `AGENT_MULTIMODAL_ENABLED`、`AGENT_MOBILE_AUTH_ENABLED`、`AGENT_MULTIMODAL_API_ENABLED` 默认关闭。使用 `AGENT_MULTIMODAL_GLOBAL_MAX_CONCURRENCY` 和 `AGENT_MULTIMODAL_DAILY_MAX_CALLS` 配置共享并发和每日调用次数上限；每日调用额度按 UTC 日计算，不等同于金额上限。生产多模态接口必须配置无网络媒体解析容器；部署步骤、存储清理和故障恢复见 [运维说明](../docs/multimodal-operations.md)。完整依赖和上线前剩余事项见 [实现状态](../docs/multimodal-implementation-status-20261103.md)。

## 检查

```sh
npm run typecheck
npm test
npx expo lint
npx expo export --platform all
```

打包检查不等于已签名安装包或设备验收。依赖审计曾报告 Expo 工具链传递依赖的中等级别问题，发布前需重新核查。
