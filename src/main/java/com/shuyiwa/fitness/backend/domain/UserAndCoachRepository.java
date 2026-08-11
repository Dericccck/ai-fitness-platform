package com.shuyiwa.fitness.backend.domain;

import org.springframework.data.jpa.repository.JpaSpecificationExecutor;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.CrudRepository;
import org.springframework.data.repository.query.Param;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;
import java.util.Map;
import java.util.Optional;

public interface UserAndCoachRepository extends CrudRepository<UserAndCoach, String>, JpaSpecificationExecutor<UserAndCoach> {

    @Query(value = "SELECT count(*) FROM user_and_coach WHERE organization_id = :organizationId AND user_id = :userId and status = 1 and deleted=0 ",nativeQuery = true)
    int searchCount(@Param("organizationId") String organizationId,@Param("userId") String userId);

    @Query(value = "SELECT count(*) FROM user_and_coach WHERE organization_id = :organizationId AND user_id = :userId and status = 0 and deleted=0 ",nativeQuery = true)
    int searchCount1(@Param("organizationId") String organizationId,@Param("userId") String userId);

    @Query(value = "SELECT count(*) FROM user_and_coach WHERE organization_id = :organizationId AND user_id = :userId and status = 4 and deleted=0 ",nativeQuery = true)
    int searchCount2(@Param("organizationId") String organizationId,@Param("userId") String userId);

    @Query(value = "SELECT * FROM user_and_coach WHERE organization_id = :organizationId AND user_id = :userId and status = 4 and deleted=0 ",nativeQuery = true)
    UserAndCoach getRelieved(@Param("organizationId") String organizationId,@Param("userId") String userId);

//    @Modifying
//    @Query(value = "update user_and_coach set class_hour = class_hour+:classHour,version=version+1 where  organization_id = :organizationId AND user_id = :userId and version=:version", nativeQuery = true)
//    int addClassHour(@Param("organizationId") String organizationId,@Param("userId") String userId,@Param("classHour")int classHour,@Param("version")int version);

    @Modifying
    @Query(value = "update user_and_coach set amount = amount+:amount,version=version+1 where  organization_id = :organizationId AND user_id = :userId and version=:version", nativeQuery = true)
    int addAmount(@Param("organizationId") String organizationId,@Param("userId") String userId,@Param("amount")int amount,@Param("version")int version);


    @Transactional
    @Modifying
    @Query(value = "update user_and_coach set amount = amount + :amount,version=version+1 where organization_id = :organizationId AND user_id = :userId and version=:version", nativeQuery = true)
    int backupAmount(@Param("organizationId") String organizationId,@Param("userId") String userId,@Param("amount")Integer amount,@Param("version") Integer version);


    @Query(value = "SELECT count(0) FROM user_and_coach WHERE organization_id = :organizationId AND coach_id = :coachId and deleted=0  and status<4 ",nativeQuery = true)
    int countByOrgAndcoach(@Param("organizationId") String organizationId,@Param("coachId") String coachId);

    Optional<UserAndCoach> findByOrganizationAndUser(@Param("organization")Organization organization,@Param("user")LoginUser user);

    @Query(value = "select * from user_and_coach where user_id=:userId and status in (0,1,4)  and deleted=false ",nativeQuery = true)
    List<UserAndCoach> findByUser(@Param("userId")String userId);

//    @Query(value="SELECT class_hour classHour , version FROM user_and_coach WHERE organization_id = :organizationId\n" +
//            "AND user_id = :userId\n" +
//            "AND deleted = FALSE",nativeQuery = true)
//    Map<String, Integer> getClassHour(@Param("organizationId") String organizationId, @Param("userId")String userId);

    @Query(value="SELECT amount, version FROM user_and_coach WHERE organization_id = :organizationId\n" +
            "AND user_id = :userId\n" +
            "AND deleted = FALSE",nativeQuery = true)
    Map<String, Integer> getAmount(@Param("organizationId") String organizationId, @Param("userId")String userId);


    @Query(value = "UPDATE user_and_coach SET amount = amount - :coursePrice ,version = version+1 \n" +
            "WHERE user_id = :userId AND organization_id = :organizationId AND version = :version",nativeQuery = true)
    @Modifying
    void minusOne(@Param("organizationId") String organizationId,@Param("userId")String userId,@Param("version")Integer version,@Param("coursePrice")Integer coursePrice);

    @Transactional
    @Modifying
    @Query(value = "update user_and_coach set status=:status,coach_id=:coachId where id=:id",nativeQuery = true)
    void updateStatusById(@Param("status") int status,@Param("coachId")String coachId,@Param("id") String id);

    @Query(value = "select * from user_and_coach where user_id=:userId and organization_id=:orgId and deleted=false ",nativeQuery = true)
    UserAndCoach findByOrganizationIdAndUserId(String orgId, String userId);

    @Query(value = "select count(DISTINCT user_id) from user_and_coach where deleted=false ",nativeQuery = true)
    Integer countUserId();
}
