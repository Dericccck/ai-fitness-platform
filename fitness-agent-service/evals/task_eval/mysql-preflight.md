# MySQL 预检（未来业务沙箱）

此模块是未来独立业务沙箱的预检工具，**尚未接入现有评测入口或 Maven 验收脚本**，执行通过也不会授权运行 Agent 任务。现有 Maven 的隔离 MySQL 验收采用自己的容器和运行标识，并使用 root 用户；它不符合这里的容器命名、标签和空 `fitness_eval` 数据库约定，不能将该预检查在现有 Maven 容器上。也不要指向现有业务数据库。

未来的调用方必须自行创建和清理一次性 `mysql:8.0` 容器，其名称为 `fitness-eval-mysql-<run_id>`、标签 `fitness.eval=<run_id>`、数据目录 `/var/lib/mysql` 使用 tmpfs、启用自动删除，3306 仅映射到本机 `127.0.0.1` 随机端口，33060 不发布，且 `fitness_eval` 库已创建并为空。`run_id` 必须是 16 位小写十六进制。容器不得挂载持久卷或主机目录。需要等到数据库实际可以查询；仅 `mysqladmin ping` 成功可能仍处于初始化阶段。

在容器准备好后，由调用方传入该次运行的标识、完整容器 ID 和实际映射端口：

```bash
TASK_EVAL_RUN_ID='<16 位小写十六进制>' \
TASK_EVAL_MYSQL_CONTAINER_ID='<64 位容器 ID>' \
TASK_EVAL_MYSQL_PORT='<127.0.0.1 映射端口>' \
.venv/bin/python -m app.evaluation.task_eval.mysql_sandbox_preflight
```

此检查使用容器内部 socket 查询，只读取身份和 schema 元数据。它不验证 Java Gateway、模型、业务审计或网络出口，也不建立完整执行隔离；输出始终为 `execution_authorized: false`。调用方负责容器生命周期，即使预检失败也必须清理其创建的容器。
