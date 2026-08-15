package com.shuyiwa.fitness.gateway.api;

import com.shuyiwa.fitness.gateway.security.AgentContext;
import com.shuyiwa.fitness.gateway.service.FitnessToolService;
import org.springframework.format.annotation.DateTimeFormat;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.time.Instant;
import java.util.List;

/**
 * 第一批只读 Tool Gateway HTTP 契约。
 *
 * <p>路径使用内部命名空间，并且所有方法都必须经过双层认证拦截器。Agent 只能读取
 * 业务事实，不能在这个阶段通过任意 SQL、任意 URL 或任意用户 ID 越权访问数据。</p>
 */
@RestController
@RequestMapping("/internal/agent-tools/v1")
public class AgentToolController {

    private final FitnessToolService service;

    public AgentToolController(FitnessToolService service) {
        this.service = service;
    }

    @GetMapping("/me")
    public ToolViews.UserView currentUser(AgentContext context) {
        return service.currentUser(context);
    }

    @GetMapping("/organizations/{organizationId}")
    public ToolViews.OrganizationView organization(
            AgentContext context,
            @PathVariable String organizationId
    ) {
        return service.organization(context, organizationId);
    }

    @GetMapping("/courses")
    public List<ToolViews.CourseView> courses(
            AgentContext context,
            @RequestParam String organizationId,
            @RequestParam(required = false) Integer limit
    ) {
        return service.courses(context, organizationId, limit);
    }

    @GetMapping("/contracts")
    public List<ToolViews.ContractView> contracts(
            AgentContext context,
            @RequestParam String organizationId,
            @RequestParam(required = false) String userId,
            @RequestParam(required = false) Integer limit
    ) {
        return service.contracts(context, organizationId, userId, limit);
    }

    @GetMapping("/appointments")
    public List<ToolViews.AppointmentView> appointments(
            AgentContext context,
            @RequestParam String organizationId,
            @RequestParam(required = false) String userId,
            @RequestParam(required = false) @DateTimeFormat(iso = DateTimeFormat.ISO.DATE_TIME) Instant from,
            @RequestParam(required = false) @DateTimeFormat(iso = DateTimeFormat.ISO.DATE_TIME) Instant to,
            @RequestParam(required = false) Integer limit
    ) {
        return service.appointments(context, organizationId, userId, from, to, limit);
    }

    @GetMapping("/booking/availability")
    public ToolViews.BookingAvailabilityView bookingAvailability(
            AgentContext context,
            @RequestParam String organizationId,
            @RequestParam(required = false) String studentId,
            @RequestParam String coachId,
            @RequestParam(required = false) String courseId,
            @RequestParam @DateTimeFormat(iso = DateTimeFormat.ISO.DATE_TIME) Instant start,
            @RequestParam @DateTimeFormat(iso = DateTimeFormat.ISO.DATE_TIME) Instant end,
            @RequestParam(required = false) String excludeAppointmentId
    ) {
        return service.bookingAvailability(
                context, organizationId, studentId, coachId, courseId, start, end, excludeAppointmentId
        );
    }
}
