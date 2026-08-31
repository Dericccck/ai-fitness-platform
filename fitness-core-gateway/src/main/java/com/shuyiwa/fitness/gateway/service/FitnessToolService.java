package com.shuyiwa.fitness.gateway.service;

import com.shuyiwa.fitness.gateway.api.ToolViews;
import com.shuyiwa.fitness.gateway.repository.FitnessReadRepository;
import com.shuyiwa.fitness.gateway.security.AgentContext;
import com.shuyiwa.fitness.gateway.security.GatewayForbiddenException;
import com.shuyiwa.fitness.gateway.security.GatewayResourceNotFoundException;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.Instant;
import java.time.LocalDate;
import java.time.Clock;
import java.time.temporal.ChronoUnit;
import java.util.ArrayList;
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
    private final Clock clock;

    /**
     * 生产装配使用系统 UTC 时钟；将默认构造入口保留为 Spring Bean 入口，避免业务代码
     * 在各处直接调用 {@link Instant#now()}，同时允许单元测试注入固定时钟验证边界日期。
     */
    @org.springframework.beans.factory.annotation.Autowired
    public FitnessToolService(FitnessReadRepository repository) {
        this(repository, Clock.systemUTC());
    }

    /**
     * 可注入时钟的构造入口，测试可以固定当前时间，避免“测试预约日期”随日历自然流逝。
     */
    FitnessToolService(FitnessReadRepository repository, Clock clock) {
        this.repository = repository;
        this.clock = clock;
    }

    public ToolViews.UserView currentUser(AgentContext context) {
        return repository.findUser(context.getSubjectUserId())
                .orElseThrow(() -> new GatewayResourceNotFoundException("用户不存在"));
    }

    public ToolViews.OrganizationView organization(AgentContext context, String organizationId) {
        requireOrganization(context, organizationId);
        return repository.findOrganization(organizationId)
                .orElseThrow(() -> new GatewayResourceNotFoundException("机构不存在"));
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
            throw new IllegalArgumentException("预约时间范围必须为 0 到 92 天");
        }
        return repository.findAppointments(organizationId, userId, start, end, normalizeLimit(limit));
    }

    /**
     * 预约写入前的只读预检。
     *
     * <p>预检包含组织、学员、教练、时间范围、教练冲突和非营业日判断。它不会锁定
     * 数据、扣减课时或创建预约，所以结果只适合展示和生成确认摘要；写入事务必须
     * 再次执行同等规则，不能把预检结果当成预约成功凭证。</p>
     */
    public ToolViews.BookingAvailabilityView bookingAvailability(
            AgentContext context,
            String organizationId,
            String requestedStudentId,
            String coachId,
            String courseId,
            Instant start,
            Instant end,
            String excludeAppointmentId
    ) {
        requireOrganization(context, organizationId);
        String studentId = resolveUserForRead(context, organizationId, requestedStudentId);
        if (coachId == null || coachId.trim().isEmpty() || !repository.isCoachInOrganization(organizationId, coachId)) {
            throw new GatewayResourceNotFoundException("机构中不存在该教练");
        }
        if (start == null || end == null || !end.isAfter(start)) {
            throw new IllegalArgumentException("预约结束时间必须晚于开始时间");
        }
        if (end.isAfter(start.plus(8, ChronoUnit.HOURS))) {
            throw new IllegalArgumentException("预约时长不能超过 8 小时");
        }

        List<String> reasons = new ArrayList<>();
        if (start.isBefore(Instant.now(clock))) {
            reasons.add("START_TIME_IN_PAST");
        }
        List<ToolViews.AppointmentView> conflicts = repository.findCoachAppointments(
                organizationId, coachId, start, end, excludeAppointmentId, 20
        );
        if (!conflicts.isEmpty()) {
            reasons.add("COACH_TIME_CONFLICT");
        }
        LocalDate date = start.atZone(java.time.ZoneId.of("Asia/Shanghai")).toLocalDate();
        if (repository.findNonBusinessDays(organizationId, date, date).contains(date)) {
            reasons.add("ORGANIZATION_NON_BUSINESS_DAY");
        }
        if (repository.findCoachVacationDays(organizationId, coachId, date, date).contains(date)) {
            reasons.add("COACH_ON_LEAVE");
        }

        return new ToolViews.BookingAvailabilityView(
                organizationId,
                studentId,
                coachId,
                courseId,
                start,
                end,
                reasons.isEmpty(),
                reasons,
                conflicts
        );
    }

    private String resolveUserForRead(AgentContext context, String organizationId, String requestedUserId) {
        String userId = requestedUserId == null || requestedUserId.trim().isEmpty()
                ? context.getSubjectUserId() : requestedUserId;

        if (context.canReadAnyUserInOrganization()) {
            return userId;
        }
        if (context.hasRole(AgentContext.ROLE_STUDENT)
                && !context.getSubjectUserId().equals(userId)) {
            throw new GatewayForbiddenException("学员只能读取自己的健身数据");
        }
        if (context.hasRole(AgentContext.ROLE_COACH)
                && !context.getSubjectUserId().equals(userId)
                && !repository.isCoachForUser(organizationId, context.getSubjectUserId(), userId)) {
            throw new GatewayForbiddenException("该教练未负责此学员");
        }
        if (!context.hasRole(AgentContext.ROLE_COACH)
                && !context.hasRole(AgentContext.ROLE_STUDENT)
                && !context.getSubjectUserId().equals(userId)) {
            throw new GatewayForbiddenException("当前上下文不能读取其他用户的数据");
        }
        if (!repository.isOrganizationMember(organizationId, userId)) {
            throw new GatewayForbiddenException("用户不是该机构成员");
        }
        return userId;
    }

    private void requireOrganization(AgentContext context, String organizationId) {
        if (organizationId == null || organizationId.trim().isEmpty()
                || !context.canAccessOrganization(organizationId)) {
            throw new GatewayForbiddenException("机构不在 Agent 上下文授权范围内");
        }
    }

    private int normalizeLimit(Integer requestedLimit) {
        if (requestedLimit == null) {
            return DEFAULT_LIMIT;
        }
        if (requestedLimit < 1 || requestedLimit > MAX_LIMIT) {
            throw new IllegalArgumentException("limit 必须介于 1 和 " + MAX_LIMIT + " 之间");
        }
        return requestedLimit;
    }
}
