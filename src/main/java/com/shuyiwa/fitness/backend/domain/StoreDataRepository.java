package com.shuyiwa.fitness.backend.domain;

import org.springframework.data.jpa.repository.JpaSpecificationExecutor;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.CrudRepository;
import org.springframework.data.repository.query.Param;

import javax.transaction.Transactional;
import java.util.Date;
import java.util.List;
import java.util.Map;

public interface StoreDataRepository extends CrudRepository<StoreData, String>, JpaSpecificationExecutor<StoreData> {

    @Query(value = "SELECT statistics_date, data FROM store_data " +
            "WHERE type = :type AND :startDate <= statistics_date" +
            " AND statistics_date < :endDate",nativeQuery = true)
    List<Map<String, Object>> findByTypeAndDate(@Param("type") String type, @Param("startDate") Date startDate, @Param("endDate")Date endDate);

    @Query(value = "select DATE_FORMAT(statistics_date,'%Y-%m') statistics_date,sum(data) data from store_data where type = :type AND  ( statistics_date >= :startDate and  statistics_date <= :endDate ) group by DATE_FORMAT(statistics_date,'%Y-%m');",nativeQuery = true)
    List<Map<String, Object>> findByTypeAndMonth(@Param("type") String type, @Param("startDate") Date startDate, @Param("endDate")Date endDate);

    @Modifying
    @Transactional
    @Query(value = "DELETE FROM store_data WHERE type = :type and statistics_date = DATE_FORMAT(:statisticsDate,'%Y-%m-%d')",nativeQuery = true)
    void deleteByTypeAndStatisticsDate(@Param("type")String type, @Param("statisticsDate")Date statisticsDate);
}
