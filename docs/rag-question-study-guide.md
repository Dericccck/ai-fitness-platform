# RAG 问题采集、独立标注与召回评测：完整操作手册

## 1. 现在交付了什么

本工具把“问题记录 → 来源/脱敏复核 → 授权候选池 → 双人独立标注 → 分歧裁决 → 冻结开发/测试集 → 实际检索对比 → 报告”接成可执行流程。

入口：`fitness-agent-service/scripts/rag_study.py`；离线工作台：`fitness-agent-service/evals/rag-study-workbench.html`。

已经提供80条合成问题及一套可复现演示。它们是程序生成的问题，不是实际学员访谈、生产日志或人工专家金标。生成器不根据检索结果修改预设标签。模拟的同意、审核人和参与者字段只用于验证契约，不表示真人参与。

当前没有真实试用问题，因此真实用户召回率仍待采集；工具、问题示例与真实试用空目录已经可以使用。没有改动主应用鉴权、聊天页面、线上RAG算法，也没有部署到云端。

## 2. 先看合成演示

以下命令均在 `fitness-agent-service` 中执行，使用项目现有 `.venv`：

```bash
.venv/bin/python -m scripts.rag_study --help
.venv/bin/python -m scripts.rag_study demo var/rag-studies/synthetic-example
.venv/bin/python -m scripts.rag_study demo var/rag-studies/synthetic-example --execute
```

不带 `--execute` 只提示执行边界，不访问数据库/模型。演示需要本地 embedding、reranker 和可创建临时数据库的本地 PostgreSQL；它沿用 `project_evidence_benchmark.isolated_database`，创建随机命名的 `fitness_evidence_*` 临时库、运行迁移、写入34份合成文档，退出时仅删除自己创建的精确库名。不要把有建库权限的演示账号用于生产试用评测。

演示复用真实本地模型和 PostgreSQL 检索，没有调用聊天模型、支付/预约工具，也不会创建真实通知。80条问题包含：

|类型|数量|用途|
|---|---:|---|
|单主题原问法/口语/错别字|60|30个知识主题，每个2条表达|
|多轮指代|6|保留前文及人工预设的指代展开|
|跨主题组合|4|一次问题需要多个证据分块|
|实时业务查询或操作|4|不能拿静态知识替代实时课时、预约、营收事实|
|授权目录无答案|4|包含2条越权索取资料的问题|
|缺上下文|1|应先澄清|
|业务范围外|1|不混入知识召回分母|

模拟标注员A/B故意有1条分歧，由模拟C裁决，用于验收分歧门禁。**不是三个人实际审核了问题。** 所有演示权限都使用固定 `STUDENT` 授权；不能据此宣称覆盖了真实教练/店长权限矩阵。

最终验收研究目录是 `var/rag-studies/synthetic-20261007-final`：58条开发集、22条测试集。原始问题在 `questions.json`，全部流程中间结果保存在同目录。最终结果见 `report-dev.md`、`report-test.md`。首次试运行目录 `synthetic-20261007` 保留供追溯；对外引用请用最终验收目录。

## 3. 真正采集用户问题

### 3.1 建立研究协议

```bash
.venv/bin/python -m scripts.rag_study init var/rag-studies/pilot-001 --source pilot
```

本次另已建立空目录 `var/rag-studies/real-pilot-20261007`，可直接使用，不必重复初始化。`init` 拒绝往非空目录写入。

在采集前编辑 `protocol.json`：说明业务场景、参与群体、问题收集窗口、研究目的，确定Top-K、候选数量、切分随机种子和测试集比例。默认Top-K=5、两路候选各20、池深度50、RRF权重0.6/0.4。不要看到分数后反复改随机种子或删难题。

使用来源：

- `synthetic`：生成或人工编造的模拟问题。
- `pilot`：参与者实际试用提出的问题。
- `production_log`：经合法授权导出的真实运行日志，不是把pilot改名就变成生产。

每个研究只能有一种来源。要混合分析，分别建研究，再明确分来源汇总。工具可以校验声明一致性，不能证明参与者身份、采集真实性或运营环境。

### 3.2 采集页面

本地浏览器直接打开 `evals/rag-study-workbench.html`。页面为单文件，无外部资源与网络请求；提供研究协议导入、问题记录、独立标注、草稿恢复和JSON导出。

给参与者协议文件和匿名编号，不给数据库账号或权限映射文件。让参与者记录实际问题与必要上下文，填写匿名会话/访谈凭据编号，确认同意用途后导出。不要诱导他们照着知识库标题提问。

不要采集姓名、手机号、身份证、详细病史、支付凭证和访问令牌。确需涉及健康场景时，优先使用去标识的最小必要表述，并遵循实际授权要求。撤回同意后，应按研究治理流程撤销包含该记录的可用数据集，不继续使用旧冻结集。

页面不自动上传、不持久化到localStorage，刷新前必须导出。下载文件通常进入系统下载目录，请移到私有研究目录后再交给负责人。不要直接上传到GitHub。

### 3.3 合并记录并生成待复核模板

```bash
.venv/bin/python -m scripts.rag_study import var/rag-studies/pilot-001 \
  --input var/rag-studies/pilot-001/participant-a.json var/rag-studies/pilot-001/participant-b.json
```

生成三个新文件：

- `questions-imported.json`：全部原始记录，保留原问题、时间、来源凭据、匿名编号和上下文。
- `triage-draft.json`：每条问题的检索输入、主题、同义问题族、脱敏和来源复核状态。
- `scopes-draft.json`：每个匿名参与者的服务端授权映射，初始为空，不能直接使用。

重复case_id、未同意、来源不同、缺少带时区时间戳会被拒绝。导入不覆盖原始文件或已有工件。新增一轮采集请使用新研究目录或在冻结前重新整理完整输入，不覆盖已发布的研究证据。

### 3.4 负责人必须完成的复核

`triage-draft.json` 示例：

```json
{
  "case_id": "pilot-q001",
  "retrieval_query": "课程开始不足24小时如何取消预约？",
  "topic": "课程取消规则",
  "family_id": "cancel-within-24h",
  "redaction_reviewed": true,
  "source_reviewed": true,
  "reviewed_by": "research-owner-01",
  "context_resolution_note": "前文明确讨论课程取消时间，展开了这句话中的指代"
}
```

不要为了更容易命中而添加原问题/前文没有的信息。展开指代必须说明依据；缺少必要前文则原样保留，后续标为 `needs_context`。这套评测测的是**经复核检索输入的检索质量**，不是自动指代改写或Supervisor路由准确率。

`scopes-draft.json` 示例（只是字段说明，不能复制成实际授权）：

```json
{
  "participant_id": "pilot-017",
  "subject": "服务端核验过的用户ID",
  "organization_ids": ["核验过的机构ID"],
  "roles": ["STUDENT"],
  "verified_by": "research-owner-01",
  "verification_reference": "受控授权记录编号"
}
```

权限来源必须是现有认证/成员关系等可信管理记录，不能相信问题里写的“我是管理员”，也不能由参与者自己选择机构和角色。CLI是受信任运营者工具，不是公开API；这一版没有新增自动登录日志采集或管理端权限审批功能。

```bash
.venv/bin/python -m scripts.rag_study prepare var/rag-studies/pilot-001 --imported
```

未复核的false字段、空授权或遗漏问题会阻止生成 `prepared.json`。

## 4. 建池、双人标注与第三人裁决

确认配置指向经过授权的知识库只读账号，限制到所需知识表。准备好embedding/reranker：如果配置成远端服务，查询与授权候选片段会发送给对应供应商并可能计费，执行前应核验数据处理授权。本地模型无这部分第三方传输。

```bash
.venv/bin/python -m scripts.rag_study pool var/rag-studies/pilot-001 --execute
.venv/bin/python -m scripts.rag_study packet var/rag-studies/pilot-001 \
  --reviewer reviewer-A --output var/rag-studies/pilot-001/packet-a.json
.venv/bin/python -m scripts.rag_study packet var/rag-studies/pilot-001 \
  --reviewer reviewer-B --output var/rag-studies/pilot-001/packet-b.json
```

候选池采用较深双路召回，同时给每题完整的已授权分块目录。标注包不展示检索方案名、排名、分数或另一人的标签。标注员必须检查候选外遗漏，避免“只把检索已经找出的东西当正确答案”。目录超过10000分块会拒绝运行，应合理缩小研究范围，不静默截断。

**标注包含授权知识正文。** 研究负责人必须有跨该研究所涉机构的评审授权；只能发给具有同等评审权限的人员。该工具的SQL按参与者过滤文档，但不负责外部文件分发的鉴权。不得把混合机构的包交给只允许看单机构的标注员；需要按机构拆研究或先获得相应授权。

两位评审者分别在工作台导入自己的packet，逐题选择：

|类别|判定|相关ID|
|---|---|---|
|answerable|授权知识库中存在回答该知识问题的证据|填写所有相关分块ID|
|no_answer|授权知识库没有足够证据，含无权访问的资料|空|
|business_tool|需要实时业务事实或执行操作|空|
|needs_context|必要指代/条件缺失，需先澄清|空|
|out_of_scope|超出平台业务范围|空|

标注相关性时同时考虑问题条件、文档有效期与业务适用性，不能仅凭相似词判断。静态退费规则是知识问题，实际退款金额/资格则通常需要合同与消费事实，必要时拆分问题或记录严格分类依据。

保存每题理由和阅读/搜索过程，可导出草稿再恢复。最终导出的A/B文件分别存为 `review-a.json`、`review-b.json`。评审ID必须不同；程序无法阻止同一个人伪填两个人名，负责人仍需保留人员与复核记录。

```bash
.venv/bin/python -m scripts.rag_study reconcile var/rag-studies/pilot-001
```

类别或相关ID集合不同就进入分歧。若存在分歧：

```bash
.venv/bin/python -m scripts.rag_study packet var/rag-studies/pilot-001 \
  --reviewer reviewer-C --conflicts-only --output var/rag-studies/pilot-001/packet-c.json
```

第三位评审者复核分歧包，在工作台看到双方标签与理由，导出后保存为 `adjudication.json`。裁决者不能是A/B之一。没有分歧则不需要第三人文件。

## 5. 冻结与运行

```bash
.venv/bin/python -m scripts.rag_study freeze var/rag-studies/pilot-001
.venv/bin/python -m scripts.rag_study evaluate var/rag-studies/pilot-001 --split dev --execute
.venv/bin/python -m scripts.rag_study report var/rag-studies/pilot-001 --split dev
.venv/bin/python -m scripts.rag_study evaluate var/rag-studies/pilot-001 --split test --execute
.venv/bin/python -m scripts.rag_study report var/rag-studies/pilot-001 --split test
```

冻结规则：同一参与者、归一化后相同问题、预声明同义问题族的连通组不能跨开发/测试集；同一个人所有问题都在同一侧。至少2个独立组，并且测试集至少有1个可回答问题才能冻结。问题族和参与者可能连成大组，所以默认30%的**组数**不一定对应30%的问题数。

正式调优应先只查看dev结果；方案与阈值确定后才执行一次test。演示为了验证工具同时跑了两侧，不应把反复看过的测试集继续称为未见测试集。下一轮实验应另建目录，冻结新协议/新独立测试问题，保留旧报告。

每个问题使用同一短事务完成授权目录、向量召回、关键词召回，事务为 `REPEATABLE READ READ ONLY`，语句超时15秒。embedding在事务前完成，rerank在释放事务后完成，不长时间占用数据库连接。整个研究不是跨数小时的单一数据库事务；每个问题的完整授权目录摘要必须与建池时一致，否则中止并要求重建池/复核。

四个方案复用线上仓储、RRF与重排结果校验：vector、keyword、hybrid、hybrid_rerank。固定同一个Top-K和问题授权，不把其他用户/机构资料拿来提高分数。比较是**架构消融**，不是某年上线前后历史对照。

## 6. 指标如何解读

- Recall@K：每条可回答问题命中的相关分块数 / 标注相关分块数，再对问题取平均。
- Precision@K：命中的相关分块数 / K；不满K时仍除以K。
- 命中率：至少命中1个相关分块的问题比例。
- MRR：首个相关证据排名倒数的平均值。
- 无答案返回上下文率：`no_answer`问题仍返回非空检索片段的比例；**不是错误回答率或拒答准确率**，因为没有调用回答模型。
- 失败：保留在全部问题和可回答问题分母中；检索失败按空结果评分，不能删掉失败样本。出现失败会禁用“稳定正向差异”结论。
- 差值：优化方案Recall减基线Recall，乘100得到**百分点**；相对提升则还要除以基线。基线0时相对变化留空。
- 置信区间：按冻结问题组做2000次配对聚类自助抽样；少于2组不输出区间，少于20组不建议稳定推广结论。20组只是保守展示门槛，不能替代样本量/统计功效设计。

没有相关证据的问题不混入Recall分母，但全部保留类别数量。复合问题有多个相关分块，命中一部分不等于Recall=100%。标注未穷尽时Recall仍可能偏高，需要抽检候选外文档、补充领域专家复核。

结果文件：

- `run-*.json`：私有逐题结果、失败类型、检索ID及原始问题；便于复查。
- `summary-*.json`：不含原问题、用户ID或知识正文的汇总。
- `report-*.md`：汇总Markdown，含来源、分母、区间和解释边界。

汇总仍含研究名称、模型名和代码摘要；对外发布前应检查研究名称是否泄露机构信息。代码摘要记录实际工作区实现，不只记录git提交。模型名称不能证明远端权重不变，正式实验需固定模型版本；不同知识快照不允许直接沿用旧金标。

## 7. 数据保存和证据边界

默认工作资料位于Git已忽略的 `fitness-agent-service/var/rag-studies/`。新建目录0700、JSON文件0600；已有父目录权限不会自动改变。工件带SHA-256摘要并拒绝覆盖。摘要用于检测意外变动，**不是数字签名，也不证明真人身份、人工评审或生产部署**。

若语料改版、权限变化、生效时间跨越导致目录变化，先重新建研究/池并复核受影响标注，不能删掉变化检测来继续计算。SQL账号应只读最小权限；执行 `demo` 才需要临时建库权限。PostgreSQL最佳实践在这里具体用于连接复用、只读短事务、超时和避免事务内调用模型。

只读评测不会回写会员数据，但可能给共享数据库带来负载，生产使用前应在测试副本验证容量并限定采样批次。单研究上限5000条；整份工件限制100MiB。较大目录与问题数相乘会增加文件体积，需按机构/时间窗口合理分研究，不能为了100MiB限制删除失败问题。

## 8. 本次结果与下一步

本次合成测试集22条，其中18条可回答、9个独立模拟问题组：vector与hybrid_rerank的Recall@5均为100%，差值0个百分点；这只是小型合成语料/预设标签范围内的结果，不能写“真实用户问答准确率100%”。

开发集52条可回答问题中，vector为100%，hybrid_rerank为98.08%，下降1.92个百分点。这说明重排并非对每个问题都有收益。不能看到下降就删题或把测试集换成更好看的切分。

具体反例是“我不想夜里收到训练提醒怎么办？”：向量检索把 `notification` 排在第一，重排后的Top-5却不再包含它。可在开发集上分析“夜里/睡眠”是否带来了偏题排序，再设计针对性的业务难例；当前工具没有擅自修改生产检索算法。

这次keyword通道Recall为0，表明当前词法通道对这组自然中文问句覆盖不足。应在dev上检查分词、规则编号提取、短关键词查询与真实业务词表；不能直接归因于模型或据此声称修复已完成。含规则编号的整句和单独编号查询不是同一个词法检索条件。

无答案问题仍返回上下文，说明检索层目前没有证明“无答案识别”能力；是否错误作答必须另建生成/拒答评测。需要安全、业务、健康领域人员补充标签与边界检查。

下一步是邀请经同意的学员、教练和门店人员真实试用，保留采集凭据，按这套流程双人独立标注。没有真实记录时，可以在简历中写“构建可追溯的RAG评测体系、完成80条合成问题流程验收”，不能改成已发生的生产召回率、用户准确率、TTFT收益或续费提升。

## 9. 验证入口

本次最终验收：相关Python测试44项通过，真实隔离PostgreSQL契约测试1项通过，离线页面逻辑测试6项通过；Ruff和新模块Mypy检查通过。两次80条合成流程均完成，最终测试集22条、开发集58条，均无执行失败。临时数据库已由测试上下文清理，未修改业务库数据。

```bash
.venv/bin/pytest -q tests/test_rag_study.py
.venv/bin/ruff check app/rag/study scripts/rag_study.py tests/test_rag_study.py
.venv/bin/mypy app/rag/study --follow-imports=silent
node --test tests/rag_study_workbench.test.mjs
AGENT_RUN_RAG_STUDY_POSTGRES_TESTS=1 .venv/bin/pytest -q tests/test_rag_study_postgres.py
```

最后一条数据库测试需要本地建临时库权限，仅在独立临时库运行。它使用固定向量验证权限和快照契约，不计入模型召回率成绩。

离线页面通过内存DOM替身测试采集、同意、标注、越权ID拒绝、草稿恢复与导出数据结构。浏览器自动打开受到当前工具file协议限制，未完成视觉/真实浏览器下载验收；请在普通本地浏览器打开，并确认下载保存位置。这个限制不影响已经实际执行的Python/数据库/本地模型评测链路。
