# Memory 评测集

`memory_labeled.json` 是人工编写的合成样本，包含 54 条提取标签和 32 条关系标签（其中 20 条是不同维度的新增/完全重复规则样本）。它用于发现回归，不代表真实用户分布，也未通过独立双人标注。样本包含长期目标、偏好、器械、时间、沟通、临时请求、他人表达、敏感信息和注入诱导。

在服务目录中运行：

```sh
.venv/bin/python scripts/memory_evaluation.py
.venv/bin/python scripts/memory_evaluation.py --relation-rules
.venv/bin/python scripts/memory_evaluation.py --live --output /tmp/memory-live-report.json
.venv/bin/python scripts/memory_evaluation.py --relation-live --output /tmp/memory-relation-live-report.json
```

默认命令只检查数据格式；`--relation-rules` 调用真实比较器，明确区分无需模型的已评分项、需模型的未评分项和尚不支持的撤销。`--live` 评测提取类别，`--relation-live` 评测新旧记忆比较，两者调用当前配置的真实模型，涉及网络和费用，错误会单独统计、进程返回非零。提取结果是类型集合的粗粒度评估，未衡量值、数量、否定关系或引用依据的完整性；没有成功模型响应不能判定模型准确率。模型重试、供应商不可达和前置跳过均不能当作正确空结果。

上线评估还需补充经脱敏和授权的真实样本、独立标注一致性、时间表达与偏好冲突的精细指标、权限隔离、遗忘与并发故障、数据规模下延迟成本，以及在真实基础设施和模型上端到端运行。禁止以本小样本的有限规则通过率代替全功能验收。
