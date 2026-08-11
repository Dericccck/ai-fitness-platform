# AI 健身多 Agent 平台

本仓库正在基于原有健身管理后端建设企业级多 Agent 平台。后续开发范围、架构边界、
实施顺序和协作规则统一以
[AI 健身多 Agent 平台开发路线与项目规则](docs/ai-fitness-agent-development-roadmap.md) 为准。

赛事与活动运营属于遗留代码，默认不纳入当前健身项目开发范围。

## Java 后端开发启动参数
--spring.profiles.active=dev --spring.cloud.config.server.bootstrap=false --spring.cloud.config.server.git.cloneOnStart=false
# 坑
* 阿里云数据库使用create-drop时，会因为CONSTRAINT而drop失败，所以如果在阿里云测试环境配置create-drop时，需要更改配置  jpa: properties: hibernate: dialect: MySQL55DialectForAli
* 本来是自动执行spring-session-jdbc-2.0.7.RELEASE.jar!/org/springframework/session/jdbc/schema-mysql.sql,阿里云数据库创建spring-session-jdbc的初始化表时，会报key超长，所以需要手动创建表···
CREATE TABLE `SPRING_SESSION_ATTRIBUTES` (   `SESSION_PRIMARY_ID` char(36) NOT NULL,   `ATTRIBUTE_NAME` varchar(191) NOT NULL,   `ATTRIBUTE_BYTES` blob NOT NULL,   PRIMARY KEY (`SESSION_PRIMARY_ID`,`ATTRIBUTE_NAME`),   CONSTRAINT `SPRING_SESSION_ATTRIBUTES_FK` FOREIGN KEY (`SESSION_PRIMARY_ID`) REFERENCES `SPRING_SESSION` (`PRIMARY_ID`) ON DELETE CASCADE ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC;
···
# 权限说明
## 1. 拥有权限ADMIN的用户位系统管理员
## 2. 拥有权限ADMIN-ORGANIZATION的用户为机构管理用户，如果用户为某个机构的管理用户，则在header里显示该机构的图标（后续，如果为多个机构的管理用户，则点击图标可以切换机构;）




本仓库于 2018-11-22 17:39:58 使用了源码自动生成模板 spring-boot-demo 。详情见template_info.md文件。

curl http://localhost:8599/api/sec/checkAuth -i

curl -i -X POST -d phone=15330013729 -d code=1234 -dremember-me=on http://localhost:8599/login

curl -i -H "cookie:remember-me=eFdrb24lMkZiMEppRTZhNyUyQkFFWXVIN3clM0QlM0Q6SzR2eTdnTjZiQTRnV1JMN2JBWlBZZyUzRCUzRA;JSESSIONID=671908794ECB04D7E136676427537034;" http://localhost:8599/api/sec/checkAuth


curl -i -H "cookie:remember-me=eFdrb24lMkZiMEppRTZhNyUyQkFFWXVIN3clM0QlM0Q6SzR2eTdnTjZiQTRnV1JMN2JBWlBZZyUzRCUzRA;JSESSIONID=671908794ECB04D7E136676427537034;" -X POST http://localhost:8599/logout

curl -i -H "cookie:remember-me=enNxeVV6Wkh3MzV0NSUyQnh1am1WenNnJTNEJTNEOlhwUHJNJTJCenlaU2pObXlYYm9UYWJ4USUzRCUzRA;JSESSIONID=D20F38DE8205A729FAD60DBEB7D8856F;" http://localhost:8599/logout


每日票调整
2020-05-15
        2.1.1.调整第六届赛事每日票数为0，5.15完成
        2.1.2.调整以往赛事每日票为0，5.15完成
        2.1.4.调整逻辑：已结束的活动不再获得任意种类的票，5.15完成

        1. update login_user set daily_votes=0; LoginUser.dailyVotes=0 //这个字段应该已经不在使用了，这里的票和赛季无关，已废弃，不过还是更新一下吧,
        2. update contest_season set daily_reward_available_vote=0; ContestSeason.dailyRewardAvailableVote=0 //则个是赛季用票
        3. 去掉领导特供票
            UPDATE `frogdb`.`user_daily_available_vote` SET `end_time` = '2020-05-15 00:00:00' WHERE (`id` = '100001');
            UPDATE `frogdb`.`user_daily_available_vote` SET `end_time` = '2020-05-15 00:00:00' WHERE (`id` = '100002');
            UPDATE `frogdb`.`user_daily_available_vote` SET `end_time` = '2020-05-15 00:00:00' WHERE (`id` = '100003');

