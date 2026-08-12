package com.shuyiwa.fitness.gateway.service;

import com.shuyiwa.fitness.gateway.api.ToolViews;
import com.shuyiwa.fitness.gateway.repository.FitnessReadRepository;
import com.shuyiwa.fitness.gateway.security.AgentContext;
import com.shuyiwa.fitness.gateway.security.GatewayForbiddenException;
import com.shuyiwa.fitness.gateway.security.GatewayResourceNotFoundException;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.List;

/**
 * Agent 只读工具的业务编排层。
 *
 * <p>所有方法都先做组织范围和用户范围校验，再访问 Repository。Controller 不允许
 * 直接拼接查询条件，避免新增接口时漏掉资源级权限。返回条数也在这里统一限制，
 * 防止模型通过一个自然语言请求拉取整张历史表。</p>
 */
@Service
@Transactional(readOnly = true)
public class FitnessToolService {

    private static final int DEFAULT_LIMIT = 20;
    private static final int MAX_LIMIT = 100;

    private final FitnessReadRepository repository;

    public FitnessToolService(FitnessReadRepository repository) {
        this.repository = repository;
    }

    public ToolViews.UserView currentUser(AgentContext context) {
        return repository.findUser(context.getSubjectUserId())
                .orElseThrow(() -> new GatewayResourceNotFoundException("user not found"));
    }

    public ToolViews.OrganizationView organization(AgentContext context, String organizationId) {
        requireOrganization(context, organizationId);
        return repository.findOrganization(organizationId)
                .orElseThrow(() -> new GatewayResourceNotFoundException("organization not found"));
    }

    public List<ToolViews.CourseView> courses(AgentContext context, String organizationId, Integer limit) {
        requireOrganization(context, organizationId);
        return repository.findCourses(organizationId, normalizeLimit(limit));
    }

    public List<ToolViews.ContractView> contracts(
            AgentContext context,
            String organizationId,
            String requestedUserId,
            Integer limit
    ) {
        requireOrganization(context, organizationId);
        String userId = resolveUserForRead(context, organizationId, requestedUserId);
        return repository.findContracts(organizationId, userId, normalizeLimit(limit));
    }

    public List<ToolViews.AppointmentView> appointments(
            AgentContext context,
            String organizationId,
            String requestedUserId,
            Instant from,
            Instant to,
            Integer limit
    ) {
        requireOrganization(context, organizationId);
        String userId = resolveUserForRead(context, organizationId, requestedUserId);
        Instant start = from == null ? Instant.now().minus(1, ChronoUnit.DAYS) : from;
        Instant end = to == null ? Instant.now().plus(30, ChronoUnit.DAYS) : to;
        if (!end.isAfter(start) || end.isAfter(start.plus(92, ChronoUnit.DAYS))) {
            throw new IllegalArgumentException("appointment time range must be 0 to 92 days");
        }
        return repository.findAppointments(organizationId, userId, start, end, normalizeLimit(limit));
    }

    private String resolveUserForRead(AgentContext context, String organizationId, String requestedUserId) {
        String userId = requestedUserId == null || requestedUserId.trim().isEmpty()
                ? context.getSubjectUserId() : requestedUserId;

        if (context.canReadAnyUserInOrganization()) {
            return userId;
        }
        if (context.hasRole(AgentContext.ROLE_STUDENT)
                && !context.getSubjectUserId().equals(userId)) {
            throw new GatewayForbiddenException("student can only read own fitness data");
        }
        if (context.hasRole(AgentContext.ROLE_COACH)
                && !context.getSubjectUserId().equals(userId)
                && !repository.isCoachForUser(organizationId, context.getSubjectUserId(), userId)) {
            throw new GatewayForbiddenException("coach is not assigned to this student");
        }
        if (!context.hasRole(AgentContext.ROLE_COACH)
                && !context.hasRole(AgentContext.ROLE_STUDENT)
                && !context.getSubjectUserId().equals(userId)) {
            throw new GatewayForbiddenException("context cannot read another user");
        }
        if (!repository.isOrganizationMember(organizationId, userId)) {
            throw new GatewayForbiddenException("user is not a member of this organization");
        }
        return userId;
    }

    private void requireOrganization(AgentContext context, String organizationId) {
        if (organizationId == null || organizationId.trim().isEmpty()
                || !context.canAccessOrganization(organizationId)) {
            throw new GatewayForbiddenException("organization is outside agent context scope");
        }
    }

    private int normalizeLimit(Integer requestedLimit) {
        if (requestedLimit == null) {
            return DEFAULT_LIMIT;
        }
        if (requestedLimit < 1 || requestedLimit > MAX_LIMIT) {
            throw new IllegalArgumentException("limit must be between 1 and " + MAX_LIMIT);
        }
        return requestedLimit;
    }
}
