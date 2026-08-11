package com.shuyiwa.fitness.gateway.repository;

import com.shuyiwa.fitness.gateway.api.ToolViews;

import java.time.Instant;
import java.util.List;
import java.util.Optional;

/** 健身核心只读数据端口，业务服务不直接依赖 SQL 细节。 */
public interface FitnessReadRepository {

    Optional<ToolViews.UserView> findUser(String userId);

    Optional<ToolViews.OrganizationView> findOrganization(String organizationId);

    List<ToolViews.CourseView> findCourses(String organizationId, int limit);

    List<ToolViews.ContractView> findContracts(String organizationId, String userId, int limit);

    List<ToolViews.AppointmentView> findAppointments(
            String organizationId,
            String userId,
            Instant from,
            Instant to,
            int limit
    );

    boolean isOrganizationMember(String organizationId, String userId);

    boolean isCoachForUser(String organizationId, String coachId, String userId);
}
