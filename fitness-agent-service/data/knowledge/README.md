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
