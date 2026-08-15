# Fitness Booking Service

预约业务写服务，属于健身业务范围；赛事、作品和活动运营不属于本服务。

该服务不依赖旧 Java 项目的 JPA Entity 图，使用显式 SQL 访问现有健身业务表，当前负责创建预约、改约和取消三条受控写路径。`fitness-core-gateway` 使用只读数据库账号，先验签和校验确认凭证，再调用本服务。

## 创建预约事务

1. 校验内部服务 Token、主体、角色、机构范围和确认声明。
2. 以 `request_id` 获取幂等锁，并检查是否已经产生预约。
3. 以机构、教练和业务日期获取并发锁。
4. 锁定合同，检查合同状态、有效期、课程、剩余课时和组织关系。
5. 检查教练已有预约、请假和机构非营业日。
6. 在同一事务中扣减合同课时、创建预约、消费确认 JTI、写审计和 Outbox。

预检结果不能替代第 4、5 步的最终校验。写服务成功返回后，预约状态为旧系统编码 `1（预约成功）`，其他状态由既有核销流程维护。

## 改约预约事务

改约 v1 只调整原预约的教练和开始/结束时间，不更换合同、课程，也不重复扣减课时。服务会：

1. 校验改约确认凭证、预约所属机构、学员/教练权限和请求幂等键。
2. 锁定原预约、原合同和新教练业务日期，检查预约状态、合同有效期、课程状态、营业日、请假和新时间冲突。
3. 使用 `expectedStartTime` 做乐观并发条件；确认卡生成后原预约被其他请求修改时，本次改约直接失败。
4. 在同一事务中更新预约、消费确认 JTI、记录审计和写入 `APPOINTMENT_RESCHEDULED` Outbox 事件。

## 取消预约事务

取消预约沿用旧系统的事实模型：不新增预约状态编码，而是将 `appointment.deleted` 标记为 `1`，并将原合同剩余课时原子加回 1。
Agent v1 只允许取消尚未开始且处于预约中、预约成功或改课中的预约；已开始、已核销或已经取消的预约不能通过该接口取消。

服务会锁定请求、预约、教练业务日期和合同，校验确认凭证、学员权限及 `expectedStartTime`，然后在同一事务中更新预约、恢复课时、
消费确认 JTI、写审计和写入 `APPOINTMENT_CANCELLED` Outbox 事件。结果显式返回 `cancelled=true` 和恢复后的剩余课时。

## 本地启动

```bash
cd /Users/a1-6/Desktop/fitness-backend
./mvnw --batch-mode -f fitness-booking-service/pom.xml test
./mvnw --batch-mode -f fitness-booking-service/pom.xml spring-boot:run
```

必须配置 `BOOKING_DB_URL`、`BOOKING_DB_USERNAME`、`BOOKING_DB_PASSWORD` 和
`BOOKING_INTERNAL_SERVICE_TOKEN`。生产环境写账号不能复用 Gateway 的只读账号。

## 真实 MySQL 集成测试

仓库提供一条默认安全跳过的集成测试，只有显式打开开关才会连接数据库。测试需要连接
专用测试库或本地开发库，不要指向生产库；它会插入带随机后缀的测试数据，并在结束时清理。
当前开发环境可以复用 Docker 中的 `fitness-mysql` 容器：宿主机端口为 `3307`，数据库为
`fitness`，账号为 `fitness`，密码为 `fitness_dev_2026`。该容器已有 Java 健身后端的业务数据，
测试不会清空或重建业务表，只会操作自身随机前缀的数据。生产环境仍应使用独立测试库账号。

```bash
export BOOKING_IT_ENABLED=true
export BOOKING_IT_DB_URL='jdbc:mysql://127.0.0.1:3307/fitness?useUnicode=true&characterEncoding=utf&connectionCollation=utf8mb4_0900_ai_ci&serverTimezone=Asia%2FShanghai'
export BOOKING_IT_DB_USERNAME='fitness'
export BOOKING_IT_DB_PASSWORD='fitness_dev_2026'
make booking-it
```

测试连接明确指定 UTF-8；不要省略 `characterEncoding`，否则某些 MySQL 客户端默认会使用
`latin1`，中文测试数据和表注释可能出现乱码。Booking Agent 启动时会按 UTF-8 读取 SQL，
并通过 `information_schema` 幂等补齐 Outbox 的重试和租约字段。

该测试验证真实旧业务表字段、事务提交、合同课时扣减、改约、取消、课时恢复、后续预约 `amount`
快照修正、同一请求幂等、MySQL 命名锁以及确认 JTI 重复时的事务回滚。未配置这些变量时，Maven
会将该测试标记为 skipped，而不是误连本地开发库。

## Outbox 与 RabbitMQ

预约事务会先把 `APPOINTMENT_CREATED`、`APPOINTMENT_RESCHEDULED` 或 `APPOINTMENT_CANCELLED` 写入 `agent_booking_outbox`。开启发布器后，服务会定时
领取待发布事件，发送到 RabbitMQ，并等待 publisher confirm；只有收到 broker 的 ack 才把事件
标记为 `PUBLISHED`。连接失败、nack 或超时会保留重试信息，超过最大次数进入 `DEAD`，不会静默丢失。

本地启动 RabbitMQ：

```bash
make infra-up-messaging
export BOOKING_OUTBOX_PUBLISHER_ENABLED=true
./mvnw --batch-mode -f fitness-booking-service/pom.xml spring-boot:run
```

RabbitMQ 管理页面为 `http://127.0.0.1:15672`，账号 `fitness_agent`，密码
`fitness_agent_secret`。当前只发布预约领域事件，不接入 Push 和短信，也没有假装下游通知已经完成。
