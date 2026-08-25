package com.shuyiwa.fitness.customer.service;

import com.shuyiwa.fitness.customer.api.CustomerServiceTicketNotFoundException;
import com.shuyiwa.fitness.customer.api.CustomerServiceTicketView;
import com.shuyiwa.fitness.customer.api.CustomerServiceTicketCreateRequest;
import com.shuyiwa.fitness.customer.repository.CustomerServiceTicketRepository;
import com.shuyiwa.fitness.customer.security.CustomerServiceActor;
import com.shuyiwa.fitness.customer.security.CustomerServiceConfirmation;
import com.shuyiwa.fitness.customer.security.CustomerServiceSecurityException;
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
        validateConfirmation(actor.getConfirmation(), request.getOrganizationId(), subjectUserId);
        return repository.insert(actor, request, subjectUserId);
    }

    /**
     * 在业务服务再次核对确认声明，形成 Gateway 之外的纵深防御。
     *
     * <p>Gateway 已经验证了签名 Token，但内部服务不能只相信请求头“看起来完整”。如果
     * 内部 Token 被错误复用或客服服务被绕过，工具、动作、机构和资源仍必须与本次工单
     * 创建契约一致；否则请求在进入事务前直接拒绝。</p>
     */
    private void validateConfirmation(CustomerServiceConfirmation confirmation,
                                      String organizationId, String subjectUserId) {
        if (confirmation == null) {
            throw new CustomerServiceSecurityException("客服工单写入必须有确认凭证");
        }
        if (!"fitness.support.ticket.create.v1".equals(confirmation.getToolId())
                || !"CREATE_CUSTOMER_SERVICE_TICKET".equals(confirmation.getAction())) {
            throw new CustomerServiceSecurityException("客服工单确认动作不匹配");
        }
        if (!organizationId.equals(confirmation.getOrganizationId())) {
            throw new CustomerServiceSecurityException("客服工单确认机构不匹配");
        }
        String expectedResource = organizationId + ":" + subjectUserId;
        if (!expectedResource.equals(confirmation.getResource())) {
            throw new CustomerServiceSecurityException("客服工单确认资源不匹配");
        }
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
