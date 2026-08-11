package com.shuyiwa.fitness.backend.domain;

import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaSpecificationExecutor;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.PagingAndSortingRepository;
import org.springframework.data.repository.query.Param;

import java.util.Date;
import java.util.List;
import java.util.Map;
import java.util.Optional;

public interface LoginUserRepository extends PagingAndSortingRepository<LoginUser, String>, JpaSpecificationExecutor<LoginUser> {


    String searchByNameOrContestantName = "" +
            "select u.* from login_user u join (select u.id from login_user u\n" +
            "left outer join contestant_info ci on ci.agent_login_user_id = u.id\n" +
            " where u.name like %:search% or ci.name like %:search%" +
            " group by u.id" +
            ")s on u.id = s.id \n" +
            "";

    @Query(value = searchByNameOrContestantName,
            countQuery = "select count(1) from (" + searchByNameOrContestantName + ")t",
            nativeQuery = true)
    Page<LoginUser> searchByNameOrContestantName(@Param("search") String search, Pageable pageable);

    Optional<LoginUser> findByPhone(String phone);

    Optional<LoginUser> findByWeiXinOpenId(String weiXinOpenId);

    Optional<LoginUser> findById(String id);

    Optional<LoginUser> findByPhoneAndPassword(String phone,String password);

    /**
     * 每日奖励规则：
     * 1. 默认每个人每日奖励票数为2（每个人可能不同）
     * 2. 到达奖励时间后，如果可用票数不足每日奖励票数，则补足。
     * 3. 如果可用票数多余每日奖励票数，则减为可用票数（产品要求，票的有效期都为当日，极限情况，每日6点更新可用票数，如果5点59分买了票未使用，也是一分钟后失效）
     * 4. 五一期间4天每日票数按照10来计算
     */
    @Query(value = "update login_user " +
            "   set available_votes =  daily_votes + daily_scoped_votes ," +
            "   last_votes_assign_time= now() " +
            "   where last_votes_assign_time< current_date() and HOUR(NOW()) >= 6", nativeQuery = true)
    @Modifying
    void assignVotes();


    @Query(value = "update login_user set daily_scoped_votes = 0 where daily_scoped_votes != 0", nativeQuery = true)
    @Modifying
    void clearScopedVotes();

    @Query(value = "UPDATE login_user u\n" +
            "        JOIN\n" +
            "    (SELECT \n" +
            "        login_user_id, SUM(value) v\n" +
            "    FROM\n" +
            "        user_daily_available_vote\n" +
            "    WHERE\n" +
            "        start_time < NOW() AND end_time > NOW()\n" +
            "    GROUP BY login_user_id) t ON u.id = t.login_user_id \n" +
            "SET \n" +
            "    u.daily_scoped_votes = t.v;", nativeQuery = true)
    @Modifying
    void updateScopedVotes();


    @Query(value = "update login_user set available_votes = available_votes + :count where id = :id", nativeQuery = true)
    @Modifying
    int increaseVotes(@Param("id") String id, @Param("count") int count);

    List<LoginUser> findByName(String name);

    List<LoginUser> findByName(String name, Pageable pageable);

    List<LoginUser> findByAvatar(Object avatar, Pageable pageable);

    @Query(value = "select * from login_user where  name like CONCAT('%',?1,'%')  or phone like CONCAT('%',?1,'%')  ", nativeQuery = true)
    Page<LoginUser> findByNameAndPhone(@Param("searchStr") String searchStr, Pageable pageable);

    Page<LoginUser> findByIsManager(@Param("isManager") Boolean isManager, Pageable pageable);

    Integer countByCreateTimeLessThan(Date next);

    @Modifying
    @Query(value = "update login_user set last_login_time = now(), last_login_ip = :ip ,last_login_ua = :ua  where id = :id ", nativeQuery = true)
    void updateLastLoginTime(@Param("id") String id, @Param("ip") String ip, @Param("ua") String ua);

    @Modifying
    @Query(value = "update login_user set last_login_time = now(), last_login_version= :version , last_login_ip = :ip ,last_login_ua = :ua  where id = :id ", nativeQuery = true)
    void updateLastLoginInfo(@Param("id") String id, @Param("version") String version, @Param("ip") String ip, @Param("ua") String ua);

    @Modifying
    @Query(value = "" +
            "insert ignore into message_task_for_user(id,login_user_id,message_task_id,create_time,status)\n" +
            "select LOWER(CONCAT(\n" +
            "      LPAD(HEX(FLOOR(RAND() * 0xffff)), 4, '0'),\n" +
            "      LPAD(HEX(FLOOR(RAND() * 0xffff)), 4, '0'), '',\n" +
            "      LPAD(HEX(FLOOR(RAND() * 0xffff)), 4, '0'), '',\n" +
            "      '4',\n" +
            "      LPAD(HEX(FLOOR(RAND() * 0x0fff)), 3, '0'), '',\n" +
            "      HEX(FLOOR(RAND() * 4 + 8)),\n" +
            "      LPAD(HEX(FLOOR(RAND() * 0x0fff)), 3, '0'), '',\n" +
            "      LPAD(HEX(FLOOR(RAND() * 0xffff)), 4, '0'),\n" +
            "      LPAD(HEX(FLOOR(RAND() * 0xffff)), 4, '0'),\n" +
            "      LPAD(HEX(FLOOR(RAND() * 0xffff)), 4, '0'))) uid,id, :messageTaskId, now(), 'INIT' from login_user" +
            "", nativeQuery = true
    )
    void insertAllToMessageTaskUser(@Param("messageTaskId") String messageTaskId);

    @Query(value = "select count(1) value,substring(create_time,1,10) label from login_user  where create_time >= :start and create_time <= :end group by label order by label", nativeQuery = true)
    List<Map<String, Object>> query(@Param("start") Date start, @Param("end") Date end);

    @Query(value = "SELECT * FROM login_user WHERE id = (SELECT coach_id from user_and_coach WHERE organization_id = :organizationId AND user_id = :userId AND deleted = FALSE AND status = 1);",nativeQuery = true)
    LoginUser findCoach(@Param("organizationId")String organizationId,@Param("userId")String userId);

    @Query(value = "select id from login_user where name like :name%",nativeQuery = true)
    List<String> findBySignatoryName(@Param("name") String name);

    @Modifying
    @Query(value = "update login_user set password = :newPassword where phone = :phone",nativeQuery = true)
    Integer changePasswordByPhone(@Param("phone") String phone,@Param("newPassword") String newPassword);
}
