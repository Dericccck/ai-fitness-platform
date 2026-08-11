package com.shuyiwa.fitness.backend.domain;

import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.PagingAndSortingRepository;
import org.springframework.data.repository.query.Param;

public interface OperationLogRepository extends PagingAndSortingRepository<OperationLog, String> {

    @Query(value = "" +
            "delete from operation_log where create_time < date_sub(now(), interval :hours hour) and entity_name = :entityName ", nativeQuery = true)
    @Modifying
    int clear(@Param("hours") int hours, @Param("entityName") String entityName);

    @Query(value = "" +
            "delete from operation_log where create_time < date_sub(now(), interval :hours hour)", nativeQuery = true)
    @Modifying
    int clearAll(@Param("hours") int hours);
}
