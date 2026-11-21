# 预约确认和提交关联诊断

单案例沙箱的 `trial.commit_link` 对照确认单、批准／凭证签发／执行成功事件和一次性凭证消费，使用同一 MySQL 一致性快照核对预约操作、预约行、业务审计及创建事件 Outbox。请求、确认、凭证、主体、机构、动作、资源和内容摘要均需匹配；数据库侧比较使用字节级比较。报告只保留数量和结果，不记录凭证或具体标识。确认事件来自应用仓储，MySQL 查询由沙箱观察器读取，二者不是独立签名的证明。

`MATCH` 说明观察到的记录可以关联，`MISMATCH` 会使诊断失败，读取失败则为 `UNKNOWN`。该字段不证明数据库之间的提交顺序，也不证明确认前审计窗口完整、在途事务全部结束、Outbox 已投递、模型自主选择工具或人工审核通过。即使 `MATCH`，未决的必要断言仍保持 `UNKNOWN`，不得作为验收通过证据。

测试使用脚本 `scripts/task_eval_cross_service_booking.sh --single-development-trial`；真实模型诊断使用 `--booking-positive-real-model-trial BOOK-007` 或 `--book-006-real-model-trial`，并按脚本要求提供单独的密钥文件和本地预算参数。沙箱依赖本机一次性容器；保管报告时仍应遵守评测环境的数据访问要求。

## 写入阶段与事务围栏诊断

隔离 MySQL 的受限写入触发器会在同一业务事务内记录 `PENDING` 或 `APPROVED` 阶段。单预约试验在 PostgreSQL 确认仓储返回后、恢复业务执行前，由本地管理连接在事务中切换阶段并统计 `PENDING` 写入。阶段行的独占锁会等待持有该行锁的触发器事务结束；切换期间的新写入等待阶段锁。可运行 `bash scripts/task_eval_mysql_barrier_lab.sh --phase-witness`，在全新 MySQL 中检查指定连接确实在该行发生锁等待、旧事务提交、新事务继续及回滚审计消失。

`approval_phase_diagnostic` 只记录本次受保护表的本地观察。`NO_PENDING_PHASE_WRITES` 表示阶段切换事务的受控读取没有发现这类已提交事件；它依赖运行器、MySQL 配置、账号及触发器完整性，且会改变并发时序。阶段切换发生在批准之后，因此发现 `PENDING` 事件也不能单凭它断言事件一定早于批准。该机制不覆盖未受监控或特权写入，确认提交与进程来源尚无独立见证，也不能证明阶段切换后的所有写事务已经结束；`audit_window_complete` 仍为 `false`，正式断言仍为 `UNKNOWN`。
