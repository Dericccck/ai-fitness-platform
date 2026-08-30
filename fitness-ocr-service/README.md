# Fitness OCR Service

这是独立部署的 PDF OCR 与文档结构化服务，当前使用 PaddleOCR PP-StructureV3，向健身
Agent 提供稳定的 `POST /v1/parse` 契约。赛事、活动运营及旧作品模块不属于本服务范围。

## 责任边界

- OCR 服务：PDF 页码裁剪、扫描页识别、版面/表格解析和结果结构化。
- Agent 服务：文件安全扫描、组织/角色权限、父子节点切片、Embedding、检索和引用。
- OCR 服务不接触业务数据库，也不决定用户能否访问文档。

## 本地开发

不安装推理 extra 时，可以运行契约测试；真正识别需要 Linux CPU/GPU 推理环境。PaddleOCR
模型会在首次加载时下载，生产环境应在镜像构建或受控模型卷中预热并固定模型版本。

```bash
cd fitness-ocr-service
uv sync --extra dev
uv run pytest
uv run uvicorn app.main:app --port 8091
```

真实部署需要安装推理依赖：

```bash
uv sync --extra inference --extra dev
```

生产环境至少设置：

```text
OCR_ENVIRONMENT=production
OCR_AUTH_REQUIRED=true
OCR_API_KEY=<secret-manager-value>
OCR_DEVICE=gpu
```

当前服务使用 `/v1/parse` multipart 请求，`pages` 是可选的 1-based 页码列表。返回结果只包含
`TEXT`/`TABLE` block，表格会统一转成带表头的 Markdown，并保留原始页码、置信度和页面区域。完整契约见
`../docs/contracts/ocr-service-v1.md`。

## 运行时约束

- 默认单并发，避免多个 PDF 同时争抢 GPU/模型内存；按压测结果调大 `OCR_MAX_CONCURRENCY`。
- 默认单文档最多 50 页、20 MiB；限制必须与 Agent 的上传上限保持一致或更严格。
- OCR 失败返回 503，结果校验失败返回 422，Agent 不会把失败响应当成纯文本继续入库。
- 生产服务必须启用 Bearer Token；本地契约测试可以关闭鉴权。
