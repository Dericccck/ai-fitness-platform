# Fitness Booking Service

预约业务写服务，属于健身业务范围；赛事、作品和活动运营不属于本服务。

该服务不依赖旧 Java 项目的 JPA Entity 图，使用显式 SQL 访问现有健身业务表，并只负责创建预约这一条受控写路径。`fitness-core-gateway` 使用只读数据库账号，先验签和校验确认凭证，再调用本服务。

## 创建预约事务

1. 校验内部服务 Token、主体、角色、机构范围和确认声明。
2. 以 `request_id` 获取幂等锁，并检查是否已经产生预约。
3. 以机构、教练和业务日期获取并发锁。
4. 锁定合同，检查合同状态、有效期、课程、剩余课时和组织关系。
5. 检查教练已有预约、请假和机构非营业日。
6. 在同一事务中扣减合同课时、创建预约、消费确认 JTI、写审计和 Outbox。

预检结果不能替代第 4、5 步的最终校验。写服务成功返回后，预约状态为旧系统编码 `1（预约成功）`，其他状态由既有核销流程维护。

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
专用测试库或测试 Schema，不要指向生产库；它会插入带随机后缀的测试数据，并在结束时清理。

```bash
export BOOKING_IT_ENABLED=true
export BOOKING_IT_DB_URL='jdbc:mysql://127.0.0.1:3307/fitness_test?useUnicode=true&characterEncoding=utf-8&serverTimezone=Asia%2FShanghai'
export BOOKING_IT_DB_USERNAME='fitness_booking_test'
export BOOKING_IT_DB_PASSWORD='请替换为测试库密码'
make booking-it
```

该测试验证真实旧业务表字段、事务提交、合同课时扣减、同一请求幂等、MySQL 命名锁以及确认
JTI 重复时的事务回滚。未配置这些变量时，Maven 会将该测试标记为 skipped，而不是误连本地
开发库。
