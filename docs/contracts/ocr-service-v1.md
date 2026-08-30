# OCR Service Contract v1

这是 Agent 服务与独立 OCR 服务之间的最小 HTTP 契约。OCR 服务可以由内部 GPU 服务或云厂商适配器实现，
但不能改变 Agent 的父子节点、引用和权限流程。

当前仓库的自建实现位于 `fitness-ocr-service`，生产默认接入
PaddleOCR PP-StructureV3；接口层不依赖具体 OCR 厂商。服务端会从 OCR 引擎的块框和
PDF 原始页尺寸计算归一化区域；若引擎没有可追溯置信度或块框，服务会失败关闭，不能
用默认值伪造识别质量。

## Request

```http
POST /v1/parse
Content-Type: multipart/form-data
Authorization: Bearer <service-secret>
```

表单字段：

- `file`：原始 PDF 文件。
- `pages`：可选，逗号分隔的缺失页码；空值表示整份 PDF。

## Response

```json
{
  "media_type": "application/pdf",
  "warnings": [],
  "blocks": [
    {
      "kind": "TEXT",
      "content": "训练前进行动态热身。",
      "heading_path": ["热身"],
      "source_page": 2,
      "confidence": 0.96,
      "source_region": {"x": 0.1, "y": 0.2, "width": 0.7, "height": 0.3},
      "metadata": {"ocr_engine": "internal-v1"}
    }
  ]
}
```

`kind` 只能是 `TEXT` 或 `TABLE`，`content` 不能为空。`confidence` 必须是 `0~1` 的模型置信度；
`source_region` 使用页面左上角为原点的归一化坐标，且右下角不能超出页面。表格还可以提供 `table_index`、
`row_start`、`row_end`；所有 block 的来源页码和区域会被复制到子节点和最终引用中。Agent 会把置信度和区域
转换为整数基点保存，低于部署门槛的结果不会解除 OCR 阻断。

## Failure rules

- HTTP 4xx/5xx、网络超时、响应不是 JSON 或响应超过大小限制：上传任务失败，不进入审核队列。
- block 结构不合法：上传任务失败，不允许按纯文本猜测入库。
- 缺少 `source_page`、`confidence` 或 `source_region`，或者来源页码/区域越界：上传任务失败，不允许解除 OCR 阻断。
- OCR 服务不负责权限，组织、角色和用户权限仍由 Agent 服务从签名上下文和 PostgreSQL ACL 过滤决定。
