# 预约业务口径澄清后的评分草案

用户明确“每个机构对应一家门店”，且 BOOK-009 的名额是私教预约时段。此版本基于
固定的 v1 草案生成：BOOK-006 用机构表示门店，BOOK-009 用机构、教练和时段表示
单个私教名额。v1 草案和原始规划数据未被改写；旧的独立 `storeId` 和团课容量
假设不再作为这两条案例的当前评分合同。

门店规则仍要求可核验的用户所选门店到机构的解析和实际预约快照；私教时段规则
要求同一机构、教练及时段的完整前后观察，并明确证明异步或并发事务已经收敛。
单次数据库快照不证明收敛，也不证明批准前没有写入。所有案例仍为未审核草稿，
BOOK-009 的规划 test 内容已经暴露，不是独立盲测。此提案不会产生真实任务结果、
临床审核或验收签名。

从 `fitness-agent-service` 目录可重新生成到一个不存在的目录：

```bash
.venv/bin/python -m app.evaluation.task_eval.private_booking_proposal \
  --source evals/task_eval/draft_parameter_proposal_v1/cases.jsonl \
  --output-dir /tmp/fitness-private-booking-proposal
```

生成器同时绑定 v1 文件摘要和解析后案例内容摘要；业务口径仍需作为正式案例的
审核材料，并在冻结测试集时重新核对。
