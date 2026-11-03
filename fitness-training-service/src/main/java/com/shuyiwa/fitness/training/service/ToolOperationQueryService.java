package com.shuyiwa.fitness.training.service;

import com.shuyiwa.fitness.training.api.ToolOperationView;
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
            "fitness.training.plan.create_draft.v1", "fitness.training.plan.submit_review.v1", "fitness.training.plan.review.v1", "fitness.training.plan.publish.v1", "fitness.training.day.record_execution.v1"));
    private final JdbcTemplate jdbc;
    public ToolOperationQueryService(JdbcTemplate jdbc) { this.jdbc = jdbc; }

    @Transactional(readOnly = true)
    public ToolOperationView query(String operationId, String toolId, String confirmationId,
            String organizationId, String actorId, String payloadHash) {
        requireId(operationId); requireId(confirmationId); requireId(organizationId); requireId(actorId);
        if (!TOOLS.contains(toolId) || payloadHash == null || !payloadHash.matches("[0-9a-fA-F]{64}")) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "Invalid operation scope");
        }
        List<ToolOperationView> rows = jdbc.query("SELECT * FROM ( "
            + "SELECT c.request_id AS operation_id, c.tool_id, c.confirmation_id, c.organization_id, "
            + "p.created_by AS actor_id, c.payload_hash, p.id AS result_reference "
            + "FROM training_confirmation_consumption c JOIN training_plan p "
            + "ON p.create_request_id=c.request_id AND p.organization_id=c.organization_id "
            + "WHERE c.tool_id='fitness.training.plan.create_draft.v1' AND c.action='CREATE_TRAINING_DRAFT' "
            + "UNION ALL "
            + "SELECT c.request_id AS operation_id, c.tool_id, c.confirmation_id, c.organization_id, "
            + "a.actor_id, c.payload_hash, a.plan_id AS result_reference "
            + "FROM training_confirmation_consumption c JOIN training_plan_audit a ON a.request_id=c.request_id "
            + "JOIN training_plan p ON p.id=a.plan_id AND p.organization_id=c.organization_id "
            + "WHERE (c.tool_id='fitness.training.plan.submit_review.v1' AND c.action='SUBMIT_TRAINING_REVIEW' AND a.action='SUBMIT_REVIEW') "
            + "OR (c.tool_id='fitness.training.plan.review.v1' AND c.action='REVIEW_TRAINING_PLAN' AND a.action='REVIEW') "
            + "OR (c.tool_id='fitness.training.plan.publish.v1' AND c.action='PUBLISH_TRAINING_PLAN' AND a.action='PUBLISH') "
            + "UNION ALL "
            + "SELECT c.request_id AS operation_id, c.tool_id, c.confirmation_id, c.organization_id, "
            + "a.actor_id, c.payload_hash, a.execution_id AS result_reference "
            + "FROM training_confirmation_consumption c JOIN training_day_execution_audit a ON a.request_id=c.request_id "
            + "JOIN training_day_execution e ON e.id=a.execution_id AND e.organization_id=c.organization_id "
            + "WHERE c.tool_id='fitness.training.day.record_execution.v1' AND c.action='RECORD_TRAINING_DAY_EXECUTION' "
            + "AND a.action='RECORD_TRAINING_DAY_EXECUTION' "
            + ") committed_operations WHERE operation_id=? ",
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
