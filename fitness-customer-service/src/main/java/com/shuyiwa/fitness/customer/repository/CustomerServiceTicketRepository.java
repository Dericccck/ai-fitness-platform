package com.shuyiwa.fitness.customer.repository;

import com.shuyiwa.fitness.customer.api.CustomerServiceTicketView;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;

import java.sql.Timestamp;
import java.time.Instant;
import java.util.List;
import java.util.Optional;

/**
 * 客服工单只读仓储。
 *
 * <p>查询条件全部是显式列和参数，服务不接受 SQL、排序字段或任意过滤表达式。写入仓储
 * 会在后续确认/幂等切片中单独增加，避免查询服务提前拥有写权限。</p>
 */
@Repository
public class CustomerServiceTicketRepository {

    private final JdbcTemplate jdbc;

    public CustomerServiceTicketRepository(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    public List<CustomerServiceTicketView> find(String organizationId, String subjectUserId,
                                                 String status, int limit) {
        StringBuilder sql = new StringBuilder("SELECT id, organization_id, subject_user_id, category, "
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
                "SELECT id, organization_id, subject_user_id, category, subject, description, status, "
                        + "related_resource_type, related_resource_id, created_at, updated_at, resolved_at "
                        + "FROM agent_customer_service_ticket WHERE organization_id = ? AND id = ?",
                new Object[]{organizationId, ticketId}, (rs, rowNum) -> map(rs));
        return result.isEmpty() ? Optional.empty() : Optional.of(result.get(0));
    }

    private CustomerServiceTicketView map(java.sql.ResultSet rs) throws java.sql.SQLException {
        CustomerServiceTicketView view = new CustomerServiceTicketView();
        view.setId(rs.getString("id"));
        view.setOrganizationId(rs.getString("organization_id"));
        view.setSubjectUserId(rs.getString("subject_user_id"));
        view.setCategory(rs.getString("category"));
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
