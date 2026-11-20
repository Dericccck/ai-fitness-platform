# 预约确认和提交关联诊断

单案例沙箱的 `trial.commit_link` 对照确认单、批准／凭证签发／执行成功事件和一次性凭证消费，使用同一 MySQL 一致性快照核对预约操作、预约行、业务审计及创建事件 Outbox。请求、确认、凭证、主体、机构、动作、资源和内容摘要均需匹配；数据库侧比较使用字节级比较。报告只保留数量和结果，不记录凭证或具体标识。确认事件来自应用仓储，MySQL 查询由沙箱观察器读取，二者不是独立签名的证明。

`MATCH` 说明观察到的记录可以关联，`MISMATCH` 会使诊断失败，读取失败则为 `UNKNOWN`。该字段不证明数据库之间的提交顺序，也不证明确认前审计窗口完整、在途事务全部结束、Outbox 已投递、模型自主选择工具或人工审核通过。即使 `MATCH`，未决的必要断言仍保持 `UNKNOWN`，不得作为验收通过证据。

测试使用脚本 `scripts/task_eval_cross_service_booking.sh --single-development-trial`；真实模型诊断使用 `--booking-positive-real-model-trial BOOK-007` 或 `--book-006-real-model-trial`，并按脚本要求提供单独的密钥文件和本地预算参数。沙箱依赖本机一次性容器；保管报告时仍应遵守评测环境的数据访问要求。
