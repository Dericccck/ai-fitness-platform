# 离线任务评测规划集 v1

`datasets/v1.jsonl` 是 120 条人工设计的合成规划题，按主场景分别为路由 20、知识 30、预约 35、经营 20、安全 15；开发集 80 条、测试规划集 40 条。每条 Case 是一个完整任务，重复运行不能增加题目数。每条含独立输入、前置假设、预期、断言和标签；`fixtures/planning-v1.json` 仅描述所需前提，不能创建夹具或证明隔离。资源、知识真值、具体时段、业务审计、失败注入和真实通信适配器均仍需实现和验证。

这些题的 `review.status` 全为 `draft`：没有伪造人工标注或专业审核记录。`test` 只是计划划分，并非冻结测试集或盲测集；family 由作者初步分组，必须由人工复核任务语义、共享知识/fixture 和跨集近似变体，按需重分组并重新版本化。任何人工审核必须记录真实标注、复核、日期和裁决引用。所有 Case 均为 `planned`、`in_scope`，未完成适配时不得作为产品能力通过，应保留在覆盖分母中并报告 SKIPPED。测试集草案不得用于发布验收。

数据协议见 `app/evaluation/task_eval/contracts.py`；字段严格校验并禁止未知字段，Case 的 `expected.assertions` 引用具名版本化规则。固定的 `allowed_routes` 目前仅标定七个明确路由题，其余需按产品路由映射和人工预期核实；这些空集合不代表允许任意路由或通过。ROUT-005 的主体是学员而请求“授课安排”，现有按学员查询的预约接口无法证明教练日程的预期路由，因此撤去未经核实的标签并保留待审核。`answer_rubric_ref` 当前只是规划引用，不代表 Rubric 已建立或校准；语义结果不可判时必须为 UNKNOWN。案例中故障注入名称是设计目标，不代表故障已实际注入或被观察到。

从 `fitness-agent-service` 目录，执行以下纯本地校验及单元测试：

```bash
python -m app.evaluation.task_eval.cli validate --dataset evals/task_eval/datasets/v1.jsonl
python -m pytest tests/task_eval/test_dataset.py -q
```

开发规划校验允许 draft test 并输出醒目告警。发布前应使用 `load_dataset(path, require_reviewed_test=True)`（或 CLI 中对应的发布模式，如有）拒绝未经审核的测试题。`load_dataset` 只检查声明的 family 是否跨集，不能替代语义审核；也不能证明人工审核、真实隔离、证据采集或产品正确。摘要是原始 JSONL 字节 SHA-256，数据改变须重新计算并冻结版本。

`BOOK-018` 和 `BOOK-026` 至 `BOOK-031` 的 `message_ref` 指向相应规划 fixture 的中文预约请求模板。适配器必须先创建真实可预约的隔离课程，再解析课程、教练和含时区的绝对时间，冻结完整消息与 fixture 摘要；不能将引用或模板原样发给 Agent。当前仅有模板，尚无生成和验证夹具的实现。预约故障类案例在开发集和测试规划集均有相似的确认、重试和异步故障，声明不同 `family_id` 不能证明语义隔离；必须由人工审核共享事故与相似变体，并在必要时整族调整分集及重新版本化。当前不能宣称分集已保证语义隔离。

## 通知偏好策略组件评测

```bash
python -m app.evaluation.task_eval.component_notification
```

运行实际通知偏好策略函数，以固定时钟及本地合成的只读持久层替身检查关闭通知、跨午夜安静时间、频率限制、频率边界与允许通知；JSON 报告列出预期、实际决策及读取范围，任何案例失败时进程返回非零。该组件运行不依赖网络、数据库或测试包，案例仅用于开发，不属于上面的 120 条任务结果。它没有运行通知 Worker、实际投递、真实数据库并发、过期检查或完整 Agent，也不能证明端到端隐私或投递安全。

安全建议评分要求 adapter 提供与判断 rubric 版本一致的 `professional_review`（含 `reviewer_id`、`qualification`、`review_ref`、`rubric_version`），并在 `evidence_refs` 中列出该审核引用；审核身份和记录的真实性须由证据适配器核实。只提供一个审核完成布尔值不足以判定；安全评分固定为 0 = FAIL、1 = UNKNOWN、2 = PASS。仓库不包含或伪造专业审核记录。

## 经营查询策略与趋势组件评测

```bash
python -m app.evaluation.task_eval.component_operations
```

运行实际经营查询策略与趋势构造函数，使用合成机构、查询和 Gateway 聚合响应模拟数据；逐条报告输入、预期和实际策略决策或趋势结果。覆盖正确查询、机构越界、指标或时间漂移、信息不足或多指标、时间桶和对比口径漂移，以及空记录、缺失时间桶和零基数的趋势处理。任何案例失败或异常时返回非零，异常不暴露内部细节。

该组件不调用 Agent、Gateway 或数据库，不验证身份签名、数据库权限、SQL、真实业务聚合或完整任务。这里的案例独立于规划集的 120 条任务，不代表那些任务已执行。
