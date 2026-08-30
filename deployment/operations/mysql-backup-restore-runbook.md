# 健身平台 MySQL 备份恢复手册

本文覆盖旧健身业务事实库和新训练/预约/客服扩展表所在的 MySQL。它用于预发布和灾备演练，不是让应用服务自行备份数据库的脚本。

当前项目明确不纳入赛事、作品和活动运营数据；恢复校验只关注机构、用户、教练、课程、合同、预约、训练、客服和 Agent 事件相关表。

仓库提供本地受控验收入口：`make gateway-mysql-backup-restore-check` 默认只做前置检查；显式增加
`ARGS="--execute --rto-target-seconds 60"` 后，会在 `fitness-mysql` 中创建唯一临时库、恢复并逐表校验，结束时自动清理。
它只适用于当前本地测试容器，不替代生产备份平台。

## 1. 安全边界

- 备份账号、数据库密码和对象存储密钥只能来自 Secret Manager 或临时受控终端，不能写进 Shell 历史、Git、脚本或日志。
- 生产恢复目标必须是唯一的隔离 MySQL 实例或临时数据库，禁止对生产源库执行 `DROP DATABASE`、`docker compose down -v` 或覆盖恢复。
- 应用账号不能承担备份职责；Gateway 只读账号、Booking/Training/Customer Service 写账号都不能作为备份账号。
- 恢复前先确认备份文件的来源、时间点、SHA-256、加密状态和病毒扫描结果；校验失败不得继续恢复。

## 2. 备份要求

生产备份应使用逻辑备份或数据库平台快照，并满足：

```text
--single-transaction
--routines
--events
--triggers
--default-character-set=utf8mb4
```

InnoDB 表使用 `--single-transaction` 可以在不长时间锁表的情况下获得一致性快照；如果数据库存在非 InnoDB 表，必须单独记录锁表影响，不能把该选项误认为所有引擎都无锁。

备份对象至少保存：

- 备份开始和结束时间，以及数据库一致性时间点；
- MySQL 版本、字符集、排序规则和备份工具版本；
- 备份文件大小、SHA-256、加密算法和对象版本；
- 备份保留期限、删除保护和恢复所需权限；
- 备份对应的应用版本、迁移版本和发布清单 SHA。

## 3. 本地 `fitness-mysql` 备份示例

当前本地容器是 `fitness-mysql`，宿主机端口为 `3307`，数据库为 `fitness`。下面命令仅作为本地演练模板；密码必须通过临时环境变量或 Secret Manager 注入，不能把真实密码替换进文件。

```bash
export MYSQL_BACKUP_PASSWORD='仅在当前终端临时注入的本地密码'
export MYSQL_BACKUP_FILE="/tmp/fitness-mysql-$(date +%Y%m%d-%H%M%S).sql"

docker exec -e MYSQL_PWD="$MYSQL_BACKUP_PASSWORD" fitness-mysql \
  mysqldump --single-transaction --routines --events --triggers \
  --default-character-set=utf8mb4 --set-gtid-purged=OFF \
  -u backup_operator fitness > "$MYSQL_BACKUP_FILE"

shasum -a 256 "$MYSQL_BACKUP_FILE"
```

本地开发账号 `fitness` 只用于既有联调，不能因为它可以连接就把它当成生产备份账号。备份完成后应清理临时密码环境变量和备份文件；生产环境应直接写入加密对象存储，不要长期落到宿主机 `/tmp`。

## 4. 恢复到隔离目标

恢复必须使用唯一的隔离容器或临时数据库名称，例如 `fitness_restore_20260830_01`。恢复前先确认目标不是生产源库：

```bash
export MYSQL_RESTORE_PASSWORD='隔离恢复目标的临时密码'
export MYSQL_BACKUP_FILE='/secure/restore/fitness-mysql-backup.sql'
export MYSQL_RESTORE_DATABASE='fitness_restore_20260830_01'

# 如需更换恢复库名，请同时替换下一条 SQL 中的固定库名，并先确认名称只包含字母、数字和下划线。

docker exec -e MYSQL_PWD="$MYSQL_RESTORE_PASSWORD" fitness-mysql \
  mysql -u restore_operator -e 'CREATE DATABASE `fitness_restore_20260830_01` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;'

docker exec -i -e MYSQL_PWD="$MYSQL_RESTORE_PASSWORD" fitness-mysql \
  mysql -u restore_operator "$MYSQL_RESTORE_DATABASE" < "$MYSQL_BACKUP_FILE"
```

如果恢复目标是独立实例，应把 `fitness-mysql` 替换为恢复实例名称，并通过网络隔离和数据库标签确认其不是生产源库。不要使用通配符删除数据库，也不要通过 `docker compose down -v` 清理恢复环境。

## 5. 恢复后校验

### 5.1 结构和字符集

```sql
SELECT VERSION();
SELECT @@character_set_server, @@collation_server;

SELECT TABLE_NAME, ENGINE, TABLE_COLLATION
FROM information_schema.TABLES
WHERE TABLE_SCHEMA = 'fitness'
ORDER BY TABLE_NAME;

SELECT TABLE_NAME, COLUMN_NAME, CHARACTER_SET_NAME, COLLATION_NAME
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = 'fitness'
  AND DATA_TYPE IN ('char', 'varchar', 'text', 'mediumtext', 'longtext')
ORDER BY TABLE_NAME, ORDINAL_POSITION;
```

重点确认业务文本列没有从 `utf8mb4` 退化为 `latin1`，并核对表数量、关键索引、外键和迁移版本。中文数据乱码时，先停止验收，检查备份导出字符集、连接字符集和恢复目标排序规则，不要直接用脚本批量替换乱码。

### 5.2 业务事实和 Agent 扩展数据

至少核对以下表的记录数、主键唯一性和关键关联：

```sql
SELECT 'organization' AS table_name, COUNT(*) AS row_count FROM organization
UNION ALL SELECT 'course', COUNT(*) FROM course
UNION ALL SELECT 'contract', COUNT(*) FROM contract
UNION ALL SELECT 'appointment', COUNT(*) FROM appointment;

-- 训练/预约/客服扩展表名称以目标环境迁移结果为准，先从 information_schema 确认存在后再核对。
SELECT TABLE_NAME
FROM information_schema.TABLES
WHERE TABLE_SCHEMA = 'fitness'
  AND TABLE_NAME LIKE 'agent_%'
ORDER BY TABLE_NAME;
```

至少抽取一条脱敏中文机构名、课程名、合同备注或客服标题进行人工比对；不得把用户手机号、身份证号、Token 或完整合同正文写入验收日志。

### 5.3 应用一致性

恢复校验完成后，按以下顺序启动只读服务：

1. 执行对应数据库迁移版本核对，确认应用版本与表结构兼容；
2. 启动 Gateway，执行 `make gateway-it`，确认机构、课程和固定经营指标只读查询通过；
3. 启动 Booking/Training/Customer Service，只做健康检查和只读查询；
4. 检查 PostgreSQL Agent 的 Checkpoint、Memory、知识索引和审计库不依赖 MySQL 恢复目标；
5. 最后才在隔离环境执行明确授权的写入验收，并按精确 ID 清理临时数据。

## 6. RTO/RPO 记录模板

每次演练至少记录：

```text
backup_consistency_at=
backup_started_at=
backup_finished_at=
restore_started_at=
restore_finished_at=
verification_finished_at=
backup_bytes=
backup_sha256=
restore_rto_seconds=
data_verification_seconds=
logical_backup_rpo=
wal_or_snapshot_rpo=
result=PASS|FAIL
```

逻辑备份只能证明备份一致性时间点，不能证明任意时间点恢复能力。正式生产 RPO 需要结合备份频率、binlog 保留、数据库快照或平台级 PITR 另行确定；RTO 则按“恢复完成 + 结构/行数/中文数据校验完成”的总时间计算，不能只记录导入耗时。

## 7. 失败和回滚处理

- 备份摘要不匹配：删除隔离目标上的不完整恢复结果，重新选择可信备份；不能继续应用启动。
- 表结构或迁移版本不匹配：停止服务，保留目标现场和日志，先完成兼容性评审。
- 中文数据校验失败：停止验收，保留原始备份不变，重新检查字符集和连接参数。
- 关键表数量不一致：检查备份范围、触发器、事件和恢复错误；不能用补数据脚本掩盖备份缺失。
- RTO 超标：记录实际耗时和瓶颈，调整备份介质、实例规格或恢复流程；不能直接降低目标门槛。

本手册完成的是可执行的 MySQL 灾备流程和证据格式，不代表当前本地 Docker 已完成生产级跨区域备份、加密对象存储、binlog/PITR 或正式 RTO/RPO 验收。
