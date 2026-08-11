package com.shuyiwa.fitness.backend.domain;

import org.springframework.data.jpa.repository.JpaSpecificationExecutor;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.PagingAndSortingRepository;
import org.springframework.data.repository.query.Param;

import java.math.BigDecimal;
import java.util.Date;
import java.util.List;
import java.util.Map;

public interface OrganizationDataDayRepository extends PagingAndSortingRepository<OrganizationDataDay, String>, JpaSpecificationExecutor<OrganizationDataDay> {
    @Query(value = "" +
            " insert into organization_data_day (id,data_time,organization_id,data_type,data) " +
            " values" +
            " (REPLACE(UUID(),'-','') ,date_format( now() ,'%Y-%m-%d') , :organizationId , :dataType, :data )" +
            " on duplicate key update  data  =  values(data)", nativeQuery = true)
    @Modifying
    int saveOrganizationDataDay(@Param("organizationId") String organizationId, @Param("dataType") String dataType, @Param("data") BigDecimal data);


    @Query(value = "select data value,data_time label from organization_data_day where data_time >= :start and data_time <= :end and organization_id = :organizationId and data_type = :dataType order by data_time", nativeQuery = true)
    List<Map<String, Object>> query(@Param("start") Date start, @Param("end") Date end, @Param("organizationId") String organizationId, @Param("dataType") String dataType);
}
