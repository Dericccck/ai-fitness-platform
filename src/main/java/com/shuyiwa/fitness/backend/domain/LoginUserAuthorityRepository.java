package com.shuyiwa.fitness.backend.domain;

import com.shuyiwa.fitness.backend.domain.dict.Authority;
import org.springframework.cache.annotation.Cacheable;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaSpecificationExecutor;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.CrudRepository;
import org.springframework.data.repository.query.Param;
import static com.shuyiwa.fitness.backend.conf.CacheRedisConf.METHOD;
import static com.shuyiwa.fitness.backend.conf.CacheRedisConf.S3600;

import java.util.Date;
import java.util.List;
import java.util.Map;

public interface LoginUserAuthorityRepository extends CrudRepository<LoginUserAuthority, String>, JpaSpecificationExecutor<LoginUserAuthority> {
    List<LoginUserAuthority> findByLoginUser_IdOrderByAuthorityAsc(String userId);

    List<LoginUserAuthority> findByAuthorityAndEntityId(Authority adminOrganization, String organizationId);

    List<LoginUserAuthority> findByAuthorityAndLoginUser(Authority adminOrganization, LoginUser loginUser);

    void deleteByLoginUserIdAndEntityId(String loginUserId,String entityId);

    @Query(value = "SELECT\n" +
            "phone\n" +
            "FROM\n" +
            "login_user\n" +
            "WHERE\n" +
            "id = (\n" +
            "SELECT\n" +
            "login_user_id\n" +
            "FROM\n" +
            "login_user_authority\n" +
            "WHERE\n" +
            "authority = 'SUPER_ADMIN_ORGANIZATION'\n" +
            "AND entity_id = :orgId\n" +
            ")",nativeQuery = true)
    String getAdminPhone (@Param("orgId") String orgId);

//    @Query(value = "DELETE FROM login_user_authority WHERE entity_id = :entityId",nativeQuery = true)
    void deleteByEntityId(@Param("entityId") String entityId);

    List<LoginUserAuthority> findByLoginUserAndEntityId(LoginUser loginUser,String organizationId);

    Integer countByLoginUserAndEntityId(LoginUser loginUser,String organizationId);

//    @Cacheable(value = S3600, keyGenerator = METHOD)
    @Query(value = "SELECT  login_user_id FROM login_user_authority WHERE entity_id =:organizationId ORDER BY create_time",nativeQuery = true)
    Page<String> getMemberIds (@Param("organizationId") String organizationId, Pageable pageable);


//    @Cacheable(value = S3600, keyGenerator = METHOD)
    @Query(value = "SELECT authority FROM login_user_authority WHERE entity_id = :organizationId\n" +
            "AND login_user_id = :userId",nativeQuery = true)
    List<String> getAuthList (@Param("organizationId") String organizationId, @Param("userId") String userId);

    LoginUserAuthority findByAuthorityAndEntityIdAndLoginUser(Authority adminOrganization, String organizationId,LoginUser loginUser);

    @Query(value = "SELECT create_time FROM login_user_authority WHERE entity_id = :organizationId\n" +
            "AND login_user_id = :userId ORDER BY create_time ASC",nativeQuery = true)
    List<Date> getCreateTime (@Param("organizationId") String organizationId,@Param("userId") String userId );

    @Query(value = "SELECT in_entity_nickname FROM login_user_authority WHERE entity_id = :organizationId\n" +
            "AND login_user_id = :userId ORDER BY create_time ASC",nativeQuery = true)
    List<String> getNickName (@Param("organizationId") String organizationId,@Param("userId") String userId );

    @Query(value = "select t1.id, t1.name, t2.in_entity_nickname from login_user t1, login_user_authority t2 where t1.id = t2.login_user_id and t2.entity_id=:organizationId",nativeQuery = true)
    List<Map<String, String>> getMembers(String organizationId);

    @Query(value = "SELECT * FROM login_user_authority WHERE authority IN ('COACH','ADMIN_ORGANIZATION') AND entity_id = :organizationId",nativeQuery = true)
    List<LoginUserAuthority> findCoach(@Param("organizationId") String organizationId);
}
