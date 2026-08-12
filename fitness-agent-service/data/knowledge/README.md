# 健身知识库资料目录

`raw/` 保存本地下载的原始资料，当前目录按主题分类。原始资料可能带有版权限制或医疗相关内容，
已通过 `.gitignore` 排除，不提交到 GitHub。

## 目录分类

- `general-fitness/`：全民健身指南、基础运动知识和面向普通用户的科普资料。
- `training-and-exercise/`：运动处方、居家训练、训练频率、强度、热身和恢复资料。
- `exercise-safety/`：动作安全、膝腰保护、平衡、肌肉耐力、高温和血压运动防护资料。
- `weight-management/`：体重、减脂和健康体重管理资料。
- `medical-guidelines/`：慢性病、肾脏和其他需要人工审核的医疗相关资料。
- `international-guidelines/`：WHO 和美国身体活动指南等外文资料。
- `reference-not-indexed/`：当前解析器暂不直接入库的参考文件，例如 PPTX。

## 入库规则

原始文件进入审核和索引流程前，需要补充来源 URL、发布日期、版权/许可证、风险等级、适用角色和
组织范围。医疗相关资料默认标记为 `requires_human_review=true`，不能直接作为个体诊断或治疗建议。

索引时保留原始文件的 SHA-256，用于增量更新、版本追踪和审计；RAG 只检索通过安全扫描、解析和
审核的版本。

## 本地校验命令

在项目根目录执行以下命令，重新生成清单并验证当前解析器对每份资料的读取结果：

```bash
make knowledge-manifest
make knowledge-validate
KNOWLEDGE_OCR_ENDPOINT=http://127.0.0.1:8091/v1/parse make knowledge-validate-ocr
```

`manifest.json` 只保存路径、哈希、来源和审核元数据，不保存原始文件内容。PDF 和 DOCX 会进入
解析验证；PPTX 当前标记为 `REFERENCE_ONLY`，不会误进入索引。验证结果中的
`PASS_WITH_WARNINGS` 通常表示 PDF 存在无法提取文本的页面，需要后续接入真实 OCR 后复核。
`knowledge-validate-ocr` 会把缺失页交给独立 OCR 服务，只回填缺失页，不重复识别已有文本页；
OCR 服务不可用、返回结构不符合契约或哈希发生变化时，验证不会伪装成通过。
