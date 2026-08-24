package com.shuyiwa.fitness.customer.service;

import com.shuyiwa.fitness.customer.api.CustomerServiceTicketNotFoundException;
import com.shuyiwa.fitness.customer.api.CustomerServiceTicketView;
import com.shuyiwa.fitness.customer.api.CustomerServiceTicketCreateRequest;
import com.shuyiwa.fitness.customer.repository.CustomerServiceTicketRepository;
import com.shuyiwa.fitness.customer.security.CustomerServiceActor;
import org.springframework.stereotype.Service;

import java.util.List;

/**
 * 客服工单业务服务。
 *
 * <p>管理员可查询机构内工单；普通用户只能查询自己的工单。即使调用方把另一个用户 ID
 * 放进查询参数，服务也会在这里拒绝，而不是依赖前端或 Agent 的自然语言约束。</p>
 */
@Service
public class CustomerServiceTicketService {

    private final CustomerServiceTicketRepository repository;

    public CustomerServiceTicketService(CustomerServiceTicketRepository repository) {
        this.repository = repository;
    }

    public List<CustomerServiceTicketView> list(CustomerServiceActor actor, String organizationId,
                                                String subjectUserId, String status, int limit) {
        requireOrganization(actor, organizationId);
        String effectiveSubject = subjectUserId;
        if (!actor.isAdministrator()) {
            if (subjectUserId != null && !actor.getUserId().equals(subjectUserId)) {
                throw new org.springframework.web.server.ResponseStatusException(
                        org.springframework.http.HttpStatus.FORBIDDEN, "只能查询自己的客服工单");
            }
            effectiveSubject = actor.getUserId();
        }
        return repository.find(organizationId, effectiveSubject, status, limit);
    }

    public CustomerServiceTicketView get(CustomerServiceActor actor, String organizationId,
                                         String ticketId) {
        requireOrganization(actor, organizationId);
        CustomerServiceTicketView ticket = repository.findById(organizationId, ticketId)
                .orElseThrow(() -> new CustomerServiceTicketNotFoundException("客服工单不存在"));
        if (!actor.isAdministrator() && !actor.getUserId().equals(ticket.getSubjectUserId())) {
            throw new org.springframework.web.server.ResponseStatusException(
                    org.springframework.http.HttpStatus.FORBIDDEN, "只能查询自己的客服工单");
        }
        return ticket;
    }

    public CustomerServiceTicketView create(CustomerServiceActor actor,
                                             CustomerServiceTicketCreateRequest request) {
        if (actor.getConfirmation() == null) {
            throw new org.springframework.web.server.ResponseStatusException(
                    org.springframework.http.HttpStatus.UNAUTHORIZED, "客服工单写入必须有确认凭证");
        }
        requireOrganization(actor, request.getOrganizationId());
        String subjectUserId = request.getSubjectUserId();
        if (subjectUserId == null || subjectUserId.trim().isEmpty()) {
            subjectUserId = actor.getUserId();
        }
        if (!actor.isAdministrator() && !actor.getUserId().equals(subjectUserId)) {
            throw new org.springframework.web.server.ResponseStatusException(
                    org.springframework.http.HttpStatus.FORBIDDEN, "只能为自己创建客服工单");
        }
        validate(request);
        return repository.insert(actor, request, subjectUserId);
    }

    private void validate(CustomerServiceTicketCreateRequest request) {
        if (request.getCategory() == null
                || !java.util.Arrays.asList("GENERAL", "APPOINTMENT", "TRAINING_PLAN",
                "COURSE", "CONTRACT").contains(request.getCategory())) {
            throw new org.springframework.web.server.ResponseStatusException(
                    org.springframework.http.HttpStatus.BAD_REQUEST, "客服工单分类不受支持");
        }
        if (isBlankOrTooLong(request.getSubject(), 255)
                || isBlankOrTooLong(request.getDescription(), 5000)) {
            throw new org.springframework.web.server.ResponseStatusException(
                    org.springframework.http.HttpStatus.BAD_REQUEST, "客服工单标题或描述长度无效");
        }
        if (request.getRelatedResourceType() != null && request.getRelatedResourceType().length() > 64) {
            throw new org.springframework.web.server.ResponseStatusException(
                    org.springframework.http.HttpStatus.BAD_REQUEST, "关联资源类型长度无效");
        }
        if (request.getRelatedResourceId() != null && request.getRelatedResourceId().length() > 128) {
            throw new org.springframework.web.server.ResponseStatusException(
                    org.springframework.http.HttpStatus.BAD_REQUEST, "关联资源标识长度无效");
        }
    }

    private boolean isBlankOrTooLong(String value, int maxLength) {
        return value == null || value.trim().isEmpty() || value.length() > maxLength;
    }

    private void requireOrganization(CustomerServiceActor actor, String organizationId) {
        if (organizationId == null || !actor.canAccessOrganization(organizationId)) {
            throw new org.springframework.web.server.ResponseStatusException(
                    org.springframework.http.HttpStatus.FORBIDDEN, "机构不在当前主体授权范围内");
        }
    }
}
