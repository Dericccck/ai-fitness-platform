package com.shuyiwa.fitness.backend.domain;

import org.springframework.data.jpa.repository.JpaSpecificationExecutor;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.CrudRepository;
import org.springframework.data.repository.PagingAndSortingRepository;
import org.springframework.data.repository.query.Param;

import java.util.Date;
import java.util.Optional;

public interface NewsRepository extends CrudRepository<News, String>, PagingAndSortingRepository<News,String>, JpaSpecificationExecutor<News>{

    @Modifying
    @Query(value = "update news set  handle_result = :handle_result,handle_user_id=:userId,version=version+1 ,handle_time=now() where id = :id  and version=:version", nativeQuery = true)
    int updateNews(@Param("handle_result") int handle_result,@Param("id") String id,@Param("version")int version,@Param("userId")String userId);

    @Query(value = "select count(0) num from news where receive_login_user_id=:receiveUserId and handle_result=0 and organization_id=:organizationId and deleted = false",nativeQuery = true)
    int countNewsByReceiveLoginUserAndHandleResult(@Param("receiveUserId")String receiveUserId,@Param("organizationId")String organizationId);

    @Query(value = "select count(0) num from news where (receive_login_user_id=:userId or create_login_user_id=:userId) and handle_result=0 and organization_id=:organizationId",nativeQuery = true)
    int countNewsByLoginUserAndHandleResult(@Param("userId")String userId,@Param("organizationId")String organizationId);

    @Query(value = "select count(0) num from news where handle_result=0 and organization_id=:organizationId and deleted=false and news_type='finishClass' and create_time >:createTime",nativeQuery = true)
    Integer countUndoNews(@Param("organizationId")String organizationId,@Param("createTime")Date createTime);

    @Query(value = "select count(0) num from news where handle_result=0 and organization_id=:organizationId and deleted=false and news_type='finishClass'",nativeQuery = true)
    Integer countUndoNewsNoCreateTime(@Param("organizationId")String organizationId);

    @Modifying
    @Query(value = "update news set  handle_result = :handle_result,handle_user_id=:userId,version=version+1 ,handle_time=now() where handle_result=0 and news_type=:newsType  and entity_id=:entityId ", nativeQuery = true)
    int forceUPdateNews(@Param("handle_result") int handle_result,@Param("userId")String userId,@Param("newsType")String newsType,@Param("entityId")String entityId);

    @Modifying
    @Query(value = "update news set  deleted = true  where entity_id=:entityId ", nativeQuery = true)
    int deleteNewsByEntityId(@Param("entityId")String entityId);

    @Query(value = "select id from news where news_type=:newsType and entity_id=:entityId and handle_result=0 and deleted=false",nativeQuery = true)
    String findByNewsTypeAndEntityId(@Param("newsType")String newsType, @Param("entityId")String entityId);
}
