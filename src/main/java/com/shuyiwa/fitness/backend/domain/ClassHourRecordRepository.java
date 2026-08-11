package com.shuyiwa.fitness.backend.domain;

import org.springframework.data.jpa.repository.JpaSpecificationExecutor;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.CrudRepository;
import org.springframework.data.repository.PagingAndSortingRepository;
import org.springframework.data.repository.query.Param;

public interface ClassHourRecordRepository extends CrudRepository<ClassHourRecord, String>, PagingAndSortingRepository<ClassHourRecord,String>, JpaSpecificationExecutor<ClassHourRecord>{

    @Query(value = "select max(times) times from class_hour_record where organization_id=:organizationId and login_user_id=:loginUserId",nativeQuery = true)
    public Integer getMaxTimesByOrganizationAndLoginUser(@Param("organizationId")String  organization,@Param("loginUserId") String loginUserId);
}
