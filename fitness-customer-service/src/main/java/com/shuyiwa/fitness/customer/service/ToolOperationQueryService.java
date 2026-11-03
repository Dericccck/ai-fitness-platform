package com.shuyiwa.fitness.customer.service;

import com.shuyiwa.fitness.customer.api.ToolOperationView;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.http.HttpStatus;
import org.springframework.web.server.ResponseStatusException;
import java.util.Arrays;
import java.util.List;

/** Queries committed business/audit facts together with the confirmation consumed in that transaction. */
@Service
public class ToolOperationQueryService {
    private static final java.util.Set<String> TOOLS = new java.util.HashSet<>(Arrays.asList(
            "fitness.support.ticket.create.v1"));
    private final JdbcTemplate jdbc;
    public ToolOperationQueryService(JdbcTemplate jdbc) { this.jdbc = jdbc; }

    @Transactional(readOnly = true)
    public ToolOperationView query(String operationId, String toolId, String confirmationId,
            String organizationId, String actorId, String payloadHash) {
        requireId(operationId); requireId(confirmationId); requireId(organizationId); requireId(actorId);
        if (!TOOLS.contains(toolId) || payloadHash == null || !payloadHash.matches("[0-9a-fA-F]{64}")) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "Invalid operation scope");
        }
        List<ToolOperationView> rows = jdbc.query("SELECT c.request_id AS operation_id, c.tool_id, c.confirmation_id, c.organization_id, "
            + "a.actor_id, c.payload_hash, t.id AS result_reference "
            + "FROM agent_customer_service_confirmation_consumption c "
            + "JOIN agent_customer_service_ticket t ON t.create_request_id=c.request_id "
            + "AND t.organization_id=c.organization_id AND t.payload_hash=c.payload_hash "
            + "JOIN agent_customer_service_ticket_audit a ON a.request_id=c.request_id AND a.ticket_id=t.id "
            + "AND a.actor_id=t.created_by_user_id AND a.action='CREATED' "
            + "WHERE c.action='CREATE_CUSTOMER_SERVICE_TICKET' AND c.tool_id='fitness.support.ticket.create.v1' AND c.request_id=? ",
                new Object[]{operationId}, (rs, rowNum) -> new ToolOperationView(
                    rs.getString("operation_id"), rs.getString("tool_id"), rs.getString("confirmation_id"),
                    rs.getString("organization_id"), rs.getString("actor_id"), rs.getString("payload_hash"),
                    "SUCCEEDED", rs.getString("result_reference")));
        if (rows.size() > 1) throw new IllegalStateException("Ambiguous operation evidence");
        if (rows.isEmpty()) return new ToolOperationView(operationId, toolId, confirmationId,
                organizationId, actorId, payloadHash, "UNKNOWN", null);
        ToolOperationView result = rows.get(0);
        if (!operationId.equals(result.getOperationId()) || !toolId.equals(result.getToolId())
                || !confirmationId.equals(result.getConfirmationId()) || !organizationId.equals(result.getOrganizationId())
                || !actorId.equals(result.getActorId()) || !payloadHash.equalsIgnoreCase(result.getPayloadHash())) {
            throw new ResponseStatusException(HttpStatus.FORBIDDEN, "Operation does not match scope");
        }
        return result;
    }
    private static void requireId(String value) {
        if (value == null || !value.matches("[A-Za-z0-9._:-]{1,128}")
                || ".".equals(value) || "..".equals(value)) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "Invalid operation identity");
        }
    }
}
