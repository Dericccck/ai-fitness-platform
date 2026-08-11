package com.shuyiwa.fitness.backend.domain;

import org.springframework.data.jpa.repository.JpaSpecificationExecutor;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.PagingAndSortingRepository;
import org.springframework.data.repository.query.Param;
import org.springframework.transaction.annotation.Transactional;

import java.util.Date;
import java.util.List;
import java.util.Map;

public interface ClassHourStatisticsRepository extends PagingAndSortingRepository<ClassHourStatistics, String>, JpaSpecificationExecutor<ClassHourStatistics> {

    @Query(value = "SELECT statistics_date, SUM(class_number) classHour FROM class_hour_statistics " +
            "WHERE organization_id=:organizationId and DATE_SUB(CURRENT_DATE(), INTERVAL :days DAY) <= statistics_date" +
            " AND statistics_date <CURRENT_DATE() GROUP BY statistics_date",nativeQuery = true)
    List<Map<String,Object>> totalStatistics(@Param("days") Integer days,@Param("organizationId")String organizationId);

    @Query(value = "SELECT statistics_date, SUM(class_number) classHour FROM class_hour_statistics " +
            "WHERE organization_id=:organizationId AND coach_id=:coachId AND DATE_SUB(CURRENT_DATE(), INTERVAL :days DAY) <= statistics_date" +
            " AND statistics_date <CURRENT_DATE() GROUP BY statistics_date\n",nativeQuery = true)
    List<Map<String, Object>> singleCoach(@Param("organizationId")String organizationId,@Param("days") Integer days, @Param("coachId") String coachId);

    @Query(value = "INSERT IGNORE INTO class_hour_statistics VALUES (REPLACE(UUID(),'-',''),:classNumber," +
            ":coachId,:createTime,:organizationId,:statisticsDate)", nativeQuery = true)
    @Modifying
    int insert(@Param("classNumber") Integer classNumber, @Param("coachId") String coachId, @Param("statisticsDate") Date statisticsDate, @Param("createTime") Date createTime, @Param("organizationId") String organizationId);

    @Query(value = "DELETE FROM class_hour_statistics  WHERE coach_id=:coachId and organization_id=:organizationId and statistics_date=:date",nativeQuery = true)
    @Modifying
    void deleteByCoachIdAndStatisticsDate(@Param("organizationId") String organizationId, @Param("coachId") String coachId, @Param("date") Date date);

    @Query(value = "DELETE FROM class_hour_statistics  WHERE organization_id=:organizationId and statistics_date=:date",nativeQuery = true)
    @Modifying
    void deleteByDate(@Param("organizationId") String organizationId, @Param("date") Date date);

    @Transactional
    @Query(value = "update class_hour_statistics set class_number=:classNumber where coach_id=:coachId and organization_id=:organizationId and statistics_date=:statisticsDate and class_number!=:classNumber",nativeQuery = true)
    @Modifying
    void update(@Param("coachId")String coachId, @Param("organizationId")String organizationId, @Param("statisticsDate")Date statisticsDate, @Param("classNumber")Integer classNumber);
}
