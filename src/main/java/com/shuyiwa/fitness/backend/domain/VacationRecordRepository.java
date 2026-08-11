package com.shuyiwa.fitness.backend.domain;

import org.springframework.data.jpa.repository.JpaSpecificationExecutor;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.PagingAndSortingRepository;

import java.util.List;

public interface VacationRecordRepository  extends PagingAndSortingRepository<VacationRecord, String>, JpaSpecificationExecutor<VacationRecord> {

    @Query(value = "select * from vacation_record where organization_id=:organization and DATE_SUB(CURRENT_DATE(), INTERVAL :days DAY) <= create_time order by create_time desc",nativeQuery = true)
    List<VacationRecord> findByDaysAndOrganization(Integer days, Organization organization);

    @Query(value = "select * from vacation_record where organization_id=:organization and DATE_SUB(CURRENT_DATE(), INTERVAL :days DAY) <= create_time and coach_id=:coachId order by create_time desc",nativeQuery = true)
    List<VacationRecord> findByDaysAndOrganizationAndCoachId(Integer days, Organization organization, String coachId);

    @Query(value = "select * from vacation_record where organization_id=:organization and coach_id=:coachId order by start_date desc",nativeQuery = true)
    List<VacationRecord> findByOrganizationAndCoachId(Organization organization, String coachId);

    @Query(value = "select * from vacation_record where organization_id=:organization order by create_time desc",nativeQuery = true)
    List<VacationRecord> findByOrganization(Organization organization);

    @Query(value = "select * from vacation_record where organization_id=:organization and coach_id=:coachId and status=:status and CURRENT_DATE() <= end_date ",nativeQuery = true)
    List<VacationRecord> findByOrganizationAndCoachIdAndStatus(Organization organization, String coachId, int status);

    @Query(value = "select * from vacation_record where organization_id=:organization and coach_id=:coachId order by create_time desc",nativeQuery = true)
    List<VacationRecord> findByOrganizationAndCoachIdAndCreateTime(Organization organization, String coachId);
}
