# 耗时与费用统计输入

该组件只汇总调用方显式提供的 JSON 记录，不运行 Agent、模型或业务服务，也不从已有报告猜测缺失数据。执行：

```bash
.venv/bin/python -m app.evaluation.task_eval.performance_report /path/to/records.json
```

输入顶层为 `records` 数组，每条记录须有唯一 `trial_id`、`model_mode`、`business_mode`、`judge_mode`（各为 `real`、`stub`、`not_used`、`unknown` 之一）、`cache_state`（`cold`、`warm`、`unknown`）、正整数 `concurrency`、布尔值 `timed_out` 和结果证据引用 `outcome_evidence_ref`。所有模式必须由执行方明确提供；混合真实和桩组件的 Trial 会单独标记为 `mixed`。

可选 `measurements` 是以 `e2e`、`model_ttft`、`first_valid_answer`、`model`、`tool`、`retrieval` 为键的对象；每项必须提供非负数值 `duration_ms`、`evidence_ref`、`start_event`、`end_event`、`scope` 和布尔值 `includes_human_wait`。仅起止点、范围及人工等待定义相同的测量才聚合。超时 Trial 的端到端耗时视为截尾值，不进入完成耗时分位数；已实际观测的其他阶段耗时仍可统计。报告同时列各指标缺失数量及证据引用。

可选 `model_usage` 和 `judge_usage` 分别记录 `input_tokens`、`output_tokens`、`input_price_per_million`、`output_price_per_million`、三位大写 `currency`、`usage_evidence_ref` 和 `pricing_evidence_ref`；单价用非负十进制字符串。缺少任一字段时，费用为 `null`，不是零。只有相应模式为 `real` 且上述字段齐全时才估算成本。不同货币不相加；有缺失时只展示标明覆盖数量的已知部分小计，不显示总成本。每条费用记录中的 token 必须适用该条所述的同一组单价；不同单价的调用需要分开提供来源记录，不得套用单一价格。

P50/P95 使用 nearest-rank 方法，小于 20 个观测值标为小样本；该阈值是展示提示，不能据此认定达到生产 SLA。所有引用、模式、价格和 token 数均为调用方声明，组件不验证其真实性或供应商实际账单；Judge 与模型费用分开报告。
