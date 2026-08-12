# OCR Service Contract v1

这是 Agent 服务与独立 OCR 服务之间的最小 HTTP 契约。OCR 服务可以由内部 GPU 服务或云厂商适配器实现，
但不能改变 Agent 的父子节点、引用和权限流程。

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
      "metadata": {"ocr_engine": "internal-v1"}
    }
  ]
}
```

`kind` 只能是 `TEXT` 或 `TABLE`，`content` 不能为空。表格还可以提供 `table_index`、`row_start`、
`row_end`；所有 block 的来源坐标会被复制到子节点和最终引用中。

## Failure rules

- HTTP 4xx/5xx、网络超时、响应不是 JSON 或响应超过大小限制：上传任务失败，不进入审核队列。
- block 结构不合法：上传任务失败，不允许按纯文本猜测入库。
- OCR 服务不负责权限，组织、角色和用户权限仍由 Agent 服务从签名上下文和 PostgreSQL ACL 过滤决定。
