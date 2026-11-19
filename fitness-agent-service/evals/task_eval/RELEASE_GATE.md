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

公开 CLI 和 `assess_release_gate` 只接受部署者预先配置的系统信任根，不接受命令行、环境变量或报告自行提供公钥。支持 Ed25519 的独立角色签名：策略冻结、报告生产、隔离见证、案例标注、案例审核和逐断言证据；高风险及安全题审核者还须具有对应领域的管理员授权，医疗和危险训练建议必须具备临床资质。标注员与审核员须为不同身份，不同角色不能使用同一身份或同一个公钥。报告签名绑定完整数据集内容、无签名的 manifest、逐 Trial 和策略；案例签名绑定完整案例；证据签名绑定试次、断言和内容摘要。只有经过独立授权的人员和服务应持有相应私钥，本项目不会生成或代签真实人工审核。

部署者需在 Linux 的 `/etc/fitness-task-eval/release-trust.json`（macOS 为 `/private/etc/fitness-task-eval/release-trust.json`）固定根配置，在 `/var/lib/fitness-task-eval/evidence/`（macOS 为 `/private/var/lib/fitness-task-eval/evidence/`）存放以 SHA-256 命名的证据原文。配置和路径各级目录必须由 root 所有、普通用户不可写，不能有符号链接；macOS 上包括根目录在内的全部路径组件还必须没有扩展 ACL（无法读取 ACL 时也阻断）；证据文件须为 root 所有、普通用户不可写的普通文件。运行门禁的服务账号必须为非 root。每条可判定必要断言须有 `evidence_ref_sha256` 和一一对应的 `evidence_content_sha256`，实际内容须存在且摘要匹配；旧版引用字符串摘要不能冒充证据内容摘要。

信任根仅由部署者安装，格式为 `{"schema_version":"1","allowed_policy_sha256":["<策略摘要>"],"keys":{"<密钥ID>":{"public_key":"<32字节Ed25519公钥的小写十六进制>","role":"<角色>","subject":"<授权主体>","clinical_reviewer":false}}}`。角色只能是 `policy`、`report`、`isolation`、`annotator`、`reviewer` 或 `evidence`；`clinical_reviewer` 仅可用于审核员，兼容既有临床授权；审核员还可配置 `review_domains` 数组，成员只能是 `clinical`、`security`、`business` 或 `knowledge`，且由管理员核验后配置。策略摘要必须与事先授权的策略相符。可用 `signed_evidence.policy_attestation_request(dataset, policy)` 在执行前准备策略见证声明；该声明同时绑定文件摘要和解析后的全部案例及数据集版本的规范化摘要，避免同一声明文件摘要被用于不同的内存案例；执行后用 `signed_evidence.attestation_requests(dataset, manifest, trials, policy)` 准备其他声明。各授权主体必须独立审核其真实材料，再以 `signed_evidence.signing_message(role, key_id, claims)` 生成待签字节并通过各自的独立签名工具签署；将 `{role,key_id,claims,signature}`（签名为 64 字节小写十六进制）放入 manifest 的 `attestations` 数组。请求生成不是审核、签名或证明，不能自动代签；不应将私钥交给评测程序。

审核领域按冻结案例的子类、风险标签和风险规则确定：危险运动及医疗建议为 `clinical`，越权、跨租户和提示注入为 `security`，未经确认写入和重复扣次为 `business`。未声明具体风险的高风险知识题、预约/运营题和路由题分别要求 `knowledge`、`business` 和 `security`。一个案例涉及多个领域时，同一审核人须同时具备全部对应授权；未知风险标签或未知安全子类阻断验收，不由调用者自行指定。医疗和危险训练断言还须由具备临床授权的审核员另外签署该 Trial 的具体断言及真实证据内容，单独审核案例本身不能替代对实际模型输出的专业审核。领域映射依赖案例风险标注真实完整，不能靠此自动发现遗漏的医疗风险。

签名验证的是部署者授权的主体确实对绑定材料作出了声明。它不能自行证明签署者资质、隔离实际有效或声明中的策略冻结时间；这些事实必须由部署者的身份认证、隔离审计和可信时间/留痕流程独立保障。root 或持有授权私钥者可改变信任，因此部署与密钥管理属于信任边界。未安装配置、签名、真实内容或相应角色的实际材料时，门禁保持 `BLOCKED`；当前项目尚未配置这些真实材料，不能将测试密钥或合成结果视为验收。

退出码：`0` 为部署信任根授权的签名和真实材料支持下的验收通过，`1` 为输入/配置错误，`2` 为有可信证据的必要断言失败，`3` 为 `BLOCKED`（缺证据或未完成）。报告输出各项原因和覆盖分子/分母；当前未被独立验证的断言不能进入可判定覆盖分子。不要把 `validate`、`plan`、组件结果或合成实验的成功当作发布验收通过。
