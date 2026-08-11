package com.shuyiwa.fitness.backend.domain;

import org.springframework.data.jpa.repository.JpaSpecificationExecutor;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.CrudRepository;
import org.springframework.data.repository.query.Param;

import java.util.Date;
import java.util.List;
import java.util.Map;

public interface StoreDataDetailsRepository extends CrudRepository<StoreDataDetails, String>, JpaSpecificationExecutor<StoreDataDetails> {

    @Query(value = "select SUM(revenue_amount) from store_data_details where (case when :coachId = '' then 1=1 else find_in_set(:coachId,coach_ids) end) and (:startDate <= create_time AND create_time <= :endDate) and type in (:types)", nativeQuery = true)
    Integer totalRevenue(@Param("startDate") Date startDate, @Param("endDate")Date endDate, @Param("coachId")String coachId, @Param("types")String[] types);
}
