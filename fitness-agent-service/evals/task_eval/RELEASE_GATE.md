# 离线验收门禁

`release-gate` 读取已建立的任务评测报告并按预先冻结的策略核对：数据摘要和版本、制品 revision、运行模式、全量选择范围、重复次数、逐条断言及证据引用、风险结果与覆盖阈值。它基于数据集和逐 Trial 记录重算覆盖率，不相信报告自报的总分或 PASS。Case/Trial 缺失、必要断言未判定、来源不可信、计划内能力未实现、草稿、阈值或身份不一致均不能通过。此命令不执行模型或业务请求。

从 `fitness-agent-service` 目录调用：

```bash
python -m app.evaluation.task_eval.cli release-gate \
  --dataset evals/task_eval/datasets/v1.jsonl \
  --manifest /path/to/manifest.json \
  --trials /path/to/trials.jsonl \
  --policy /path/to/frozen-policy.json
```

策略必须在运行前确定，按需配置以下字段：`dataset_sha256`、`dataset_version`、完整 `revision`、`mode`（`model_sandbox` / `integration_sandbox`）、`split`（仅 `test`）、`repeats`、`min_trial_decision_coverage`、`metric_coverage` 和 `risk_coverage`。两个覆盖字典必须分别列出范围内每个必要规则和每类风险的明确阈值，取值为大于 0 且不超过 1 的有限数值。阈值由实际基线及业务风险确定，系统不预设质量门槛，也不能用宽松阈值消除必要断言的 UNKNOWN。自定义小数据集可显式添加 `--no-quota-check`，这不会放宽任何验收安全要求。

`manifest.json` 必须使用已冻结策略的 `gate_policy_sha256`，可通过 `gate_policy_sha256(GatePolicy.model_validate_json(...))` 计算；并记录该策略、测试范围与制品在运行前冻结的可验证来源。该哈希仅用于绑定，并非证明策略确实在执行前冻结或内容可信。报告必须包括全部所选案例、范围外与范围内标识、所有计划 Trial 和必要断言；只提供汇总、改写案例范围、伪造证据引用、把合成桩称为真实运行，均不能取得通过。

当前仓库尚无能独立认证**报告来源与完整性、策略冻结、环境隔离、人工审阅和逐条业务证据**的可信适配器。公开 CLI 和 `assess_release_gate` 不接受用户自报或传入的验证器，因此即使 JSON 声称已隔离、已审核或全部通过，也会明确报告 `BLOCKED`。现有合成 Runner 的报告不满足发布验收契约。可信适配器以后接入时必须独立验证原始数据集字节、报告、策略及相应证据，不能把字符串、布尔值或证据哈希本身当作可信证明。单元测试中的 `TestOnlyVerifier` 仅验证规则引擎，不是可用的生产验证器。

退出码：`0` 为可信证据支持下的验收通过（当前不可达），`1` 为输入/配置错误，`2` 为有可信证据的必要断言失败，`3` 为 `BLOCKED`（缺证据或未完成）。报告输出各项原因和覆盖分子/分母；当前未被独立验证的断言不能进入可判定覆盖分子。不要把 `validate`、`plan`、组件结果或合成实验的成功当作发布验收通过。
