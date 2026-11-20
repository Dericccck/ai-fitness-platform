# 合成知识检索草稿

`corpus.json` 为 KNOW-001～KNOW-010 提供逐案例隔离的虚构资料和草稿关联标签，使用 hash 同原始数据集及其案例内容绑定。资料未经专业审核，不可用于健康建议或实际门店政策；其中取消规则仅适用于虚构门店。三个 test 案例及标签均对开发可见，不能作为独立盲测或泛化能力证明。

从 `fitness-agent-service` 目录执行：

```sh
.venv/bin/python -m app.evaluation.task_eval.knowledge_draft_corpus \
  --dataset evals/task_eval/datasets/v1.jsonl \
  --output var/task-eval/draft-knowledge-retrieval.json
```

输出文件必须不存在。探针调用真实 `RagService`，但仓库、向量编码和重排器均为本地替身；每例只有一条相关资料与一条无关资料，按 top-1 计算召回。因此即使十例召回均通过，也只是小样本组件观察，不能证明真实知识库、生产检索、授权、模型答案、引用准确性或临床安全。CLI 在组件全通过时返回 3，表示完整任务未评估；组件失败返回 2。不得将该报告或草稿评分参数作为发布验收证据。

`draft_parameter_proposal_v1` 中的相应文档 ID、top-k 和来源 hash 用此资料生成。修改原始案例或资料后必须重新建立、审核和绑定版本；生成器不会对原始数据静默套用旧标签。
