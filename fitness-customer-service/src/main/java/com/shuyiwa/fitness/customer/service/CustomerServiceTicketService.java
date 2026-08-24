package com.shuyiwa.fitness.customer.service;

import com.shuyiwa.fitness.customer.api.CustomerServiceTicketNotFoundException;
import com.shuyiwa.fitness.customer.api.CustomerServiceTicketView;
import com.shuyiwa.fitness.customer.repository.CustomerServiceTicketRepository;
import com.shuyiwa.fitness.customer.security.CustomerServiceActor;
import org.springframework.stereotype.Service;

import java.util.List;

/**
 * 客服工单只读业务服务。
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

    private void requireOrganization(CustomerServiceActor actor, String organizationId) {
        if (organizationId == null || !actor.canAccessOrganization(organizationId)) {
            throw new org.springframework.web.server.ResponseStatusException(
                    org.springframework.http.HttpStatus.FORBIDDEN, "机构不在当前主体授权范围内");
        }
    }
}
