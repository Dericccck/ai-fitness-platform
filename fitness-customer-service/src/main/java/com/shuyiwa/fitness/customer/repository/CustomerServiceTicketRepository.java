package com.shuyiwa.fitness.customer.repository;

import com.shuyiwa.fitness.customer.api.CustomerServiceTicketView;
import com.shuyiwa.fitness.customer.api.CustomerServiceConflictException;
import com.shuyiwa.fitness.customer.api.CustomerServiceTicketCreateRequest;
import com.shuyiwa.fitness.customer.security.CustomerServiceActor;
import com.shuyiwa.fitness.customer.security.CustomerServiceConfirmation;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;
import org.springframework.transaction.annotation.Transactional;

import java.sql.Timestamp;
import java.time.Instant;
import java.util.List;
import java.util.Optional;

/**
 * 客服工单仓储。
 *
 * <p>查询条件全部是显式列和参数，服务不接受 SQL、排序字段或任意过滤表达式。写入仓储
 * 只允许在同一事务内完成业务写入、确认 JTI 消费和审计落库。</p>
 */
@Repository
public class CustomerServiceTicketRepository {

    private final JdbcTemplate jdbc;

    public CustomerServiceTicketRepository(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    public List<CustomerServiceTicketView> find(String organizationId, String subjectUserId,
                                                 String status, int limit) {
        StringBuilder sql = new StringBuilder("SELECT id, organization_id, subject_user_id, created_by_user_id, category, source, "
                + "subject, description, status, related_resource_type, related_resource_id, "
                + "created_at, updated_at, resolved_at FROM agent_customer_service_ticket "
                + "WHERE organization_id = ?");
        java.util.List<Object> args = new java.util.ArrayList<>();
        args.add(organizationId);
        if (subjectUserId != null && !subjectUserId.trim().isEmpty()) {
            sql.append(" AND subject_user_id = ?");
            args.add(subjectUserId);
        }
        if (status != null && !status.trim().isEmpty()) {
            sql.append(" AND status = ?");
            args.add(status);
        }
        sql.append(" ORDER BY created_at DESC LIMIT ?");
        args.add(limit);
        return jdbc.query(sql.toString(), args.toArray(), (rs, rowNum) -> map(rs));
    }

    public Optional<CustomerServiceTicketView> findById(String organizationId, String ticketId) {
        List<CustomerServiceTicketView> result = jdbc.query(
                "SELECT id, organization_id, subject_user_id, created_by_user_id, category, source, subject, description, status, "
                        + "related_resource_type, related_resource_id, created_at, updated_at, resolved_at "
                        + "FROM agent_customer_service_ticket WHERE organization_id = ? AND id = ?",
                new Object[]{organizationId, ticketId}, (rs, rowNum) -> map(rs));
        return result.isEmpty() ? Optional.empty() : Optional.of(result.get(0));
    }

    /**
     * 在一个事务中创建工单、消费确认 JTI 并写入审计。
     *
     * <p>同一 request_id 重试时直接复用原工单；如果相同 request_id 携带了不同的参数摘要，
     * 则返回冲突，防止幂等键被错误复用。JTI 消费放在业务写入之后但仍在同一事务内，
     * 任一步失败都会整体回滚。</p>
     */
    @Transactional
    public CustomerServiceTicketView insert(CustomerServiceActor actor,
                                             CustomerServiceTicketCreateRequest request,
                                             String subjectUserId) {
        CustomerServiceConfirmation confirmation = actor.getConfirmation();
        String requestId = actor.getRequestId();
        Optional<ExistingTicket> existing = findByRequestId(requestId);
        if (existing.isPresent()) {
            if (!existing.get().payloadHash.equalsIgnoreCase(confirmation.getPayloadHash())) {
                throw new CustomerServiceConflictException("客服工单请求 ID 已绑定其他内容");
            }
            return findById(request.getOrganizationId(), existing.get().id)
                    .orElseThrow(() -> new IllegalStateException("客服工单幂等记录不存在"));
        }

        String ticketId = java.util.UUID.randomUUID().toString();
        try {
            jdbc.update("INSERT INTO agent_customer_service_ticket "
                            + "(id, organization_id, subject_user_id, created_by_user_id, category, source, subject, "
                            + "description, status, related_resource_type, related_resource_id, "
                            + "create_request_id, payload_hash) VALUES (?, ?, ?, ?, ?, 'AGENT', ?, ?, 'OPEN', ?, ?, ?, ?)",
                    ticketId, request.getOrganizationId(), subjectUserId, actor.getUserId(),
                    request.getCategory(), request.getSubject(), request.getDescription(),
                    request.getRelatedResourceType(), request.getRelatedResourceId(), requestId,
                    confirmation.getPayloadHash());
        } catch (DuplicateKeyException exception) {
            ExistingTicket raced = findByRequestId(requestId)
                    .orElseThrow(() -> new IllegalStateException("客服工单幂等记录不存在"));
            if (!raced.payloadHash.equalsIgnoreCase(confirmation.getPayloadHash())) {
                throw new CustomerServiceConflictException("客服工单请求 ID 已绑定其他内容");
            }
            return findById(request.getOrganizationId(), raced.id)
                    .orElseThrow(() -> new IllegalStateException("客服工单幂等记录不存在"));
        }

        consumeConfirmation(actor, confirmation);
        jdbc.update("INSERT INTO agent_customer_service_ticket_audit "
                        + "(ticket_id, action, actor_id, request_id, from_status, to_status, comment) "
                        + "VALUES (?, 'CREATED', ?, ?, NULL, 'OPEN', NULL)",
                ticketId, actor.getUserId(), requestId);
        return findById(request.getOrganizationId(), ticketId)
                .orElseThrow(() -> new IllegalStateException("客服工单写入后无法读取"));
    }

    private void consumeConfirmation(CustomerServiceActor actor,
                                     CustomerServiceConfirmation confirmation) {
        try {
            jdbc.update("INSERT INTO agent_customer_service_confirmation_consumption "
                            + "(jti, confirmation_id, tool_id, action, organization_id, resource, "
                            + "request_id, payload_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    confirmation.getJti(), confirmation.getConfirmationId(), confirmation.getToolId(),
                    confirmation.getAction(), confirmation.getOrganizationId(), confirmation.getResource(),
                    actor.getRequestId(), confirmation.getPayloadHash());
        } catch (DuplicateKeyException exception) {
            throw new CustomerServiceConflictException("确认凭证已被消费，不能重复创建客服工单");
        }
    }

    private Optional<ExistingTicket> findByRequestId(String requestId) {
        List<ExistingTicket> result = jdbc.query(
                "SELECT id, payload_hash FROM agent_customer_service_ticket WHERE create_request_id = ?",
                new Object[]{requestId},
                (rs, rowNum) -> new ExistingTicket(rs.getString("id"), rs.getString("payload_hash")));
        return result.isEmpty() ? Optional.empty() : Optional.of(result.get(0));
    }

    private static final class ExistingTicket {
        private final String id;
        private final String payloadHash;

        private ExistingTicket(String id, String payloadHash) {
            this.id = id;
            this.payloadHash = payloadHash;
        }
    }

    private CustomerServiceTicketView map(java.sql.ResultSet rs) throws java.sql.SQLException {
        CustomerServiceTicketView view = new CustomerServiceTicketView();
        view.setId(rs.getString("id"));
        view.setOrganizationId(rs.getString("organization_id"));
        view.setSubjectUserId(rs.getString("subject_user_id"));
        view.setCreatedByUserId(rs.getString("created_by_user_id"));
        view.setCategory(rs.getString("category"));
        view.setSource(rs.getString("source"));
        view.setSource(rs.getString("source"));
        view.setSubject(rs.getString("subject"));
        view.setDescription(rs.getString("description"));
        view.setStatus(rs.getString("status"));
        view.setRelatedResourceType(rs.getString("related_resource_type"));
        view.setRelatedResourceId(rs.getString("related_resource_id"));
        view.setCreatedAt(toInstant(rs.getTimestamp("created_at")));
        view.setUpdatedAt(toInstant(rs.getTimestamp("updated_at")));
        view.setResolvedAt(toInstant(rs.getTimestamp("resolved_at")));
        return view;
    }

    private static Instant toInstant(Timestamp timestamp) {
        return timestamp == null ? null : timestamp.toInstant();
    }
}
