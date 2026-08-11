package com.shuyiwa.fitness.backend.domain;

import com.shuyiwa.fitness.backend.domain.dict.SystemSettingEnum;
import org.springframework.data.jpa.repository.JpaSpecificationExecutor;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.PagingAndSortingRepository;
import org.springframework.data.repository.query.Param;

import java.util.Date;
import java.util.List;

public interface SystemSettingsRepository extends PagingAndSortingRepository<SystemSettings, String>, JpaSpecificationExecutor<SystemSettings> {

    List<SystemSettings> findByTypeAndOrganization(SystemSettingEnum systemSettingEnum,Organization organization);

    int countByTypeAndOrganization(SystemSettingEnum systemSettingEnum,Organization organization);

    SystemSettings findByIdAndOrganization(String id, Organization organization);

    List<SystemSettings> findByAuthorAndOrganizationAndType(String author, Organization organization, SystemSettingEnum systemSettingEnum);

    @Query(value = "select * from system_settings where organization_id=:organization and type=:holiday and DATE_SUB(CURRENT_DATE(), INTERVAL :days DAY) <= create_time",nativeQuery = true)
    List<SystemSettings> findByDaysAndOrganization(Integer days, Organization organization, String holiday);

    @Query(value = "select * from system_settings where organization_id=:organization and type=:holiday and DATE_SUB(CURRENT_DATE(), INTERVAL :days DAY) <= create_time and author=:coachId",nativeQuery = true)
    List<SystemSettings> findByDaysAndOrganizationAndAuthor(Integer days, Organization organization, String holiday, String coachId);
}
