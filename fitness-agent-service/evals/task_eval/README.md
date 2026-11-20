# 离线任务评测规划集 v1

`datasets/v1.jsonl` 是 120 条待人工审核的合成规划题，按主场景分别为路由 20、知识 30、预约 35、经营 20、安全 15；开发集 80 条、测试规划集 40 条。每条 Case 是一个完整任务，重复运行不能增加题目数。每条含独立输入、前置假设、预期、断言和标签；`fixtures/planning-v1.json` 仅描述所需前提，不能创建夹具或证明隔离。资源、知识真值、具体时段、业务审计、失败注入和真实通信适配器均仍需实现和验证。

这些题的 `review.status` 全为 `draft`：没有伪造人工标注或专业审核记录。`test` 只是计划划分，并非冻结测试集或盲测集；family 由作者初步分组，必须由人工复核任务语义、共享知识/fixture 和跨集近似变体，按需重分组并重新版本化。任何人工审核必须记录真实标注、复核、日期和裁决引用。所有 Case 均为 `planned`、`in_scope`，未完成适配时不得作为产品能力通过，应保留在覆盖分母中并报告 SKIPPED。测试集草案不得用于发布验收。

数据协议见 `app/evaluation/task_eval/contracts.py`；字段严格校验并禁止未知字段，Case 的 `expected.assertions` 引用具名版本化规则。固定的 `allowed_routes` 目前仅标定六个明确路由题，其余需按产品路由映射和人工预期核实；这些空集合不代表允许任意路由或通过。ROUT-005 的主体是学员而请求“授课安排”，现有按学员查询的预约接口无法证明教练日程的预期路由，因此撤去未经核实的标签并保留待审核。`answer_rubric_ref` 当前只是规划引用，不代表 Rubric 已建立或校准；语义结果不可判时必须为 UNKNOWN。案例中故障注入名称是设计目标，不代表故障已实际注入或被观察到。

从 `fitness-agent-service` 目录，执行以下纯本地校验及单元测试：

```bash
python -m app.evaluation.task_eval.cli validate --dataset evals/task_eval/datasets/v1.jsonl
python -m pytest tests/task_eval/test_dataset.py -q
```

开发规划校验允许 draft test 并输出醒目告警。发布前应使用 `load_dataset(path, require_reviewed_test=True)`（或 CLI 中对应的发布模式，如有）拒绝未经审核的测试题。`load_dataset` 只检查声明的 family 是否跨集，不能替代语义审核；也不能证明人工审核、真实隔离、证据采集或产品正确。摘要是原始 JSONL 字节 SHA-256，数据改变须重新计算并冻结版本。

`BOOK-018` 和 `BOOK-026` 至 `BOOK-031` 的 `message_ref` 指向相应规划 fixture 的中文预约请求模板。完整任务适配器必须先创建真实可预约的隔离课程，再解析课程、教练和含时区的绝对时间，冻结完整消息与 fixture 摘要；不能将引用或模板原样发给 Agent。这些案例尚无此类完整任务夹具。预约故障类案例在开发集和测试规划集均有相似的确认、重试和异步故障，声明不同 `family_id` 不能证明语义隔离；必须由人工审核共享事故与相似变体，并在必要时整族调整分集及重新版本化。当前不能宣称分集已保证语义隔离。

`BOOK-018` 另提供版本化的合成开发 fixture `dev_fixtures/book-018-v1.json`：本地组件探针校验原案例与 fixture 摘要、身份及消息参数一致后，发送展开的消息并驱动拒绝流程。该组件探针使用脚本模型和业务替身。另有显式启用的本机临时服务试验，可选择脚本模型或一次受预算约束的真实 DeepSeek 调用；二者均使用合成身份和预约参数，不能证明时间实际可预约、完整业务审计或完整任务通过，也不替代上述全链路夹具；fixture 不用于测试集。运行方法和真实调用的前置条件见服务根目录 README。

## 通知偏好策略组件评测

```bash
python -m app.evaluation.task_eval.component_notification
```

运行实际通知偏好策略函数，以固定时钟及本地合成的只读持久层替身检查关闭通知、跨午夜安静时间、频率限制、频率边界与允许通知；JSON 报告列出预期、实际决策及读取范围，任何案例失败时进程返回非零。该组件运行不依赖网络、数据库或测试包，案例仅用于开发，不属于上面的 120 条任务结果。它没有运行通知 Worker、实际投递、真实数据库并发、过期检查或完整 Agent，也不能证明端到端隐私或投递安全。

安全建议评分要求 adapter 提供与判断 rubric 版本一致的 `professional_review`（含 `reviewer_id`、`qualification`、`review_ref`、`rubric_version`），并在 `evidence_refs` 中列出该审核引用；审核身份和记录的真实性须由证据适配器核实。只提供一个审核完成布尔值不足以判定；安全评分固定为 0 = FAIL、1 = UNKNOWN、2 = PASS。仓库不包含或伪造专业审核记录。

## 人工与 Judge 评分校准统计

在取得真实的人评和 Judge 独立评分后，从 `fitness-agent-service` 目录运行 `python -m app.evaluation.task_eval.calibration --dataset evals/task_eval/datasets/v1.jsonl --ratings ratings.json --output calibration-report.json`。输入 JSON 为 `schema_version`、`dataset_sha256`、`rubric_ref`、`judge_model`、`prompt_version`、`ratings`；每个 rating 含开发集 `case_id`、语义断言 ID `metric_id`、`human_score`、`judge_score` 和对应的 `human_evidence_sha256`、`judge_evidence_sha256`。分数为 0、1、2 或无法判断时的 null；null 的证据摘要也必须为 null，其他分数必须附 64 位小写 SHA-256。每个案例与指标只能出现一次。

输出总量、可比较分母、未知数量、混淆矩阵，并按指标规则、场景和风险分组，单列人工判 0 而 Judge 判 1、2 或未知的数量。安全建议规则中的 1 表示 UNKNOWN，不进入可比较分母；普通语义规则中的 1 则是可比较的部分得分。分母为零时一致率是 null。输入中的评分及证据摘要仍是待核验声明；此工具不会认证评审身份、独立性或证据真实性，也不会生成标注或授权发布。

## 经营查询策略与趋势组件评测

```bash
python -m app.evaluation.task_eval.component_operations
```

运行实际经营查询策略与趋势构造函数，使用合成机构、查询和 Gateway 聚合响应模拟数据；逐条报告输入、预期和实际策略决策或趋势结果。覆盖正确查询、机构越界、指标或时间漂移、信息不足或多指标、时间桶和对比口径漂移，以及空记录、缺失时间桶和零基数的趋势处理。任何案例失败或异常时返回非零，异常不暴露内部细节。

该组件不调用 Agent、Gateway 或数据库，不验证身份签名、数据库权限、SQL、真实业务聚合或完整任务。这里的案例独立于规划集的 120 条任务，不代表那些任务已执行。

## 预约服务的可选 Java 组件观察

`BOOK-019/020/021/023/024` 可关联到实际 Java `BookingService` 的五个不同测试：合同失效、课时不足、跨学员操作、教练时段冲突和学员时段冲突。运行前需要本机 Maven、JDK 和已经下载到项目 `.mvn/repository` 的依赖；评测使用 Maven 离线模式，将当前源码复制到临时目录，只运行固定的五个方法，并校验本次测试进程退出码及新生成报告的方法、数量和结果。源码测试契约变化后需复核并更新夹具，旧报告不能用于证明新运行。

从 `fitness-agent-service` 目录执行：

```bash
TASK_EVAL_JAVA_COMPONENTS=1 \
  python -m app.evaluation.task_eval.cli dataset-local --dataset evals/task_eval/datasets/v1.jsonl \
  --split all --execute --output-dir /tmp/fitness-eval-with-java
```

每次数据集评测自动创建独立运行上下文，五个案例在同一次评测中共享该次套件结果；不同评测不会复用结果。未启用时这些 Java 案例保持 `SKIPPED`。五项测试的仓储均为 Mockito 替身，不证明真实数据库、并发、Agent 对话或答案质量；即使全部通过，相关任务仍为 `INCONCLUSIVE`。`BOOK-025` 则只观察 Python 确认服务对合成冲突的处理，不能证明模型没有误报成功。

## BOOK-030 本机 RabbitMQ 重复投递观察

在 `fitness-agent-service` 目录执行以下命令；报告路径必须为不存在的绝对路径：

```bash
bash scripts/task_eval_cross_service_booking.sh --broker-duplicate-trial \
  "$PWD/var/task-eval/book-030-rabbit-report.json"
```

该模式要求本机 Docker、JDK 和离线 Maven 依赖，以及本机已有的 PostgreSQL、MySQL、RabbitMQ 镜像。启动器创建带随机运行标识、临时存储和仅监听 loopback 的容器；RabbitMQ 使用随机凭据和本次运行专用的交换机、队列，退出时删除容器。实验用脚本模型产生一次经确认的合成预约；真实 Java Outbox 发布器将事件发送至隔离 RabbitMQ，评测读取原始消息并核对 MySQL 发布状态，再以完全相同的消息字节重投。实际通知 Worker 将通知写入临时 PostgreSQL 站内收件箱，核验学员与教练各收到一次，以及投递后再次重投仍无重复；同时检查预约、课时和另一租户的见证快照。

这是组件观察，重投由评测程序发出；不运行真实模型，也不向外部通知渠道或真实用户投递。数据库快照不是完整独立事务审计。案例仍为草稿，组件成功不代表 BOOK-030 任务或 120 条案例验收通过。重复运行时应使用新的报告文件名。

全量本地探索报告可显式启用此观察：

```bash
TASK_EVAL_BOOK030_BROKER=1 .venv/bin/python -m app.evaluation.task_eval.cli dataset-local \
  --dataset evals/task_eval/datasets/v1.jsonl --split all --execute \
  --output-dir "$PWD/var/task-eval/with-book030-new-run"
```

每次运行新建隔离服务并生成一次性报告，检查启动器退出码、来源、事件和投递证据及报告摘要，不复用历史报告。组件失败或证据不足时案例标记为 `ERROR`；成功也仅为 `INCONCLUSIVE`，模型结果与完整任务仍是未知。临时环境需要本机 Docker、JDK、离线 Maven 依赖和指定的本地镜像。
