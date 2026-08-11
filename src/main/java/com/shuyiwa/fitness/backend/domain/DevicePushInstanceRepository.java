package com.shuyiwa.fitness.backend.domain;

import org.springframework.data.jpa.repository.JpaSpecificationExecutor;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.CrudRepository;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;

@Repository
public interface DevicePushInstanceRepository extends CrudRepository<DevicePushInstance, String>, JpaSpecificationExecutor<DevicePushInstance> {

    @Query(value = " insert ignore into device_push_instance(id,login_user_id,device_push_id,app,create_time,update_time,status,version)" +
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
            "      LPAD(HEX(FLOOR(RAND() * 0xffff)), 4, '0'))) uid" +
            " ,t.uid, :devicePushId , :app , now(), now(), 'INIT' ,0 from (" +
            " select agent_login_user_id uid from contestant_info " +
            " where deleted=false and contestant_type='INDIVIDUAL' and contest_season_id= :contestSeasonId" +
            " union" +
            " select ci.agent_login_user_id uid from contestant_info ci ,contestant_info p" +
            " where ci.contestant_type='GROUP_MEMBER' and ci.parent_id = p.id and p.contest_season_id = :contestSeasonId" +
            " ) t left outer join login_user_task_progress p on t.uid = p.login_user_id and p.login_user_task_id = :loginUserTaskId" +
            " where p.complete_time is null" +
            "", nativeQuery = true)
    @Modifying
    int appliedAndNotFinishTask(@Param("devicePushId") String devicePushId
            , @Param("contestSeasonId") String contestSeasonId, @Param("loginUserTaskId") String loginUserTaskId
            , @Param("app") String app);

    @Query(value = "update device_push_instance set update_time = now(),status='DONE',request_id=:requestId,message_id=:messageId,version=version+1 where id= :id", nativeQuery = true)
    @Modifying
    int devicePushInstanceFinish(@Param("id") String id, @Param("requestId") String requestId, @Param("messageId") String messageId);

    @Query(value = "update device_push_instance set update_time = now(),status='IGNORE',version=version+1 where id= :id", nativeQuery = true)
    @Modifying
    int devicePushInstanceIgnore(@Param("id") String id);

    @Query(value = "update device_push_instance set update_time = now(),status='FAILED',version=version+1 where id= :id", nativeQuery = true)
    @Modifying
    int devicePushInstanceFailed(@Param("id") String id);

}
